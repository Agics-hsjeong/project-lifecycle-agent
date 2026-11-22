# 실제 프로젝트 관제 연결 규약

[활동 관찰 규약](OBSERVATION.md)을 먼저 적용한다. 사용자 작업은 Codex·Claude Code에서 진행하고 관제는 실제 상태를 시각화한다. 화면의 승인 입력을 내부 작업의 필수 조건으로 만들지 않는다.

1. discover/inspect로 실제 고객 프로젝트를 찾고 AGENTS/CLAUDE/HANDOFF를 확인한다. 설치 폴더를 고객 프로젝트로 사용하지 않는다.
2. workspace-snapshot으로 실제 TASK/RUN·역할·산출물·활동·연결을 확인한다. 원장이 비어 있으면 가짜 작업자를 만들지 않는다.
3. 필요한 작업·선후 관계·역할·일정은 workspace-configure로 기록한다. 같은 역할이라는 이유로 TASK와 RUN을 추정 결합하지 않는다. 새 내부 작업마다 사람 게이트를 생성하지 않는다.
4. 검증된 tools/workspace_ui/manifest.json을 확인하고 workspace-serve <project> --open으로 관찰 서버를 유지한다. 사용자에게 Node 설치나 UI 빌드를 넘기지 않는다. Windows background helper는 Hidden을 사용한다.
5. 현재 호스트의 실제 역할 작업은 OBSERVATION의 focus/delegate 연결과 공식 훅·현재 세션 관측기로 자동 전달한다. 인계·검토 요약만 workspace-activity로 남기고 상태·검증 근거는 기존 엔진으로 직렬 기록한다. 별도 상주 처리를 요청한 경우 provider와 작업 execution 계획을 연결한다. 같은 TASK를 중복 실행하지 않는다.
6. UI의 프로젝트 ID·마지막 갱신·현재 행동·실제 활성 작업자를 확인한다. 접속 토큰을 공개 로그·HANDOFF·산출물에 넣지 않는다. 연결 실패를 demo로 바꾸지 않는다.

## 기존 게이트와 자동 검토

명시적인 사람 게이트의 기존 결정·파일 해시·컨텍스트 검사 API는 유지한다. 내부 검토 자동화 요청이면 해당 게이트의 required:false를 명시하고 관찰 대상으로 전환하며 가짜 승인 결정을 만들지 않는다. business current_stage와 단계 승인·외부 배포 권한은 별개다.

독립 검토는 workspace-review의 실제 근거·파일 해시·담당 역할로 기록한다. QA가 찾은 결함은 원 개발자 scope로 전달해 제한된 자동 수정과 재검증을 수행한다. 인증·권한·입력 충돌은 무한 재시도하지 않는다. 사람에게 필요한 질문은 현재 대화에서 설명하고 관제에는 이유를 보여준다.

## 기존 낮은 수준 연결

workspace-host의 heartbeat/claim/started/completed/failed/retry는 실제 작업자와 명시 실행 계획의 연결을 유지한다. RUN/STEP 결과는 기존 run-result로 검증한다. 상주 실행기와 현재 호스트가 같은 작업을 동시에 수신하지 않는다. host capability와 viewer capability를 분리한다. 브라우저 조회로 임의 셸이나 실행 파일을 등록하지 않는다.

실제 실행·미집계·실패·미실행 범위를 구분한다. 합성 테스트와 native 모델 E2E를 구분하고 실제 확인한 증거만 완료 보고한다.

## 현재 실행과 과거 이력

기본 화면의 완료율은 current_scope의 작업만 집계한다. 최근 실행이 실패·stale여도 숨기지 않는다. 실행 이력에서 이전 RUN·분리된 TASK·실패·무효 근거를 확인한다. TASK 제목과 다음 행동 메모는 별개다. 실제 ID와 표시 이름을 구분하고, 작업자 종료 후 결과가 아직 확인되지 않은 상태를 완료로 표시하지 않는다.
