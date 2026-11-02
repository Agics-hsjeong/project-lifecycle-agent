"""Local generated HTML. No server, telemetry, JavaScript or external assets."""
from html import escape
import json
from urllib.parse import quote
from tools.storage import commit, confined, digest, json_bytes, lock


def write(folder, output="reports/overview.html", actor="agent"):
    from tools import lifecycle, runs, starter
    root, _ = starter.load_project(folder)
    target = confined(root, output)
    if not output.startswith("reports/") or target.suffix.lower() != ".html":
        raise ValueError("현황 파일은 reports/ 아래 HTML로 생성하세요.")
    with lock(root):
        metadata = confined(root, "state/dashboard.json")
        known = json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {}
        if target.exists() and digest(target.read_bytes()) != known.get(output):
            raise ValueError("현황 파일에 별도 편집이 있습니다. 덮어쓰지 않습니다. 다른 출력 경로를 사용하세요.")
        view = lifecycle.inspect_project(root)
        work = runs.inspect(root)
        def text(value):
            return escape(str(value), quote=True)
        def link(label, relative):
            try:
                starter.local_file(root, relative)
            except (ValueError, OSError):
                return text(label)
            from pathlib import PurePosixPath
            # Output can be nested within reports; calculate a portable relative link.
            import posixpath
            href = posixpath.relpath(relative, str(PurePosixPath(output).parent))
            return '<a href="' + quote(href, safe="/") + '">' + text(label) + '</a>'
        def table(headers, rows):
            return "<table><thead><tr>" + "".join("<th>" + text(h) + "</th>" for h in headers) + "</tr></thead><tbody>" + "".join(
                "<tr>" + "".join("<td>" + cell + "</td>" for cell in row) + "</tr>" for row in rows) + "</tbody></table>"
        project = view["project"]
        labels = {"draft": "초안", "in_review": "검토 중", "approved": "승인", "not_applicable": "적용 제외",
                  "todo": "예정", "doing": "진행 중", "done": "완료", "planned": "예정", "running": "실행 중",
                  "blocked": "차단", "awaiting_decision": "판단 대기", "succeeded": "완료", "failed": "실패",
                  "skipped": "적용 제외", "stale": "재검증 필요", "extracted": "본문 추출", "partial": "일부 미판독",
                  "needs_reader": "판독 도구 필요", "needs_ocr": "OCR 필요", "reviewed": "호스트 판독 확인",
                  "baseline": "기준 자료", "operating_note": "운영 메모"}
        def label(value):
            return text(labels.get(value, value))
        profiles = {"si": "외주·SI", "product": "자체 제품", "api": "API·UI 없는 사업", "maintenance": "유지보수", "legacy": "기존 11단계"}
        body = "<p class='brand'>프로젝트이음 · Project Ieum</p><h1>" + text(project["name"]) + "</h1>"
        body += "<p>현재 단계: <strong>" + text(starter.STAGES[view["stage"]][0]) + "</strong> · 업무 프로필: " + text(profiles.get(view["workflow"]["profile"], view["workflow"]["profile"])) + "</p>"
        body += "<p>이 파일은 생성 시점의 로컬 현황입니다. 변경·실행은 호스트 에이전트에 요청하세요.</p>"
        body += "<h2>현재 확인할 사항</h2><ul>" + "".join("<li>" + text(i) + "</li>" for i in view["issues"] + view["gate_issues"]) + "</ul>"
        body += "<h2>현재 단계 산출물</h2>" + table(["ID", "산출물", "상태"], [
            [text(r["deliverable_id"]), link(r["title"], r["path"]), label(r["status"])] for r in view["deliverables"]])
        body += "<h2>미완료 작업</h2>" + table(["ID", "상태", "담당자", "다음 행동"], [
            [text(r["task_id"]), label(r["status"]), text(r["owner"]), text(r["next_action"])] for r in view["open_tasks"]])
        body += "<h2>원문 판독</h2>" + table(["자료", "용도", "판독 상태"], [
            [link(r["original_name"], r["path"]), label(r.get("purpose", "baseline")), label(r["extraction"]["status"])] for r in view["inputs"]])
        body += "<h2>작업 실행·재개</h2>"
        for run in work["runs"]:
            body += "<h3>" + text(run["id"] + " · " + run["title"]) + "</h3>" + table(["단계", "상태", "다음 실행 가능"], [
                [text(s["title"]), label(s["effective_status"]), "가능" if s["ready_to_start"] else "대기"] for s in run["steps"]])
        from tools import artifacts
        body += "<h2>출력 검수</h2>"
        for role, record in artifacts.read(root)["reviews"].items():
            body += "<p>" + text({"qualitative": "정성제안서", "presentation": "발표자료"}.get(role, role)) + " · " + link(record["source_file"], record["source_file"]) + " · 기록상 " + text(record["page_count"]) + "페이지 · " + link("검수 기록", record["review_ref"]) + "</p>"
        body += "<footer>생성 기준 revision: " + text(view["revision"]) + " · " + text(starter.now()) + "</footer>"
        css = "body{font-family:system-ui,'Malgun Gothic',sans-serif;background:#f4f7f9;color:#142a42;margin:0}main{max-width:1100px;margin:auto;padding:36px 24px}.brand,a{color:#087f8c}h1{font-size:2rem}h2{margin-top:36px}table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #dce4e8;overflow-wrap:anywhere}footer{margin:36px 0;color:#526274;font-size:.85rem}@media(max-width:600px){main{padding:20px 12px}table{font-size:.85rem}th,td{padding:8px}}"
        html = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>' + text(project["name"]) + " · 프로젝트이음</title><style>" + css + "</style></head><body><main>" + body + "</main></body></html>"
        content = html.encode("utf-8")
        known[output] = digest(content)
        changes = {output: content, "state/dashboard.json": json_bytes(known)}
        revision = lifecycle.add_event(root, changes, "dashboard", actor, {"path": output})
        commit(root, changes)
        return {"path": str(target), "revision": revision, "published": False}
