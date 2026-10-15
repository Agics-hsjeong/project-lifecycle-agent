import csv
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from proposal_fixtures import complete_package
from tools import lifecycle, proposal, starter


class ProposalTests(unittest.TestCase):
    def setUp(self):
        base = starter.ROOT / ".test-output"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.project = starter.init_project(Path(self.temp.name), "bid", "가상 제안", "si")
        self.profile = complete_package(self.project)
        deliveries = starter.read_table(self.project, "deliverables")
        for row in deliveries:
            (self.project / row["path"]).write_text("가상 테스트 전용 산출물", encoding="utf-8")
        lifecycle.apply_patch(self.project, {
            "project": {"objective": "가상 목표", "scope_baseline": "가상 범위", "project_manager": "가상 PM", "client_or_product_owner": "가상 책임자"},
            "requirements": [{"source": "가상 RFP §1", "description": "가상 요구", "priority": "must", "acceptance_criteria": "가상 기준", "status": "baselined"}],
            "deliverables": [{"deliverable_id": row["deliverable_id"], "owner": "가상 담당자", "status": "in_review"} for row in deliveries],
            "handoff": "# 가상 테스트 인수인계\n\n제안 검토 준비"})
        data = starter.load_project(self.project)[1]
        data["current_stage"] = "proposal"
        self.write_project(data)
        (self.project / "review.md").write_text("가상 승인 결정", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_project(self, data):
        (self.project / "project.json").write_text(json.dumps(data), encoding="utf-8")

    def issues(self):
        root, data, tables = lifecycle.bundle(self.project)
        return proposal.check(root, data, tables)

    def apply(self, **changes):
        return lifecycle.apply_patch(self.project, {"proposal": changes})

    def approve(self):
        return lifecycle.record_review(self.project, "가상 검토자", "review.md", "가상 결정")

    def test_new_project_requires_separate_proposal_artifacts(self):
        data = starter.load_project(self.project)[1]
        self.assertEqual(len(starter.required_deliverables(data, "proposal")), 6)
        self.assertEqual(self.issues(), [])
        self.approve()
        self.assertEqual(starter.check_project(self.project)[2], [])
        self.assertEqual(len(lifecycle.inspect_project(self.project)["deliverables"]), 6)

    def test_coverage_and_reciprocal_slide_links_block_review(self):
        criterion = dict(self.profile["criteria"][0], proposal_section="", slide_ids=["SLIDE-999"])
        self.apply(criteria=[criterion])
        issues = self.issues()
        self.assertTrue(any("대응 절" in issue for issue in issues))
        self.assertTrue(any("양방향" in issue for issue in issues))
        with self.assertRaises(lifecycle.GateError):
            self.approve()
        self.apply(criteria=[dict(self.profile["criteria"][0], status="not_applicable", exclusion_reason="가상 제외")])
        self.assertTrue(any("모든 평가항목" in issue for issue in self.issues()))

    def test_time_limit_and_missing_speaker_notes_are_checked(self):
        slide = dict(self.profile["slides"][0], seconds=601, speaker_notes="")
        self.apply(slides=[slide])
        self.assertTrue(any("제한 시간" in issue for issue in self.issues()))
        self.assertTrue(any("speaker_notes" in issue for issue in self.issues()))

    def test_missing_and_fake_exports_do_not_pass(self):
        (self.project / "exports/presentation.pptx").write_text("Markdown slides", encoding="utf-8")
        self.assertTrue(any("올바른 문서 패키지" in issue for issue in self.issues()))
        (self.project / "exports/qualitative.docx").unlink()
        self.assertTrue(any("qualitative" in issue for issue in self.issues()))

    def test_pptx_slide_count_matches_plan(self):
        with ZipFile(self.project / "exports/presentation.pptx", "a") as archive:
            archive.writestr("ppt/slides/slide2.xml", "<slide/>")
        self.assertTrue(any("슬라이드 수" in issue for issue in self.issues()))

    def test_approved_exports_and_profile_changes_stale_review(self):
        self.approve()
        self.assertEqual(lifecycle.stale_reviews(self.project, "proposal", strict=True), [])
        with ZipFile(self.project / "exports/presentation.pptx", "a") as archive:
            archive.writestr("docProps/custom.xml", "<changed/>")
        self.assertTrue(lifecycle.stale_reviews(self.project, "proposal", strict=True))
        self.approve()
        self.apply(presentation_minutes=12)
        self.assertTrue(lifecycle.stale_reviews(self.project, "proposal", strict=True))

    def test_no_presentation_requires_explicit_exclusion(self):
        self.apply(presentation_required=False, presentation_file="", slides=[],
                   criteria=[dict(self.profile["criteria"][0], slide_ids=[])])
        self.assertTrue(any("제외" in issue for issue in self.issues()))
        lifecycle.apply_patch(self.project, {"proposal": {"presentation_exclusion_reason": "가상 원문: 발표 없음"},
            "deliverables": [{"deliverable_id": identifier, "status": "not_applicable", "exclusion_reason": "가상 원문: 발표 없음"}
                             for identifier in ("DEL-016", "DEL-017")]})
        self.assertEqual(self.issues(), [])
        self.approve()

    def make_legacy(self):
        data = starter.load_project(self.project)[1]
        data.pop("proposal_package")
        self.write_project(data)
        rows = [row for row in starter.read_table(self.project, "deliverables") if row["deliverable_id"] not in proposal.EXTRA_IDS]
        with (self.project / "registers/deliverables.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=starter.HEADERS["deliverables"].split(","))
            writer.writeheader()
            writer.writerows(rows)

    def test_legacy_opt_in_preserves_customer_files_and_invalidates_old_proposal(self):
        self.make_legacy()
        self.assertEqual(len(starter.required_deliverables(starter.load_project(self.project)[1], "proposal")), 2)
        self.approve()
        original = (self.project / "docs/16-proposal-presentation.md").read_bytes()
        original_reviews = (self.project / "registers/reviews.csv").read_bytes()
        result = lifecycle.setup_proposal(self.project)
        self.assertFalse(result["unchanged"])
        self.assertEqual((self.project / "docs/16-proposal-presentation.md").read_bytes(), original)
        self.assertEqual((self.project / "registers/reviews.csv").read_bytes(), original_reviews)
        self.assertTrue(lifecycle.stale_reviews(self.project, "proposal", strict=True))
        self.assertTrue(lifecycle.setup_proposal(self.project)["unchanged"])

    def test_legacy_id_collision_never_overwrites_customer_artifact(self):
        self.make_legacy()
        lifecycle.apply_patch(self.project, {"deliverables": [{"deliverable_id": "DEL-015", "stage": "proposal", "path": "docs/02-proposal.md", "title": "고객 자체 산출물"}]})
        before = (self.project / "registers/deliverables.csv").read_bytes()
        with self.assertRaisesRegex(ValueError, "충돌"):
            lifecycle.setup_proposal(self.project)
        self.assertEqual((self.project / "registers/deliverables.csv").read_bytes(), before)
        self.assertNotIn("proposal_package", starter.load_project(self.project)[1])

    def test_invalid_profile_change_is_atomic(self):
        before = (self.project / proposal.PROFILE).read_bytes()
        for change in ({"presentation_minutes": True}, {"presentation_minutes": float("nan")}, {"unknown": "bad"},
                       {"criteria": [self.profile["criteria"][0]] * 2}, {"qualitative_file": "../outside.docx"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.apply(**change)
            self.assertEqual((self.project / proposal.PROFILE).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
