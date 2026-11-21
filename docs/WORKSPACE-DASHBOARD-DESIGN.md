# 프로젝트이음 관제 워크스페이스 설계안

> 후속 요구 반영: 관제는 역할별 활동과 진행·대기·완료를 보여주는 관찰 화면으로 변경한다. 아래 초기 승인 중심 설계는 이력이며 현재 기본 UX는 [관제 사용법](WORKSPACE.md)과 [호스트 관찰 규약](AUTOMATIC-EXECUTION.md)을 따른다.

상태: 2026-11-19 사용자 승인. 실제 프로젝트 데이터·개별 산출물 승인·수정 요청을 포함해 구현 및 검증을 진행한다. 아래는 승인된 설계 기준이며, 완료 범위와 검증 결과는 WORKSPACE-VALIDATION.md에서 별도로 기록한다.
분석 기준: 현재 main 0dbc340 / 프로젝트이음 2.0.0-beta.2 / 2026-11-19.

## 1. 제품 방향과 유지할 자산

프로젝트이음은 제안부터 운영까지 일의 기준을 잇는 개발방법론 에이전트 스타트팩이다. 새 화면은 실제 로컬 프로젝트의 작업·근거·사람 결정·실행 주체를 연결하는 관제 워크스페이스로 설계한다.

- 공개 GitHub Pages에는 동일한 UI의 명확한 가상 시뮬레이션을 제공한다.
- 실제 고객 프로젝트는 로컬 서버와 기존 관리 엔진을 통해 조회·변경한다. 고객 자료를 공개 사이트로 복사하거나 자동 업로드하지 않는다.
- 기존 Astro/Starlight 소개·문서 사이트와 호스팅은 유지하고, 워크스페이스 화면만 별도 React 영역으로 구성한다.
- 공식 symbol.png, PROJECT IEUM 표기, 자체 호스팅 Pretendard와 기존 토큰을 재사용한다.

## 2. 저장소에서 확인한 사실

| 항목 | 현재 구현 | 설계에 반영할 점 |
| --- | --- | --- |
| 화면·기술 | website/src/pages/index.astro, Astro 7.4.2 / Starlight 0.42.7. React 통합은 아직 없음 | 기존 페이지를 교체하지 않고 워크스페이스 경로 추가 |
| 로컬 현황 | tools/dashboard.py의 reports/overview.html 정적 HTML, 현재 단계·원문·원장·run 상태·revision 제공 | 새 3뷰는 공통 snapshot DTO를 사용 |
| 업무 단계 | tools/starter.py의 11단계. si/product/api/maintenance 프로필별 활성 단계가 다름 | 웹의 5그룹은 표시·집계 계층으로 유지 |
| 역할 | skills/ieum/references/roles.json의 18개 계약과 호스트 기본 작업자/전용 역할 | 역할 정의와 실제 에이전트 실행 인스턴스 구분 |
| 작업 원장 | TASK/REQ/DEL/REV/RISK CSV, TASK에는 role/owner/status/due_date 등이 있음 | 상위 TASK·예정 시작/종료·단계·진척 필드는 확장이 필요 |
| 실행 DAG | state/runs.json에 STEP 의존성·입출력·시도·결과·해시·stale/ready 상태 존재 | 노드 키는 project/RUN/STEP로 구성. TASK와 STEP 명시 연결 추가 |
| 추적 관계 | REQ↔DEL, TASK→REQ, REQ→설계·코드·테스트, STEP→입출력/선행 STEP | 근거가 있는 관계만 표시하고 출처를 열 수 있게 함 |
| 승인 | 기존 review는 단계 단위 일괄 승인/반려. AI 검토와 사람 승인은 별개 | DB/API 한 산출물 승인에 기존 stage review를 그대로 호출하면 안 됨 |
| Diff | 엔진 관리 파일의 변경 전 내용·변경 후 해시와 복원 계획 존재 | 일반 코드/문서 Diff는 Git 또는 산출물 버전 스냅샷을 추가 연결 |
| 실시간 관제 | 엔진은 기록을 관리하며 실제 모델 실행은 활성 호스트가 담당 | 실행기 연결·실제 작업자 ID·heartbeat·관제 명령 수신 확인이 필요 |
| 토큰·비용 | 공통 수집·합산 경로 없음 | 미집계를 0으로 표시하지 않으며 실제 수집원·범위·시각 표시 |

