"""Optional, versioned bid/pitch package checks. No model or document renderer."""
import json
import math
import re
from zipfile import BadZipFile, ZipFile

EXTRA_IDS = ["DEL-015", "DEL-016", "DEL-017", "DEL-018"]
PROFILE = "proposal/package.json"
FIELDS = {"schema_version", "presentation_required", "presentation_exclusion_reason", "presentation_minutes",
          "timing_source", "qualitative_file", "presentation_file", "additional_files", "criteria", "slides"}
CRITERION_FIELDS = {"id", "name", "source", "weight", "requirement_ids", "proposal_section", "slide_ids",
                    "evidence_refs", "status", "exclusion_reason"}
SLIDE_FIELDS = {"id", "title", "message", "seconds", "criterion_ids", "evidence_refs", "speaker_notes", "visual"}


def enabled(data):
    return data.get("proposal_package", False) is True


def read(root):
    from tools.storage import confined
    return json.loads(confined(root, PROFILE).read_text(encoding="utf-8-sig"))


def number(value, *, nullable=True):
    return (nullable and value is None) or (type(value) in (int, float) and math.isfinite(value) and value >= 0)


def validate(profile):
    """Validate drafts without pretending they are ready for submission."""
    if not isinstance(profile, dict) or set(profile) != FIELDS or profile.get("schema_version") != 1:
        raise ValueError("제안 패키지 구조/필드를 확인하세요.")
    if type(profile["presentation_required"]) is not bool or not number(profile["presentation_minutes"]):
        raise ValueError("발표 여부·시간 형식을 확인하세요.")
    for key in ("presentation_exclusion_reason", "timing_source", "qualitative_file", "presentation_file"):
        if not isinstance(profile[key], str):
            raise ValueError(f"proposal/{key}: 문자열이 필요합니다.")
    if not isinstance(profile["additional_files"], list) or any(not isinstance(item, str) for item in profile["additional_files"]):
        raise ValueError("additional_files는 파일 경로 목록이어야 합니다.")
    for group, fields, prefix in (("criteria", CRITERION_FIELDS, "EVAL"), ("slides", SLIDE_FIELDS, "SLIDE")):
        if not isinstance(profile[group], list):
            raise ValueError(f"{group}는 목록이어야 합니다.")
        seen = set()
        for row in profile[group]:
            if not isinstance(row, dict) or set(row) != fields:
                raise ValueError(f"{group}: 필수 필드와 허용 필드를 확인하세요.")
            identifier = row["id"]
            if not isinstance(identifier, str) or not re.fullmatch(prefix + r"-\d{3,}", identifier) or identifier in seen:
                raise ValueError(f"{group}: 중복/잘못된 ID")
            seen.add(identifier)
            lists = {"requirement_ids", "slide_ids", "evidence_refs"} if group == "criteria" else {"criterion_ids", "evidence_refs"}
            numeric = "weight" if group == "criteria" else "seconds"
            for key, value in row.items():
                if key in lists:
                    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                        raise ValueError(f"{identifier}/{key}: 문자열 목록이 필요합니다.")
                elif key == numeric:
                    if not number(value):
                        raise ValueError(f"{identifier}/{key}: 0 이상 수치 또는 null이 필요합니다.")
                elif not isinstance(value, str):
                    raise ValueError(f"{identifier}/{key}: 문자열이 필요합니다.")
            if group == "criteria" and row["status"] not in {"unmapped", "mapped", "not_applicable"}:
                raise ValueError(f"{identifier}: 잘못된 대응 상태")


def files(profile):
    result = {value for value in [profile["qualitative_file"], profile["presentation_file"], *profile["additional_files"]] if value}
    for row in profile["criteria"] + profile["slides"]:
        result.update(row["evidence_refs"])
    return result


def check_export(path, role, slide_count=None):
    """Basic container checks only; host must render and visually inspect the output."""
    suffix = path.suffix.lower()
    allowed = {".docx", ".pdf", ".hwp", ".hwpx"} if role == "qualitative" else {".pptx", ".pdf"}
    if suffix not in allowed:
        raise ValueError(f"{role}: 제출 파일 형식 미지원 ({suffix}); 원본과 렌더링 결과를 확인하세요.")
    with path.open("rb") as handle:
        header = handle.read(8)
    if suffix == ".pdf":
        if not header.startswith(b"%PDF-"):
            raise ValueError("PDF 파일 헤더가 올바르지 않습니다.")
    elif suffix == ".hwp":
        if header != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise ValueError("HWP 파일 헤더가 올바르지 않습니다.")
    else:
        try:
            with ZipFile(path) as archive:
                names = archive.namelist()
                required = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".hwpx": "Contents/content.hpf"}[suffix]
                if required not in names or (suffix != ".hwpx" and "[Content_Types].xml" not in names):
                    raise ValueError("제출 파일의 필수 문서 구조가 없습니다.")
                if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
                    raise ValueError("제출 파일 검사 크기 제한 초과")
                if archive.testzip() is not None:
                    raise ValueError("제출 파일 압축 데이터가 손상되었습니다.")
                if suffix == ".pptx" and slide_count is not None:
                    actual = sum(bool(re.fullmatch(r"ppt/slides/slide\d+\.xml", name)) for name in names)
                    if actual != slide_count:
                        raise ValueError("발표 PPTX 슬라이드 수와 관리 목록이 다릅니다.")
        except BadZipFile as exc:
            raise ValueError("제출 파일이 올바른 문서 패키지가 아닙니다.") from exc


