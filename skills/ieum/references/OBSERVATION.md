# 실행을 보여주는 관제 연결

사용자는 현재 AI 코딩 호스트에서 목표·질문·수정 방향을 전달한다. 주 에이전트가 역할을 조율하고 관제는 관찰 화면이다. 내부 구현·AI 검토마다 승인 버튼이나 별도 입력을 요구하지 않는다.

## 실행을 자동 연결하는 순서

1. 실제 고객 프로젝트·REQ/TASK·입력을 확인한다. 일반 코딩 요청이나 플러그인 소스에 새 관리 체계를 강제하지 않는다. 작업명은 TASK의 `title`로 한 번 기록하고 다음 행동·완료 메모와 분리한다.
2. 여러 단계는 `run-create`의 `bindings: {"STEP-001":"TASK-001"}`와 `scope: {"title":"현재 요청의 작업"}`로 원자적으로 연결한다. 단순 역할 이름으로 STEP을 추정하지 않는다. 기본 진척도는 최근 선언한 실행이다. 과거 실패·무효 검증은 이력에서 보존한다. 독립 작업은 workspace-configure의 명시적 current_scope로 선택할 수 있다.
3. 플러그인 `hooks/`는 등록된 이음 프로젝트에서 호스트가 제공하는 SessionStart/SubagentStart/SubagentStop/도구 시작·종료를 최소 메타데이터로 수집한다. 실제 native ID는 호스트 응답에서 연결하며 별칭을 실제 ID로 만들지 않는다. Codex의 새 훅은 호스트의 Review hooks 또는 Settings > Hooks에서 사용자 검토가 필요할 수 있다. 신뢰 기록을 수정하거나 신뢰 검사를 우회하지 않는다.
4. **Codex**: 현재 세션의 도구 환경에서 플러그인 `tools/native_bridge.py attach <실제 프로젝트>`를 한 번 실행한다. CODEX_THREAD_ID/CODEX_SESSION_ID로 해당 세션 원본만 연결한다. 현재 프로젝트를 작업 폴더로 연 세션에서 사용한다. 다른 대화·과거 세션은 검색해 관측하지 않는다. 공식 훅이 포착하지 않는 collaboration 이벤트는 현재 세션의 persisted rollout에서 보조 관측한다. 원본 형식 변경·접근 실패는 연결 오류로 표시하고 가상 이벤트로 메우지 않는다.
5. 주 에이전트가 직접 수행할 단계는 `tools/native_bridge.py focus <project> --provider codex|claude --task TASK-001 --role backend`를 한 번 호출한다. Codex는 현재 세션 환경을 사용한다. Claude는 훅이 연결한 이 프로젝트의 유일한 활성 세션을 사용하며, 둘 이상이면 실제 세션 ID를 --session으로 전달한다. 없는 ID를 만들어내지 않는다.
6. **run-create의 bindings 성공을 확인한 후**, 하위 에이전트를 실행하기 직전 `tools/native_bridge.py delegate <project> --provider codex|claude --task TASK-002 --role qa`를 호출한다. 반환된 Codex `task_name`(ieum_로 시작하며 영문 소문자·숫자·밑줄만 사용)을 spawn_agent의 task_name에, Claude `description`을 Agent의 description에 그대로 포함한다. 이 토큰은 실제 작업을 연결하는 키이며 사용자에게 입력을 요구하지 않는다. 실제 역할 계약·담당 경로·입력·검증 기준은 별도로 전달한다. 복수 작업자·세션은 provider/session/native ID로 구분한다. 모호하면 미연결로 표시하고 추정하지 않는다.
7. 실제 도구 실행·종료는 훅/현재 세션 관측기가 자동 기록한다. 같은 시작·종료를 workspace-activity와 가짜 별칭으로 중복 보고하지 않는다. 중요한 인계·검토 요약만 workspace-activity/workspace-review로 남긴다. 숨은 추론·원시 명령·프롬프트·도구 결과 전문은 저장하지 않는다.
8. TASK/RUN 결과는 기존 엔진으로 실제 파일·테스트·해시 근거를 확인한 뒤 직렬 기록한다. **작업자 실행 종료는 검증 통과가 아니다.** 결과 원장이 미완료이면 화면은 ‘실행 종료·결과 확인 중’으로 표시하며 완료율을 올리지 않는다. 긴 추론 중 새 이벤트가 없으면 마지막 관측 시각을 표시하고 임의 종료·진척 증가를 만들지 않는다.

관제 서버는 `workspace-serve <project> --open`으로 연결한다. 서버는 등록한 현재 Codex 세션을 0.75초 간격으로 읽고 브라우저는 실제 API를 갱신한다. 별도 상주 실행 요청이 있을 때만 provider 실행기를 붙인다. 현재 앱과 실행기가 같은 TASK를 중복 수행하지 않는다. 서버 종료는 코딩 호스트의 작업을 종료시키지 않는다.

## 판단과 이력

AI 검토·수정·재검증은 해당 역할끼리 이어간다. 사업 판단·권한·범위를 넘는 외부 실행만 현재 대화에서 확인한다. 사람 판단을 AI가 승인했다고 쓰지 않는다. 기존 명시적 사람 게이트는 이력을 보존하고 내부 검토 자동화 시 required:false로 전환할 수 있다.

관측 이벤트는 비즈니스 revision이나 검증 영수증을 변경하지 않는다. 이전 RUN에 연결된 늦은 작업자 응답은 재사용 TASK의 새 RUN을 덮지 않는다. 이력에서 과거 실패·stale·산출물 근거를 열어볼 수 있다. 연결 실패는 진단으로 보존하고 정상 완료로 꾸미지 않는다.


## Windows 관측 파일 경합

작업자 ID와 현재 상태는 `workspace-snapshot`의 `agents[].native_agent_id`, `session_id`, `task_id`와 TASK의 실행 상태로 확인한다. `state/workspace-native.json`을 직접 반복 읽는 진단으로 실행 중인 관측 저장과 경쟁하지 않는다. 관리 조회 API는 내부 읽기·쓰기를 직렬화한다.

관측 데이터는 업무 원장과 별도의 단일 파일 원자 저장을 사용한다. 다른 프로그램이 계속 파일을 열고 있으면 관측 반영이 지연될 수 있으며 보관함·현재 세션 커서로 재전달한다. 이를 해결하려고 pending.json을 임의 삭제하거나 접근 권한·훅 신뢰 검사를 우회하지 않는다. 기존 업무 트랜잭션의 미완료 기록은 실제 원인을 확인한 뒤 기존 표준 복구 규약을 따른다.
