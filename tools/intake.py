"""Source preservation metadata and bounded, non-executing text extraction."""
import hashlib
import importlib.util
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import unquote, urlsplit
from tools.document_safety import xml_bytes, pdf_reader
from zipfile import ZipFile

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024


def hash_file(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def hwpx_section_paths(archive):
    """Use package spine order; old section-only inputs get numeric ordering."""
    members = archive.namelist()
    package_name = "Contents/content.hpf"
    if package_name in members:
        info = archive.getinfo(package_name)
        if info.file_size > MAX_XML_BYTES or members.count(package_name) != 1:
            raise ValueError("HWPX 패키지 구조를 확인하세요.")
        package = xml_bytes(archive.read(package_name))
        local = lambda node: node.tag.rsplit("}", 1)[-1]
        spine = next((node for node in package.iter() if local(node) == "spine"), None)
        if spine is not None:
            items = {}
            for manifest in (n for n in package.iter() if local(n) == "manifest"):
                for item in manifest:
                    if local(item) != "item":
                        continue
                    identifier = item.attrib.get("id")
                    if not identifier or identifier in items:
                        raise ValueError("HWPX 본문 manifest ID가 없거나 중복됩니다.")
                    items[identifier] = item.attrib.get("href", "")
            result, seen_ids = [], set()
            for reference in spine:
                if local(reference) != "itemref":
                    continue
                identifier = reference.attrib.get("idref")
                href = items.get(identifier, "")
                if not identifier or identifier in seen_ids or not href or "\\" in href:
                    raise ValueError("HWPX 본문 순서 연결이 없거나 중복됩니다.")
                seen_ids.add(identifier)
                parsed = urlsplit(href)
                if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                    raise ValueError("HWPX 본문 외부 참조를 허용하지 않습니다.")
                decoded = unquote(parsed.path)
                if "\\" in decoded:
                    raise ValueError("HWPX 본문 경로가 잘못되었습니다.")
                name = posixpath.normpath(posixpath.join("Contents", decoded))
                if (not name.startswith("Contents/") or ".." in PurePosixPath(name).parts
                        or not name.endswith(".xml") or name in result or members.count(name) != 1):
                    raise ValueError("HWPX 본문 경로가 없거나 잘못되었습니다.")
                result.append(name)
            if not result:
                raise ValueError("HWPX 본문 순서가 비어 있습니다.")
            return result
    # Legacy/synthetic section-only archives have no spine. Never sort 10 before 2.
    sections = [(int(match[1]), name) for name in members
                if (match := re.fullmatch(r"Contents/section(\d+)\.xml", name))]
    if len({index for index, _ in sections}) != len(sections):
        raise ValueError("HWPX 본문 구역이 중복됩니다.")
    return [name for _, name in sorted(sections)]


def extract_text(path, *, coverage=None):
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
            names = ["word/document.xml"] if extension == ".docx" else hwpx_section_paths(archive)
            if not names or sum(archive.getinfo(name).file_size for name in names) > MAX_XML_BYTES:
                return None, "needs_reader", "XML 본문 누락 또는 추출 크기 제한 초과"
            paragraphs = []
            for name in names:
                raw = archive.read(name)
                root = xml_bytes(raw)
                for paragraph in root.iter():
                    if paragraph.tag.rsplit("}", 1)[-1] == "p":
                        paragraphs.append("".join(node.text or "" for node in paragraph.iter() if node.tag.rsplit("}", 1)[-1] == "t"))
            return "\n".join(paragraphs), "extracted", "표·도형·각주 등은 원본과 대조하세요."
    if extension == ".pdf" and importlib.util.find_spec("pypdf"):
        reader = pdf_reader(path)
        pages, states = [], []
        for index, page in enumerate(reader.pages, 1):
            try:
                content = page.extract_text() or ""
                state = "extracted" if content.strip() else "needs_ocr"
            except Exception:
                content, state = "", "failed"
            pages.append(f"[page {index}]\n{content}")
            states.append({"page": index, "status": state})
        if coverage is not None:
            coverage.update(page_count=len(states), pages=states)
        if any(item["status"] == "extracted" for item in states):
            status = "extracted" if all(item["status"] == "extracted" for item in states) else "partial"
            return "\n\n".join(pages), status, "추출 텍스트와 원본을 대조하고 미판독 페이지를 OCR·확인하세요."
        return None, "needs_ocr", "텍스트가 없는 PDF입니다. OCR 도구가 필요합니다."
    return None, "needs_reader", "호스트의 문서 읽기/OCR 도구를 사용하세요. PDF 자동 추출은 선택적 pypdf가 있을 때 지원합니다."


def make_record(source, identifier, relative, timestamp):
    source = Path(source)
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("자동 등록 파일은 64 MiB 이하여야 합니다. 대용량 자료는 분리하거나 별도로 참조하세요.")
    checksum = hash_file(source)
    coverage = {}
    try:
        text, status, reason = extract_text(source, coverage=coverage)
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
        "purpose": "baseline",
        "extraction": {"status": status, "path": extracted_path,
                       **({"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()} if text is not None else {}),
                       "note": reason, **coverage},
    }, text
