"""Private reference libraries, explicit style selection and output comparison records."""
import json
import re
from pathlib import Path
import stat

from tools.intake import hash_file, MAX_SOURCE_BYTES
from tools.storage import confined, commit, digest, json_bytes, lock
from tools.template_analysis import analyze

KINDS = {"qualitative": {".docx", ".dotx", ".pdf", ".hwp", ".hwpx", ".png", ".jpg", ".jpeg", ".webp"},
         "presentation": {".pptx", ".potx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}}
MANIFEST = "templates/manifest.json"
SELECTION = "templates/selection.json"
CHECKS = "templates/output-checks.json"
TEXT_FIELDS = {"style_notes", "layout_rules", "writing_rules", "reusable_patterns", "replace_rules", "coverage"}


def read_json(root, name, default):
    path = confined(root, name)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else default


def library(root):
    value = read_json(root, MANIFEST, {"schema_version": 1, "templates": []})
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("templates"), list):
        raise ValueError("참고 템플릿 목록 구조가 잘못되었습니다.")
    seen = set()
    for item in value["templates"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not re.fullmatch(r"TPL-\d{3,}", item["id"]) or item["id"] in seen or item.get("kind") not in KINDS:
            raise ValueError("참고 템플릿 ID/종류가 잘못되었습니다.")
        seen.add(item["id"])
        for key in ("path", "analysis_ref", "profile_ref", "sha256", "name"):
            if not isinstance(item.get(key), str):
                raise ValueError("참고 템플릿 필수 정보 누락")
        if (item["analysis_ref"] != f"templates/analysis/{item['id']}.json" or
            item["profile_ref"] != f"templates/profiles/{item['id']}.json" or
            not item["path"].startswith(f"templates/library/{item['id']}/") or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("참고 템플릿 내부 경로/해시 형식이 잘못되었습니다.")
        confined(root, item["path"])
    return value


def selection(root):
    value = read_json(root, SELECTION, {kind: "" for kind in KINDS})
    if not isinstance(value, dict) or set(value) != set(KINDS) or any(not isinstance(item, str) for item in value.values()):
        raise ValueError("참고 템플릿 선택 구조가 잘못되었습니다.")
    return value


def source_files(folder):
    folder = Path(folder).resolve()
    result, unsupported = [], []
    for kind in KINDS:
        parent = folder / kind
        if not parent.exists():
            continue
        for path in sorted([parent, *parent.rglob("*")]):
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("참고 템플릿 연결 파일/폴더는 자동 등록하지 않습니다.")
            if path.is_file() and path.name != "README.md":
                if path.suffix.lower() in KINDS[kind]:
                    result.append((kind, path))
                else:
                    unsupported.append(str(path))
    return result, unsupported


def scan(folder, source=None, actor="agent"):
    from tools import lifecycle as engine, starter as core
    root, _ = core.load_project(folder)
    if source is None and not confined(root, "templates").exists():
        setup(root, actor)
    source = Path(source).resolve() if source else confined(root, "templates")
    if not source.is_dir():
        raise ValueError("참고 템플릿 폴더가 없습니다. qualitative/presentation 하위 폴더를 사용하세요.")
    candidates, unsupported = source_files(source)
    result = {"imported": [], "unchanged": [], "errors": [], "unsupported": unsupported}
    for kind, path in candidates:
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                raise ValueError("템플릿은 64 MiB 이하여야 합니다.")
            content = path.read_bytes()
            checksum = digest(content)
            with lock(root):
                catalog = library(root)
                existing = next((item for item in catalog["templates"] if item["kind"] == kind and item["sha256"] == checksum), None)
                if existing:
                    if hash_file(core.local_file(root, existing["path"])) != checksum:
                        raise ValueError("보존된 템플릿 원본이 변경되었습니다.")
                    result["unchanged"].append(existing["id"])
                    continue
                identifier = engine.next_id(catalog["templates"], "id", "TPL")
                relative = f"templates/library/{identifier}/{path.name}"
                confined(root, relative)
                analysis_ref = f"templates/analysis/{identifier}.json"
                profile_ref = f"templates/profiles/{identifier}.json"
                try:
                    observations = analyze(path)
                except Exception as exc:
                    observations = {"schema_version": 1, "status": "needs_reader", "error": type(exc).__name__,
                                    "notice": "자동 구조 분석 실패. 원본을 보존했으며 호스트의 문서 도구로 읽어야 합니다."}
                if hash_file(path) != checksum:
                    raise ValueError("분석 중 템플릿 원본이 변경되었습니다.")
                record = {"id": identifier, "kind": kind, "name": path.name, "path": relative, "sha256": checksum,
                          "imported_at": core.now(), "analysis_ref": analysis_ref, "profile_ref": profile_ref,
                          "status": observations["status"]}
                catalog["templates"].append(record)
                selected = selection(root)
                profile = {"template_id": identifier, "source_sha256": checksum, "status": "draft",
                           **dict.fromkeys(TEXT_FIELDS, ""), "visual_refs": [], "visual_hashes": {}}
                changes = {relative: content, MANIFEST: json_bytes(catalog), SELECTION: json_bytes(selected),
                           analysis_ref: json_bytes(observations), profile_ref: json_bytes(profile)}
                engine.add_event(root, changes, "template-import", actor, {"template_id": identifier, "kind": kind})
                commit(root, changes)
                result["imported"].append(record)
        except (ValueError, OSError) as exc:
            result["errors"].append({"source": str(path), "error": str(exc)})
    # Pick a sole candidate only after registration. A crash cannot accidentally choose the first of many.
    if result["imported"] or result["unchanged"]:
        with lock(root):
            selected = selection(root)
            previous_selection = dict(selected)
            catalog = library(root)["templates"]
            for kind in KINDS:
                candidates_of_kind = [item["id"] for item in catalog if item["kind"] == kind]
                if not selected[kind] and len(candidates_of_kind) == 1:
                    selected[kind] = candidates_of_kind[0]
            if selected != previous_selection or result["imported"]:
                changes = {SELECTION: json_bytes(selected)}
                engine.add_event(root, changes, "template-scan", actor, {"imported": [item["id"] for item in result["imported"]]})
                commit(root, changes)
    result["selection"] = selection(root)
    return result


def hashes(root, refs):
    from tools import starter as core
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise ValueError("시각 검토 경로는 문자열 목록이어야 합니다.")
    values = {ref: core.local_file(root, ref) for ref in refs}
    images = [path for path in values.values() if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
    if refs and not images:
        raise ValueError("시각 검토 근거에 실제 렌더링 이미지(PNG/JPEG/WebP)를 포함하세요.")
    for path in images:
        with path.open("rb") as handle:
            header = handle.read(12)
        if not (header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"\xff\xd8\xff") or
                (header.startswith(b"RIFF") and header[8:12] == b"WEBP")):
            raise ValueError("시각 검토 이미지의 기본 형식이 올바르지 않습니다.")
    return {ref: hash_file(path) for ref, path in values.items()}


def setup(folder, actor="agent"):
    from tools import lifecycle as engine, starter as core
    root, _ = core.load_project(folder)
    with lock(root):
        changes = {}
        for kind in KINDS:
            relative = f"templates/{kind}/README.md"
            if not confined(root, relative).exists():
                changes[relative] = (core.BLUEPRINT / relative).read_bytes()
        if not changes:
            return {"unchanged": True, "folders": [f"templates/{kind}" for kind in KINDS]}
        revision = engine.add_event(root, changes, "template-setup", actor, {"files": sorted(changes)})
        commit(root, changes)
        return {"unchanged": False, "revision": revision, "folders": [f"templates/{kind}" for kind in KINDS]}


def patch_changes(root, payload):
    from tools import starter as core
    catalog = {item["id"]: item for item in library(root)["templates"]}
    selected = selection(root)
    changes = {}
    if "template_selection" in payload:
        patch = payload["template_selection"]
        if not isinstance(patch, dict) or set(patch) - set(KINDS):
            raise ValueError("템플릿 선택 종류를 확인하세요.")
        for kind, identifier in patch.items():
            if not isinstance(identifier, str) or (identifier and (identifier not in catalog or catalog[identifier]["kind"] != kind)):
                raise ValueError("템플릿 ID와 제안서/발표 종류가 맞지 않습니다.")
            selected[kind] = identifier
        changes[SELECTION] = json_bytes(selected)
    if "template_profiles" in payload:
        patches = payload["template_profiles"]
        if not isinstance(patches, list):
            raise ValueError("템플릿 분석 결과는 목록이어야 합니다.")
        seen = set()
        for patch in patches:
            if not isinstance(patch, dict) or set(patch) - (TEXT_FIELDS | {"template_id", "status", "visual_refs"}):
                raise ValueError("템플릿 분석 필드를 확인하세요.")
            identifier = patch.get("template_id")
            if not isinstance(identifier, str) or identifier not in catalog or identifier in seen:
                raise ValueError("템플릿 분석 ID가 없거나 중복됩니다.")
            seen.add(identifier)
            record = catalog[identifier]
            if hash_file(core.local_file(root, record["path"])) != record["sha256"]:
                raise ValueError("분석 대상 템플릿 원본이 변경되었습니다.")
            profile = read_json(root, record["profile_ref"], {})
            if not isinstance(profile, dict):
                raise ValueError("템플릿 분석 기록 구조가 잘못되었습니다.")
            profile.update(patch)
            if any(not isinstance(profile.get(key), str) for key in TEXT_FIELDS) or profile.get("status") not in {"draft", "analyzed"}:
                raise ValueError("템플릿 분석의 상태/문자열 형식을 확인하세요.")
            profile["visual_hashes"] = hashes(root, profile.get("visual_refs", []))
            if profile["status"] == "analyzed" and (any(core.missing(profile[key]) for key in TEXT_FIELDS) or not profile["visual_hashes"]):
                raise ValueError("분석 완료에는 스타일·구성·문체·재사용·교체 규칙과 시각 검토 근거가 필요합니다.")
            changes[record["profile_ref"]] = json_bytes(profile)
    if "template_checks" in payload:
        checks = read_json(root, CHECKS, {})
        patches = payload["template_checks"]
        if not isinstance(patches, list) or not isinstance(checks, dict):
            raise ValueError("템플릿 적용 검수 구조가 잘못되었습니다.")
        seen = set()
        for patch in patches:
            if not isinstance(patch, dict) or set(patch) != {"kind", "output_ref", "review_ref", "visual_refs", "notes"}:
                raise ValueError("적용 검수의 종류·출력·검토 기록·이미지·메모가 필요합니다.")
            kind = patch["kind"]
            if not isinstance(kind, str) or kind not in KINDS or kind in seen or not selected[kind]:
                raise ValueError("적용 검수 대상의 템플릿을 먼저 선택하세요.")
            seen.add(kind)
            record = catalog[selected[kind]]
            profile_path = record["profile_ref"]
            profile_bytes = changes.get(profile_path, confined(root, profile_path).read_bytes())
            profile = json.loads(profile_bytes)
            if profile.get("status") != "analyzed" or core.missing(patch["notes"]):
                raise ValueError("템플릿 분석과 구체적인 비교 검수 내용이 필요합니다.")
            visual_hashes = hashes(root, patch["visual_refs"])
            if not visual_hashes:
                raise ValueError("생성 결과의 렌더링 비교 근거가 필요합니다.")
            output = core.local_file(root, patch["output_ref"])
            review = core.local_file(root, patch["review_ref"])
            if not review.read_text(encoding="utf-8-sig").strip():
                raise ValueError("스타일 비교 검토 기록이 비어 있습니다.")
            checks[kind] = {**patch, "template_id": record["id"], "template_sha256": record["sha256"],
                           "profile_sha256": digest(profile_bytes), "output_sha256": hash_file(output),
                           "review_sha256": hash_file(review), "visual_hashes": visual_hashes}
        changes[CHECKS] = json_bytes(checks)
    return changes


def overview(root):
    catalog = library(root)["templates"]
    pending = []
    sources, unsupported = source_files(confined(root, "templates"))
    for kind, path in sources:
        checksum = hash_file(path)
        if not any(item["kind"] == kind and item["sha256"] == checksum for item in catalog):
            pending.append({"kind": kind, "path": str(path)})
    return {"templates": catalog, "selection": selection(root), "pending": pending, "unsupported": unsupported}


def check(root, proposal_profile):
    from tools import starter as core
    try:
        state = overview(root)
        issues = ["참고 템플릿: 폴더의 신규/변경 파일을 template-scan으로 등록하세요."] if state["pending"] else []
        catalog = {item["id"]: item for item in state["templates"]}
        checks = read_json(root, CHECKS, {})
        if not isinstance(checks, dict):
            raise ValueError("템플릿 적용 검수 구조 오류")
        for kind in KINDS:
            if kind == "presentation" and not proposal_profile["presentation_required"]:
                continue
            identifier = state["selection"][kind]
            candidates = [item for item in catalog.values() if item["kind"] == kind]
            if not identifier and not candidates:
                continue
            if identifier not in catalog or catalog[identifier]["kind"] != kind:
                issues.append(f"{kind}: 적용할 참고 템플릿을 선택하세요.")
                continue
            record = catalog[identifier]
            profile = read_json(root, record["profile_ref"], {})
            if not isinstance(profile, dict) or profile.get("status") != "analyzed" or any(core.missing(profile.get(key)) for key in TEXT_FIELDS):
                issues.append(f"{identifier}: 시각·문체·구성 분석을 완료하세요.")
                continue
            if hash_file(core.local_file(root, record["path"])) != record["sha256"] or profile.get("source_sha256") != record["sha256"]:
                issues.append(f"{identifier}: 원본이 변경되었습니다.")
            visual_refs = profile.get("visual_refs", [])
            if not visual_refs or hashes(root, visual_refs) != profile.get("visual_hashes"):
                issues.append(f"{identifier}: 분석 시각 검토 근거가 없거나 변경되었습니다.")
            evidence = checks.get(kind, {})
            output_ref = proposal_profile["qualitative_file" if kind == "qualitative" else "presentation_file"]
            if not isinstance(evidence, dict) or evidence.get("template_id") != identifier or evidence.get("output_ref") != output_ref:
                issues.append(f"{kind}: 선택한 템플릿과 최종 제출본의 비교 검수가 필요합니다.")
                continue
            if (evidence.get("template_sha256") != record["sha256"] or
                evidence.get("profile_sha256") != hash_file(core.local_file(root, record["profile_ref"])) or
                evidence.get("output_sha256") != hash_file(core.local_file(root, output_ref)) or
                evidence.get("review_sha256") != hash_file(core.local_file(root, evidence["review_ref"])) or
                not evidence.get("visual_refs") or hashes(root, evidence["visual_refs"]) != evidence.get("visual_hashes")):
                issues.append(f"{kind}: 적용 검수 후 원본·분석·출력·검토 증거가 변경되었습니다.")
        return issues
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return [f"참고 템플릿: {exc}"]


def review_files(root, proposal_profile):
    result = set()
    catalog = {item["id"]: item for item in library(root)["templates"]}
    selected = selection(root)
    checks = read_json(root, CHECKS, {})
    for kind, identifier in selected.items():
        if not identifier or (kind == "presentation" and not proposal_profile["presentation_required"]):
            continue
        record = catalog[identifier]
        result.update((SELECTION, record["path"], record["analysis_ref"], record["profile_ref"]))
        profile = read_json(root, record["profile_ref"], {})
        result.update(profile.get("visual_refs", []))
        if kind in checks:
            result.update((CHECKS, checks[kind]["review_ref"]))
            result.update(checks[kind]["visual_refs"])
    return result


def prepare(folder, kind, output, actor="agent"):
    from tools import lifecycle as engine, starter as core
    root, _ = core.load_project(folder)
    with lock(root):
        identifier = selection(root).get(kind)
        record = next((item for item in library(root)["templates"] if item["id"] == identifier), None)
        if not record:
            raise ValueError("작업 사본을 만들 템플릿을 먼저 선택하세요.")
        original = core.local_file(root, record["path"])
        if original.suffix.lower() not in {".docx", ".pptx"}:
            raise ValueError("이 형식은 직접 편집 사본을 지원하지 않습니다. 원본을 보고 호스트 제작 도구로 재구성하세요.")
        destination = confined(root, output)
        if not output.startswith("working/templates/") or destination.suffix.lower() != original.suffix.lower():
            raise ValueError("원본과 같은 확장자로 working/templates/ 아래에 작업 사본을 만드세요.")
        if destination.exists():
            raise ValueError("기존 작업 파일을 덮어쓰지 않습니다.")
        content = original.read_bytes()
        if digest(content) != record["sha256"]:
            raise ValueError("보존된 원본이 변경되었습니다.")
        changes = {output: content}
        revision = engine.add_event(root, changes, "template-prepare", actor, {"template_id": identifier, "output_ref": output})
        commit(root, changes)
        return {"template_id": identifier, "working_file": output, "revision": revision,
                "notice": "이전 내용이 포함된 작업 사본입니다. 고객명·수치·실적·주석·노트·메타데이터 등을 교체·검수해야 하며 제출본이 아닙니다."}
