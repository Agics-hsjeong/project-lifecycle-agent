"""Asset isolation and corruption checks for the distributable local UI."""
import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from tools import artefact_delivery, build_workspace, lifecycle, native_observation as native, starter, workspace, workspace_server


class WorkspaceAssetTests(unittest.TestCase):
    def test_bundle_copies_only_entry_dependencies_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "website/dist", root / "tools/ui"
            (source / "workspace").mkdir(parents=True)
            (source / "_astro").mkdir()
            (source / "fonts").mkdir()
            (source / "downloads").mkdir()
            (source / "workspace/index.html").write_text('<script type="module" src="/project-lifecycle-agent/_astro/entry.js"></script>', encoding="utf-8")
            (source / "_astro/entry.js").write_text('import "./shared.js"; import "./style.css";', encoding="utf-8")
            (source / "_astro/shared.js").write_text('export const version=1;', encoding="utf-8")
            (source / "_astro/style.css").write_text('@font-face {src:url("/project-lifecycle-agent/fonts/test.woff2")}', encoding="utf-8")
            (source / "fonts/test.woff2").write_bytes(b"font fixture")
            (source / "downloads/private.txt").write_text("must not ship", encoding="utf-8")
            with patch.object(build_workspace, "ROOT", root):
                result = build_workspace.bundle(source, output)
                self.assertEqual(result["files"], 5)
                self.assertFalse((output / "downloads").exists())
                self.assertTrue(build_workspace.verify(output)["ready"])
                (output / "_astro/shared.js").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    build_workspace.verify(output)

    def test_manifest_traversal_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "manifest.json").write_text(json.dumps({"schema_version": 1, "base": build_workspace.BASE,
                "entry": build_workspace.ENTRY, "sha256": {build_workspace.ENTRY: "x", "../outside": "y"}}), encoding="utf-8")
            # The entry must be valid so the traversal field is the failing one.
            (folder / "workspace").mkdir()
            data = b"fixture"
            (folder / build_workspace.ENTRY).write_bytes(data)
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            manifest["sha256"][build_workspace.ENTRY] = build_workspace.checksum(data)
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsafe asset path"):
                build_workspace.verify(folder)

    def test_source_fingerprint_is_independent_of_checkout_line_endings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "website/package.json"
            target.parent.mkdir()
            target.write_bytes(b'{\r\n  "name": "fixture"\r\n}\r\n')
            with patch.object(build_workspace, "ROOT", root):
                before = build_workspace.source_hashes()
                target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
                self.assertEqual(before, build_workspace.source_hashes())

    def test_stale_website_build_cannot_be_relabelled_as_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = root / "website/dist/workspace/index.html"
            source = root / "website/src/workspace/main.ts"
            entry.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            entry.write_text("<html></html>", encoding="utf-8")
            source.write_text("export const revised = true;", encoding="utf-8")
            stamp = entry.stat().st_mtime + 10
            os.utime(source, (stamp, stamp))
            with patch.object(build_workspace, "ROOT", root), self.assertRaisesRegex(ValueError, "newer than the build"):
                build_workspace.bundle(root / "website/dist", root / "tools/ui")

    def test_missing_or_external_entry_is_not_bundled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "dist"
            source.mkdir()
            with patch.object(build_workspace, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "Missing or unsafe"):
                    build_workspace.bundle(source, root / "tools/ui")

    def test_root_alias_is_canonicalized_before_output_confinement(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "canonical-root"
            source = root / "website/dist"
            (source / "workspace").mkdir(parents=True)
            (source / build_workspace.ENTRY).write_text("<html>alias fixture</html>", encoding="utf-8")
            alias = base / "root-alias"
            try:
                alias.symlink_to(root, target_is_directory=True)
            except OSError:
                self.skipTest("Directory alias creation is unavailable; Windows 8.3 coverage is separate")
            with patch.object(build_workspace, "ROOT", alias):
                result = build_workspace.bundle(alias / "website/dist", alias / "tools/ui")
                self.assertTrue(result["ready"])
                self.assertEqual(result["files"], 1)
                self.assertTrue(build_workspace.verify(alias / "tools/ui")["ready"])
                with self.assertRaisesRegex(ValueError, "inside tools"):
                    build_workspace.bundle(alias / "website/dist", alias / "tools-sibling/ui")
                self.assertFalse((root / "tools-sibling/ui").exists())

    @unittest.skipUnless(os.name == "nt", "Native Windows 8.3 TEMP aliases")
    def test_native_short_temp_alias_resolves_to_the_same_tools_root(self):
        import ctypes
        from ctypes import wintypes
        with tempfile.TemporaryDirectory(prefix="workspace-asset-long-name-") as temporary:
            root = Path(temporary)
            source = root / "website/dist"
            (source / "workspace").mkdir(parents=True)
            (source / build_workspace.ENTRY).write_text("<html>Windows short path fixture</html>", encoding="utf-8")
            short_name = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
            short_name.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
            short_name.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            count = short_name(str(root), buffer, len(buffer))
            if not count:
                self.skipTest("Filesystem does not provide an 8.3 alias")
            self.assertLess(count, len(buffer))
            alias = Path(buffer.value)
            if str(alias).casefold() == str(alias.resolve()).casefold():
                self.skipTest("Filesystem returned no distinct 8.3 TEMP alias")
            self.assertEqual(alias.resolve(), root.resolve())
            with patch.object(build_workspace, "ROOT", alias):
                result = build_workspace.bundle(alias / "website/dist", alias / "tools/ui")
                self.assertTrue(result["ready"])
                self.assertEqual(result["files"], 1)
                self.assertTrue(build_workspace.verify(alias / "tools/ui")["ready"])
                with self.assertRaisesRegex(ValueError, "inside tools"):
                    build_workspace.bundle(alias / "website/dist", alias / "tools-sibling/ui")
                self.assertFalse((root / "tools-sibling/ui").exists())

    def test_output_outside_canonical_tools_never_gets_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "website/dist"
            (source / "workspace").mkdir(parents=True)
            (source / build_workspace.ENTRY).write_text("<html>confined fixture</html>", encoding="utf-8")
            with patch.object(build_workspace, "ROOT", root):
                for output in (root / "tools-sibling/ui", root.parent / (root.name + "-outside/ui"), root / "tools/../outside/ui"):
                    with self.subTest(output=output), self.assertRaisesRegex(ValueError, "inside tools"):
                        build_workspace.bundle(source, output)
                    self.assertFalse(output.exists())


class WorkspaceArtifactAccessTests(unittest.TestCase):
    """Display metadata may degrade; evidence, path and delivery checks stay strict."""

    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="beta7-asset-access-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temporary.name), "synthetic", "Synthetic artifact access", "product"))
        self.blocked_ref, self.healthy_ref = "reports/synthetic-review.md", "reports/synthetic-healthy.md"
        self.blocked, self.healthy = self.root / self.blocked_ref, self.root / self.healthy_ref
        self.blocked.parent.mkdir(parents=True, exist_ok=True)
        self.blocked.write_text("Synthetic review version one.\n", encoding="utf-8")
        self.healthy.write_text("Synthetic healthy artifact.\n", encoding="utf-8")
        lifecycle.apply_patch(self.root, {"expected_revision": workspace.revision(self.root),
            "tasks": [{"role": "backend", "next_action": "Prepare synthetic report"}]})
        workspace.configure(self.root, {"expected_revision": workspace.revision(self.root), "tasks": [{
            "id": "TASK-001", "stage": "implementation", "execution": {
                "instruction": "Prepare synthetic reports", "allowed_paths": ["reports"],
                "expected_outputs": [self.blocked_ref, self.healthy_ref], "verification_commands": []}}]})
        native.focus(self.root, "TASK-001", "backend", "codex", "synthetic-session")
        self.observe("initial-tool", "tool_started")
        self.revision = workspace.revision(self.root)
        self.original_hash = workspace.file_hash(self.root, self.blocked_ref)
        self.healthy_hash = workspace.file_hash(self.root, self.healthy_ref)

    def observe(self, identifier, kind):
        return native.ingest(self.root, "codex", {"event_id": identifier, "kind": kind,
            "session_id": "synthetic-session", "source": "host_hook", "tool_name": "exec_command"})

    @contextlib.contextmanager
    def deny(self, operation="open", exception=None):
        failure = exception or PermissionError(errno.EACCES, "PRIVATE synthetic denial must never appear in metadata", str(self.blocked))
        original = getattr(Path, operation)
        def denied(path, *args, **kwargs):
            # lstat(follow_symlinks=False) is the confinement/reparse guard,
            # not display metadata; its failures must remain strict.
            if path == self.blocked and (operation != "stat" or kwargs.get("follow_symlinks") is not False):
                raise failure
            return original(path, *args, **kwargs)
        with patch.object(Path, operation, denied):
            yield failure

    def artifact(self, view, relative):
        return next(item for item in view["artefacts"] if item["path"] == relative)

    def assert_unavailable(self, item):
        self.assertEqual(item["access_status"], "unavailable")
        self.assertEqual(item["access_message"], "파일에 접근할 수 없어 현재 버전을 확인하지 못했습니다.")
        self.assertIsNone(item["sha256"])
        self.assertIsNone(item["size_bytes"])
        self.assertEqual(item["preview_kind"], "none")
        for field in ("previewable", "downloadable", "download_allowed"):
            self.assertIs(item[field], False)
        self.assertNotIn("PRIVATE", json.dumps(item))
        self.assertNotIn(str(self.root), json.dumps(item))

    def test_open_denial_isolated_from_other_artifacts_and_live_worker_progress(self):
        before_seq = native.read(self.root)["seq"]
        with self.deny():
            first = workspace.snapshot(self.root)
            self.assert_unavailable(self.artifact(first, self.blocked_ref))
            healthy = self.artifact(first, self.healthy_ref)
            self.assertEqual(healthy["sha256"], self.healthy_hash)
            self.assertTrue(healthy["download_allowed"])
            self.observe("continued-tool", "tool_started")
            second = workspace.snapshot(self.root)
            task = next(task for task in second["tasks"] if task["id"] == "TASK-001")
            self.assertEqual(task["native_agent_id"], "synthetic-session")
            self.assertEqual(task["execution_state"], "running")
            self.assertEqual(native.read(self.root)["seq"], before_seq + 1)
            self.assertTrue(any(event["id"] == "continued-tool" for event in native.read(self.root)["events"]))
            self.assertEqual(second["summary_counts"]["completed"], 0)
        self.assertEqual(workspace.revision(self.root), self.revision)
        self.assertFalse((self.root / "state/pending.json").exists())

    def test_stat_denial_marks_only_that_artifact_unavailable(self):
        with self.deny("stat"):
            view = workspace.snapshot(self.root)
            self.assert_unavailable(self.artifact(view, self.blocked_ref))
            self.assertEqual(self.artifact(view, self.healthy_ref)["sha256"], self.healthy_hash)
        self.assertEqual(workspace.revision(self.root), self.revision)

    def test_access_restoration_rehashes_external_edit_without_revision_change(self):
        with self.deny():
            self.assert_unavailable(self.artifact(workspace.snapshot(self.root), self.blocked_ref))
        self.blocked.write_text("Synthetic review changed outside the engine after access was restored.\n", encoding="utf-8")
        item = self.artifact(workspace.snapshot(self.root), self.blocked_ref)
        expected = hashlib.sha256(self.blocked.read_bytes()).hexdigest()
        self.assertNotEqual(expected, self.original_hash)
        self.assertEqual(item["sha256"], expected)
        self.assertEqual(item["size_bytes"], self.blocked.stat().st_size)
        self.assertEqual(item["preview_kind"], "text")
        self.assertTrue(item["download_allowed"])
        self.assertNotEqual(item.get("access_status"), "unavailable")
        self.assertEqual(workspace.revision(self.root), self.revision)

    def test_unavailable_file_never_bypasses_preview_or_download_checks(self):
        with self.deny():
            self.assert_unavailable(self.artifact(workspace.snapshot(self.root), self.blocked_ref))
            with self.assertRaises(PermissionError):
                workspace.diff(self.root, self.blocked_ref)
            with self.assertRaises(artefact_delivery.DeliveryError) as caught:
                artefact_delivery.get(self.root, "file:" + self.blocked_ref, self.original_hash)
            self.assertIn(caught.exception.status, {404, 409})
            delivered = artefact_delivery.get(self.root, "file:" + self.healthy_ref, self.healthy_hash)
            self.assertEqual(delivered["body"], self.healthy.read_bytes())

    def test_unreadable_output_cannot_create_a_completion_receipt(self):
        workspace.configure(self.root, {"expected_revision": self.revision, "policy": {"auto_run": True}})
        base = {"agent_id": "synthetic-host", "provider": "codex", "role": "backend"}
        claimed = workspace.host_event(self.root, {**base, "expected_revision": workspace.revision(self.root),
            "kind": "claim", "task_id": "TASK-001"})
        command = claimed["command"]["id"]
        workspace.host_event(self.root, {**base, "expected_revision": workspace.revision(self.root),
            "kind": "started", "command_id": command})
        revision = workspace.revision(self.root)
        csv_before = (self.root / "registers/tasks.csv").read_bytes()
        store_before = (self.root / workspace.STORE).read_bytes()
        with self.deny() as failure:
            self.assert_unavailable(self.artifact(workspace.snapshot(self.root), self.blocked_ref))
            with self.assertRaises(PermissionError) as caught:
                workspace.file_hash(self.root, self.blocked_ref)
            self.assertIs(caught.exception, failure)
            with self.assertRaises(PermissionError):
                workspace.host_event(self.root, {**base, "expected_revision": revision,
                    "kind": "completed", "command_id": command, "evidence_refs": [self.blocked_ref, self.healthy_ref],
                    "verified_outputs": {self.blocked_ref: {"sha256": self.original_hash, "bytes": self.blocked.stat().st_size},
                                         self.healthy_ref: {"sha256": self.healthy_hash, "bytes": self.healthy.stat().st_size}}})
        self.assertEqual(workspace.revision(self.root), revision)
        self.assertEqual((self.root / "registers/tasks.csv").read_bytes(), csv_before)
        self.assertEqual((self.root / workspace.STORE).read_bytes(), store_before)
        self.assertEqual(next(c for c in workspace.read(self.root)["queue"] if c["id"] == command)["status"], "running")
        self.assertFalse((self.root / "state/pending.json").exists())

    def test_unreadable_gate_version_cannot_be_approved(self):
        workspace.configure(self.root, {"expected_revision": self.revision, "gates": [{
            "id": "SYNTHETIC-GATE", "task_id": "TASK-001", "paths": [self.blocked_ref]}]})
        view = workspace.snapshot(self.root)
        gate = view["gates"][0]
        revision = workspace.revision(self.root)
        with self.deny():
            with self.assertRaises(PermissionError):
                workspace.version_hashes(self.root, gate)
            with self.assertRaises(PermissionError):
                workspace.decide(self.root, {"action_id": "synthetic-blocked-approval", "expected_revision": revision,
                    "gate_id": gate["id"], "decision": "approve", "actor": "Synthetic human fixture",
                    "feedback": "Synthetic test only", "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]})
        self.assertEqual(workspace.revision(self.root), revision)
        self.assertEqual(workspace.read(self.root)["decisions"], [])

    def test_other_artifact_read_errors_still_propagate(self):
        for failure in (OSError(errno.EIO, "Synthetic I/O failure"), ValueError("Synthetic malformed artifact")):
            with self.subTest(error=type(failure).__name__), self.deny(exception=failure):
                with self.assertRaises(type(failure)) as caught:
                    workspace.snapshot(self.root)
                self.assertIs(caught.exception, failure)
        self.assertEqual(workspace.revision(self.root), self.revision)

    def test_control_metadata_permission_errors_are_not_hidden(self):
        target = self.root / workspace.STORE
        original = Path.open
        failure = PermissionError(errno.EACCES, "Synthetic control metadata denial", str(target))
        def denied(path, *args, **kwargs):
            if path == target:
                raise failure
            return original(path, *args, **kwargs)
        with patch.object(Path, "open", denied):
            with self.assertRaises(PermissionError) as caught:
                workspace.snapshot(self.root)
        self.assertIs(caught.exception, failure)
        self.assertFalse((self.root / "state/write.lock").exists())

    def test_artifact_path_validation_stays_outside_the_permission_fallback(self):
        for relative in ("../outside.md", "state/workspace.json", ".env", ".codex/config.toml"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                workspace.valid_artifact(self.root, relative)
        original = workspace.valid_artifact
        for failure in (PermissionError(errno.EACCES, "Synthetic path validation denial"), ValueError("Synthetic path rejection")):
            def invalid(root, relative):
                if relative == self.blocked_ref:
                    raise failure
                return original(root, relative)
            with self.subTest(error=type(failure).__name__), patch.object(workspace, "valid_artifact", invalid):
                with self.assertRaises(type(failure)) as caught:
                    workspace.snapshot(self.root)
                self.assertIs(caught.exception, failure)

    @unittest.skipUnless(os.name == "nt", "requires a real exclusive Windows file handle")
    def test_real_exclusive_file_reader_keeps_snapshot_http_200_and_progress_live(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        server = workspace_server.make_server(self.root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(route):
            req = Request(server.origin + route, headers={"Authorization": "Bearer " + server.human_token})
            try:
                with urlopen(req, timeout=5) as response:
                    return response.status, response.read()
            except HTTPError as error:
                return error.code, error.read()
        handle = kernel.CreateFileW(str(self.blocked), 0x80000000, 0, None, 3, 0x80, None)
        try:
            self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
            with self.assertRaises(PermissionError):
                self.blocked.read_bytes()
            for index in range(3):
                self.observe("http-live-" + str(index), "tool_started")
                status, raw = request("/api/snapshot")
                self.assertEqual(status, 200)
                view = json.loads(raw)
                self.assert_unavailable(self.artifact(view, self.blocked_ref))
                self.assertEqual(self.artifact(view, self.healthy_ref)["sha256"], self.healthy_hash)
                task = next(task for task in view["tasks"] if task["id"] == "TASK-001")
                self.assertEqual(task["native_agent_id"], "synthetic-session")
                self.assertEqual(task["execution_state"], "running")
            status, raw = request("/api/artefact?" + urlencode({"id": "file:" + self.blocked_ref, "sha256": self.original_hash}))
            self.assertIn(status, {404, 409})
            status, raw = request("/api/artefact?" + urlencode({"id": "file:" + self.healthy_ref, "sha256": self.healthy_hash}))
            self.assertEqual(status, 200)
            self.assertEqual(raw, self.healthy.read_bytes())
        finally:
            if handle != wintypes.HANDLE(-1).value:
                kernel.CloseHandle(handle)
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.artifact(workspace.snapshot(self.root), self.blocked_ref)["sha256"], self.original_hash)
        self.assertEqual(workspace.revision(self.root), self.revision)
