"""Opt-in executable profiles. Legacy projects retain their original 11 stages."""
PROFILES = {
    "si": ["discovery", "proposal", "kickoff", "planning", "design", "publishing", "implementation", "verification", "release", "handover", "maintenance"],
    "product": ["discovery", "planning", "implementation", "verification", "release", "maintenance"],
    "api": ["discovery", "proposal", "kickoff", "planning", "implementation", "verification", "release", "handover", "maintenance"],
    "maintenance": ["discovery", "planning", "implementation", "verification", "release", "maintenance"],
}


def validate(value, data, tables):
    import re
    if not isinstance(value, dict) or set(value) - {"profile", "release", "blockers"} or value.get("profile") not in PROFILES:
        raise ValueError("workflow profile은 si/product/api/maintenance여야 합니다.")
    if data["current_stage"] not in PROFILES[value["profile"]]:
        raise ValueError("현재 단계를 제외하는 프로필로 변경할 수 없습니다. 먼저 범위·이행을 검토하세요.")
    release = value.get("release")
    if release is not None:
        if not isinstance(release, dict) or set(release) != {"id", "requirement_ids"} or not re.fullmatch(r"REL-\d{3,}", release.get("id", "")):
            raise ValueError("release는 REL-ID와 requirement_ids가 필요합니다.")
        ids = release["requirement_ids"]
        if not isinstance(ids, list) or not ids or any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids) or not set(ids).issubset({r["requirement_id"] for r in tables["requirements"]}):
            raise ValueError("릴리스의 실제 요구사항 ID 목록을 확인하세요.")
    blockers = value.get("blockers", {})
    if not isinstance(blockers, dict):
        raise ValueError("blockers는 단계별 객체여야 합니다.")
    for stage, groups in blockers.items():
        if stage not in PROFILES[value["profile"]] or not isinstance(groups, dict) or set(groups) - {"tasks", "risks"}:
            raise ValueError("진행 차단 단계·종류를 확인하세요.")
        for group, identifiers in groups.items():
            key = {"tasks": "task_id", "risks": "risk_id"}[group]
            if not isinstance(identifiers, list) or any(not isinstance(item, str) for item in identifiers) or not set(identifiers).issubset({r[key] for r in tables[group]}):
                raise ValueError("없는 진행 차단 ID입니다.")


def stages(data):
    from tools import starter
    value = data.get("workflow")
    if value is None:
        return list(starter.STAGES)
    if not isinstance(value, dict) or value.get("profile") not in PROFILES:
        raise ValueError("workflow 구조가 잘못되었습니다.")
    return PROFILES[value["profile"]][:]


def selected(data, row, stage):
    from tools import starter
    release = data.get("workflow", {}).get("release")
    if release and list(starter.STAGES).index(stage) >= 3:
        return row["requirement_id"] in release["requirement_ids"]
    return True


def gate_issues(root, data, tables, stage):
    from tools import starter
    issues = []
    for group, identifiers in data.get("workflow", {}).get("blockers", {}).get(stage, {}).items():
        key = {"tasks": "task_id", "risks": "risk_id"}[group]
        for identifier in identifiers:
            row = next((r for r in tables[group] if r[key] == identifier), None)
            if row is None or row["status"] != ("done" if group == "tasks" else "closed"):
                issues.append(f"{stage}: 필수 선행조건 {identifier} 미해결")
            elif group == "risks":
                try:
                    starter.local_file(root, row["response"])
                except ValueError:
                    issues.append(f"{identifier}: 위험 종료의 실제 근거 파일이 필요합니다.")
    return issues