일부 기존 로컬 샘플에는 작업·실행 기록이 없으므로 빈 상태도 정상 화면이다. 19개 예시 작업·일정·진행률은 시뮬레이션 데이터로 구분한다.

## 3. 시각 체계

| 용도 | 재사용/제안 값 |
| --- | --- |
| 깊은 배경 | --ieum-night: #07171d |
| 패널 표면 | --ieum-night-raised: #10272e |
| 선택·주요 액션·완료 | --ieum-mint: #8cf3ce |
| 브랜드 보조 teal | --ieum-teal: #08747b. 다크 배경의 작은 본문색으로는 사용하지 않음 |
| 실행 상태 | --ieum-blue: #b2b8ff |
| 사람 승인 대기 | --ieum-gold: #ead3a2 |
| 수정 요청·실패 | 절제된 rose 의미 토큰 신규 추가, 색과 아이콘·텍스트를 함께 사용 |
| 경계 | --ieum-line: rgba(163,203,197,.18) |
| 서체 | 기존 Pretendard. 파일 ID·코드·금액은 monospace/tabular numerals |
| 반경 | 큰 패널 18px, 작업 카드 12px, 버튼 8px. 기존 18/28px 언어의 업무 밀도 조정 |
| 간격·타이포 | 8/12/16/24px 간격, 본문 14/20px, 보조 12/18px, 카드 제목 14–15px/600 |

화면 바탕은 매트한 네이비로 두고 유리 효과는 헤더·Inspector 표면에만 약하게 적용한다. 선택은 민트 외곽선과 얕은 배경, 승인 대기는 골드 배지로 구분한다. 지속 네온 글로우·자동 회전·모든 엣지의 흐름 애니메이션은 사용하지 않는다. 읽어야 하는 정보와 선택한 실행 경로에만 강조를 둔다.

## 4. 공통 레이아웃

1440×900 기준:

- 상단 64px: 공식 심볼·프로젝트 선택·실제 버전 / 3개 뷰 탭 / 승인 대기·활성 작업자·토큰/비용.
- 그 아래 48px: 연결 상태·마지막 갱신 시각, 검색, 상태/역할/단계 필터, 전체/현재 작업 보기.
- 필요 시 5그룹 요약 스트립: 현재 그룹·완료 작업 수·승인 대기 수. 상세 캔버스가 패닝되어도 전체 맥락 유지.
- 본문: 가용 공간을 사용하는 뷰와 우측 Inspector 384px. 패널은 360–520px 조절 가능하고 닫을 수 있다.
- 1200px 미만: Inspector를 오버레이로 전환. 800px 이하: 작업 목록·승인 큐 우선, 전체 화면 상세. 그래프·간트는 별도 확대 보기.

19개 작업과 Inspector를 동시에 축소해 한 화면에 억지로 넣지 않는다. 기본은 현재 그룹과 주변 의존 작업을 읽기 크기로 보여주고 전체 맞춤에서는 그룹 요약/간략 카드로 정보 밀도를 전환한다.

HUD의 “활성”은 실제 실행 인스턴스만 센다. 18은 가능한 역할 종류이며 활성 작업자 수의 분모로 쓰지 않는다. 미연결·오래된 heartbeat·미집계 비용·일정 미정은 명확한 상태로 표시한다.

## 5. 5개 표시 그룹과 실제 단계

| 표시 그룹 | 대응하는 실제 엔진 단계 |
| --- | --- |
| 01 사업과 요구사항 | discovery |
| 02 제안과 발표 | proposal |
| 03 설계와 구현 | kickoff, planning, design, publishing, implementation |
| 04 검증과 배포 | verification, release |
| 05 인수와 운영 | handover, maintenance |

현재 업무 프로필에서 빠진 단계는 완료로 꾸미지 않고 적용되지 않음을 표시한다. 기존 current_stage와 승인 조건을 5단계로 덮어쓰지 않는다.

## 6. 워크플로우 뷰

