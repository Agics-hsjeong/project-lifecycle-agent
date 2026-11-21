"""Reader receipts for partial or unsupported sources; originals stay unchanged."""
import json
from tools.storage import commit, confined, consistent_read, json_bytes, lock
from tools.intake import hash_file


def gate_issues(root):
    path = confined(root, "inputs/manifest.json")
    if not path.exists():
        return []
    result = []
    for record in json.loads(path.read_text(encoding="utf-8"))["inputs"]:
        if record.get("purpose") == "operating_note":
            continue
        extraction = record.get("extraction", {})
        identifier = record.get("input_id", "Unknown input")
        try:
            if hash_file(confined(root, record["path"])) != record["sha256"]:
                raise ValueError("등록 원본 해시 불일치")
        except (KeyError, ValueError, OSError):
            result.append(f"{identifier}: 등록 원본 변경·누락, 원본을 확인하고 새 입력으로 등록하세요.")
            continue
        if extraction.get("path"):
            try:
                if not extraction.get("sha256"):
                    result.append(f"{identifier}: 이전 추출문의 등록 해시가 없습니다. 실제 판독 후 input-review로 확인하세요.")
                elif hash_file(confined(root, extraction["path"])) != extraction["sha256"]:
                    raise ValueError("등록 추출문 해시 불일치")
            except (ValueError, OSError):
                result.append(f"{identifier}: 등록 추출문 변경·누락, 원본 기준 재추출·재판독이 필요합니다.")
        status = extraction.get("status")
        if status == "reviewed":
            try:
                proof = confined(root, extraction["review_ref"])
                if hash_file(proof) != extraction["review_sha256"]:
                    raise ValueError("판독 근거 변경")
            except (KeyError, ValueError, OSError):
                result.append(f"{identifier}: 판독 근거 변경·누락, 재확인 필요")
        elif status != "extracted":
            pending = [p["page"] for p in extraction.get("pages", []) if p["status"] != "extracted"]
            result.append(f"{identifier}: 원문 판독 미완료 ({status}, 페이지 {pending}); 실제 판독 후 input-review")
    return result


def review(folder, payload, actor="agent"):
    from tools import lifecycle, starter, runs
    root, _ = starter.load_project(folder)
    if not isinstance(payload, dict) or set(payload) != {"expected_revision", "input_id", "evidence_ref", "page_count", "pages"}:
        raise ValueError("input-review 필드를 확인하세요.")
    count, pages = payload["page_count"], payload["pages"]
    if count is not None and (type(count) is not int or not 0 < count <= 2000):
        raise ValueError("실제 페이지 수를 확인하세요.")
    if not isinstance(pages, list) or any(type(p) is not int for p in pages) or sorted(pages) != (list(range(1, count + 1)) if count else []):
        raise ValueError("전체 페이지의 실제 판독 근거가 필요합니다.")
    with lock(root):
        runs.require_revision(root, payload)
        manifest = confined(root, "inputs/manifest.json")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        record = next((item for item in data["inputs"] if item["input_id"] == payload["input_id"]), None)
        if record is None or hash_file(confined(root, record["path"])) != record["sha256"]:
            raise ValueError("입력 ID·원본 해시를 확인하세요.")
        if record["path"].lower().endswith(".pdf") and count is None:
            raise ValueError("PDF 판독에는 전체 페이지 수가 필요합니다.")
        extraction = record["extraction"]
        if extraction.get("page_count") is not None and count != extraction["page_count"]:
            raise ValueError("원문 페이지 수와 판독 목록이 다릅니다.")
        extracted_sha = None
        if extraction.get("path"):
            extracted_sha = hash_file(starter.local_file(root, extraction["path"]))
            if extraction.get("sha256") and extracted_sha != extraction["sha256"]:
                raise ValueError("등록 추출문이 변경되었습니다. 원본 기준으로 재추출·재판독하세요.")
        proof = starter.local_file(root, payload["evidence_ref"])
        if proof.suffix.lower() not in {".md", ".txt", ".json"} or not proof.read_text(encoding="utf-8-sig").strip():
            raise ValueError("실제 판독 결과 텍스트가 필요합니다.")
        if "TODO" in proof.read_text(encoding="utf-8-sig"):
            raise ValueError("미작성 판독 결과는 사용할 수 없습니다.")
        extraction.update(raw_status=extraction.get("raw_status", extraction["status"]), status="reviewed",
                          review_ref=payload["evidence_ref"], review_sha256=hash_file(proof), reviewed_by=actor,
                          page_count=count, reviewed_pages=pages)
        if extracted_sha is not None:
            # Older records get a digest only after an explicit real reader receipt.
            extraction["sha256"] = extracted_sha
            if hash_file(starter.local_file(root, extraction["path"])) != extracted_sha:
                raise ValueError("판독 중 추출문이 변경되었습니다.")
        if hash_file(confined(root, record["path"])) != record["sha256"]:
            raise ValueError("판독 중 원본이 변경되었습니다.")
        changes = {"inputs/manifest.json": json_bytes(data)}
        revision = lifecycle.add_event(root, changes, "input-review", actor, {"input_id": record["input_id"], "pages": pages})
        commit(root, changes)
        return {"revision": revision, "status": "reviewed", "notice": "호스트의 실제 판독 근거를 보관했습니다. 내용 진위의 자동 인증은 아닙니다."}
