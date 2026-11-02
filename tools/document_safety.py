"""Shared bounded document parsing. No macros, entities or external commands."""
import importlib.util
import xml.etree.ElementTree as ET

MAX_XML_BYTES = 16 * 1024 * 1024


def xml_bytes(raw):
    if len(raw) > MAX_XML_BYTES:
        raise ValueError("XML 크기 제한 초과")
    normalized = raw.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
        raise ValueError("DTD/엔터티 선언이 있는 XML은 자동 추출하지 않습니다.")
    return ET.fromstring(raw)


def pdf_reader(path):
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("PDF 검사 크기 제한 초과")
    if not importlib.util.find_spec("pypdf"):
        raise ValueError("PDF 실제 구조 검증에는 호스트의 pypdf 런타임이 필요합니다. 헤더 검사만으로 완료 처리하지 않습니다.")
    from pypdf import PdfReader
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ValueError("암호화된 PDF는 자동 검증하지 않습니다.")
        if not 0 < len(reader.pages) <= 2000:
            raise ValueError("PDF 페이지 수 제한 또는 빈 문서")
        for page in reader.pages:
            _ = page.mediabox
            contents = page.get_contents()
            if contents is not None:
                contents.get_data()
        return reader
    except Exception as exc:
        raise ValueError("PDF 구조를 읽지 못했습니다. 실제 문서 도구로 확인하세요.") from exc