- @xyflow/react + elkjs로 그룹·노드·포트를 배치한다. 그룹 폭 약 240–280px, 카드 폭 약 208–240px, 높이는 내용에 맞추되 기본 88–104px, 노드 간 20px 이상 간격.
- 카드: TASK/STEP ID → 제목 2행 → 역할·실제 실행 호스트 → 상태·막힌 이유. 긴 파일 경로와 전체 로그는 Inspector로 이동한다.
- 일반 의존선은 단색의 얇은 선, 선택한 경로만 강조한다. 피드백선은 캔버스 외곽 레인으로 분리한 점선으로 표현한다.
- 수정 요청은 새 TASK/실행 시도와 버전으로 연결한다. 피드백 표시선을 기존 실행 DAG의 순환 depends_on으로 넣지 않는다.
- 좌하단 줌·전체 맞춤·현재 작업 포커스, 우하단 미니맵. 포커스 이동 시 선택한 노드가 Inspector 뒤에 가려지지 않도록 viewport 여백을 적용한다.
- 노드 이동은 우선 화면 배치 변경이며 실행 순서나 실제 의존성 변경으로 해석하지 않는다.

시뮬레이션의 19개 세부 작업 예:

| 그룹 | 작업 | 실제 역할 계약의 예 | 기본 산출물 연결 |
| --- | --- | --- | --- |
| 01 | 1 원본 등록, 2 사업 목표, 3 요구사항 정리 | orchestrator / po / spec | inputs/, docs/01-discovery.md, registers/requirements.csv |
| 02 | 4 평가 전략, 5 정성제안서, 6 발표·대본, 7 제출 점검 | proposal-strategist / proposal-producer | docs/02-proposal.md, docs/15–18, proposal/ |
| 03 | 8 착수, 9 기능·IA, 10 기술/API 설계, 11 데이터 모델, 12 디자인 시스템·화면·카피, 13 프론트 구현, 14 백엔드 구현 | orchestrator / spec·ia / architect / database / design-system·ui·ux-writer / frontend / backend | docs/04–09, technical.source_roots 아래 실제 코드 |
| 04 | 15 QA, 16 보안·코드 리뷰, 17 배포·복구 | qa / security·reviewer / devops | docs/10–11, 테스트·검토·배포 근거 |
| 05 | 18 인수·매뉴얼, 19 운영·변경 | technical-writer / devops·orchestrator | docs/12–14, HANDOFF.md, 관련 원장 |

이 표는 기본 산출물과 역할에 대응한 설계 예다. API/SQL/화면 코드 파일은 실제 생성된 경로나 명시한 예정 산출물로 표시한다. 현재 없는 erd_schema.sql 등을 기존 실적·산출물로 꾸미지 않는다.

## 7. 작업 지도

- 기본은 읽기 쉬운 2D Task–Artefact–Agent 관계 보기. Cytoscape.js로 선택 항목의 1-depth를 강조하고 한 단계 더 확장해 2-depth를 탐색한다.
- 요청한 3차원적 탐색은 선택형 3D 모드로 분리한다. 자동 회전 없이 라벨은 읽을 수 있게 고정하고, 2D 복귀·관계 목록을 제공한다.
- produces / references / depends_on / validates의 유형을 구분한다. 관계 라벨은 선택·hover·키보드 포커스 시 표시한다.
- 같은 프로젝트·상대경로의 파일은 한 노드로 합치고, 버전은 Inspector에서 비교한다. 같은 역할의 다른 작업자는 실제 실행 ID로 구분한다.
- 실제 생성 관계는 유효한 출력·해시/실행 근거, 검증 관계는 명시한 검토 대상·검증 기록이 있을 때 표시한다. 단순히 역할이나 REQ가 같다는 이유만으로 의존·검증 관계를 만들지 않는다.
- 각 연결은 원장 행·RUN/STEP·파일 버전 등 근거를 열 수 있다. 추정 관계는 기본 화면에서 증명된 관계와 섞지 않는다.

## 8. WBS / Gantt

