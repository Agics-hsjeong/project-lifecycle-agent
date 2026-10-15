import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("starter", Path(__file__).resolve().parents[1] / "tools" / "starter.py")
starter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(starter)


class StarterTests(unittest.TestCase):
    def setUp(self):
        self.test_root = (starter.ROOT / ".test-output").resolve()
        self.test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_root)
        self.parent = Path(self.temp.name)
        self.project = starter.init_project(self.parent, "test-project", "테스트 사업", "si")

    def tearDown(self):
        self.assertTrue(self.parent.resolve().is_relative_to(self.test_root))
        self.temp.cleanup()

    def write_table(self, name, rows):
        with (self.project / "registers" / (name + ".csv")).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=starter.HEADERS[name].split(","))
            writer.writeheader()
            writer.writerows(rows)

    def prepare(self, stage="discovery"):
        path = self.project / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(current_stage=stage, project_manager="테스트 PM", client_or_product_owner="테스트 담당자",
                    objective="신청 상태 조회", scope_baseline="REQ-001")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.write_table("requirements", [{
            "requirement_id": "REQ-001", "source": "가상 테스트 입력 1절", "description": "상태 조회",
            "acceptance_criteria": "자신의 신청만 조회", "priority": "must", "status": "accepted",
            "design_refs": "docs/evidence.md", "implementation_refs": "docs/evidence.md",
            "test_refs": "docs/evidence.md", "deliverable_ids": "DEL-012", "exclusion_reason": ""
        }])
        # Synthetic evidence is used only by tests, never shipped as project approval.
        (self.project / "docs" / "evidence.md").write_text("가상 테스트 전용 근거", encoding="utf-8")
        rows = starter.read_table(self.project, "deliverables")
        for row in rows:
            row.update(owner="테스트 담당자", status="approved", requirement_ids="REQ-001")
            (self.project / row["path"]).write_text("가상 테스트 전용 문서", encoding="utf-8")
        self.write_table("deliverables", rows)
        self.write_table("reviews", [{
            "review_id": "REV-001", "stage": stage, "decision": "approved", "reviewer": "테스트 검토자",
            "reviewed_at": "2026-10-15T10:00:00+09:00", "evidence_ref": "docs/evidence.md",
            "notes": "테스트에서만 사용하는 검토 기록"
        }])

    def issues(self, stage=None):
        return starter.check_project(self.project, stage)[2]

    def test_create_both_types_and_import(self):
        second = starter.init_project(self.parent, "test-product", "제품", "product")
        self.assertEqual(starter.load_project(second)[1]["business_type"], "product")
        self.assertIn("@AGENTS.md", (second / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(len(starter.read_table(second, "deliverables")), 14)

    def test_never_overwrite_existing_project(self):
        sentinel = self.project / "customer.txt"
        sentinel.write_text("보존", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            starter.init_project(self.parent, "test-project", "다른 이름", "si")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "보존")

    def test_invalid_slug_cannot_escape(self):
        for slug in ("../escape", "CON", "con", "a/b", "a\\b", ""):
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                starter.init_project(self.parent, slug, "사업", "si")

    def test_fresh_project_is_not_ready(self):
        issues = self.issues()
        self.assertTrue(any("요구사항" in issue for issue in issues))
        self.assertTrue(any("TODO" in issue for issue in issues))
        self.assertTrue(any("검토 기록 없음" in issue for issue in issues))

    def test_valid_records_pass_every_stage(self):
        for stage in starter.STAGES:
            with self.subTest(stage=stage):
                self.prepare(stage)
                self.assertEqual(self.issues(), [])

    def test_required_deliverable_row_cannot_be_removed(self):
        self.prepare()
        self.write_table("deliverables", [r for r in starter.read_table(self.project, "deliverables") if r["deliverable_id"] != "DEL-001"])
        self.assertTrue(any("필수 산출물 행 누락" in issue for issue in self.issues()))

    def test_latest_rejection_blocks_older_approval(self):
        self.prepare()
        reviews = starter.read_table(self.project, "reviews")
        reviews.append(dict(reviews[0], review_id="REV-002", decision="rejected", reviewed_at="2026-10-15T11:00:00+09:00"))
        self.write_table("reviews", list(reversed(reviews)))
        self.assertTrue(any("최신 검토" in issue for issue in self.issues()))

    def test_implementation_requires_real_local_references(self):
        self.prepare("implementation")
        requirements = starter.read_table(self.project, "requirements")
        requirements[0]["implementation_refs"] = "../outside.md"
        self.write_table("requirements", requirements)
        self.assertTrue(any("외부 경로" in issue for issue in self.issues()))
        requirements[0]["implementation_refs"] = "missing.py"
        self.write_table("requirements", requirements)
        self.assertTrue(any("실제 파일" in issue for issue in self.issues()))

    def test_verification_does_not_accept_implemented_only(self):
        self.prepare("verification")
        requirements = starter.read_table(self.project, "requirements")
        requirements[0]["status"] = "implemented"
        requirements[0]["test_refs"] = ""
        self.write_table("requirements", requirements)
        issues = self.issues()
        self.assertTrue(any("검증 완료" in issue for issue in issues))
        self.assertTrue(any("test_refs" in issue for issue in issues))

    def test_handover_checks_two_way_trace(self):
        self.prepare("handover")
        deliverables = starter.read_table(self.project, "deliverables")
        for row in deliverables:
            if row["deliverable_id"] == "DEL-012":
                row["requirement_ids"] = ""
        self.write_table("deliverables", deliverables)
        self.assertTrue(any("역방향" in issue for issue in self.issues()))

    def test_not_applicable_requires_reason(self):
        self.prepare("publishing")
        deliverables = starter.read_table(self.project, "deliverables")
        for row in deliverables:
            if row["deliverable_id"] == "DEL-008":
                row["status"] = "not_applicable"
        self.write_table("deliverables", deliverables)
        self.assertTrue(any("적용 제외 사유" in issue for issue in self.issues()))
        for row in deliverables:
            if row["deliverable_id"] == "DEL-008":
                row["exclusion_reason"] = "UI 없는 API 사업; 검토 기록 참조"
        self.write_table("deliverables", deliverables)
        self.assertEqual(self.issues(), [])

    def test_duplicate_and_unknown_ids_are_reported(self):
        self.prepare()
        requirements = starter.read_table(self.project, "requirements")
        requirements[0]["deliverable_ids"] = "DEL-999"
        self.write_table("requirements", requirements * 2)
        issues = self.issues()
        self.assertTrue(any("중복" in issue for issue in issues))
        self.assertTrue(any("없는 산출물" in issue for issue in issues))

    def test_invalid_csv_has_actionable_error(self):
        (self.project / "registers" / "tasks.csv").write_text("bad,header\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "헤더"):
            self.issues()

    def test_rfp_copy_preserves_original_and_registers_source(self):
        original = self.parent / "RFP.md"
        original.write_text("원본 요구사항", encoding="utf-8")
        project = starter.init_project(self.parent, "rfp-project", "RFP 사업", "si", original)
        self.assertEqual((project / "inputs/rfp/RFP.md").read_bytes(), original.read_bytes())
        self.assertEqual(original.read_text(encoding="utf-8"), "원본 요구사항")
        self.assertIn(str(original), (project / "inputs/README.md").read_text(encoding="utf-8"))

    def test_missing_rfp_does_not_create_partial_project(self):
        with self.assertRaisesRegex(ValueError, "원본 파일"):
            starter.init_project(self.parent, "missing-source", "실패 사업", "si", self.parent / "missing.pdf")
        self.assertFalse((self.parent / "missing-source").exists())

    def test_cli_uses_workspace_instead_of_plugin_install_folder(self):
        result = starter.main(["init", "--workspace", str(self.parent), "--name", "작업 공간 사업", "--slug", "workspace-project"])
        self.assertEqual(result, 0)
        self.assertTrue((self.parent / "projects/workspace-project/project.json").is_file())

    def test_default_workspace_is_current_working_folder(self):
        with patch.object(starter.Path, "cwd", return_value=self.parent):
            result = starter.main(["init", "--name", "기본 작업 공간", "--slug", "default-workspace"])
        self.assertEqual(result, 0)
        self.assertTrue((self.parent / "projects/default-workspace/project.json").is_file())


if __name__ == "__main__":
    unittest.main()
