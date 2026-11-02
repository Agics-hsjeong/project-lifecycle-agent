"""Bind real exports, complete rendered-page inventories and host QA receipts."""
import json
import struct
import zlib
from tools.document_safety import pdf_reader
from tools.intake import hash_file
from tools.storage import commit, confined, json_bytes, lock

STORE = "proposal/output-reviews.json"


def png(path):
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("렌더링 PNG 크기 제한 초과")
    raw = path.read_bytes()
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("실제 PNG 렌더링 이미지가 필요합니다.")
    position, header, data, ended = 8, None, bytearray(), False
    while position < len(raw):
        if position + 12 > len(raw):
            raise ValueError("잘린 PNG 청크")
        length, = struct.unpack(">I", raw[position:position+4])
        kind = raw[position+4:position+8]
        end = position + 12 + length
        if end > len(raw):
            raise ValueError("잘린 PNG 데이터")
        content = raw[position+8:position+8+length]
        checksum, = struct.unpack(">I", raw[position+8+length:end])
        if zlib.crc32(kind + content) & 0xffffffff != checksum:
            raise ValueError("PNG 체크섬 손상")
        if header is None and kind != b"IHDR":
            raise ValueError("PNG IHDR 누락")
        if kind == b"IHDR":
            if header is not None or length != 13:
                raise ValueError("PNG 헤더 손상")
            header = struct.unpack(">IIBBBBB", content)
        elif kind == b"IDAT":
            data.extend(content)
        elif kind == b"IEND":
            if length or end != len(raw):
                raise ValueError("PNG 종료 손상")
            ended = True
        position = end
    if header is None or not ended or not data:
        raise ValueError("PNG 이미지 데이터 누락")
    width, height, depth, color, compression, filtering, interlace = header
    depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    if not 0 < width <= 20000 or not 0 < height <= 20000 or depth not in depths.get(color, set()) or compression or filtering or interlace:
        raise ValueError("지원하지 않는 PNG 크기·형식. 일반 PNG로 다시 렌더링하세요.")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    stride = (width * channels * depth + 7) // 8 + 1
    expected = stride * height
    if expected > 128 * 1024 * 1024:
        raise ValueError("PNG 압축 해제 크기 제한 초과")
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(bytes(data), expected + 1)
    except zlib.error as exc:
        raise ValueError("PNG 압축 데이터 손상") from exc
    if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or any(pixels[i] > 4 for i in range(0, len(pixels), stride)):
        raise ValueError("PNG 픽셀 데이터 손상")
    return {"width": width, "height": height}


def read(root):
    path = confined(root, STORE)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "reviews": {}}
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("reviews"), dict):
        raise ValueError("출력 검수 기록 형식 오류")
    return data


def review(folder, payload, actor="agent"):
    from tools import lifecycle, proposal, reference_templates, runs, starter
    root, _ = starter.load_project(folder)
    fields = {"expected_revision", "role", "source_file", "pdf_file", "pages", "review_ref"}
    if not isinstance(payload, dict) or set(payload) != fields or payload["role"] not in {"qualitative", "presentation"}:
        raise ValueError("artifact-review 필드·종류를 확인하세요.")
    with lock(root):
        runs.require_revision(root, payload)
        source = starter.local_file(root, payload["source_file"])
        pdf = starter.local_file(root, payload["pdf_file"])
        if pdf.suffix.lower() != ".pdf":
            raise ValueError("실제 렌더링 PDF가 필요합니다.")
        if source.suffix.lower() == ".pdf" and source != pdf:
            raise ValueError("PDF 제출본과 검수 PDF는 같은 파일이어야 합니다.")
        before = {payload["source_file"]: hash_file(source), payload["pdf_file"]: hash_file(pdf)}
        proposal.check_export(source, payload["role"])
        count = len(pdf_reader(pdf).pages)
        pages = payload["pages"]
        if not isinstance(pages, list) or len(pages) != count:
            raise ValueError("전체 PDF 페이지의 이미지 목록이 필요합니다.")
        refs, indexes, image_hashes = set(), [], {}
        for page in pages:
            if not isinstance(page, dict) or set(page) != {"page", "image_ref"} or type(page["page"]) is not int or not isinstance(page["image_ref"], str):
                raise ValueError("페이지 번호·이미지 경로 형식 오류")
            if page["image_ref"] in refs:
                raise ValueError("페이지마다 별도 렌더링 파일이 필요합니다.")
            image = starter.local_file(root, page["image_ref"])
            checksum = hash_file(image)
            png(image)
            if hash_file(image) != checksum:
                raise ValueError("검수 중 이미지 변경")
            image_hashes[page["image_ref"]] = checksum
            refs.add(page["image_ref"])
            indexes.append(page["page"])
        if sorted(indexes) != list(range(1, count+1)):
            raise ValueError("페이지 누락·중복")
        proof = starter.local_file(root, payload["review_ref"])
        if proof.suffix.lower() not in {".md", ".txt", ".json"}:
            raise ValueError("실제 내용·스타일 검수 기록이 필요합니다.")
        content = proof.read_text(encoding="utf-8-sig")
        if not content.strip() or "TODO" in content:
            raise ValueError("빈/미작성 검수 기록은 사용할 수 없습니다.")
        files = {**before, **image_hashes,
                 payload["review_ref"]: hash_file(proof)}
        if any(hash_file(starter.local_file(root, ref)) != checksum for ref, checksum in files.items()):
            raise ValueError("검수 중 원본·PDF 변경")
        record = {k: v for k, v in payload.items() if k != "expected_revision"}
        record.update(files=files, page_count=count, actor=actor,
                      template_id=reference_templates.selection(root).get(payload["role"], ""))
        data = read(root)
        data["reviews"][payload["role"]] = record
        changes = {STORE: json_bytes(data)}
        revision = lifecycle.add_event(root, changes, "artifact-review", actor,
                                       {"role": payload["role"], "page_count": count, "files": sorted(files)})
        commit(root, changes)
        return {"revision": revision, "page_count": count, "notice": "파일·전체 이미지 목록·호스트 검수 근거를 연결했습니다. 내용·스타일을 자동 인증하거나 사람의 승인으로 대체하지 않습니다."}


def files(root):
    if not confined(root, STORE).exists():
        return set()
    refs = {STORE}
    for record in read(root)["reviews"].values():
        refs.update(record["files"])
    return refs


def check(root, project, profile):
    from tools import reference_templates, starter
    strict = project.get("workflow", {}).get("profile") in {"si", "api"}
    if not strict and not confined(root, STORE).exists():
        return []
    result = []
    data = read(root)
    roles = ["qualitative"] + (["presentation"] if profile["presentation_required"] else [])
    selected = reference_templates.selection(root)
    for role in roles:
        record = data["reviews"].get(role)
        if not record:
            result.append(f"{role}: 전체 출력 렌더링·검수 근거가 필요합니다. artifact-review")
            continue
        if record["source_file"] != profile[role + "_file"] or record["template_id"] != selected.get(role, ""):
            result.append(f"{role}: 제출 파일·스타일 선택과 출력 검수가 다릅니다.")
        for ref, checksum in record["files"].items():
            try:
                if hash_file(starter.local_file(root, ref)) != checksum:
                    raise ValueError("변경")
            except (ValueError, OSError):
                result.append(f"{role}: 검수 후 파일 변경·누락 ({ref})")
    return result
