"""Regression coverage for preserved inputs and actual document package order."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

import test_lifecycle as scenario
from tools import inputs, intake, lifecycle, proposal, starter, template_analysis


class InputIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = scenario.LifecycleTests()
        with contextlib.redirect_stdout(io.StringIO()):
            self.fixture.setUp()
        self.root = self.fixture.project
        self.source = self.fixture.parent / "rfp.txt"
        self.source.write_bytes(b"Original scope: only view inquiries.\n")
        self.record = lifecycle.ingest(self.root, self.source)["input"]

    def tearDown(self):
        self.fixture.tearDown()

    def review(self):
        self.fixture.write("reviews/read.md", "Actual synthetic reader comparison against the preserved original.")
        return inputs.review(self.root, {
            "expected_revision": lifecycle.events(self.root)[-1]["revision"],
            "input_id": self.record["input_id"], "evidence_ref": "reviews/read.md",
            "page_count": None, "pages": []})

    def test_first_approval_rejects_changed_extracted_source(self):
        self.fixture.prepare()
        (self.root / self.record["path"]).write_bytes(b"Changed scope: delete all inquiries.")
        self.assertTrue(any("등록 원본" in issue for issue in inputs.gate_issues(self.root)))
        with self.assertRaises(lifecycle.GateError):
            self.fixture.approve()
        self.assertEqual(starter.read_table(self.root, "reviews"), [])

    def test_extracted_text_digest_binds_actual_utf8_bytes(self):
        extraction = self.record["extraction"]
        self.assertEqual(extraction["sha256"], intake.hash_file(self.root / extraction["path"]))
        self.assertEqual(inputs.gate_issues(self.root), [])
        (self.root / extraction["path"]).write_bytes(b"Injected extracted scope.")
        self.assertTrue(any("등록 추출문" in issue for issue in inputs.gate_issues(self.root)))
        with self.assertRaisesRegex(ValueError, "추출문이 변경"):
            self.review()

    def test_missing_source_is_rejected_after_reader_receipt(self):
        self.review()
        self.assertEqual(inputs.gate_issues(self.root), [])
        (self.root / self.record["path"]).unlink()
        self.assertTrue(any("등록 원본" in issue for issue in inputs.gate_issues(self.root)))

    def test_legacy_extracted_digest_needs_explicit_reader_receipt(self):
        path = self.root / "inputs/manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["inputs"][0]["extraction"].pop("sha256")
        path.write_text(json.dumps(manifest), encoding="utf-8")
        original = (self.root / self.record["path"]).read_bytes()
        self.assertTrue(any("이전 추출문" in issue for issue in inputs.gate_issues(self.root)))
        self.review()
        current = json.loads(path.read_text(encoding="utf-8"))["inputs"][0]
        self.assertEqual(current["extraction"]["sha256"], intake.hash_file(self.root / current["extraction"]["path"]))
        self.assertEqual(inputs.gate_issues(self.root), [])
        self.assertEqual((self.root / current["path"]).read_bytes(), original)

    def test_reader_receipt_cannot_adopt_changed_original(self):
        (self.root / self.record["path"]).write_bytes(b"Changed original.")
        with self.assertRaisesRegex(ValueError, "원본 해시"):
            self.review()

    def test_project_init_preserves_multiline_extraction_digest(self):
        with contextlib.redirect_stdout(io.StringIO()):
            project = Path(starter.init_project(self.fixture.parent, "multiline", "Synthetic", "product", self.source))
        record = json.loads((project / "inputs/manifest.json").read_text(encoding="utf-8"))["inputs"][0]
        self.assertEqual(record["extraction"]["sha256"], intake.hash_file(project / record["extraction"]["path"]))
        self.assertEqual(inputs.gate_issues(project), [])


class DocumentPackageOrderTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="document-integrity-", dir=parent)
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def deck(self, active=(1,), physical=(1, 2), *, relationships=None, entries=None):
        path = self.root / "sample.pptx"
        entries = entries if entries is not None else ''.join(
            f'<p:sldId id="{256+i}" r:id="rId{number}"/>' for i, number in enumerate(active))
        relationships = relationships if relationships is not None else ''.join(
            f'<Relationship Id="rId{number}" Target="slides/slide{number}.xml"/>' for number in set(active))
        with ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="urn:p" xmlns:r="urn:r"><p:sldIdLst>'+entries+'</p:sldIdLst></p:presentation>')
            archive.writestr("ppt/_rels/presentation.xml.rels", '<Relationships>'+relationships+'</Relationships>')
            for number in physical:
                archive.writestr(f"ppt/slides/slide{number}.xml", f'<p:sld xmlns:p="urn:p"><p:p><p:t>Slide {number}</p:t></p:p></p:sld>')
        return path

    def test_orphan_pptx_slide_is_not_counted_or_analyzed(self):
        path = self.deck()
        proposal.check_export(path, "presentation", 1)
        with self.assertRaisesRegex(ValueError, "슬라이드 수"):
            proposal.check_export(path, "presentation", 2)
        self.assertEqual(template_analysis.analyze(path)["slide_count"], 1)

    def test_active_slide_order_is_shared_with_analysis(self):
        path = self.deck(active=(2, 1))
        proposal.check_export(path, "presentation", 2)
        with ZipFile(path) as archive:
            ordered = template_analysis.slide_paths(archive, template_analysis.xml(archive, "ppt/presentation.xml"))
        self.assertEqual(ordered, ["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"])

    def test_missing_active_slide_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "슬라이드 경로"):
            proposal.check_export(self.deck(active=(1, 2), physical=(1,)), "presentation", 2)

    def test_ambiguous_external_or_wrong_slide_relationship_is_rejected(self):
        cases = [
            ('<Relationship Id="rId1" Target="slides/slide1.xml"/><Relationship Id="rId1" Target="slides/slide2.xml"/>', None),
            ('<Relationship Id="rId1" Target="https://example.test/slide.xml" TargetMode="External"/>', None),
            ('<Relationship Id="rId1" Target="slides/slide1.xml" Type="urn:relationship/notesSlide"/>', None),
            ('<Relationship Id="rId1" Target="../../outside.xml"/>', None),
            (None, '<p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId1"/>'),
            (None, '<p:sldId id="256" r:id="rId1"/><p:sldId id="256" r:id="rId2"/>'),
        ]
        for relationships, entries in cases:
            with self.subTest(relationships=relationships, entries=entries), self.assertRaises(ValueError):
                proposal.check_export(self.deck(active=(1, 2), relationships=relationships, entries=entries), "presentation", 2)

    def hwpx(self, spine=None, *, missing=None):
        path = self.root / "sample.hwpx"
        with ZipFile(path, "w") as archive:
            for number in range(12):
                if number != missing:
                    archive.writestr(f"Contents/section{number}.xml", f'<section><p><t>CHAPTER-{number}</t></p></section>')
            if spine is not None:
                items = ''.join(f'<item id="section{i}" href="section{i}.xml"/>' for i in range(12))
                refs = ''.join(f'<itemref idref="section{i}"/>' for i in spine)
                archive.writestr("Contents/content.hpf", '<package xmlns="urn:opf"><manifest>'+items+'</manifest><spine>'+refs+'</spine></package>')
        return path

    def test_legacy_hwpx_numeric_section_order(self):
        text, status, _ = intake.extract_text(self.hwpx())
        self.assertEqual(status, "extracted")
        self.assertEqual(text.splitlines(), [f"CHAPTER-{i}" for i in range(12)])

    def test_hwpx_package_spine_wins_over_filename_order(self):
        text, status, _ = intake.extract_text(self.hwpx(spine=(10, 2, 0)))
        self.assertEqual(status, "extracted")
        self.assertEqual(text.splitlines(), ["CHAPTER-10", "CHAPTER-2", "CHAPTER-0"])

    def test_missing_or_duplicate_spine_section_needs_reader(self):
        for spine, missing in [((0, 11), 11), ((0, 0), None)]:
            record, text = intake.make_record(self.hwpx(spine=spine, missing=missing), "INPUT-001", "inputs/sample.hwpx", "synthetic")
            self.assertEqual(record["extraction"]["status"], "needs_reader")
            self.assertIsNone(text)


if __name__ == "__main__":
    unittest.main()
