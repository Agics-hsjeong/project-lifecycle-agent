"""Engine commands and explicitly connected native-host workbench execution."""
import json
from pathlib import Path

COMMANDS = {"history", "undo-preview", "undo", "run-create", "run-result", "run-resume", "input-review", "artifact-review"}
COMMANDS.update({"workspace-snapshot", "workspace-configure", "workspace-decide", "workspace-serve", "workspace-host", "workspace-runner", "workspace-activity", "workspace-review"})
COMMANDS.update({"dashboard", "upgrade-preview", "upgrade", "upgrade-restore-preview", "upgrade-restore"})


def add_parser(commands, name):
    parser = commands.add_parser(name)
    parser.add_argument("project")
    if name in {"workspace-serve", "workspace-runner"}:
        parser.add_argument("--provider", choices=("codex", "claude"), required=name == "workspace-runner")
        parser.add_argument("--executable", help="명시한 네이티브 CLI 실행 파일 또는 JavaScript 경로")
        parser.add_argument("--verification-executable", help="독립 검증용 Codex sandbox CLI 경로(모델 호출 없음)")
        parser.add_argument("--job-timeout", type=int, default=900)
    if name == "workspace-serve":
        parser.add_argument("--port", type=int, default=0)
        parser.add_argument("--open", action="store_true", help="로컬 관제 화면을 브라우저에서 열기")
    if name in {"workspace-configure", "workspace-decide", "workspace-host", "workspace-activity", "workspace-review"}:
        parser.add_argument("--file", type=Path, required=True)
        if name == "workspace-decide":
            parser.add_argument("--actor", required=True, help="실제 결정을 내린 사람 검토자")
        else:
            parser.add_argument("--actor", default="agent")
    if name == "dashboard":
        parser.add_argument("--output", default="reports/overview.html")
        parser.add_argument("--actor", default="agent")
    if name == "upgrade-restore-preview":
        parser.add_argument("--upgrade-id", required=True)
    if name == "undo-preview":
        parser.add_argument("--revision", required=True)
    if name in {"undo", "run-create", "run-result", "input-review", "artifact-review", "upgrade", "upgrade-restore"}:
        parser.add_argument("--file", type=Path, required=True)
        parser.add_argument("--actor", default="agent")
    if name == "run-resume":
        parser.add_argument("--run-id")
    parser.add_argument("--json", action="store_true")


def run(args):
    from tools import history, lifecycle, runs, inputs, artifacts, dashboard, upgrade
    if args.command.startswith("workspace-"):
        from tools import workspace
        if args.command == "workspace-snapshot":
            return workspace.snapshot(args.project)
        if args.command in {"workspace-serve", "workspace-runner"}:
            if not 1 <= args.job_timeout <= 3600:
                raise ValueError("작업 제한 시간은 1~3600초입니다.")
            options = {"timeout": args.job_timeout}
            if args.executable:
                options["executable"] = args.executable
            if args.verification_executable:
                options["verification_executable"] = args.verification_executable
            if args.command == "workspace-runner":
                from tools.workspace_runner import run_forever
                return run_forever(args.project, args.provider, provider_options=options)
            from tools import workspace_server
            return workspace_server.serve(args.project, port=args.port, open_browser=args.open,
                                          provider=args.provider, runner_options=options)
        if args.file.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("요청 파일 크기 제한 초과")
        payload = json.loads(args.file.read_text(encoding="utf-8-sig"))
        if args.command == "workspace-host":
            return workspace.host_event(args.project, payload)
        if args.command == "workspace-activity":
            return workspace.activity_event(args.project, payload)
        if args.command == "workspace-review":
            return workspace.review_event(args.project, payload)
        return {"workspace-configure": workspace.configure, "workspace-decide": workspace.decide}[args.command](args.project, payload, args.actor)
    if args.command == "upgrade-preview":
        return upgrade.preview(args.project)
    if args.command == "upgrade-restore-preview":
        return upgrade.restore_preview(args.project, args.upgrade_id)
    if args.command == "dashboard":
        return dashboard.write(args.project, args.output, args.actor)
    if args.command == "history":
        return {"events": lifecycle.events(Path(args.project))}
    if args.command == "undo-preview":
        return history.preview(args.project, args.revision)
    if args.command == "run-resume":
        return runs.inspect(args.project, args.run_id)
    if args.file.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("요청 파일 크기 제한 초과")
    payload = json.loads(args.file.read_text(encoding="utf-8-sig"))
    return {"undo": history.undo, "run-create": runs.create, "run-result": runs.result, "input-review": inputs.review, "artifact-review": artifacts.review, "upgrade": upgrade.apply, "upgrade-restore": upgrade.restore}[args.command](args.project, payload, args.actor)