def check(root, data, tables):
    from tools import starter as core
    if not enabled(data):
        return []
    issues = []
    try:
        profile = read(root)
        validate(profile)
    except (ValueError, OSError) as exc:
        return [f"제안 패키지: {exc}"]
    req_ids = {row["requirement_id"] for row in tables["requirements"]}
    criteria = {row["id"]: row for row in profile["criteria"]}
    slides = {row["id"]: row for row in profile["slides"]}
    if not criteria:
        issues.append("제안 패키지: 원문 평가항목 또는 사업 판단 기준을 등록하세요.")
    elif all(row["status"] == "not_applicable" for row in criteria.values()):
        issues.append("제안 패키지: 모든 평가항목이 제외되어 있습니다. 실제 판단 기준을 확인하세요.")
    for identifier, row in criteria.items():
        for key in ("name", "source"):
            if core.missing(row[key]):
                issues.append(f"{identifier}: {key} 누락")
        if set(row["requirement_ids"]) - req_ids:
            issues.append(f"{identifier}: 없는 요구사항 연결")
        if row["status"] == "not_applicable":
            if core.missing(row["exclusion_reason"]):
                issues.append(f"{identifier}: 평가항목 제외 근거 누락")
            continue
        if row["status"] != "mapped" or core.missing(row["proposal_section"]):
            issues.append(f"{identifier}: 정성제안서 대응 절 누락/미완료")
        if not row["evidence_refs"]:
            issues.append(f"{identifier}: 제안 주장 근거 누락")
        if profile["presentation_required"] and not row["slide_ids"]:
            issues.append(f"{identifier}: 발표 슬라이드 연결 누락")
        for slide_id in row["slide_ids"]:
            if slide_id not in slides or identifier not in slides[slide_id]["criterion_ids"]:
                issues.append(f"{identifier}: {slide_id} 슬라이드 양방향 연결 오류")
    if profile["presentation_required"]:
        if not slides or core.missing(profile["timing_source"]):
            issues.append("제안 발표: 슬라이드와 발표 시간 원문/합의 근거가 필요합니다.")
        for identifier, row in slides.items():
            for key in ("title", "message", "speaker_notes", "visual"):
                if core.missing(row[key]):
                    issues.append(f"{identifier}: {key} 미작성")
            if row["seconds"] is None or row["seconds"] <= 0:
                issues.append(f"{identifier}: 발표 시간 배분 누락")
            for criterion_id in row["criterion_ids"]:
                if criterion_id not in criteria or identifier not in criteria[criterion_id]["slide_ids"]:
                    issues.append(f"{identifier}: {criterion_id} 평가항목 양방향 연결 오류")
        limit = profile["presentation_minutes"]
        if limit is not None and sum(row["seconds"] or 0 for row in slides.values()) > limit * 60:
            issues.append("제안 발표: 슬라이드 시간 합계가 발표 제한 시간을 초과합니다.")
    else:
        if core.missing(profile["presentation_exclusion_reason"]):
            issues.append("제안 발표: 발표 제외 사유가 필요합니다.")
        for identifier in ("DEL-016", "DEL-017"):
            row = next((item for item in tables["deliverables"] if item["deliverable_id"] == identifier), {})
            if row.get("status") != "not_applicable" or core.missing(row.get("exclusion_reason")):
                issues.append(f"{identifier}: 발표 제외를 원장에도 근거와 함께 기록하세요.")
    roles = [("qualitative", profile["qualitative_file"])]
    if profile["presentation_required"]:
        roles.append(("presentation", profile["presentation_file"]))
    for role, relative in roles:
        try:
            check_export(core.local_file(root, relative), role, len(slides))
        except (ValueError, OSError) as exc:
            issues.append(f"제안 제출/{role}: {exc}")
    if profile["presentation_required"] and profile["qualitative_file"] == profile["presentation_file"]:
        issues.append("정성제안서와 발표자료는 서로 다른 제출 파일이어야 합니다.")
    for relative in files(profile):
        try:
            core.local_file(root, relative)
        except ValueError as exc:
            issues.append(f"제안 패키지/{relative}: {exc}")
    from tools import reference_templates
    issues.extend(reference_templates.check(root, profile))
    return issues