- 한 가상 행 모델을 좌측 트리·우측 타임라인이 공유한다. 행 높이 44px, 작업명 고정·선택 행 일치, 시간 영역 가로 스크롤.
- Phase → TASK 계층, 접기/펼치기, 담당·상태·기한·진척. 일/주/월 눈금과 사용자의 시간대 기준 현재 선.
- 예정 일정·실제 실행 구간·기준 일정을 다른 표식으로 보여준다. 예정 시작/종료가 없으면 일정 미정 레인에 둔다.
- 진행률은 유효 완료 STEP/전체 STEP 또는 명시한 TASK 가중치 등 분모를 표시한다. stale은 완료에서 제외한다. 근거 없는 42%·90%를 채우지 않는다.
- 실제 병목은 승인·미충족 의존성·오류에서 도출한다. 기간 데이터가 없으면 임계 경로라고 단정하지 않는다.

## 9. 공통 Inspector

모든 뷰는 selectedEntity와 같은 snapshot revision을 공유한다. 뷰를 바꿔도 선택과 Inspector 탭을 유지한다. 파일/작업자에서 WBS로 이동하면 관련 TASK를 강조하고, 관계가 여러 개면 선택 목록을 제공한다.

탭: 이음 Diff / 연결 정보 / 일정·리소스 / 실시간 로그 / 가이드라인.

- Diff 기본은 unified. 넓게 보기에서 나란히 비교. 파일·검토 대상 버전·해시·추가/삭제 수를 표시한다.
- 비교 기준이 없으면 신규 파일/기준 없음으로 표시한다. DOCX/PPTX/PDF는 제작 도구가 남긴 페이지 렌더링·버전·텍스트를 제공하고 이진 파일을 가짜 코드 Diff로 표현하지 않는다.
- 연결 정보는 직접 의존과 영향받는 후속 작업을 구분한다.
- 로그는 타임스탬프·실제 실행자·근거·출처를 표시하고 상세 로그를 접을 수 있다. 토큰/모델 정보는 수집됐을 때만 표시한다.
- 하단 고정 액션: 수정 지시(사유·대상·수용 기준), 수정 요청, 승인 후 계속. 대상 Gate·산출물 버전·영향 작업 수를 명시한다.
- 실제 Gate가 없는 항목·미연결 실행기·오래된 revision에서는 할 수 있는 행동과 제한 이유를 보여준다.

## 10. 승인과 피드백의 실제 동작

기존 stage review를 개별 산출물 승인으로 오용하지 않는다. 개별 Gate ID·대상 파일 해시·사람 결정·근거·시간·실행 연결을 기록하는 모델을 추가한다.

1. 사용자가 본 Diff·대상 해시·expected_revision을 검증한다.
2. 실제 사용자 결정과 근거를 append-only로 기록한다. AI accept/revise/blocked와 사람 approved/rejected를 별도 상태로 표시한다.
3. 승인 후 준비된 작업을 다시 계산하고 연결된 호스트에 실행 요청을 보낸다. 호스트의 수신·실행 시작 확인 전에는 실행 중으로 꾸미지 않는다.
4. 전체 단계의 완료 기준을 충족했을 때만 기존 advance를 별도로 허용한다. 한 파일 승인으로 업무 단계 전체를 넘기지 않는다.
5. 수정 요청은 대상 버전·사유·관련 REQ와 새 TASK/실행 시도로 연결한다. 영향받는 후속 작업만 대기시키며 과거 결과를 삭제하지 않는다.
6. 새 내용으로 바뀐 파일은 과거 승인을 현재 승인으로 사용하지 않는다. 오래된 화면의 승인·중복 클릭은 서버에서 재검증/멱등 처리한다.

실행기가 연결되지 않으면 승인 저장과 다음 작업 실행 대기를 구분한다. 공개 시뮬레이션에서는 같은 흐름을 가상 상태에만 적용하고 모드를 계속 표시한다.

## 11. 데이터·호스트 연결 구조

공통 Snapshot DTO: projectId, revision, capturedAt, mode, 활성 프로필/단계/표시 그룹, REQ/TASK/DEL/INPUT/REVIEW, RUN/STEP, 명시 관계·근거, Gate, 일정, 실제 실행자, usage, capabilities.

공통 Action DTO: actionId/idempotencyKey, projectId, expectedRevision, targetId/targetHashes, decision/feedback, actor/evidence, 실행 요청 범위.

