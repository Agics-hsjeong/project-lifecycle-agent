"""Synthetic container fixtures for structural checks, never rendered customer files."""
import json
from zipfile import ZipFile


def complete_package(project):
    exports = project / "exports"
    exports.mkdir(exist_ok=True)
    with ZipFile(exports / "qualitative.docx", "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Synthetic proposal</w:t></w:r></w:p></w:body></w:document>')
    with ZipFile(exports / "presentation.pptx", "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
        archive.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
    (project / "docs/proposal-proof.md").write_text("가상 테스트 주장 근거", encoding="utf-8")
    profile = {
        "schema_version": 1, "presentation_required": True, "presentation_exclusion_reason": "",
        "presentation_minutes": 10, "timing_source": "가상 RFP 발표 조건 §1",
        "qualitative_file": "exports/qualitative.docx", "presentation_file": "exports/presentation.pptx", "additional_files": [],
        "criteria": [{"id": "EVAL-001", "name": "수행 방안", "source": "가상 RFP 평가표 §2", "weight": 30,
                      "requirement_ids": ["REQ-001"], "proposal_section": "3.1 수행 방안", "slide_ids": ["SLIDE-001"],
                      "evidence_refs": ["docs/proposal-proof.md"], "status": "mapped", "exclusion_reason": ""}],
        "slides": [{"id": "SLIDE-001", "title": "수행 방안", "message": "가상 근거 기반 제안", "seconds": 60,
                    "criterion_ids": ["EVAL-001"], "evidence_refs": ["docs/proposal-proof.md"],
                    "speaker_notes": "가상 테스트 전용 대본", "visual": "가상 절차 도식"}]}
    (project / "proposal/package.json").write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return profile
