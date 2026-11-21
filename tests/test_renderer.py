"""Real Poppler integration and adversarial PDF-page provenance regressions."""
import json
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from proposal_fixtures import complete_package
from tools import artifact_renderer, artifacts, lifecycle, proposal, starter
from tools.storage import digest, json_bytes


def synthetic_pdf(label="Synthetic proposal", pages=2):
    """Small standards-compliant PDF, no external document dependency."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for number in range(pages):
        page_id, content_id = len(objects) + 1, len(objects) + 2
        page_ids.append(page_id)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode())
        stream = f"0.08 0.5 0.45 rg 24 230 352 40 re f BT /F1 18 Tf 24 180 Td ({label} - {number + 1}) Tj ET".encode("ascii")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Count {pages} /Kids [{' '.join(str(item) + ' 0 R' for item in page_ids)}] >>".encode()
    raw, offsets = b"%PDF-1.4\n", [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(raw)
    raw += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    raw += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return raw + f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


class RendererFixture(unittest.TestCase):
    def setUp(self):
        base = starter.ROOT / ".test-output"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = starter.init_project(Path(self.temp.name), "render-test", "Synthetic render proof", "si")
        complete_package(self.root)
        (self.root / "exports/render.pdf").write_bytes(synthetic_pdf())
        (self.root / "docs/visual-review.md").write_text("Host reviewed content and page layout. Synthetic fixture only.", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, rendered):
        events = lifecycle.events(self.root)
        return {"expected_revision": events[-1]["revision"] if events else None, "role": "qualitative",
                "source_file": "exports/qualitative.docx", "pdf_file": "exports/render.pdf",
                "pages": rendered["pages"], "review_ref": "docs/visual-review.md"}

class RendererPolicyTests(RendererFixture):
    def test_renderer_timeout_terminates_owned_process(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "시간 제한"):
            artifact_renderer._run([sys.executable, "-c", "import time; time.sleep(30)"], started + 0.1)
        self.assertLess(time.monotonic() - started, 5)

    def test_renderer_diagnostic_output_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "출력 크기|처리하지"):
            artifact_renderer._run([sys.executable, "-c", "import sys; sys.stdout.write('x'*100000)"], time.monotonic() + 5)

    def test_missing_tool_fails_without_receipt_or_images(self):
        with patch.object(artifact_renderer.shutil, "which", return_value=None), self.assertRaisesRegex(ValueError, "Poppler"):
            artifact_renderer.render(self.root, "exports/render.pdf")
        self.assertFalse((self.root / artifact_renderer.STORE).exists())
        self.assertFalse((self.root / "proposal/rendered").exists())

    def test_strict_review_needs_receipt_before_any_write(self):
        payload = self.payload({"pages": []})
        payload["require_render_provenance"] = True
        with self.assertRaisesRegex(ValueError, "영수증"):
            artifacts.review(self.root, payload)
        self.assertFalse((self.root / artifacts.STORE).exists())

    def test_pdf_must_be_same_as_pdf_source(self):
        (self.root / "exports/other.pdf").write_bytes(synthetic_pdf("Other"))
        with self.assertRaisesRegex(ValueError, "같은 파일"):
            artifact_renderer.render(self.root, "exports/render.pdf", "exports/other.pdf")

    def test_input_cannot_leave_project_or_use_state(self):
        for ref in ("../outside.pdf", "state/events.jsonl", "exports/render.pdf:bad", "exports\\render.pdf"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                artifact_renderer.render(self.root, ref)


@unittest.skipUnless(shutil.which("pdftoppm") and shutil.which("pdfinfo"), "Poppler runtime not installed")
class RealRendererTests(RendererFixture):
    def rendered(self, same_pdf=False):
        return artifact_renderer.render(self.root, "exports/render.pdf", None if same_pdf else "exports/qualitative.docx")

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "Output review requires host pypdf runtime")
    def test_real_poppler_receipt_records_binary_and_input_hashes(self):
        rendered = self.rendered()
        receipt = artifact_renderer.read(self.root)["receipts"][rendered["renderer_receipt_id"]]
        self.assertEqual([1, 2], [page["page"] for page in receipt["pages"]])
        self.assertIn("pdftoppm version", receipt["renderer"]["version"])
        self.assertRegex(receipt["renderer"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual("host_supplied_unverified", rendered["source_to_pdf"])
        for page in receipt["pages"]:
            self.assertGreater(artifacts.png(self.root / page["image_ref"])["width"], 100)
        payload = self.payload(rendered)
        payload.update(renderer_receipt_id=rendered["renderer_receipt_id"], require_render_provenance=True)
        result = artifacts.review(self.root, payload)
        self.assertEqual("pdf_pages_verified", result["render_provenance"]["status"])
        self.assertEqual("host_reported", result["render_provenance"]["content_style_review"])
        self.assertFalse(any("qualitative" in issue for issue in artifacts.check(self.root, starter.load_project(self.root)[1], proposal.read(self.root))))

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "Legacy output review requires host pypdf runtime")
    def test_legacy_review_explicitly_reports_unverified_provenance(self):
        rendered = self.rendered()
        result = artifacts.review(self.root, self.payload(rendered))
        self.assertEqual("host_reported_unverified", result["render_provenance"]["status"])
        self.assertIn("출처 미확인", result["notice"])

    def test_unrelated_valid_png_cannot_replace_rendered_page(self):
        rendered = self.rendered()
        receipt = artifact_renderer.read(self.root)["receipts"][rendered["renderer_receipt_id"]]
        unrelated = "exports/unrelated.png"
        (self.root / unrelated).write_bytes((self.root / receipt["pages"][1]["image_ref"]).read_bytes())
        changed = [dict(item) for item in rendered["pages"]]
        changed[0]["image_ref"] = unrelated
        with self.assertRaisesRegex(ValueError, "전체 PDF 페이지"):
            artifact_renderer.validate(self.root, rendered["renderer_receipt_id"], "exports/qualitative.docx", "exports/render.pdf", changed)

    def test_changed_pdf_source_and_image_each_invalidate_receipt(self):
        rendered = self.rendered()
        for ref in ("exports/render.pdf", "exports/qualitative.docx", rendered["pages"][0]["image_ref"]):
            path = self.root / ref
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "변경"):
                artifact_renderer.validate(self.root, rendered["renderer_receipt_id"], "exports/qualitative.docx", "exports/render.pdf", rendered["pages"])
            path.write_bytes(original)

    def test_copy_of_receipt_cannot_be_forged_without_managed_event_anchor(self):
        rendered = self.rendered()
        registry = artifact_renderer.read(self.root)
        receipt = registry["receipts"][rendered["renderer_receipt_id"]]
        receipt["source_to_pdf"] = "same_pdf"
        receipt["receipt_sha256"] = digest(json_bytes({key: value for key, value in receipt.items() if key != "receipt_sha256"}))
        (self.root / artifact_renderer.STORE).write_bytes(json_bytes(registry))
        with self.assertRaisesRegex(ValueError, "무결성"):
            artifact_renderer.validate(self.root, rendered["renderer_receipt_id"], "exports/qualitative.docx", "exports/render.pdf", rendered["pages"])

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "Output review requires host pypdf runtime")
    def test_post_review_receipt_deletion_is_reported_by_gate(self):
        rendered = self.rendered()
        payload = self.payload(rendered)
        payload["renderer_receipt_id"] = rendered["renderer_receipt_id"]
        artifacts.review(self.root, payload)
        (self.root / artifact_renderer.STORE).unlink()
        issues = artifacts.check(self.root, starter.load_project(self.root)[1], proposal.read(self.root))
        self.assertTrue(any("PDF 렌더 출처 검증 실패" in issue for issue in issues))

    def test_pdf_to_png_same_pdf_claim_has_exact_sha_binding(self):
        rendered = self.rendered(same_pdf=True)
        proof = artifact_renderer.validate(self.root, rendered["renderer_receipt_id"], "exports/render.pdf", "exports/render.pdf", rendered["pages"])
        self.assertEqual("same_pdf", proof["source_to_pdf"])

    def test_real_module_cli_returns_current_revision_and_registered_pages(self):
        result = subprocess.run([sys.executable, "-m", "tools.artifact_renderer", str(self.root), "--pdf", "exports/render.pdf"],
                                cwd=starter.ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(lifecycle.events(self.root)[-1]["revision"], rendered["revision"])
        self.assertEqual(2, len(rendered["pages"]))


if __name__ == "__main__":
    unittest.main()