- SimulationAdapter: 명확한 가상 fixture, 승인 정지·병렬 분기·QA 반려·재작업·재검증 시나리오. 새로고침/초기화 지원.
- SnapshotAdapter: 엔진이 내보낸 제한 DTO를 로컬에서 표시한다. 읽기 전용 상태로 변경·실행 버튼을 제한한다.
- LocalProjectAdapter: loopback 서버가 프로젝트를 명시 등록하고 기존 잠금·revision·해시 규칙으로 조회/액션을 처리한다. 파일 변화·SSE 또는 폴링으로 갱신 시각을 표시한다.
- HostAdapter: 현재 Codex/Claude 호스트의 실제 작업자 ID·역할·작업 연결·시작/완료/heartbeat·결과를 표준 이벤트로 전달하고 승인 후 준비 작업을 받아 실행한다. 초기 미연결 상태와 수신 대기를 지원한다.

TASK–STEP–DEL 연결, 계층·예정 일정, 개별 Gate·피드백, 실제 실행자/usage는 현재 schema에 없는 확장이다. 기존 고객 CSV를 덮어쓰지 않는 별도 메타데이터와 검증·이행 경로를 설계한다.

로컬 브리지는 127.0.0.1 바인딩, 명시 등록한 프로젝트의 confined 상대경로, Origin/세션/CSRF 검증, 허용 액션만 사용한다. 임의 셸 명령·경로를 UI에서 실행하지 않는다. 사람 승인 권한과 에이전트의 작업 보고 권한을 구분한다. 공개 데모가 고객 로컬 API에 자동 연결되게 하지 않는다.

## 12. 권장 구현 구성과 순서

- 기존 Astro 페이지의 별도 workspace 경로 + @astrojs/react 통합.
- WorkflowView: @xyflow/react + elkjs.
- KnowledgeMapView: Cytoscape.js, 선택형 3D 어댑터는 지연 로딩.
- WbsView: React 트리와 CSS Grid/SVG 타임라인, 공통 행/스크롤 모델.
- WorkspaceStore: 3뷰·Inspector·필터·선택·capabilities가 공유하는 상태.
- 타입/상태 매핑/데이터 어댑터를 시각 컴포넌트와 분리한다.
- UI 빌드 결과는 검증된 자산 manifest/해시와 함께 패키징하여 일반 사용자가 Node·셸·CSV 편집을 일상 절차로 수행하지 않게 한다.

구현 순서:

1. 공통 셸·디자인 토큰·동일 데이터의 세 뷰·19노드 시뮬레이션·Inspector를 완성한다.
2. 기존 원장/run의 읽기 DTO와 빈/미집계/일정 미정 상태를 연결한다.
3. 개별 Gate·버전 Diff·피드백 모델과 revision/멱등 검증 로컬 액션을 추가한다.
4. 실제 호스트 실행 이벤트·승인 후 실행 요청·필요한 범위의 usage 어댑터를 연결한다.
5. 기존 엔진 회귀·브라우저·모바일·키보드·성능·공개 데모/고객 자료 분리를 검증한 뒤 배포한다.

완료 기준: 세 뷰가 같은 상태·선택·버전을 표시, 승인 대기 클릭 시 해당 노드 포커스, 승인/수정/재작업 루프 정상, 오래된 Diff의 승인 거부, 현재 근거가 바뀐 결과의 재검증, 실제 미연결 상태 표시, 비용·일정 누락 시 허위 수치 없음, 320px/태블릿/데스크톱에서 가독성·키보드/터치·reduced-motion 대응.

## 13. 참고 근거

- 브랜드: website/src/styles/tokens.css, fonts.css, studio.css, widgets.css, mobile.css, components/Brand.astro.
- 5그룹: website/src/components/ProjectExplorer.astro.
- 원장·단계: tools/starter.py, workflow.py, blueprint/registers/*.csv.
- 실행·승인·변경: tools/runs.py, lifecycle.py, history.py, dashboard.py.
- 역할·자동 실행: skills/ieum/references/roles.json, AUTO-EXECUTION.md, COLLABORATION.md.
- React Flow 레이아웃: https://reactflow.dev/learn/layouting/layouting
- Cytoscape.js 관계 탐색: https://js.cytoscape.org/#collection/traversing
- Astro React 통합: https://docs.astro.build/en/guides/integrations-guide/react/

이 문서는 설계 제안이다. 대시보드 기능·로컬 액션·실시간 수집이 현재 구현되어 있다고 주장하지 않는다.
