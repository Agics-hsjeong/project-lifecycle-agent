"""Explicit retention of terminal native jobs' disposable copies and raw logs.

This is a local privacy/disk maintenance operation, not lifecycle rollback or
project cleanup. Only the standard sibling .ieum-runtime/<project>/jobs tree is
eligible. It removes workspace/ copies and stdout/stderr/verification raw logs;
structured receipts, final/result JSON, user originals/outputs, project state,
history and runtimes dependency caches are retained. Custom job roots are never
accepted. Preview is read-only; applying its saved JSON is a separate explicit
action. Callers must choose both age and free-space thresholds (0 free bytes
means age-only selection). No automatic purge is enabled by importing this file.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid

from tools.storage import confined, digest, json_bytes, lock, process_alive

JOURNAL = "retention-journal.jsonl"
JOB = re.compile(r"attempt-[0-9a-f]{32}/CMD-[0-9a-f]{32}\Z")
RAW_LOG = re.compile(r"(?:stdout\.jsonl|stderr\.log|verification-\d+\.(?:stdout|stderr))\Z")


class RetentionError(ValueError):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result or {}


def _roots(folder):
    from tools import starter
    # Do not resolve away an alias before checking its components.
    lexical = Path(folder).absolute()
    _no_links(lexical)
    root, _ = starter.load_project(lexical)
    runtime = confined(root.parent, ".ieum-runtime/" + root.name)
    _no_links(runtime)
    jobs = confined(root.parent, ".ieum-runtime/" + root.name + "/jobs")
    _no_links(jobs)
    return root, runtime, jobs


def _no_links(path):
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("연결·재분석 경로는 보존 정리 대상으로 사용할 수 없습니다.")


def _json(path, default):
    if not path.exists():
        return default
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("보존 검사 메타데이터 크기 제한 초과")
    return json.loads(path.read_text(encoding="utf-8"))


def _queue(root):
    path = confined(root, "state/workspace.json")
    value = _json(path, {"queue": []})
    if not isinstance(value, dict) or not isinstance(value.get("queue"), list) or any(not isinstance(item, dict) for item in value["queue"]):
        raise ValueError("실행 큐 형식 오류: 정리하지 않습니다.")
    ids = [item.get("id") for item in value["queue"]]
    if any(not isinstance(item, str) for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("실행 큐 ID 형식 오류")
    return {item["id"]: item for item in value["queue"]}


def _active(root, queue):
    lease = _json(confined(root, "state/workspace-runner.lock"), None)
    if lease is not None:
        if not isinstance(lease, dict) or type(lease.get("pid")) is not int:
            raise ValueError("실행기 잠금 형식 오류: 정리하지 않습니다.")
        if lease.get("command_id") or lease.get("child_pid"):
            raise ValueError("실행기 작업·자식 프로세스 소유권이 남아 있어 정리하지 않습니다.")
        if process_alive(lease["pid"]):
            raise ValueError("실행 중인 실행기가 있어 정리하지 않습니다.")
    observed = _json(confined(root, "state/workspace-runtime.json"), {"runners": {}})
    if not isinstance(observed, dict) or not isinstance(observed.get("runners"), dict):
        raise ValueError("실행기 관측 형식 오류: 정리하지 않습니다.")
    for runner in observed["runners"].values():
        if not isinstance(runner, dict):
            raise ValueError("실행기 관측 형식 오류")
        child, pid = runner.get("child_pid"), runner.get("pid")
        if (type(child) is int and child > 0 and process_alive(child)) or (
                runner.get("status") in {"starting", "waiting", "running", "stopping"}
                and type(pid) is int and pid > 0 and process_alive(pid)):
            raise ValueError("살아 있는 실행기·자식 프로세스 관측이 있어 정리하지 않습니다.")
    if any(item.get("status") not in {"completed", "failed", "cancelled", "skipped"} for item in queue.values()):
        raise ValueError("활성·대기 작업 큐가 있어 정리하지 않습니다.")


def _pending(runtime):
    path = confined(runtime, JOURNAL)
    if not path.exists():
        return []
    pending = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if len(line) > 1024 * 1024:
                raise ValueError("보존 감사 기록 크기 오류")
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("operation_id"), str):
                raise ValueError("보존 감사 기록 형식 오류")
            if event.get("status") == "delete_started":
                pending[event["operation_id"]] = event.get("path")
            elif event.get("status") in {"deleted", "delete_failed"}:
                pending.pop(event["operation_id"], None)
    return list(pending.values())


def _fingerprint(path):
    _no_links(path)
    before = path.lstat()
    if not (stat.S_ISDIR(before.st_mode) or stat.S_ISREG(before.st_mode)):
        raise ValueError("일반 파일·폴더만 정리할 수 있습니다.")
    entries = []
    paths = [path]
    directories_before = {path: before} if path.is_dir() else {}
    if path.is_dir():
        for current, directories, files in os.walk(path, followlinks=False):
            current = Path(current)
            directories_before.setdefault(current, current.lstat())
            for name in sorted(directories + files):
                entry = current / name
                _no_links(entry)
                paths.append(entry)
    for entry in sorted(paths):
        info = entry.lstat()
        row = {"path": entry.relative_to(path).as_posix(), "mtime_ns": info.st_mtime_ns,
               "size": info.st_size, "kind": "directory" if stat.S_ISDIR(info.st_mode) else "file"}
        if row["kind"] == "file":
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("특수 파일은 정리하지 않습니다.")
            checksum = hashlib.sha256()
            with entry.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    checksum.update(chunk)
            after = entry.lstat()
            if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ValueError("보존 목록 작성 중 파일 변경")
            row["sha256"] = checksum.hexdigest()
        entries.append(row)
    if any(directory.lstat().st_mtime_ns != info.st_mtime_ns for directory, info in directories_before.items()):
        raise ValueError("보존 목록 작성 중 폴더 변경")
    return {"sha256": digest(json_bytes(entries)), "bytes": sum(item["size"] for item in entries if item["kind"] == "file"),
            "latest_mtime_ns": max(item["mtime_ns"] for item in entries), "entries": len(entries)}


def _terminal(root, jobs, job_ref, queue):
    if not isinstance(job_ref, str) or not JOB.fullmatch(job_ref):
        raise ValueError("표준 attempt UUID/CMD UUID 경로가 필요합니다.")
    job = confined(jobs, job_ref)
    _no_links(job)
    receipt_path = confined(job, "supervisor-receipt.json")
    _no_links(receipt_path)
    receipt = _json(receipt_path, None)
    command = queue.get(job.name)
    if not isinstance(receipt, dict) or receipt.get("status") not in {"completed", "failed"} or not command or command.get("status") not in {"completed", "failed"}:
        raise ValueError("감독자·큐 종료 근거가 없는 작업은 정리하지 않습니다.")
    if receipt.get("command_id") != job.name or receipt.get("attempt_id") != job.parent.name:
        raise ValueError("작업 디렉터리와 감독자 영수증이 다릅니다.")
    return job, digest(receipt_path.read_bytes())


def _policy(age, free):
    if isinstance(age, bool) or not isinstance(age, (float, int)) or not math.isfinite(age) or age < 0:
        raise ValueError("유한한 0 이상의 나이 임계값이 필요합니다.")
    if type(free) is not int or free < 0:
        raise ValueError("0 이상의 정수 가용 디스크 임계값이 필요합니다.")


def preview(folder, *, older_than_seconds, min_free_bytes):
    _policy(older_than_seconds, min_free_bytes)
    root, runtime, jobs = _roots(folder)
    with lock(root):
        queue = _queue(root)
        _active(root, queue)
        pending = _pending(runtime)
        if pending:
            raise RetentionError("이전 삭제의 감사 완료 기록이 없습니다. 실제 경로를 확인한 뒤 감사 기록을 복구하세요.", {"unresolved_deletions": pending})
        free = shutil.disk_usage(root.parent).free
        cutoff = time.time_ns() - int(older_than_seconds * 1e9)
        targets, skipped = [], []
        if jobs.exists() and (min_free_bytes == 0 or free < min_free_bytes):
            for attempt in sorted(jobs.iterdir()):
                _no_links(attempt)
                if not re.fullmatch(r"attempt-[0-9a-f]{32}", attempt.name) or not attempt.is_dir():
                    skipped.append({"path": attempt.name, "reason": "nonstandard_path"})
                    continue
                for job in sorted(attempt.iterdir()):
                    _no_links(job)
                    job_ref = job.relative_to(jobs).as_posix()
                    try:
                        job, receipt_sha = _terminal(root, jobs, job_ref, queue)
                    except ValueError:
                        skipped.append({"path": job_ref, "reason": "no_terminal_receipt"})
                        continue
                    for entry in sorted(job.iterdir()):
                        if entry.name != "workspace" and not RAW_LOG.fullmatch(entry.name):
                            continue
                        fingerprint = _fingerprint(entry)
                        if fingerprint["latest_mtime_ns"] > cutoff:
                            skipped.append({"path": entry.relative_to(jobs).as_posix(), "reason": "too_recent"})
                            continue
                        targets.append({"job": job_ref, "name": entry.name, "receipt_sha256": receipt_sha, **fingerprint})
        plan = {"schema_version": 1, "project": str(root), "runtime_root": str(runtime),
                "created_at_ns": time.time_ns(), "policy": {"older_than_seconds": older_than_seconds, "min_free_bytes": min_free_bytes},
                "free_bytes": free, "targets": targets, "skipped": skipped,
                "reclaimable_bytes": sum(item["bytes"] for item in targets)}
        plan["plan_sha256"] = digest(json_bytes(plan))
        return plan


def _journal(runtime, event):
    runtime.mkdir(parents=True, exist_ok=True)
    path = confined(runtime, JOURNAL)
    _no_links(path)
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "ab") as handle:
        handle.write(json_bytes(event).replace(b"\n", b"") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def apply(folder, plan):
    root, runtime, jobs = _roots(folder)
    if not isinstance(plan, dict) or plan.get("schema_version") != 1 or not isinstance(plan.get("targets"), list):
        raise ValueError("미리보기 보존 계획이 필요합니다.")
    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if digest(json_bytes(body)) != plan.get("plan_sha256") or plan.get("project") != str(root) or plan.get("runtime_root") != str(runtime):
        raise ValueError("보존 계획 무결성·프로젝트 경계 오류")
    policy = plan.get("policy", {})
    if not isinstance(policy, dict):
        raise ValueError("보존 계획 임계값 형식 오류")
    _policy(policy.get("older_than_seconds"), policy.get("min_free_bytes"))
    result = {"deleted": [], "failed": [], "audit_incomplete": False}
    with lock(root):
        queue = _queue(root)
        _active(root, queue)
        if _pending(runtime):
            raise RetentionError("이전 삭제 감사 기록이 미완료 상태입니다.")
        if policy["min_free_bytes"] and shutil.disk_usage(root.parent).free >= policy["min_free_bytes"]:
            raise ValueError("가용 디스크 임계값을 충족하므로 삭제하지 않습니다.")
        if type(plan.get("created_at_ns")) is not int or plan["created_at_ns"] > time.time_ns():
            raise ValueError("보존 계획 생성 시각 오류")
        cutoff = plan["created_at_ns"] - int(policy["older_than_seconds"] * 1e9)
        validated = []
        for item in plan["targets"]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or (item["name"] != "workspace" and not RAW_LOG.fullmatch(item["name"])):
                raise ValueError("작업 사본·원본 로그 외에는 정리할 수 없습니다.")
            job, receipt_sha = _terminal(root, jobs, item.get("job"), queue)
            path = confined(job, item["name"])
            fingerprint = _fingerprint(path)
            if receipt_sha != item.get("receipt_sha256") or any(fingerprint[key] != item.get(key) for key in fingerprint):
                raise ValueError("미리보기 이후 파일·해시·수정 시각·크기 변경: 계획을 다시 만드세요.")
            if fingerprint["latest_mtime_ns"] > cutoff:
                raise ValueError("나이 임계값을 충족하지 않는 대상")
            if str(path) in [str(value[0]) for value in validated]:
                raise ValueError("중복 정리 대상")
            validated.append((path, item))
        if validated:
            _journal(runtime, {"operation_id": uuid.uuid4().hex, "status": "plan_accepted", "plan_sha256": plan["plan_sha256"], "at_ns": time.time_ns()})
        for path, item in validated:
            try:
                current_queue = _queue(root)
                _active(root, current_queue)
                _, current_receipt = _terminal(root, jobs, item["job"], current_queue)
                if current_receipt != item["receipt_sha256"]:
                    raise ValueError("삭제 직전 감독자 영수증 변경")
                _no_links(path)
                if _fingerprint(path)["sha256"] != item["sha256"]:
                    raise ValueError("삭제 직전 대상 변경")
            except (OSError, ValueError) as exc:
                raise RetentionError("삭제 직전 상태가 변경되어 중지했습니다.", result) from exc
            operation = {"operation_id": uuid.uuid4().hex, "path": path.relative_to(jobs).as_posix(),
                         "sha256": item["sha256"], "plan_sha256": plan["plan_sha256"]}
            try:
                _journal(runtime, {**operation, "status": "delete_started", "at_ns": time.time_ns()})
            except OSError as exc:
                raise RetentionError("삭제 시작 감사 기록 실패: 이 대상은 삭제하지 않았습니다.", result) from exc
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as exc:
                result["failed"].append({"path": operation["path"], "still_exists": path.exists(), "may_be_partial": True})
                try:
                    _journal(runtime, {**operation, "status": "delete_failed", "at_ns": time.time_ns(), "still_exists": path.exists()})
                except OSError:
                    result["audit_incomplete"] = True
                raise RetentionError("삭제 중 오류: 일부 파일이 삭제되었을 수 있습니다.", result) from exc
            result["deleted"].append(operation["path"])
            try:
                _journal(runtime, {**operation, "status": "deleted", "at_ns": time.time_ns()})
            except OSError as exc:
                result["audit_incomplete"] = True
                raise RetentionError("대상은 삭제되었지만 감사 완료 기록을 저장하지 못했습니다.", result) from exc
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("preview")
    inspect.add_argument("project")
    inspect.add_argument("--older-than-hours", required=True, type=float)
    inspect.add_argument("--min-free-bytes", required=True, type=int)
    execute = commands.add_parser("apply")
    execute.add_argument("project")
    execute.add_argument("--apply-file", required=True)
    args = parser.parse_args()
    try:
        result = preview(args.project, older_than_seconds=args.older_than_hours * 3600, min_free_bytes=args.min_free_bytes) if args.command == "preview" else apply(args.project, _json(Path(args.apply_file), None))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), **getattr(exc, "result", {})}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
