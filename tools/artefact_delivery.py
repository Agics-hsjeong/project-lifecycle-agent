"""Authenticated registered-file delivery. No arbitrary-path or active inline API."""
import hashlib
import io
import re
from pathlib import Path
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

from tools import workspace
from tools.sensitive_data import is_sensitive_path, contains_sensitive_data
from tools.storage import consistent_read

MAX_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024


class DeliveryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _check_content(raw):
    if contains_sensitive_data(raw):
        raise DeliveryError("인증 정보로 보이는 내용이 있어 파일 전달을 차단했습니다.", 403)
    if raw[:2] != b"PK":
        return
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            size = 0
            for member in archive.infolist():
                if member.is_dir():
                    continue
                size += member.file_size
                if size > MAX_EXPANDED_BYTES or member.file_size > MAX_BYTES:
                    raise DeliveryError("압축 문서 검사 크기 한도를 초과했습니다.", 413)
                if is_sensitive_path(member.filename):
                    raise DeliveryError("압축 문서에 인증 자료 경로가 있어 전달을 차단했습니다.", 403)
                if member.filename.lower().endswith((".xml", ".json", ".txt", ".yaml", ".yml", ".md", ".pem", ".key")):
                    with archive.open(member) as entry:
                        content = entry.read(MAX_BYTES + 1)
                    if len(content) > MAX_BYTES or contains_sensitive_data(content):
                        raise DeliveryError("압축 문서에서 인증 정보로 보이는 내용을 찾았습니다.", 403)
    except BadZipFile as error:
        raise DeliveryError("압축 문서의 구조를 확인할 수 없습니다.") from error


def _mime(path, raw):
    extension = path.suffix.lower()
    if extension == '.pdf' and raw.startswith(b'%PDF-'):
        return 'application/pdf'
    if extension == '.png' and raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if extension in {'.jpg', '.jpeg'} and raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    return 'application/octet-stream'


@consistent_read
def get(folder, identifier, expected_sha):
    if not isinstance(identifier, str) or not identifier or not isinstance(expected_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha):
        raise DeliveryError("등록된 산출물 ID와 SHA-256이 필요합니다.")
    snapshot = workspace.snapshot(folder)
    item = next((item for item in snapshot['artefacts'] if item['id'] == identifier), None)
    if item is None or not item.get('exists'):
        raise DeliveryError("등록된 산출물이 없습니다.", 404)
    if item.get('sha256') != expected_sha:
        raise DeliveryError("산출물이 변경됐습니다. 최신 목록에서 다시 확인하세요.", 409)
    root = Path(folder).resolve()
    path = workspace.valid_artifact(root, item['path'])
    if is_sensitive_path(item['path']):
        raise DeliveryError("인증 자료는 전달할 수 없습니다.", 403)
    if path.stat().st_size > MAX_BYTES:
        raise DeliveryError("파일 수령 한도는 32MiB입니다. 현재 AI에서 원본 위치를 확인하세요.", 413)
    with path.open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise DeliveryError("파일 수령 한도는 32MiB입니다.", 413)
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise DeliveryError("파일을 읽는 동안 산출물이 변경됐습니다.", 409)
    _check_content(raw)
    ascii_name = 'download' + re.sub(r'[^a-zA-Z0-9.]', '', path.suffix)[:12]
    return {"body": raw, "content_type": _mime(path, raw),
            "headers": {"Content-Disposition": 'attachment; filename="' + ascii_name + '"; filename*=UTF-8\'\'' + quote(path.name, safe=''),
                        "X-Content-SHA256": expected_sha,
                        "Content-Security-Policy": "default-src 'none'; sandbox; frame-ancestors 'none'"}}
