"""Source preservation metadata and bounded, non-executing text extraction."""
import hashlib
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024


def hash_file(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def extract_text(path):
    path = Path(path)
    extension = path.suffix.lower()
    if extension in {".md", ".txt", ".csv", ".json", ".tsv"}:
        content = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-16", "cp949"):
            try:
                if encoding == "utf-16" and not content.startswith((b"\xff\xfe", b"\xfe\xff")):
                    continue
                return content.decode(encoding), "extracted", ""
            except UnicodeError:
                continue
        return None, "needs_reader", "텍스트 인코딩을 판별하지 못했습니다."
    if extension in {".docx", ".hwpx"}:
        with ZipFile(path) as archive:
            names = ["word/document.xml"] if extension == ".docx" else sorted(name for name in archive.namelist() if name.startswith("Contents/section") and name.endswith(".xml"))
            if not names or sum(archive.getinfo(name).file_size for name in names) > MAX_XML_BYTES:
                return None, "needs_reader", "XML 본문 누락 또는 추출 크기 제한 초과"
            paragraphs = []
            for name in names:
                raw = archive.read(name)
                if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    return None, "needs_reader", "DTD/외부 엔터티 선언이 있는 XML은 자동 추출하지 않습니다."
                root = ET.fromstring(raw)
                for paragraph in root.iter():
                    if paragraph.tag.rsplit("}", 1)[-1] == "p":
                        paragraphs.append("".join(node.text or "" for node in paragraph.iter() if node.tag.rsplit("}", 1)[-1] == "t"))
            return "\n".join(paragraphs), "extracted", "표·도형·각주 등은 원본과 대조하세요."
    if extension == ".pdf" and importlib.util.find_spec("pypdf"):
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            return None, "needs_reader", "암호화된 PDF는 호스트의 문서 도구로 읽으세요."
        pages = [f"[page {index}]\n{page.extract_text() or ''}" for index, page in enumerate(reader.pages, 1)]
        if any(page.split("\n", 1)[1].strip() for page in pages):
            return "\n\n".join(pages), "extracted", "추출 텍스트와 원본 페이지를 대조하세요."
        return None, "needs_ocr", "텍스트가 없는 PDF입니다. OCR 도구가 필요합니다."
    return None, "needs_reader", "호스트의 문서 읽기/OCR 도구를 사용하세요. PDF 자동 추출은 선택적 pypdf가 있을 때 지원합니다."


def make_record(source, identifier, relative, timestamp):
    source = Path(source)
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("자동 등록 파일은 64 MiB 이하여야 합니다. 대용량 자료는 분리하거나 별도로 참조하세요.")
    checksum = hash_file(source)
    try:
        text, status, reason = extract_text(source)
    except Exception as exc:
        # Parsing failure must not discard or misrepresent the preserved original.
        text, status, reason = None, "needs_reader", f"자동 추출 실패: {type(exc).__name__}"
    if text is not None and not text.strip():
        text, status, reason = None, "needs_reader", "본문 텍스트가 비어 있습니다."
    if text is not None and "\x00" in text:
        text, status, reason = None, "needs_reader", "본문에 바이너리 제어 문자가 있습니다."
    if hash_file(source) != checksum:
        raise ValueError("추출 중 원본이 변경되었습니다. 다시 읽으세요.")
    extracted_path = f"inputs/{identifier}-extracted.txt" if text is not None else None
    return {
        "input_id": identifier, "original_name": source.name, "path": relative,
        "sha256": checksum, "size": source.stat().st_size, "imported_at": timestamp,
        "extraction": {"status": status, "path": extracted_path, "note": reason},
    }, text
