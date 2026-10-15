"""Synthetic reference documents and images; not claims of visual fidelity."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from proposal_fixtures import complete_package
from tools import lifecycle, reference_templates as refs, starter
from tools.template_analysis import analyze

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=")


def docx(path, accent="123456", text="OLD CLIENT"):
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f'<w:document xmlns:w="urn:w"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p><w:tbl><w:tblGrid><w:gridCol w:w="4000"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>Claim evidence</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440"/></w:sectPr></w:body></w:document>')
        archive.writestr("word/styles.xml", f'<w:styles xmlns:w="urn:w"><w:style w:styleId="Heading1"><w:name w:val="Heading 1"/><w:rPr><w:rFonts w:eastAsia="Sample Font"/><w:color w:val="{accent}"/><w:sz w:val="32"/></w:rPr></w:style></w:styles>')


def pptx(path):
    with ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="urn:p" xmlns:r="urn:r"><p:sldIdLst><p:sldId id="257" r:id="rId2"/><p:sldId id="256" r:id="rId1"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')
        archive.writestr("ppt/_rels/presentation.xml.rels", '<Relationships><Relationship Id="rId1" Target="slides/slide1.xml"/><Relationship Id="rId2" Target="slides/slide2.xml"/><Relationship Id="external" TargetMode="External" Target="https://example.test/do-not-fetch"/></Relationships>')
        for number in (1, 2):
            archive.writestr(f"ppt/slides/slide{number}.xml", f'<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:sp><p:spPr><a:xfrm><a:off x="100" y="200"/><a:ext cx="300" cy="400"/></a:xfrm></p:spPr><a:p><a:r><a:rPr sz="2400"/><a:t>Slide {number}</a:t></a:r></a:p></p:sp></p:sld>')
        archive.writestr("ppt/theme/theme1.xml", '<a:theme xmlns:a="urn:a"><a:latin typeface="Sample Sans"/><a:srgbClr val="ABCDEF"/></a:theme>')


class ReferenceTemplateTests(unittest.TestCase):
    def setUp(self):
        root = starter.ROOT / ".test-output"
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.parent = Path(self.temp.name)
        self.project = starter.init_project(self.parent, "reference", "가상 스타일 검증", "si")

    def tearDown(self):
        self.temp.cleanup()

    def source(self, name="example.docx"):
        path = self.project / "templates/qualitative" / name
        docx(path)
        return path

    def profile(self, identifier):
        proof = self.project / "qa/reference.png"
        proof.parent.mkdir(exist_ok=True)
        proof.write_bytes(PNG)
        return {"template_id": identifier, "status": "analyzed", "style_notes": "가상 색상·서체 근거",
                "layout_rules": "가상 표지·본문·표 구성", "writing_rules": "가상 주장→근거 문체",
                "reusable_patterns": "가상 주장/증거 표", "replace_rules": "이전 고객명·실적·노트 교체",
                "coverage": "가상 표지·본문·표를 본 것으로 가정하는 엔진 검증", "visual_refs": ["qa/reference.png"]}

    def test_docx_anatomy_and_presentation_order(self):
        source = self.source()
        result = analyze(source)
        self.assertIn("Sample Font", result["fonts"])
        self.assertIn("123456", result["colors"])
        self.assertEqual(result["sections"][0]["pgSz"]["w"], "11906")
        self.assertEqual(result["tables"][0]["grid"], ["4000"])
        slides = self.project / "templates/presentation/deck.pptx"
        pptx(slides)
        result = analyze(slides)
        self.assertEqual(result["slide_count"], 2)
        self.assertEqual(result["slides"][0]["shapes"][0]["text"], "Slide 2")
        self.assertEqual(result["slides"][1]["shapes"][0]["text"], "Slide 1")
        self.assertEqual(result["external_links"][0]["target"], "https://example.test/do-not-fetch")

    def test_drop_folder_preservation_deduplication_and_versions(self):
        original = self.source()
        original_bytes = original.read_bytes()
        self.assertEqual(len(refs.overview(self.project)["pending"]), 1)
        first = refs.scan(self.project)
        identifier = first["imported"][0]["id"]
        preserved = self.project / first["imported"][0]["path"]
        self.assertEqual(preserved.read_bytes(), original_bytes)
        self.assertEqual(refs.scan(self.project)["unchanged"], [identifier])
        docx(original, accent="FEDCBA", text="NEW VERSION")
        second = refs.scan(self.project)
        self.assertEqual(second["imported"][0]["id"], "TPL-002")
        self.assertEqual(preserved.read_bytes(), original_bytes)
        self.assertEqual(second["selection"]["qualitative"], identifier)

    def test_multiple_styles_require_selection_and_kind_matching(self):
        self.source("a.docx")
        docx(self.project / "templates/qualitative/b.docx", accent="AABBCC")
        result = refs.scan(self.project)
        self.assertEqual(result["selection"]["qualitative"], "")
        identifier = result["imported"][0]["id"]
        with self.assertRaises(ValueError):
            lifecycle.apply_patch(self.project, {"template_selection": {"presentation": identifier}})
        lifecycle.apply_patch(self.project, {"template_selection": {"qualitative": identifier}})
        self.assertEqual(refs.selection(self.project)["qualitative"], identifier)

    def test_failed_analysis_keeps_original_and_is_not_complete(self):
        path = self.project / "templates/presentation/scanned.pdf"
        path.write_bytes(b"%PDF-1.7\nSynthetic unreadable reference")
        result = refs.scan(self.project)
        record = result["imported"][0]
        self.assertEqual(record["status"], "needs_reader")
        self.assertEqual((self.project / record["path"]).read_bytes(), path.read_bytes())
        self.assertEqual(refs.read_json(self.project, record["profile_ref"], {})["status"], "draft")

    def test_analysis_requires_visual_images_and_keeps_original_readonly(self):
        path = self.source()
        record = refs.scan(self.project)["imported"][0]
        profile = self.profile(record["id"])
        with self.assertRaises(ValueError):
            lifecycle.apply_patch(self.project, {"template_profiles": [{**profile, "visual_refs": []}]})
        (self.project / "qa/fake.png").write_bytes(b"not a rendered image")
        with self.assertRaises(ValueError):
            lifecycle.apply_patch(self.project, {"template_profiles": [{**profile, "visual_refs": ["qa/fake.png"]}]})
        lifecycle.apply_patch(self.project, {"template_profiles": [profile]})
        self.assertEqual(path.read_bytes(), (self.project / record["path"]).read_bytes())

    def test_working_copy_never_overwrites_or_mislabels_as_final(self):
        self.source()
        record = refs.scan(self.project)["imported"][0]
        result = refs.prepare(self.project, "qualitative", "working/templates/draft.docx")
        self.assertIn("제출본이 아닙니다", result["notice"])
        self.assertEqual((self.project / result["working_file"]).read_bytes(), (self.project / record["path"]).read_bytes())
        for output in ("working/templates/draft.docx", "exports/final.docx", "working/templates/draft.pptx", "../outside.docx"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                refs.prepare(self.project, "qualitative", output)

    def ready(self):
        profile = complete_package(self.project)
        self.source()
        record = refs.scan(self.project)["imported"][0]
        lifecycle.apply_patch(self.project, {"template_profiles": [self.profile(record["id"])]})
        (self.project / "qa/output.png").write_bytes(PNG)
        (self.project / "qa/comparison.md").write_text("가상 비교 검수: 색·서체·표·문체·이전 자료 교체 확인", encoding="utf-8")
        check = {"kind": "qualitative", "output_ref": profile["qualitative_file"], "review_ref": "qa/comparison.md",
                 "visual_refs": ["qa/output.png"], "notes": "가상 엔진 검수 기록"}
        return profile, record, check

    def test_output_comparison_is_bound_to_selected_source_profile_and_output(self):
        profile, record, check = self.ready()
        self.assertTrue(refs.check(self.project, profile))
        lifecycle.apply_patch(self.project, {"template_checks": [check]})
        self.assertEqual(refs.check(self.project, profile), [])
        with ZipFile(self.project / profile["qualitative_file"], "a") as archive:
            archive.writestr("docProps/custom.xml", "<changed/>")
        self.assertTrue(any("변경" in issue for issue in refs.check(self.project, profile)))
        lifecycle.apply_patch(self.project, {"template_checks": [check]})
        lifecycle.apply_patch(self.project, {"template_profiles": [{"template_id": record["id"], "style_notes": "새 분석"}]})
        self.assertTrue(any("변경" in issue for issue in refs.check(self.project, profile)))

    def test_reference_changes_invalidate_proposal_approval(self):
        profile, record, check = self.ready()
        data = starter.load_project(self.project)[1]
        data.update(current_stage="proposal", objective="가상 목표", scope_baseline="가상 범위", project_manager="가상 PM", client_or_product_owner="가상 책임자")
        (self.project / "project.json").write_text(json.dumps(data), encoding="utf-8")
        deliveries = starter.read_table(self.project, "deliverables")
        for row in deliveries:
            (self.project / row["path"]).write_text("가상 산출물", encoding="utf-8")
        lifecycle.apply_patch(self.project, {"requirements": [{"source": "가상 원문", "description": "가상 요구", "priority": "must", "acceptance_criteria": "가상 기준"}],
            "deliverables": [{"deliverable_id": row["deliverable_id"], "owner": "가상 담당자"} for row in deliveries],
            "template_checks": [check]})
        (self.project / "review.md").write_text("가상 검토 근거", encoding="utf-8")
        lifecycle.record_review(self.project, "가상 책임자", "review.md", "가상 검증 승인")
        self.assertEqual(lifecycle.stale_reviews(self.project, "proposal", strict=True), [])
        (self.project / "qa/comparison.md").write_text("다르게 편집된 비교 기록", encoding="utf-8")
        self.assertTrue(lifecycle.stale_reviews(self.project, "proposal", strict=True))

    def test_shared_folder_import_and_legacy_setup_preserve_customer_data(self):
        shared = self.parent / "company/qualitative"
        shared.mkdir(parents=True)
        docx(shared / "company.docx")
        result = refs.scan(self.project, shared.parent)
        self.assertEqual(len(result["imported"]), 1)
        path = self.project / "templates/qualitative/README.md"
        path.write_text("고객 안내 보존", encoding="utf-8")
        refs.setup(self.project)
        self.assertEqual(path.read_text(encoding="utf-8"), "고객 안내 보존")

    def test_dtd_fallback_and_reserved_manifest_paths(self):
        path = self.source()
        with ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", '<!DOCTYPE root [<!ENTITY x "bad">]><root>&x;</root>')
        result = refs.scan(self.project)
        self.assertEqual(result["imported"][0]["status"], "needs_reader")
        catalog = refs.library(self.project)
        catalog["templates"][0]["profile_ref"] = "project.json"
        (self.project / refs.MANIFEST).write_text(json.dumps(catalog), encoding="utf-8")
        original = (self.project / "project.json").read_bytes()
        with self.assertRaises(ValueError):
            lifecycle.apply_patch(self.project, {"template_profiles": [{"template_id": result["imported"][0]["id"]}]})
        self.assertEqual((self.project / "project.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
