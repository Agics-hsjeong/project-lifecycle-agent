"""Additional commands; all work is performed by the active host, never a daemon."""
import json
from pathlib import Path

COMMANDS = {"history", "undo-preview", "undo", "run-create", "run-result", "run-resume", "input-review", "artifact-review"}
COMMANDS.update({"dashboard", "upgrade-preview", "upgrade", "upgrade-restore-preview", "upgrade-restore"})


def add_parser(commands, name):
    parser = commands.add_parser(name)
    parser.add_argument("project")
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
