"""Loopback project workbench with explicitly selected native host execution."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import threading
from urllib.parse import parse_qs, unquote, urlparse
import webbrowser

from tools import starter, workspace
from tools.storage import confined, json_bytes

MAX_BODY = 256 * 1024
DEFAULT_ASSETS = Path(__file__).resolve().parent / "workspace_ui"


def _read_with_lock_grace(folder, operation, timeout=1.5, interval=0.04):
    """Wait briefly only for another live engine reader/writer's lock.

    Snapshot polling and Diff GETs may arrive together. Never remove a lock,
    recover a journal, or retry stale revisions and other validation errors.
    Idempotent human decisions may also wait for lock acquisition; their
    revision/context conflicts are never retried.
    """
    lock_path = Path(folder).resolve() / "state/write.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            return operation()
        except ValueError as exc:
            cause = exc.__cause__
            if not isinstance(cause, FileExistsError) or not cause.filename or Path(cause.filename).resolve() != lock_path:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(interval, remaining))


def _set_supervisor(server, **fields):
    with server.supervisor_lock:
        server.supervisor_state.update(fields, updated_at=starter.now())


def _snapshot_with_supervisor(server, value):
    # HTTP availability is separate from actual runner connectivity. This is
    # local server health, not a project event or a fabricated agent heartbeat.
    with server.supervisor_lock:
        health = dict(server.supervisor_state)
    return {**value, "host": {**value.get("host", {}), "supervisor": health}}


def _supervise(server, provider, runner_function, stop_event, options, retry_errors=(), retry_delays=(0.2, 0.4, 0.8, 1.6, 3.2)):
    """Retry only acquisition conflicts, bounded and interruptible.

    A duplicate owner's lifetime lease is never removed. Authentication, invalid
    policy, running-task failure and unknown errors are not silently replayed.
    """
    for attempt in range(len(retry_delays) + 1):
        if stop_event.is_set():
            _set_supervisor(server, status="stopped", next_retry_seconds=None)
            return
        _set_supervisor(server, status="running", attempts=attempt + 1,
                        last_error=None, next_retry_seconds=None)
        try:
            runner_function(server.project, provider, stop_event=stop_event, provider_options=options)
        except Exception as exc:
            summary = workspace.public_summary(str(exc) or type(exc).__name__)
            if isinstance(exc, retry_errors) and attempt < len(retry_delays):
                delay = retry_delays[attempt]
                _set_supervisor(server, status="retrying", last_error=summary, next_retry_seconds=delay)
                if stop_event.wait(delay):
                    _set_supervisor(server, status="stopped", next_retry_seconds=None)
                    return
                continue
            _set_supervisor(server, status="failed", last_error=summary, next_retry_seconds=None)
            print(json.dumps({"runner_error": summary, "provider": provider}, ensure_ascii=False), file=sys.stderr, flush=True)
            return
        _set_supervisor(server, status="stopped", next_retry_seconds=None)
        return


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, folder, host, port, assets):
        if host != "127.0.0.1":
            raise ValueError("관제 서버는 127.0.0.1에서만 실행합니다.")
        self.project = starter.load_project(folder)[0]
        self.supervisor_lock = threading.Lock()
        self.supervisor_state = {"configured": False, "provider": None, "status": "disabled",
                                 "attempts": 0, "last_error": None, "next_retry_seconds": None,
                                 "updated_at": starter.now()}
        self.human_token = secrets.token_urlsafe(32)
        self.host_token = secrets.token_urlsafe(32)
        self.assets = Path(assets).resolve() if assets else DEFAULT_ASSETS
        self.manifest = None
        manifest = self.assets / "manifest.json"
        if manifest.is_file():
            value = json.loads(manifest.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1 or not isinstance(value.get("sha256"), dict):
                raise ValueError("관제 UI manifest가 잘못되었습니다.")
            self.manifest = value
        super().__init__((host, port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.url = self.origin + "/project-lifecycle-agent/workspace/?mode=local#token=" + self.human_token


class Handler(BaseHTTPRequestHandler):
    server_version = "IeumLocal/1"

    def log_message(self, format, *args):
        # Never log request headers, tokens or customer file contents.
        return

    def respond(self, code, body, content_type="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json_bytes(body)
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for name, value in (headers or {}).items():
            if name not in {"Content-Disposition", "X-Content-SHA256"}:
                continue
            self.send_header(name, value)
        self.send_header("Content-Security-Policy", (headers or {}).get("Content-Security-Policy") or "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; worker-src 'self' blob:; frame-src blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        try:
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # Browsers cancel superseded polls/Diff requests. A response may
            # already be partly sent, so never retry it as a file/API error.
            self.close_connection = True
            return

    def authorized(self, host=False, mutation=False):
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.respond(403, {"error": "허용되지 않은 Host입니다.", "code": "host_forbidden"})
            return False
        origin = self.headers.get("Origin")
        if (origin is not None and origin != self.server.origin) or (mutation and not host and origin != self.server.origin):
            self.respond(403, {"error": "로컬 관제 화면에서 요청하세요.", "code": "origin_forbidden"})
            return False
        token = self.server.host_token if host else self.server.human_token
        supplied = self.headers.get("Authorization", "")
        if not secrets.compare_digest(supplied, "Bearer " + token):
            self.respond(403, {"error": "유효한 세션 권한이 필요합니다.", "code": "unauthorized"})
            return False
        return True

    def route(self):
        path = urlparse(self.path).path
        return path[len("/project-lifecycle-agent"):] if path.startswith("/project-lifecycle-agent/") else path

    def do_OPTIONS(self):
        self.respond(403, {"error": "교차 출처 접근은 허용하지 않습니다.", "code": "origin_forbidden"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        route = self.route()
        if route.startswith("/api/"):
            host = route.startswith("/api/host/")
            if not self.authorized(host=host):
                return
            try:
                if route in {"/api/snapshot", "/api/host/snapshot"}:
                    value = _read_with_lock_grace(self.server.project, lambda: workspace.snapshot(self.server.project)) if route == "/api/snapshot" else workspace.snapshot(self.server.project)
                    self.respond(200, _snapshot_with_supervisor(self.server, value))
                elif route == "/api/artefact":
                    from tools import artefact_delivery
                    query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                    if set(query) != {"id", "sha256"} or any(len(values) != 1 for values in query.values()):
                        raise ValueError("하나의 산출물 ID와 SHA-256이 필요합니다.")
                    delivered = _read_with_lock_grace(self.server.project, lambda: artefact_delivery.get(
                        self.server.project, query["id"][0], query["sha256"][0]))
                    self.respond(200, delivered["body"], delivered["content_type"], delivered["headers"])
                elif route == "/api/diff":
                    query = parse_qs(urlparse(self.path).query)
                    if set(query) != {"path"} or len(query["path"]) != 1:
                        raise ValueError("하나의 산출물 경로가 필요합니다.")
                    self.respond(200, _read_with_lock_grace(self.server.project, lambda: workspace.diff(self.server.project, query["path"][0])))
                else:
                    self.respond(404, {"error": "없는 API입니다.", "code": "not_found"})
            except (ValueError, OSError) as exc:
                self.failure(exc)
            return
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            self.respond(403, {"error": "허용되지 않은 Host입니다."})
            return
        try:
            manifest = self.server.manifest
            if not manifest:
                self.respond(503, "관제 UI 빌드가 없습니다. 플러그인의 검증된 workspace_ui 자산이 필요합니다.", "text/plain; charset=utf-8")
                return
            relative = unquote(route).lstrip("/")
            if relative in {"", "workspace", "workspace/"}:
                relative = manifest.get("entry", "workspace/index.html")
            elif relative.endswith("/"):
                relative += "index.html"
            if relative not in manifest["sha256"]:
                self.respond(404, "Not found", "text/plain")
                return
            path = confined(self.server.assets, relative)
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest["sha256"][relative]:
                raise ValueError("관제 UI 자산의 무결성 검증에 실패했습니다.")
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif path.suffix in {".html", ".css", ".svg"}:
                content_type += "; charset=utf-8"
            self.respond(200, raw, content_type)
        except (OSError, ValueError) as exc:
            self.failure(exc)

    def do_POST(self):
        route = self.route()
        host = route.startswith("/api/host/")
        if not self.authorized(host=host, mutation=True):
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("application/json 요청이 필요합니다.")
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("요청 본문 크기 제한을 초과했습니다.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("유한한 JSON 숫자만 허용합니다.")))
            if not isinstance(payload, dict):
                raise ValueError("JSON 객체 요청이 필요합니다.")
            if route == "/api/action":
                self.respond(200, _read_with_lock_grace(self.server.project, lambda: workspace.decide(self.server.project, payload)))
            elif route == "/api/host/activity":
                self.respond(200, _read_with_lock_grace(self.server.project, lambda: workspace.activity_event(self.server.project, payload)))
            elif route == "/api/host/review":
                self.respond(200, _read_with_lock_grace(self.server.project, lambda: workspace.review_event(self.server.project, payload)))
            elif route in {"/api/host/event", "/api/host/claim", "/api/host/heartbeat", "/api/host/started", "/api/host/completed", "/api/host/failed", "/api/host/retry"}:
                if route != "/api/host/event":
                    kind = route.rsplit("/", 1)[-1]
                    if payload.get("kind", kind) != kind:
                        raise ValueError("호스트 이벤트 종류가 일치하지 않습니다.")
                    payload["kind"] = kind
                self.respond(200, workspace.host_event(self.server.project, payload))
            else:
                self.respond(404, {"error": "없는 API입니다.", "code": "not_found"})
        except (ValueError, OSError, TypeError) as exc:
            self.failure(exc)

    def failure(self, exc):
        conflict = isinstance(exc, workspace.Conflict) or "잠금" in str(exc)
        status = getattr(exc, "status", 409 if conflict else 400)
        self.respond(status, {"error": workspace.public_summary(str(exc)), "code": "conflict" if status == 409 else "invalid_request"})


def make_server(folder, host="127.0.0.1", port=0, assets=None):
    return LocalServer(folder, host, port, assets)


def serve(folder, port=0, assets=None, open_browser=False, provider=None, runner_options=None):
    if provider not in {None, "codex", "claude"}:
        raise ValueError("실행 호스트는 codex 또는 claude여야 합니다.")
    if runner_options is not None and not isinstance(runner_options, dict):
        raise ValueError("실행기 옵션은 객체여야 합니다.")
    runner_function = None
    if provider:
        from tools.workspace_runner import run_forever
        runner_function = run_forever
    server = make_server(folder, port=port, assets=assets)
    stop_event, runner_thread = threading.Event(), None
    if runner_function:
        # Also initialize lightweight server fakes used by lifecycle callers.
        server.supervisor_lock = threading.Lock()
        server.supervisor_state = {"configured": True, "provider": provider, "status": "starting",
                                   "attempts": 0, "last_error": None, "next_retry_seconds": None,
                                   "updated_at": starter.now()}
        duplicate = getattr(sys.modules.get("tools.workspace_runner"), "RunnerAlreadyRunning", ())
        retry_errors = (duplicate,) if isinstance(duplicate, type) else ()
        runner_thread = threading.Thread(target=_supervise,
            args=(server, provider, runner_function, stop_event, runner_options, retry_errors),
            name="ieum-workspace-runner", daemon=True)
        runner_thread.start()
    runtime = tempfile.TemporaryDirectory(prefix="ieum-workspace-")
    os.chmod(runtime.name, 0o700)
    credential = Path(runtime.name) / "host-session.json"
    session = {"origin": server.origin, "host_token": server.host_token, "pid": os.getpid(), "kind": "local-host-capability"}
    # Ephemeral local runtime credential, not project evidence or an engine mutation.
    fd = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(json_bytes(session))
    print(json.dumps({"url": server.url, "host_session": str(credential), "mode": "local", "provider": provider}, ensure_ascii=False), flush=True)
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if runner_thread:
            runner_thread.join(timeout=20)
        server.server_close()
        runtime.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--assets")
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--provider", choices=("codex", "claude"), help="승인·수정 요청을 자동 처리할 실행 호스트")
    args = parser.parse_args()
    serve(args.project, args.port, args.assets, args.open_browser, provider=args.provider)


if __name__ == "__main__":
    main()
