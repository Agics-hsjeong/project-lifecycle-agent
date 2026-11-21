"""Supervisor-owned, bounded PDF -> PNG rendering and byte-bound provenance.

Run from the installed plugin with ``python -m tools.artifact_renderer PROJECT
--pdf exports/proposal.pdf [--source exports/proposal.docx]``. This renderer
does not convert office documents or certify their content/style. Receipts live
in protected engine state, outside generated workers' accepted output paths.
They are local audit evidence, not a digital signature against a hostile host.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from tools.intake import hash_file
from tools.storage import commit, confined, digest, json_bytes, lock

STORE = "state/render-receipts.json"
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_PAGES = 100
MAX_TOTAL_BYTES = 128 * 1024 * 1024
TIMEOUT_SECONDS = 120


def read(root):
    path = confined(root, STORE)
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "receipts": {}}
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("receipts"), dict):
        raise ValueError("PDF 렌더 영수증 형식 오류")
    return value


def _file(root, ref):
    path = confined(root, ref)
    if path.relative_to(root).parts[0] == "state" or not path.is_file():
        raise ValueError("프로젝트 내부의 실제 산출물 파일이 필요합니다.")
    return path


def _run(argv, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("PDF 렌더링 시간 제한 초과")
    # Poppler writes diagnostics to this bounded-on-read spool, never a model
    # command interpreter. Input is a supervisor-owned frozen PDF copy.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, shell=False,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise ValueError("PDF 렌더링 시간 제한 초과")
                if os.fstat(output.fileno()).st_size > 65536:
                    raise ValueError("Poppler 진단 출력 크기 제한 초과")
                try:
                    process.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        output.seek(0)
        content = output.read(65537)
    if process.returncode or len(content) > 65536:
        raise ValueError("Poppler가 PDF를 처리하지 못했습니다. 원본 문서와 도구 상태를 확인하세요.")
    return content.decode("utf-8", errors="replace")


def _tool(name, deadline):
    found = shutil.which(name)
    if not found:
        raise ValueError(f"실제 PDF 렌더링에는 Poppler {name} 도구가 필요합니다.")
    path = Path(found).resolve()
    checksum = hash_file(path)
    version = _run([str(path), "-v"], deadline).strip().splitlines()
    return path, {"tool": name, "version": version[0][:240] if version else "unknown", "sha256": checksum}


def render(folder, pdf_ref, source_ref=None, actor="supervisor"):
    """Render all pages and atomically record hashes after input revalidation."""
    from tools import artifacts, lifecycle, starter
    root, _ = starter.load_project(folder)
    source_ref = source_ref or pdf_ref
    with lock(root):
        pdf, source = _file(root, pdf_ref), _file(root, source_ref)
        if pdf.suffix.lower() != ".pdf" or not 0 < pdf.stat().st_size <= MAX_PDF_BYTES:
            raise ValueError("64 MiB 이하의 실제 PDF가 필요합니다.")
        if source.stat().st_size > MAX_PDF_BYTES:
            raise ValueError("렌더 원본 크기 제한 64 MiB 초과")
        if source.suffix.lower() == ".pdf" and source != pdf:
            raise ValueError("PDF 제출본과 렌더 PDF는 같은 파일이어야 합니다.")
        pdf_bytes = pdf.read_bytes()
        if not pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("실제 PDF 파일이 필요합니다.")
        pdf_sha, source_sha = digest(pdf_bytes), hash_file(source)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    render_exe, renderer = _tool("pdftoppm", deadline)
    info_exe, inspector = _tool("pdfinfo", deadline)
    identifier = uuid.uuid4().hex
    pages, changes, total = [], {}, 0
    with tempfile.TemporaryDirectory(prefix="ieum-pdf-render-") as temporary:
        temporary = Path(temporary)
        frozen = temporary / "input.pdf"
        frozen.write_bytes(pdf_bytes)
        info = _run([str(info_exe), str(frozen)], deadline)
        match = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
        if not match or not 0 < int(match.group(1)) <= MAX_PAGES:
            raise ValueError(f"PDF 렌더링은 1~{MAX_PAGES}페이지를 지원합니다.")
        if re.search(r"^Encrypted:\s+yes\b", info, re.MULTILINE):
            raise ValueError("암호화된 PDF는 자동 렌더링하지 않습니다.")
        for page in range(1, int(match.group(1)) + 1):
            prefix = temporary / f"page-{page:04d}"
            _run([str(render_exe), "-f", str(page), "-l", str(page), "-singlefile",
                  "-png", "-r", "120", "-scale-to", "1600", str(frozen), str(prefix)], deadline)
            image = prefix.with_suffix(".png")
            dimensions = artifacts.png(image)
            raw = image.read_bytes()
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("전체 렌더 이미지 크기 제한 초과")
            ref = f"proposal/rendered/{identifier}/page-{page:04d}.png"
            pages.append({"page": page, "image_ref": ref, "sha256": digest(raw), **dimensions})
            changes[ref] = raw
        if hash_file(frozen) != pdf_sha or hash_file(render_exe) != renderer["sha256"] or hash_file(info_exe) != inspector["sha256"]:
            raise ValueError("렌더링 중 PDF·Poppler 변경")
    receipt = {"schema_version": 1, "id": identifier, "created_at": starter.now(), "actor": actor,
               "pdf_ref": pdf_ref, "pdf_sha256": pdf_sha, "source_ref": source_ref,
               "source_sha256": source_sha, "source_to_pdf": "same_pdf" if source == pdf else "host_supplied_unverified",
               "renderer": renderer, "inspector": inspector, "parameters": {"dpi": 120, "max_dimension": 1600}, "pages": pages}
    receipt["receipt_sha256"] = digest(json_bytes(receipt))
    with lock(root):
        if hash_file(_file(root, pdf_ref)) != pdf_sha or hash_file(_file(root, source_ref)) != source_sha:
            raise ValueError("렌더링 중 원본·PDF 변경: 다시 렌더링하세요.")
        registry = read(root)
        registry["receipts"][identifier] = receipt
        changes[STORE] = json_bytes(registry)
        revision = lifecycle.add_event(root, changes, "artifact-render", actor,
                                       {"receipt_id": identifier, "receipt_sha256": receipt["receipt_sha256"], "page_count": len(pages)})
        commit(root, changes)
    return {"revision": revision, "renderer_receipt_id": identifier,
            "pages": [{"page": item["page"], "image_ref": item["image_ref"]} for item in pages],
            "render_provenance": "pdf_pages_verified", "source_to_pdf": receipt["source_to_pdf"],
            "notice": "PDF→PNG 대응만 확인했습니다. 원본 문서→PDF 변환 계보와 내용·스타일 검수는 별도입니다."}


def validate(root, identifier, source_ref, pdf_ref, pages):
    """Reject supplied images unless the exact supervisor render is current."""
    from tools import lifecycle
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("올바른 감독자 렌더 영수증 ID가 필요합니다.")
    receipt = read(root)["receipts"].get(identifier)
    if not isinstance(receipt, dict):
        raise ValueError("등록된 감독자 PDF 렌더 영수증이 없습니다.")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    checksum = digest(json_bytes(body))
    # A canonical state record must also be anchored in the managed event log.
    if checksum != receipt.get("receipt_sha256") or not any(
            event.get("action") == "artifact-render" and event.get("details", {}).get("receipt_id") == identifier
            and event.get("details", {}).get("receipt_sha256") == checksum for event in lifecycle.events(root)):
        raise ValueError("감독자 렌더 영수증 무결성 오류")
    if receipt.get("source_ref") != source_ref or receipt.get("pdf_ref") != pdf_ref:
        raise ValueError("렌더 영수증의 원본·PDF가 검수 대상과 다릅니다.")
    for ref, expected in ((source_ref, receipt.get("source_sha256")), (pdf_ref, receipt.get("pdf_sha256"))):
        if hash_file(_file(root, ref)) != expected:
            raise ValueError("렌더링 후 원본·PDF 변경: 다시 렌더링하세요.")
    expected_pages = [{"page": item["page"], "image_ref": item["image_ref"]} for item in receipt["pages"]]
    if sorted(pages, key=lambda item: item["page"]) != expected_pages:
        raise ValueError("검수 이미지가 감독자가 렌더링한 전체 PDF 페이지와 다릅니다.")
    for page in receipt["pages"]:
        if hash_file(_file(root, page["image_ref"])) != page["sha256"]:
            raise ValueError("렌더링 후 페이지 이미지 변경: 다시 렌더링하세요.")
    return {"status": "pdf_pages_verified", "receipt_id": identifier, "receipt_sha256": checksum,
            "source_to_pdf": receipt["source_to_pdf"], "content_style_review": "host_reported"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("--pdf", required=True, help="프로젝트 상대 PDF 경로")
    parser.add_argument("--source", help="선택: 원본 DOCX/PPTX 등의 프로젝트 상대 경로")
    parser.add_argument("--actor", default="supervisor")
    args = parser.parse_args()
    try:
        result = render(args.project, args.pdf, args.source, args.actor)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
