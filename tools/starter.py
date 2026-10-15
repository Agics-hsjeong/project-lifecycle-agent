"""Local, dependency-free project starter manager. No model/API calls."""
import argparse
import csv
import json
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = ROOT / "blueprint"
STAGES = {
    "discovery": ("사업 검토", ["DEL-001"]),
    "proposal": ("제안·사업 실행안", ["DEL-002", "DEL-003"]),
    "kickoff": ("착수", ["DEL-004"]),
    "planning": ("기획·설계", ["DEL-005", "DEL-006"]),
    "design": ("디자인", ["DEL-007"]),
    "publishing": ("퍼블리싱", ["DEL-008"]),
    "implementation": ("구현", ["DEL-009"]),
    "verification": ("검증", ["DEL-010"]),
    "release": ("배포", ["DEL-011"]),
    "handover": ("납품·인수인계", ["DEL-012", "DEL-013"]),
    "maintenance": ("유지보수", ["DEL-014"]),
}
HEADERS = {
    "requirements": "requirement_id,source,description,acceptance_criteria,priority,status,design_refs,implementation_refs,test_refs,deliverable_ids,exclusion_reason",
    "deliverables": "deliverable_id,stage,title,path,owner,version,status,requirement_ids,exclusion_reason",
    "tasks": "task_id,requirement_ids,role,owner,status,priority,due_date,next_action,evidence_refs",
    "risks": "risk_id,description,impact,probability,owner,response,status",
    "reviews": "review_id,stage,decision,reviewer,reviewed_at,evidence_ref,notes",
}
REQ_STATES = {"proposed", "baselined", "implemented", "verified", "accepted", "deferred"}
DEL_STATES = {"draft", "in_review", "approved", "not_applicable"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def split_refs(value):
    return [item.strip() for item in value.split(";") if item.strip()]


def missing(value):
    return not isinstance(value, str) or value.strip() in {"", "TODO", "미정", "TEMPLATE"}


def load_project(folder):
    folder = Path(folder).resolve()
    data = json.loads((folder / "project.json").read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("지원하지 않는 project.json 구조/스키마입니다.")
    if data.get("business_type") not in {"si", "product"}:
        raise ValueError("business_type은 si 또는 product여야 합니다.")
    if data.get("methodology") not in {"hybrid", "waterfall", "agile"}:
        raise ValueError("methodology는 hybrid, waterfall, agile 중 하나여야 합니다.")
    if data.get("current_stage") not in STAGES:
        raise ValueError("알 수 없는 current_stage입니다.")
    return folder, data


def read_table(folder, name):
    path = folder / "registers" / (name + ".csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != HEADERS[name].split(","):
            raise ValueError(f"{path.name}: 헤더/열 순서가 기본형과 다릅니다.")
        rows = []
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path.name}:{line}: CSV 열 수가 맞지 않습니다.")
            if any(value.strip() for value in row.values()):
                rows.append({key: value.strip() for key, value in row.items()})
        return rows


def local_file(folder, value):
    if not value or ":" in value or "\\" in value:
        raise ValueError("프로젝트 기준 '/' 경로가 필요합니다")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("프로젝트 외부 경로는 허용하지 않습니다")
    target = (folder / relative).resolve()
    if not target.is_relative_to(folder.resolve()) or not target.is_file():
        raise ValueError("프로젝트 내부의 실제 파일이 아닙니다")
    return target


def init_project(parent, slug, name, business_type, rfp=None):
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", slug):
        raise ValueError("slug는 영문 소문자로 시작하고 소문자·숫자·하이픈만 사용합니다.")
    if slug.upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}:
        raise ValueError("운영체제 예약 이름은 사용할 수 없습니다.")
    if not name.strip() or business_type not in {"si", "product"}:
        raise ValueError("이름과 사업 유형을 확인하세요.")
    source = Path(rfp).resolve() if rfp else None
    if source and not source.is_file():
        raise ValueError("RFP 원본 파일을 찾을 수 없습니다.")
    parent = Path(parent).resolve()
    target = parent / slug
    parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation: never merge or overwrite an existing customer project.
    target.mkdir(exist_ok=False)
    shutil.copytree(BLUEPRINT, target, dirs_exist_ok=True)
    data = json.loads((target / "project.json").read_text(encoding="utf-8"))
    data.update(project_id=slug, name=name.strip(), business_type=business_type,
                created_at=now(), updated_at=now())
    (target / "project.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if source:
        input_dir = target / "inputs" / "rfp"
        input_dir.mkdir()
        copied = input_dir / source.name
        shutil.copy2(source, copied)
        title = source.name.replace("|", "\\|")
        readme = target / "inputs" / "README.md"
        with readme.open("a", encoding="utf-8") as handle:
            handle.write(f"\n\n자동 등록 입력: `{copied.relative_to(target).as_posix()}`\n")
            handle.write(f"\n- 자료명: {title}\n- 원본 위치: `{source}`\n- 등록 일시: {now()}\n- 원문 버전·출처·사용 범위: 확인 필요\n")
    return target


def check_project(folder, selected_stage=None):
    folder, data = load_project(folder)
    stage = selected_stage or data["current_stage"]
    if stage not in STAGES:
        raise ValueError("알 수 없는 단계입니다.")
    issues = []
    tables = {name: read_table(folder, name) for name in HEADERS}
    requirements, deliverables = tables["requirements"], tables["deliverables"]
    req_ids = {row["requirement_id"] for row in requirements}
    del_ids = {row["deliverable_id"] for row in deliverables}

    for field in ("project_id", "name", "project_manager", "client_or_product_owner", "objective", "scope_baseline"):
        if missing(data.get(field)):
            issues.append(f"project.json: {field} 미작성")
    for name, prefix in (("requirements", "REQ"), ("deliverables", "DEL"), ("tasks", "TASK"), ("risks", "RISK"), ("reviews", "REV")):
        key = {"requirements": "requirement_id", "deliverables": "deliverable_id", "tasks": "task_id", "risks": "risk_id", "reviews": "review_id"}[name]
        counts = Counter(row[key] for row in tables[name])
        for identifier, count in counts.items():
            if not re.fullmatch(prefix + r"-\d{3,}", identifier) or count > 1:
                issues.append(f"{name}: 잘못되거나 중복된 ID {identifier!r}")

    def files(label, value, required=False, inspect_template=False):
        refs = split_refs(value)
        if required and not refs:
            issues.append(f"{label}: 파일 경로 누락")
        for ref in refs:
            try:
                path = local_file(folder, ref)
                if inspect_template and path.suffix.lower() in {".md", ".txt"}:
                    if re.search(r"\bTODO\b", path.read_text(encoding="utf-8-sig")):
                        issues.append(f"{label}: 미작성 TODO가 남음 ({ref})")
            except (ValueError, OSError, UnicodeError) as exc:
                issues.append(f"{label}: {ref} — {exc}")

    if not requirements:
        issues.append("requirements: 분석한 실제 요구사항을 최소 1개 등록하세요.")
    order = list(STAGES).index(stage)
    active = [row for row in requirements if row["status"] != "deferred"]
    if requirements and not active:
        issues.append("requirements: 모든 요구사항이 제외되어 있습니다. 진행 범위를 확인하세요.")
    for row in requirements:
        rid = row["requirement_id"]
        for field in ("source", "description", "acceptance_criteria", "priority"):
            if missing(row[field]):
                issues.append(f"{rid}: {field} 미작성")
        if row["status"] not in REQ_STATES:
            issues.append(f"{rid}: 알 수 없는 요구사항 상태")
        if row["status"] == "deferred":
            if missing(row["exclusion_reason"]):
                issues.append(f"{rid}: 범위 제외 사유 누락")
            continue
        if order >= 3 and row["status"] == "proposed":
            issues.append(f"{rid}: 기획 이후 요구사항 기준선(baselined) 필요")
        if order >= 6 and row["status"] not in {"implemented", "verified", "accepted"}:
            issues.append(f"{rid}: 구현 완료 상태 필요")
        if order >= 7 and row["status"] not in {"verified", "accepted"}:
            issues.append(f"{rid}: 검증 완료 상태 필요")
        if order >= 9 and row["status"] != "accepted":
            issues.append(f"{rid}: 인수 근거와 accepted 상태 필요")
        for field, threshold in (("design_refs", 3), ("implementation_refs", 6), ("test_refs", 7)):
            files(f"{rid}/{field}", row[field], required=order >= threshold)
        links = split_refs(row["deliverable_ids"])
        if order >= 9 and not links:
            issues.append(f"{rid}: 납품 산출물 ID 누락")
        for identifier in links:
            if identifier not in del_ids:
                issues.append(f"{rid}: 없는 산출물 ID {identifier}")
            elif order >= 9:
                linked = next(d for d in deliverables if d["deliverable_id"] == identifier)
                if linked["status"] != "approved":
                    issues.append(f"{rid}: 납품 연결 {identifier}가 approved 상태가 아님")
                if rid not in split_refs(linked["requirement_ids"]):
                    issues.append(f"{rid}: {identifier}의 역방향 요구사항 연결 누락")

    required = set(STAGES[stage][1])
    for identifier in sorted(required - del_ids):
        issues.append(f"{stage}: 필수 산출물 행 누락 {identifier}")
    for row in deliverables:
        did = row["deliverable_id"]
        if row["stage"] not in STAGES or row["status"] not in DEL_STATES:
            issues.append(f"{did}: 잘못된 단계/산출물 상태")
        for rid in split_refs(row["requirement_ids"]):
            if rid not in req_ids:
                issues.append(f"{did}: 없는 요구사항 ID {rid}")
        if did not in required:
            continue
        if row["stage"] != stage:
            issues.append(f"{did}: 필수 산출물 단계가 {stage}와 다름")
        if row["status"] == "not_applicable":
            if missing(row["exclusion_reason"]):
                issues.append(f"{did}: 적용 제외 사유 누락")
            continue
        for field in ("owner", "version", "title"):
            if missing(row[field]):
                issues.append(f"{did}: {field} 미작성")
        if row["status"] != "approved":
            issues.append(f"{did}: 미승인 상태 ({row['status']})")
        files(did, row["path"], required=True, inspect_template=True)

    reviews = []
    for row in tables["reviews"]:
        if row["stage"] not in STAGES or row["decision"] not in {"approved", "rejected"}:
            issues.append(f"{row['review_id']}: 잘못된 검토 단계/결정")
        if row["stage"] != stage:
            continue
        if missing(row["reviewer"]):
            issues.append(f"{row['review_id']}: 검토자 누락")
        try:
            stamp = datetime.fromisoformat(row["reviewed_at"])
            if stamp.utcoffset() is None:
                raise ValueError("시간대 누락")
            reviews.append((stamp, row))
        except ValueError:
            issues.append(f"{row['review_id']}: 검토 일시 형식/시간대 오류")
        files(row["review_id"], row["evidence_ref"], required=True, inspect_template=True)
        if missing(row["notes"]):
            issues.append(f"{row['review_id']}: 검토 내용/적용 제외 근거 누락")
    if not reviews:
        issues.append(f"{stage}: 책임자 검토 기록 없음")
    elif max(reviews, key=lambda item: item[0])[1]["decision"] != "approved":
        issues.append(f"{stage}: 최신 검토 결정이 approved가 아님")
    for row in tables["tasks"]:
        if row["status"] not in {"todo", "doing", "blocked", "done"}:
            issues.append(f"{row['task_id']}: 잘못된 작업 상태")
        for rid in split_refs(row["requirement_ids"]):
            if rid not in req_ids:
                issues.append(f"{row['task_id']}: 없는 요구사항 ID {rid}")
        files(row["task_id"], row["evidence_refs"])
    for ref in ("AGENTS.md", "CLAUDE.md", "HANDOFF.md", "process/RULES.md", "process/WORKFLOW.md", "process/ROLES.md"):
        files("공통 파일", ref, required=True)
    return data, stage, issues


def print_status(folder):
    folder, data = load_project(folder)
    deliverables = read_table(folder, "deliverables")
    tasks = read_table(folder, "tasks")
    stage = data["current_stage"]
    approved = sum(row["status"] == "approved" for row in deliverables)
    print(f"{data['project_id']} | {data['name']} | {data['business_type']} | {STAGES[stage][0]} ({stage})")
    print(f"  산출물 승인 {approved}/{len(deliverables)} | 작업 상태 {dict(Counter(row['status'] for row in tasks))}")
    print(f"  {folder}")


def print_context(folder):
    folder, data = load_project(folder)
    print_status(folder)
    stage = data["current_stage"]
    print("\n먼저 읽을 파일: AGENTS.md, project.json, HANDOFF.md, process/RULES.md, process/WORKFLOW.md, process/ROLES.md")
    print("현재 단계 산출물:")
    for row in read_table(folder, "deliverables"):
        if row["deliverable_id"] in STAGES[stage][1]:
            print(f"- {row['deliverable_id']} | {row['path']} | {row['status']}")
    print("\n작업 요청: 현재 단계를 확인하고 근거·가정·질문을 구분해 진행해줘. 관련 원장과 HANDOFF.md를 갱신해줘.")
    print("이 출력은 읽기 목록입니다. 실제 입력·인수인계 파일을 읽어야 하며 대화 첨부만으로 로컬 저장되지는 않습니다.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="사업·프로젝트 에이전트 스타터팩 관리")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("init", help="새 프로젝트 생성 (덮어쓰기 금지)")
    create.add_argument("--name", required=True)
    create.add_argument("--slug", required=True)
    create.add_argument("--type", choices=["si", "product"], default="si")
    create.add_argument("--workspace", type=Path, default=Path.cwd(), help="고객 프로젝트를 저장할 작업 공간 (플러그인 설치 경로와 분리)")
    create.add_argument("--rfp", type=Path, help="새 프로젝트 inputs에 보존할 원본 파일")
    status = commands.add_parser("status", help="프로젝트 현황")
    status.add_argument("project", nargs="?")
    check = commands.add_parser("check", help="문서와 원장 구조 점검")
    check.add_argument("project")
    check.add_argument("--stage", choices=list(STAGES))
    context = commands.add_parser("context", help="AI에게 전달할 읽기 목록")
    context.add_argument("project")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            target = init_project(args.workspace / "projects", args.slug, args.name, args.type, args.rfp)
            print(f"생성 완료: {target}\nproject.json의 담당자·목표·범위를 입력하고 inputs/에 원본 자료를 추가하세요.")
        elif args.command == "status":
            if args.project:
                print_status(args.project)
            else:
                projects = sorted((Path.cwd() / "projects").glob("*/project.json"))
                if not projects:
                    print("프로젝트 없음. init 명령으로 생성하세요.")
                failed = False
                for project in projects:
                    try:
                        print_status(project.parent)
                    except (ValueError, OSError) as exc:
                        print(f"오류: {project.parent} — {exc}", file=sys.stderr)
                        failed = True
                return 2 if failed else 0
        elif args.command == "context":
            print_context(args.project)
        else:
            data, stage, issues = check_project(args.project, args.stage)
            print(f"{data['name']} / {stage}: 구조 점검 {'미완료' if issues else '통과'}")
            for issue in issues:
                print(f"- {issue}")
            print("내용·실행 결과·승인 진위의 검증은 책임자 검토와 실제 증거 확인이 필요합니다.")
            return 1 if issues else 0
    except (ValueError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
