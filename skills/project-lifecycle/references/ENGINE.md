# 에이전트 내부 엔진 사용 규약

모든 경로는 실제 값으로 치환한다. 셸에 맞게 인자를 안전하게 전달한다. 명령 문자열에 사용자 자료를 그대로 이어 붙이지 않는다. 아래 명령을 사용자에게 실행하라고 넘기지 않는다.

```text
python <plugin-root>/tools/starter.py doctor
python <plugin-root>/tools/starter.py discover <workspace>
python <plugin-root>/tools/starter.py init --workspace <workspace> --name <name> --slug <slug> --type si --rfp <source> --json
python <plugin-root>/tools/starter.py ingest <project> <source>
python <plugin-root>/tools/starter.py inspect <project>
python <plugin-root>/tools/starter.py apply <project> --file <patch.json> --actor <agent-or-person>
python <plugin-root>/tools/starter.py review <project> --reviewer <person> --evidence reviews/<decision>.md --notes <actual-decision> --decision approved
python <plugin-root>/tools/starter.py advance <project>
python <plugin-root>/tools/starter.py rewind <project> --to planning --reason <reason> --evidence reviews/<change>.md
python <plugin-root>/tools/starter.py recover <project>
python <plugin-root>/tools/starter.py proposal-setup <project>
python <plugin-root>/tools/starter.py template-setup <project>
python <plugin-root>/tools/starter.py template-scan <project> --source <reference-folder>
python <plugin-root>/tools/starter.py template-prepare <project> --kind qualitative --output working/templates/draft.docx
```

새 엔진 명령은 JSON을 반환한다. 기존 init/status/check/context는 --json을 지원한다.

## apply 예시

변경 파일은 에이전트가 작성한다. 기존 값을 지우지 않도록 필요한 필드만 포함한다.

```json
{
  "expected_revision": null,
  "project": {
    "objective": "신청자가 자신의 신청 상태를 확인한다",
    "scope_baseline": "RFP 2.1과 확인된 고객 답변",
    "project_manager": "확인된 담당자",
    "client_or_product_owner": "확인된 책임자"
  },
  "requirements": [{
    "source": "inputs/sources/INPUT-001/rfp.md §2.1",
    "description": "자신의 신청 상태 조회",
    "acceptance_criteria": "타인의 신청은 조회할 수 없다",
    "priority": "must",
    "status": "proposed"
  }],
  "tasks": [{"role": "analyst", "owner": "담당 에이전트", "status": "doing", "next_action": "인증 조건 확인"}],
  "deliverables": [{"deliverable_id": "DEL-001", "owner": "담당자", "status": "in_review"}],
  "handoff": "# 인수인계\n\n사업 검토 초안 작성. 다음은 인증 조건 확인."
}
```

반환한 assigned.requirements[0]을 다음 변경의 requirement_ids에 사용한다. ID를 생략한 행은 신규 등록, 기존 ID는 차이만 갱신한다. 삭제는 제공하지 않으며 제외 요구는 deferred와 사유로 기록한다.

- `expected_revision`은 최신 inspect의 값. 첫 기록 이전에는 null이다. 생략 가능하지만 공유 작업에서는 항상 보낸다.
- `project`는 name/objective/scope_baseline/client_or_product_owner/project_manager/technical/tailoring/methodology를 지원한다. current_stage는 변경할 수 없다.
- 원장 각 값은 문자열이다. 여러 ID·파일은 `;`로 구분한다. 추가 CSV 열과 열 순서는 보존된다.
- approved는 apply에서 지정할 수 없다. review가 검사 후 해당 단계 필수 산출물을 승인한다. 이미 승인된 행의 편집은 in_review로 되돌린다.
- done 작업은 실제 evidence_refs가 필요하다. 파일 참조는 프로젝트 내부의 `/` 상대 경로다. 외부 산출물은 로컬 명세에 URL·버전·확인일을 기록한다.
- review는 현재·이전 단계만 가능하다. 이전 검토를 재확인할 때 `--stage`를 지정한다. 검토 근거 파일을 수정하면 이전 스냅샷도 무효가 되므로 새 파일에 재검토 내용을 기록한다.
- apply의 `proposal`은 정성제안·발표 패키지 설정과 평가/슬라이드 목록을 갱신한다. 실제 형식과 필드는 [제안 규약](PROPOSAL.md)을 따른다. 기존 프로젝트는 proposal-setup을 먼저 실행한다.
- template_selection/template_profiles/template_checks는 [참고 템플릿 규약](REFERENCE-TEMPLATES.md)에 따라 선택·분석·제출본 비교 검수를 기록한다. template-scan은 파일별로 보존하며 일부 등록 실패는 errors 목록과 종료 코드 2로 보고한다.

## 결과와 복구

성공은 JSON과 종료 코드 0. gate_blocked는 종료 코드 1과 issues, invalid_request는 종료 코드 2다. 오류 JSON은 stderr로 출력한다. doctor는 실행 준비가 안 됐을 때 1을 반환한다.

state/events.jsonl은 변경 이력, state/reviews/는 검토 시점의 해시, state/pending.json은 복구용 변경 전 데이터다. 엔진이 관리한다. 인증·전자서명·변조 방지 기능은 아니다.

`recover`는 미완료 작업 이전 상태로 되돌린다. 현재 파일이 기록한 변경 전/후 어디에도 해당하지 않으면 중단한다. 살아 있는 PID의 잠금은 `--abandoned`로도 해제하지 않는다.
