"""Synthetic project scenarios; these approvals are never customer approvals."""
import base64
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import intake, lifecycle as engine, starter, storage
from proposal_fixtures import complete_package


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        test_root = starter.ROOT / ".test-output"
        test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.parent = Path(self.temp.name)
        self.project = starter.init_project(self.parent, "scenario", "가상 검증", "si")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, text="가상 테스트 근거"):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def prepare(self):
        deliveries = starter.read_table(self.project, "deliverables")
        for row in deliveries:
            self.write(row["path"], "# 가상 산출물\n\nREQ-001의 확인 가능한 검증용 내용")
        self.write("docs/design.md")
        self.write("src/example.py", "value = 1\n")
        self.write("tests/result.md", "가상 회귀 결과: 통과")
        complete_package(self.project)
        return engine.apply_patch(self.project, {
            "project": {"project_manager": "가상 PM", "client_or_product_owner": "가상 담당자",
                        "objective": "신청 조회", "scope_baseline": "REQ-001"},
            "requirements": [{"source": "가상 입력 1절", "description": "신청 조회", "priority": "must",
                              "acceptance_criteria": "자신의 신청만 조회", "status": "baselined"}],
            "deliverables": [{"deliverable_id": row["deliverable_id"], "owner": "가상 담당자", "requirement_ids": "REQ-001"} for row in deliveries],
            "handoff": "# 인수인계\n\n가상 시나리오 실행 중. 다음 단계 자료를 확인한다."})

    def approve(self, stage=None):
        stage = stage or starter.load_project(self.project)[1]["current_stage"]
        evidence = f"reviews/{stage}.md"
        self.write(evidence, f"가상 테스트에서만 사용하는 {stage} 결정 근거")
        return engine.record_review(self.project, "가상 검토자", evidence, "가상 검증 전용 승인", stage=stage)

    def test_apply_assigns_ids_preserves_custom_columns_and_conflicts(self):
        task_file = self.project / "registers/tasks.csv"
        fields = list(reversed(starter.HEADERS["tasks"].split(","))) + ["team"]
        task_file.write_text(",".join(fields) + "\n", encoding="utf-8")
        result = self.prepare()
        self.assertEqual(result["assigned"]["requirements"], ["REQ-001"])
        update = engine.apply_patch(self.project, {"expected_revision": result["revision"],
            "tasks": [{"requirement_ids": "REQ-001", "next_action": "실제 분석", "team": "A"}]})
        self.assertEqual(update["assigned"]["tasks"], ["TASK-001"])
        self.assertEqual(starter.read_table(self.project, "tasks")[0]["team"], "A")
        self.assertEqual(task_file.read_text(encoding="utf-8").splitlines()[0], ",".join(fields))
        with self.assertRaisesRegex(ValueError, "변경되었습니다"):
            engine.apply_patch(self.project, {"expected_revision": result["revision"], "handoff": "stale"})

    def test_invalid_change_is_rejected_without_partial_write(self):
        original = (self.project / "project.json").read_bytes()
        for payload in ({"project": {"current_stage": "release"}},
                        {"deliverables": [{"deliverable_id": "DEL-001", "status": "approved"}]},
                        {"tasks": [{"status": "done"}]},
                        {"requirements": [{"deliverable_ids": "DEL-999"}]},
                        {"deliverables": [{"deliverable_id": "DEL-001", "path": "../private.txt"}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                engine.apply_patch(self.project, payload)
            self.assertEqual((self.project / "project.json").read_bytes(), original)
        self.assertFalse((self.project / "state/events.jsonl").exists())

    def test_approval_stales_on_document_evidence_scope_or_input_change(self):
        self.prepare()
        for relative in ("docs/01-discovery.md", "reviews/discovery.md"):
            self.approve()
            self.assertEqual(engine.stale_reviews(self.project, "discovery", strict=True), [])
            self.write(relative, "변경된 내용")
            self.assertTrue(engine.stale_reviews(self.project, "discovery", strict=True))
        self.approve()
        engine.apply_patch(self.project, {"project": {"scope_baseline": "범위 변경"}})
        self.assertTrue(engine.stale_reviews(self.project, "discovery", strict=True))
        self.approve()
        source = self.parent / "additional.txt"
        source.write_text("신규 입력", encoding="utf-8")
        engine.ingest(self.project, source)
        with self.assertRaises(engine.GateError):
            engine.transition(self.project)

    def test_status_progression_does_not_invalidate_scope_review(self):
        self.prepare()
        self.approve()
        engine.apply_patch(self.project, {"requirements": [{"requirement_id": "REQ-001", "status": "implemented", "implementation_refs": "src/example.py"}]})
        self.assertEqual(engine.stale_reviews(self.project, "discovery", strict=True), [])

    def test_full_lifecycle_and_rewind(self):
        self.prepare()
        stages = list(starter.STAGES)
        for stage in stages:
            if stage == "planning":
                engine.apply_patch(self.project, {"requirements": [{"requirement_id": "REQ-001", "design_refs": "docs/design.md"}]})
            if stage == "implementation":
                engine.apply_patch(self.project, {"requirements": [{"requirement_id": "REQ-001", "status": "implemented", "implementation_refs": "src/example.py"}]})
            if stage == "verification":
                engine.apply_patch(self.project, {"requirements": [{"requirement_id": "REQ-001", "status": "verified", "test_refs": "tests/result.md"}]})
            if stage == "handover":
                engine.apply_patch(self.project, {"requirements": [{"requirement_id": "REQ-001", "status": "accepted", "deliverable_ids": "DEL-012"}]})
            self.approve()
            self.assertEqual(starter.check_project(self.project)[2], [], stage)
            if stage != "maintenance":
                result = engine.transition(self.project)
                self.assertEqual(result["to"], stages[stages.index(stage) + 1])
        self.assertFalse(engine.inspect_project(self.project)["can_advance"])
        with self.assertRaisesRegex(ValueError, "지속 운영"):
            engine.transition(self.project)
        self.write("reviews/change.md", "가상 변경 요청: 구현 단계 재검토")
        engine.transition(self.project, target="implementation", backwards=True, reason="가상 변경", evidence="reviews/change.md")
        self.assertEqual(starter.load_project(self.project)[1]["current_stage"], "implementation")
        self.assertEqual(next(row for row in starter.read_table(self.project, "deliverables") if row["deliverable_id"] == "DEL-009")["status"], "in_review")
        self.assertTrue(engine.stale_reviews(self.project, "implementation", strict=True))

    def test_skips_and_unfinished_handoff_are_blocked(self):
        self.prepare()
        self.approve()
        with self.assertRaisesRegex(ValueError, "건너뛸"):
            engine.transition(self.project, target="release")
        self.write("HANDOFF.md", "TODO")
        with self.assertRaises(engine.GateError):
            engine.transition(self.project)

    def test_rejected_review_and_legacy_review_cannot_advance(self):
        self.prepare()
        review = self.approve()
        (self.project / f"state/reviews/{review['review_id']}.json").unlink()
        with self.assertRaises(engine.GateError):
            engine.transition(self.project)
        engine.record_review(self.project, "가상 검토자", "reviews/discovery.md", "가상 반려", decision="rejected")
        with self.assertRaises(engine.GateError):
            engine.transition(self.project)

    def test_init_failure_removes_new_project_only(self):
        sentinel = self.project / "keep.txt"
        sentinel.write_text("보존", encoding="utf-8")
        with patch.object(starter.shutil, "copytree", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                starter.init_project(self.parent, "failed", "실패", "si")
        self.assertFalse((self.parent / "failed").exists())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "보존")

    def test_intake_preserves_versions_and_deduplicates(self):
        source = self.parent / "제안요청서.txt"
        source.write_bytes("회원 신청".encode("cp949"))
        first = engine.ingest(self.project, source)
        self.assertEqual(first["input"]["extraction"]["status"], "extracted")
        self.assertTrue(engine.ingest(self.project, source)["unchanged"])
        source.write_text("변경된 요구사항", encoding="utf-8")
        second = engine.ingest(self.project, source)
        self.assertEqual(second["input"]["input_id"], "INPUT-002")
        self.assertEqual((self.project / first["input"]["path"]).read_bytes(), "회원 신청".encode("cp949"))

    def test_docx_hwpx_extraction_and_unsafe_xml_fallback(self):
        for extension, name in (("docx", "word/document.xml"), ("hwpx", "Contents/section0.xml")):
            source = self.parent / f"rfp.{extension}"
            with ZipFile(source, "w") as archive:
                archive.writestr(name, '<root><p><t>신청 관리</t></p></root>')
            record, text = intake.make_record(source, "INPUT-001", "inputs/rfp", starter.now())
            self.assertEqual(text, "신청 관리")
            self.assertEqual(record["extraction"]["status"], "extracted")
        with ZipFile(source, "w") as archive:
            archive.writestr(name, '<!DOCTYPE root [<!ENTITY x "unsafe">]><root><p><t>&x;</t></p></root>')
        record, text = intake.make_record(source, "INPUT-001", "inputs/rfp", starter.now())
        self.assertIsNone(text)
        self.assertEqual(record["extraction"]["status"], "needs_reader")

    def test_cli_json_and_customer_cwd(self):
        result = subprocess.run([sys.executable, str(starter.ROOT / "tools/starter.py"), "inspect", str(self.project)],
                                cwd=self.parent, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["can_advance"])
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            code = starter.main(["advance", str(self.project)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["kind"], "gate_blocked")

    def test_malformed_project_type_returns_structured_error(self):
        path = self.project / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["current_stage"] = []
        path.write_text(json.dumps(data), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(starter.main(["inspect", str(self.project)]), 2)
        self.assertEqual(json.loads(output.getvalue())["kind"], "invalid_request")

    def test_serialized_writes_and_rollback(self):
        self.write("docs/a.txt", "before")
        with storage.lock(self.project):
            with self.assertRaisesRegex(ValueError, "잠금"):
                engine.apply_patch(self.project, {"handoff": "동시 변경"})
            original = storage.atomic_write
            def fail_once(path, value):
                if Path(path).name == "fail.txt":
                    raise OSError("simulated disk failure")
                return original(path, value)
            with patch.object(storage, "atomic_write", side_effect=fail_once), self.assertRaises(OSError):
                storage.commit(self.project, {"docs/a.txt": b"after", "docs/fail.txt": b"new"})
        self.assertEqual((self.project / "docs/a.txt").read_text(encoding="utf-8"), "before")
        self.assertFalse((self.project / "state/pending.json").exists())

    def pending(self):
        self.write("docs/a.txt", "after")
        entry = {"path": "docs/a.txt", "before": base64.b64encode(b"before").decode(), "after": storage.digest(b"after")}
        self.write("state/pending.json", json.dumps({"schema_version": 1, "entries": [entry]}))

    def test_crash_recovery_and_unrelated_edit_protection(self):
        self.pending()
        with self.assertRaisesRegex(ValueError, "미완료"):
            engine.apply_patch(self.project, {"handoff": "blocked"})
        self.assertTrue(storage.recover(self.project)["recovered"])
        self.assertEqual((self.project / "docs/a.txt").read_bytes(), b"before")
        self.pending()
        self.write("docs/a.txt", "human edit")
        with self.assertRaisesRegex(ValueError, "복구 중단"):
            storage.recover(self.project)
        self.assertEqual((self.project / "docs/a.txt").read_text(encoding="utf-8"), "human edit")

    def test_live_lock_is_not_removed_and_dead_lock_can_recover(self):
        self.pending()
        self.write("state/write.lock", json.dumps({"pid": os.getpid()}))
        with self.assertRaisesRegex(ValueError, "살아 있어"):
            storage.recover(self.project, abandoned=True)
        with patch.object(storage, "process_alive", return_value=False):
            self.assertTrue(storage.recover(self.project, abandoned=True)["recovered"])
        self.assertFalse((self.project / "state/write.lock").exists())


if __name__ == "__main__":
    unittest.main()
