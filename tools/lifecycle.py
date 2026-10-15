"""Managed registers, review snapshots and stage transitions for host agents."""
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import re
import sys
import uuid

from tools import starter as core
from tools import proposal
from tools.intake import hash_file, make_record
from tools.storage import commit, confined, digest, json_bytes, lock, recover

IDS = {"requirements": ("requirement_id", "REQ"), "deliverables": ("deliverable_id", "DEL"),
       "tasks": ("task_id", "TASK"), "risks": ("risk_id", "RISK"), "reviews": ("review_id", "REV")}
COMMANDS = {"apply", "review", "advance", "rewind", "ingest", "discover", "inspect", "doctor", "recover", "proposal-setup"}


class GateError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("단계 완료 기준 미충족: " + "; ".join(issues))


def bundle(folder):
    root, data = core.load_project(folder)
    return root, data, {name: core.read_table(root, name) for name in core.HEADERS}


def next_id(rows, key, prefix):
    numbers = [int(row[key].rsplit("-", 1)[1]) for row in rows if re.fullmatch(prefix + r"-\d{3,}", row[key])]
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


def table_bytes(root, name, rows):
    with (root / f"registers/{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
        fields = next(csv.reader(handle))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def events(root):
    path = confined(root, "state/events.jsonl")
    result = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if not isinstance(record, dict) or not isinstance(record.get("revision"), str):
                raise ValueError("진행 이력 형식이 잘못되었습니다.")
            result.append(record)
    return result


def add_event(root, changes, action, actor, details):
    if core.missing(actor):
        raise ValueError("작업 주체(actor)가 필요합니다.")
    log = events(root)
    revision = uuid.uuid4().hex
    log.append({"revision": revision, "at": core.now(), "action": action, "actor": actor, "details": details})
    changes["state/events.jsonl"] = ("\n".join(json.dumps(item, ensure_ascii=False) for item in log) + "\n").encode("utf-8")
    return revision


def validate_tables(root, tables):
    errors = []
    req_ids = {row["requirement_id"] for row in tables["requirements"]}
    del_ids = {row["deliverable_id"] for row in tables["deliverables"]}
    for name, rows in tables.items():
        key, prefix = IDS[name]
        seen = set()
        for row in rows:
            identifier = row[key]
            if not re.fullmatch(prefix + r"-\d{3,}", identifier) or identifier in seen:
                errors.append(f"{name}: 중복/잘못된 ID {identifier}")
            seen.add(identifier)
            allowed = {"requirements": core.REQ_STATES, "deliverables": core.DEL_STATES,
                       "tasks": {"todo", "doing", "blocked", "done"}}.get(name)
            if allowed and row["status"] not in allowed:
                errors.append(f"{identifier}: 잘못된 상태")
            if name == "deliverables" and row["stage"] not in core.STAGES:
                errors.append(f"{identifier}: 잘못된 단계")
            links = "deliverable_ids" if name == "requirements" else "requirement_ids"
            if links in row:
                valid = del_ids if name == "requirements" else req_ids
                for reference in core.split_refs(row[links]):
                    if reference not in valid:
                        errors.append(f"{identifier}: 없는 연결 ID {reference}")
            path_fields = {"requirements": ("design_refs", "implementation_refs", "test_refs"),
                           "tasks": ("evidence_refs",), "deliverables": ("path",)}.get(name, ())
            for field in path_fields:
                for reference in core.split_refs(row[field]):
                    try:
                        core.local_file(root, reference)
                    except ValueError as exc:
                        errors.append(f"{identifier}/{field}: {exc}")
            if name == "tasks" and row["status"] == "done" and not row["evidence_refs"]:
                errors.append(f"{identifier}: 완료 증거가 필요합니다.")
            if name == "deliverables" and row["status"] == "not_applicable" and core.missing(row["exclusion_reason"]):
                errors.append(f"{identifier}: 적용 제외 사유가 필요합니다.")
    if errors:
        raise ValueError("; ".join(errors))


def apply_patch(folder, payload, actor="agent"):
    """Upsert known fields. Review decisions and stage changes have separate commands."""
    root, _ = core.load_project(folder)
    if not isinstance(payload, dict) or set(payload) - {"project", "requirements", "tasks", "deliverables", "risks", "handoff", "expected_revision", "proposal"}:
        raise ValueError("지원하지 않는 변경 항목입니다.")
    with lock(root):
        root, data, tables = bundle(root)
        history = events(root)
        actual = history[-1]["revision"] if history else None
        if "expected_revision" in payload and payload["expected_revision"] != actual:
            raise ValueError("진행 기록이 변경되었습니다. inspect 후 변경안을 다시 작성하세요.")
        changes, assigned = {}, {}
        if "project" in payload:
            patch = payload["project"]
            allowed = {"name", "objective", "scope_baseline", "client_or_product_owner", "project_manager", "technical", "tailoring", "methodology"}
            if not isinstance(patch, dict) or set(patch) - allowed:
                raise ValueError("프로젝트의 단계·ID·스키마는 직접 바꿀 수 없습니다.")
            for key, value in patch.items():
                if key == "technical":
                    if not isinstance(value, dict) or set(value) - {"stack", "repository", "test_commands", "deployment_target"}:
                        raise ValueError("technical 구조가 잘못되었습니다.")
                    if any(not isinstance(item, str) for field, item in value.items() if field != "test_commands"):
                        raise ValueError("technical 값은 문자열이어야 합니다.")
                    if "test_commands" in value and (not isinstance(value["test_commands"], list) or any(not isinstance(command, str) for command in value["test_commands"])):
                        raise ValueError("test_commands는 문자열 목록이어야 합니다.")
                    data[key] = {**data.get(key, {}), **value}
                elif key == "tailoring":
                    if not isinstance(value, list):
                        raise ValueError("tailoring은 목록이어야 합니다.")
                    data[key] = value
                else:
                    if not isinstance(value, str):
                        raise ValueError(f"{key}는 문자열이어야 합니다.")
                    if key == "methodology" and value not in {"hybrid", "waterfall", "agile"}:
                        raise ValueError("지원하지 않는 방법론입니다.")
                    data[key] = value
        for name in ("requirements", "tasks", "deliverables", "risks"):
            if name not in payload:
                continue
            if not isinstance(payload[name], list):
                raise ValueError(f"{name}는 행 목록이어야 합니다.")
            key, prefix = IDS[name]
            rows = tables[name]
            with (root / f"registers/{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
                fields = next(csv.reader(handle))
            assigned[name] = []
            for patch in payload[name]:
                if not isinstance(patch, dict) or set(patch) - set(fields) or any(not isinstance(value, str) for value in patch.values()):
                    raise ValueError(f"{name}: 필드 이름과 문자열 값을 확인하세요.")
                identifier = patch.get(key) or next_id(rows, key, prefix)
                row = next((item for item in rows if item[key] == identifier), None)
                if row is None:
                    row = dict.fromkeys(fields, "")
                    row[key] = identifier
                    if "status" in fields:
                        row["status"] = {"requirements": "proposed", "tasks": "todo", "deliverables": "draft", "risks": "open"}[name]
                    rows.append(row)
                if name == "deliverables" and patch.get("status") == "approved":
                    raise ValueError("산출물 승인은 실제 검토 근거로 review에서 기록하세요.")
                if name == "deliverables" and row["status"] == "approved" and any(row.get(field) != value for field, value in patch.items()):
                    row["status"] = "in_review"
                row.update(patch)
                row[key] = identifier
                assigned[name].append(identifier)
            changes[f"registers/{name}.csv"] = table_bytes(root, name, rows)
        validate_tables(root, tables)
        if "proposal" in payload:
            if not proposal.enabled(data):
                raise ValueError("기존 프로젝트는 proposal-setup으로 제안 패키지를 먼저 추가하세요.")
            patch = payload["proposal"]
            if not isinstance(patch, dict) or set(patch) - proposal.FIELDS or "schema_version" in patch:
                raise ValueError("제안 패키지 변경 필드를 확인하세요.")
            profile = {**proposal.read(root), **patch}
            proposal.validate(profile)
            for relative in proposal.files(profile):
                core.local_file(root, relative)
            changes[proposal.PROFILE] = json_bytes(profile)
        if "handoff" in payload:
            if not isinstance(payload["handoff"], str) or not payload["handoff"].strip():
                raise ValueError("handoff는 비어 있지 않은 Markdown이어야 합니다.")
            changes["HANDOFF.md"] = payload["handoff"].encode("utf-8")
        data["updated_at"] = core.now()
        changes["project.json"] = json_bytes(data)
        revision = add_event(root, changes, "apply", actor, {"assigned": assigned, "files": sorted(changes)})
        commit(root, changes)
        return {"revision": revision, "assigned": assigned, "changed_files": sorted(changes)}


def setup_proposal(folder, actor="agent"):
    """Opt in legacy projects without overwriting customer files or approval history."""
    root, _ = core.load_project(folder)
    with lock(root):
        root, data, tables = bundle(root)
        if proposal.enabled(data):
            proposal.validate(proposal.read(root))
            return {"unchanged": True, "required_deliverables": core.required_deliverables(data, "proposal")}
        rows = tables["deliverables"]
        templates = [row for row in core.read_table(core.BLUEPRINT, "deliverables") if row["deliverable_id"] in proposal.EXTRA_IDS]
        changes, adopted = {}, []
        with (root / "registers/deliverables.csv").open(encoding="utf-8-sig", newline="") as handle:
            fields = next(csv.reader(handle))
        for template in templates:
            existing = next((row for row in rows if row["deliverable_id"] == template["deliverable_id"]), None)
            if existing and (existing["path"] != template["path"] or existing["stage"] != "proposal"):
                raise ValueError(f"{template['deliverable_id']}: 기존 사용자 산출물과 충돌합니다. 덮어쓰지 않습니다.")
            if not existing:
                rows.append({**dict.fromkeys(fields, ""), **template})
            target = confined(root, template["path"])
            if target.exists():
                adopted.append(template["path"])
            else:
                changes[template["path"]] = (core.BLUEPRINT / template["path"]).read_bytes()
        profile_path = confined(root, proposal.PROFILE)
        if profile_path.exists():
            proposal.validate(proposal.read(root))
        else:
            changes[proposal.PROFILE] = (core.BLUEPRINT / proposal.PROFILE).read_bytes()
        data.update(proposal_package=True, updated_at=core.now())
        changes["project.json"] = json_bytes(data)
        changes["registers/deliverables.csv"] = table_bytes(root, "deliverables", rows)
        revision = add_event(root, changes, "proposal-setup", actor, {"added_ids": proposal.EXTRA_IDS, "preserved_files": adopted})
        commit(root, changes)
        return {"unchanged": False, "revision": revision, "preserved_files": adopted,
                "required_deliverables": core.required_deliverables(data, "proposal")}


def fingerprint(root, stage, evidence, *, tables=None):
    _, data, actual_tables = bundle(root)
    tables = tables if tables is not None else actual_tables
    order = list(core.STAGES).index(stage)
    required = set(core.required_deliverables(data, stage))
    documents = [row for row in tables["deliverables"] if row["deliverable_id"] in required]
    files = {evidence}
    fields = ["requirement_id", "source", "description", "acceptance_criteria", "priority", "exclusion_reason"]
    for field, threshold in (("design_refs", 3), ("implementation_refs", 6), ("test_refs", 7), ("deliverable_ids", 9)):
        if order >= threshold:
            fields.append(field)
    requirements = []
    for row in tables["requirements"]:
        item = {field: row[field] for field in fields}
        item["deferred"] = row["status"] == "deferred"
        requirements.append(item)
        for field in ("design_refs", "implementation_refs", "test_refs"):
            if field in fields:
                files.update(core.split_refs(row[field]))
    for document in documents:
        if document["status"] != "not_applicable":
            files.update(core.split_refs(document["path"]))
    manifest = root / "inputs/manifest.json"
    inputs = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {"inputs": []}
    if not isinstance(inputs, dict) or not isinstance(inputs.get("inputs"), list):
        raise ValueError("입력 자료 목록이 잘못되었습니다.")
    for item in inputs["inputs"]:
        files.add(item["path"])
        if item.get("extraction", {}).get("path"):
            files.add(item["extraction"]["path"])
    if stage == "proposal" and proposal.enabled(data):
        profile = proposal.read(root)
        proposal.validate(profile)
        files.add(proposal.PROFILE)
        files.update(proposal.files(profile))
    hashes = {ref: hash_file(core.local_file(root, ref)) for ref in sorted(files)}
    scope = {key: data.get(key) for key in ("business_type", "methodology", "objective", "scope_baseline")}
    body = {"stage": stage, "scope": scope, "requirements": sorted(requirements, key=lambda item: item["requirement_id"]),
            "documents": sorted(documents, key=lambda item: item["deliverable_id"]), "files": hashes}
    return digest(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()), hashes


def latest_review(rows, stage):
    matches = [row for row in rows if row["stage"] == stage]
    for row in matches:
        try:
            timestamp = datetime.fromisoformat(row["reviewed_at"])
            if timestamp.utcoffset() is None:
                raise ValueError("시간대 없음")
        except ValueError as exc:
            raise ValueError(f"{row['review_id']}: 잘못된 검토 일시입니다.") from exc
    if not matches:
        return None
    latest = max(datetime.fromisoformat(row["reviewed_at"]) for row in matches)
    if sum(datetime.fromisoformat(row["reviewed_at"]) == latest for row in matches) != 1:
        raise ValueError(f"{stage}: 같은 시각의 최신 검토가 여러 개입니다.")
    return next(row for row in matches if datetime.fromisoformat(row["reviewed_at"]) == latest)


def stale_reviews(folder, stage, *, strict=False):
    root, _, tables = bundle(folder)
    review = latest_review(tables["reviews"], stage)
    if not review or review["decision"] != "approved":
        return [f"{stage}: 승인된 검토 기록이 없습니다."] if strict else []
    path = confined(root, f"state/reviews/{review['review_id']}.json")
    if not path.exists():
        return [f"{stage}: 과거 검토에 증거 스냅샷이 없습니다. review로 재확인하세요."] if strict else []
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        current, _ = fingerprint(root, stage, review["evidence_ref"])
        if not isinstance(snapshot, dict) or snapshot.get("review_id") != review["review_id"] or snapshot.get("fingerprint") != current:
            return [f"{stage}: 승인 후 범위·문서·증거가 변경되었습니다. 재검토가 필요합니다."]
    except (ValueError, OSError, KeyError, TypeError):
        return [f"{stage}: 검토 증거를 읽을 수 없거나 변경되었습니다."]
    return []


def record_review(folder, reviewer, evidence, notes, *, decision="approved", stage=None):
    root, _ = core.load_project(folder)
    if decision not in {"approved", "rejected"} or core.missing(reviewer) or core.missing(notes):
        raise ValueError("검토자·결정·검토 내용이 필요합니다.")
    with lock(root):
        root, data, tables = bundle(root)
        stage = stage or data["current_stage"]
        if stage not in core.STAGES or list(core.STAGES).index(stage) > list(core.STAGES).index(data["current_stage"]):
            raise ValueError("현재 또는 이전 단계만 검토할 수 있습니다.")
        evidence_path = core.local_file(root, evidence)
        if evidence_path.suffix.lower() in {".md", ".txt"} and (not evidence_path.read_text(encoding="utf-8-sig").strip() or re.search(r"\bTODO\b", evidence_path.read_text(encoding="utf-8-sig"))):
            raise ValueError("검토 근거가 비어 있거나 미작성 상태입니다.")
        required = set(core.required_deliverables(data, stage))
        for row in tables["deliverables"]:
            if row["deliverable_id"] in required and row["status"] != "not_applicable":
                row["status"] = "approved" if decision == "approved" else "in_review"
        if decision == "approved":
            _, _, issues = core.check_project(root, stage, tables_override=tables, include_review=False, check_snapshots=False)
            if issues:
                raise GateError(issues)
        identifier = next_id(tables["reviews"], "review_id", "REV")
        review = {"review_id": identifier, "stage": stage, "decision": decision, "reviewer": reviewer,
                  "reviewed_at": datetime.now().astimezone().isoformat(timespec="microseconds"), "evidence_ref": evidence, "notes": notes}
        tables["reviews"].append(review)
        changes = {"registers/deliverables.csv": table_bytes(root, "deliverables", tables["deliverables"]),
                   "registers/reviews.csv": table_bytes(root, "reviews", tables["reviews"])}
        if decision == "approved":
            checksum, files = fingerprint(root, stage, evidence, tables=tables)
            changes[f"state/reviews/{identifier}.json"] = json_bytes({"schema_version": 1, "review_id": identifier,
                                                                     "stage": stage, "fingerprint": checksum, "files": files})
        revision = add_event(root, changes, "review", reviewer, review)
        commit(root, changes)
        return {"review_id": identifier, "stage": stage, "decision": decision, "revision": revision,
                "notice": "검토 근거를 기록합니다. 검토자 인증·전자서명 기능은 아닙니다."}


def transition(folder, *, target=None, actor="agent", reason="", evidence=None, backwards=False):
    root, _ = core.load_project(folder)
    with lock(root):
        root, data, _ = bundle(root)
        stages = list(core.STAGES)
        previous = data["current_stage"]
        index = stages.index(previous)
        if backwards:
            if target not in stages or stages.index(target) >= index or core.missing(reason) or not evidence:
                raise ValueError("되돌리기는 이전 단계·사유·근거가 필요합니다.")
            core.local_file(root, evidence)
        else:
            if index == len(stages) - 1:
                raise ValueError("유지보수는 지속 운영 단계입니다. 다음 단계가 없습니다.")
            target = target or stages[index + 1]
            if target != stages[index + 1]:
                raise ValueError("단계를 건너뛸 수 없습니다. 적용 제외 사유를 검토한 뒤 순서대로 진행하세요.")
            _, _, issues = core.check_project(root, previous, check_snapshots=False)
            if re.search(r"\bTODO\b", core.local_file(root, "HANDOFF.md").read_text(encoding="utf-8-sig")):
                issues.append("HANDOFF.md에 미작성 TODO가 있습니다. 다음 작업을 기록하세요.")
            for stage in stages[:index + 1]:
                issues.extend(stale_reviews(root, stage, strict=True))
            if issues:
                raise GateError(issues)
        data.update(current_stage=target, updated_at=core.now())
        changes = {"project.json": json_bytes(data)}
        if backwards:
            tables = {name: core.read_table(root, name) for name in core.HEADERS}
            for row in tables["deliverables"]:
                if stages.index(row["stage"]) >= stages.index(target) and row["status"] == "approved":
                    row["status"] = "in_review"
            changes["registers/deliverables.csv"] = table_bytes(root, "deliverables", tables["deliverables"])
        handoff = core.local_file(root, "HANDOFF.md").read_text(encoding="utf-8-sig")
        changes["HANDOFF.md"] = (handoff + f"\n\n## 단계 변경 — {core.now()}\n\n{previous} → {target}\n\n사유: {reason or '완료 기준과 검토 증거 확인'}\n\n다음: {core.STAGES[target][0]} 단계 문서와 원장을 확인하세요.\n").encode("utf-8")
        revision = add_event(root, changes, "rewind" if backwards else "advance", actor,
                             {"from": previous, "to": target, "reason": reason, "evidence_ref": evidence})
        commit(root, changes)
        return {"from": previous, "to": target, "revision": revision}


def ingest(folder, source, actor="agent"):
    root, _ = core.load_project(folder)
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError("입력 원본 파일이 없습니다.")
    with lock(root):
        manifest = confined(root, "inputs/manifest.json")
        data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {"schema_version": 1, "inputs": []}
        if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("inputs"), list):
            raise ValueError("입력 자료 목록 구조가 잘못되었습니다.")
        checksum = hash_file(source)
        existing = next((item for item in data["inputs"] if item["sha256"] == checksum), None)
        if existing and core.local_file(root, existing["path"]).is_file():
            if hash_file(root / existing["path"]) != checksum:
                raise ValueError("이미 등록된 원본이 변경되었습니다. 원본을 확인하세요.")
            return {"input": existing, "unchanged": True}
        identifier = next_id(data["inputs"], "input_id", "INPUT")
        relative = f"inputs/sources/{identifier}/{source.name}"
        confined(root, relative)
        record, extracted = make_record(source, identifier, relative, core.now())
        content = source.read_bytes()
        if digest(content) != record["sha256"]:
            raise ValueError("등록 중 원본이 변경되었습니다. 다시 읽으세요.")
        data["inputs"].append(record)
        changes = {relative: content, "inputs/manifest.json": json_bytes(data)}
        if extracted is not None:
            changes[record["extraction"]["path"]] = extracted.encode("utf-8")
        readme = core.local_file(root, "inputs/README.md").read_text(encoding="utf-8-sig")
        changes["inputs/README.md"] = (readme + f"\n\n- {identifier}: `{relative}` — {record['extraction']['status']} — SHA256 `{record['sha256']}`\n").encode("utf-8")
        revision = add_event(root, changes, "ingest", actor, {"input_id": identifier, "path": relative})
        commit(root, changes)
        return {"input": record, "revision": revision, "unchanged": False}


def discover(workspace):
    workspace = Path(workspace).resolve()
    candidates = [workspace] if (workspace / "project.json").is_file() else []
    for parent in {workspace / "projects", workspace if workspace.name == "projects" else workspace / "projects"}:
        if parent.is_dir():
            candidates.extend(path.parent for path in parent.glob("*/project.json"))
    result = []
    for candidate in sorted(set(candidates)):
        try:
            root, data = core.load_project(candidate)
            result.append({"path": str(root), "project_id": data["project_id"], "name": data["name"], "stage": data["current_stage"]})
        except (ValueError, OSError) as exc:
            result.append({"path": str(candidate), "error": str(exc)})
    return result


def inspect_project(folder):
    root, data, tables = bundle(folder)
    _, stage, issues = core.check_project(root)
    if re.search(r"\bTODO\b", core.local_file(root, "HANDOFF.md").read_text(encoding="utf-8-sig")):
        issues.append("HANDOFF.md에 미작성 TODO가 있습니다. 다음 작업을 기록하세요.")
    manifest = root / "inputs/manifest.json"
    inputs = json.loads(manifest.read_text(encoding="utf-8"))["inputs"] if manifest.exists() else []
    history = events(root)
    prior = []
    for item in list(core.STAGES)[:list(core.STAGES).index(stage) + 1]:
        prior.extend(stale_reviews(root, item, strict=True))
    return {"project": data, "path": str(root), "revision": history[-1]["revision"] if history else None,
            "stage": stage, "deliverables": [row for row in tables["deliverables"] if row["deliverable_id"] in core.required_deliverables(data, stage)],
            "proposal_package": {"enabled": proposal.enabled(data), "profile": proposal.PROFILE if proposal.enabled(data) else None},
            "open_tasks": [row for row in tables["tasks"] if row["status"] != "done"], "inputs": inputs,
            "read_files": ["AGENTS.md", "project.json", "HANDOFF.md", "process/RULES.md", "process/WORKFLOW.md", "process/ROLES.md"],
            "issues": issues, "gate_issues": prior,
            "can_advance": stage != "maintenance" and not issues and not prior,
            "next_actions": ([f"해결: {issue}" for issue in list(dict.fromkeys(issues + prior))[:8]] or
                             (["운영 이슈·변경 요청·검증 결과를 기록하고 유지보수하세요."] if stage == "maintenance" else
                              ["실제 검토 근거를 확인하고 advance로 다음 단계를 시작하세요."]))}


def doctor(folder=None):
    manifests = [json.loads((core.ROOT / name).read_text(encoding="utf-8")) for name in ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json")]
    checks = {"python_compatible": sys.version_info >= (3, 10), "manifest_identity_matches": len({item["name"] for item in manifests}) == 1,
              "manifest_version_matches": len({item["version"] for item in manifests}) == 1,
              "template_present": (core.BLUEPRINT / "project.json").is_file(),
              "skill_present": (core.ROOT / "skills/project-lifecycle/SKILL.md").is_file()}
    result = {"ready": all(checks.values()), "checks": checks, "version": manifests[0]["version"], "python": sys.version.split()[0]}
    if folder:
        root, _ = core.load_project(folder)
        result["pending_transaction"] = (root / "state/pending.json").exists()
        result["write_locked"] = (root / "state/write.lock").exists()
        result["ready"] = result["ready"] and not result["pending_transaction"] and not result["write_locked"]
    return result


def add_parsers(commands):
    for name in COMMANDS:
        parser = commands.add_parser(name, help={"apply": "원장·프로젝트·인수인계 갱신", "review": "근거와 검토 스냅샷 기록", "advance": "완료 기준 확인 후 다음 단계", "rewind": "변경 근거로 이전 단계 재검토", "ingest": "입력 원본 보존·추출", "discover": "작업 공간의 프로젝트 검색", "inspect": "이어하기 상태와 다음 행동", "doctor": "실행 환경 점검", "recover": "미완료 기록 복구", "proposal-setup": "기존 프로젝트에 정성제안·발표 패키지 추가"}[name])
        if name == "discover":
            parser.add_argument("workspace", nargs="?", default=str(Path.cwd()))
        elif name == "doctor":
            parser.add_argument("project", nargs="?")
        else:
            parser.add_argument("project")
        if name == "apply":
            parser.add_argument("--file", type=Path, required=True)
        if name == "ingest":
            parser.add_argument("source", type=Path)
        if name == "review":
            parser.add_argument("--reviewer", required=True)
            parser.add_argument("--evidence", required=True)
            parser.add_argument("--notes", required=True)
            parser.add_argument("--decision", choices=("approved", "rejected"), default="approved")
            parser.add_argument("--stage", choices=list(core.STAGES))
        if name in {"advance", "rewind"}:
            parser.add_argument("--to", choices=list(core.STAGES), required=name == "rewind")
            parser.add_argument("--reason", default="")
        if name == "rewind":
            parser.add_argument("--evidence", required=True)
        if name == "recover":
            parser.add_argument("--abandoned", action="store_true")
        if name in {"apply", "ingest", "advance", "rewind", "proposal-setup"}:
            parser.add_argument("--actor", default="agent")
        parser.add_argument("--json", action="store_true", help="항상 구조화된 JSON 결과를 반환합니다.")


def run_args(args):
    if args.command == "proposal-setup":
        return setup_proposal(args.project, args.actor)
    if args.command == "apply":
        return apply_patch(args.project, json.loads(args.file.read_text(encoding="utf-8-sig")), args.actor)
    if args.command == "ingest":
        return ingest(args.project, args.source, args.actor)
    if args.command == "review":
        return record_review(args.project, args.reviewer, args.evidence, args.notes, decision=args.decision, stage=args.stage)
    if args.command in {"advance", "rewind"}:
        return transition(args.project, target=args.to, actor=args.actor, reason=args.reason,
                          evidence=getattr(args, "evidence", None), backwards=args.command == "rewind")
    if args.command == "discover":
        return {"projects": discover(args.workspace)}
    if args.command == "inspect":
        return inspect_project(args.project)
    if args.command == "doctor":
        return doctor(args.project)
    return recover(args.project, abandoned=args.abandoned)
