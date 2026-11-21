# 이음 명령과 자동 역할 실행

프로젝트이음은 **프로젝트 스타트팩 에이전트**입니다. 원하는 결과와 자료를 전달하면 요청·현재 단계·기존 산출물을 보고 필요한 역할과 실행 순서를 선택합니다. 사용자가 역할명을 알거나 “팀으로 처리해줘”라고 덧붙일 필요가 없습니다. 사업 결정과 사람의 승인 권한은 사용자에게 있습니다.

이 안내는 **2.0.0-beta.5 기준**입니다. 이전 `v2.0.0-beta.1` ZIP에는 이음 진입점·역할 에이전트가 포함되지 않습니다. 실제 호스트 실행과 공개 배포 결과는 아래 검증 범위와 별도 릴리스 기록으로 구분합니다.

## 원하는 결과를 요청하세요

```text
이음 첨부 RFP로 제안서와 발표자료를 만들어줘.
이음 MVP 범위와 기능 정책, 화면 흐름을 구체화해줘.
이음 문의 등록·조회 기능을 구현하고 테스트해줘.
이음 출시 전에 결함과 보안 문제를 점검해줘.
이음 이어서 해줘. 실제 결과와 미완료부터 확인해줘.
```

“이음 시작” 같은 문장은 자연어 요청입니다. 별도로 설치하는 한국어 슬래시 명령이 아닙니다. 명시적으로 호출하려면 다음 진입점을 사용하세요.

| 호스트 | 쉬운 진입점 | 별칭 준비 전·기존 호출 |
| --- | --- | --- |
| Codex | `$ieum` | 기존 `$project-lifecycle`도 지원 |
| Claude Code | **`/ieum`** — 고객 프로젝트별 별칭 설정 후 | `/project-lifecycle-agent:ieum`, 기존 `/project-lifecycle-agent:project-lifecycle` |

플러그인 설치 ID `project-lifecycle-agent`와 marketplace `project-lifecycle-marketplace`는 유지합니다. 첫 사용에는 **“프로젝트이음으로 시작하고 짧은 명령도 준비해줘”**라고 자연어로 요청하세요.

Claude Code에서는 에이전트가 실제 고객 프로젝트에 `.claude/skills/ieum/SKILL.md`를 준비합니다. 생성된 고객 프로젝트 폴더를 Claude Code 작업 폴더로 열고 새 세션의 명령 목록에서 확인하세요. 플러그인 설치만으로 짧은 별칭이 자동 등록됐다고 안내하지 않습니다.

```text
/ieum 제안서를 만들어줘
/ieum 문의 등록·조회 기능을 구현하고 테스트해줘
/ieum 배포 전에 점검해줘
/ieum 이어서
```

별칭은 요청을 설치된 플러그인 스킬에 그대로 전달합니다. 별칭에 엔진·고객 데이터를 복제하지 않고 기존 동명 스킬이나 `.claude/commands/ieum.md`를 덮어쓰지 않습니다. 개인·관리자 스킬과 명령 우선순위는 실제 호스트에서 확인합니다.

## 자동으로 역할과 실행 방식을 선택합니다

주 에이전트가 요청 범위·현재 단계·선행 결과·수용 기준을 확인하고 필요한 최소 역할을 정합니다.

| 작업 | 실행 방식 |
| --- | --- |
| 질문·도움말·현황 조회·간단한 수정 | 현재 에이전트가 직접 수행 |
| 여러 전문 영역에 걸친 구현·제안 제작 | 필요한 역할로 분리하고 실제 하위 에이전트 도구로 위임 |
| 작성 결과의 독립 검토가 필요한 작업 | QA·보안·리뷰 중 필요한 역할에 별도 검토 위임 |
| 선행 입력·공유 계약이 정해지지 않은 작업 | 입력·계약부터 정리하고 의존 순서에 따라 실행 |
| 호스트가 위임을 지원하지 않거나 인증·권한·한도에 막힌 작업 | 실제 제한과 대체 수행 방식을 보고하고 가능한 작업 계속 |

예를 들어 “문의 등록·조회 기능을 구현하고 테스트해줘”라는 요청은 기존 API·데이터 계약 확인 → 필요한 백엔드·프론트엔드 작업 → QA·리뷰 → 결과 통합으로 나눌 수 있습니다. 간단한 한 파일 수정에 모든 역할을 실행하지 않고, “기획만” 요청에 개발·배포를 추가하지 않습니다.

역할 전환만 한 경우와 별도 에이전트를 실제로 실행한 경우를 구분합니다. 역할 계획을 나열하거나 파일을 생성한 것만으로 실행·완료를 주장하지 않습니다.

## 역할 구성

16개 전문 역할과 제안 전략·제안서/발표자료 제작 역할 2개를 제공합니다. 각 역할은 입력·산출물·검증·책임 경계·인수인계 대상을 갖습니다.

| 파트 | 역할 ID와 이름 |
| --- | --- |
| 기획·전략 | `po` PO, `spec` 기능 명세, `ia` UX 리서치·정보설계 |
| 디자인·UX | `design-system` 디자인 시스템, `ui` UI 디자인, `ux-writer` UX 카피 |
| 아키텍처·개발 | `architect` 시스템 아키텍처, `database` DB, `backend` 백엔드, `frontend` 프론트·퍼블리싱, `devops` 인프라·배포 |
| 품질·보안·운영 | `qa` 테스트, `security` 보안 감사, `technical-writer` 기술·사용자 문서 |
| 협업·검토 | `orchestrator` 작업 조율, `reviewer` 코드 리뷰 |
| 제안·발표 | `proposal-strategist` 정성제안 전략, `proposal-producer` 제안서·발표자료 제작 |

특정 모델·프레임워크·클라우드를 강제하지 않습니다. 원천은 `skills/ieum/references/roles.json`과 `COLLABORATION.md`입니다. 생성된 Claude 에이전트 파일을 직접 수정하지 말고 원천을 고친 뒤 재생성합니다.

## 협업과 검증 규칙

- 위임 전 실제 프로젝트 루트·REQ/TASK·입력 파일·기준 버전·담당 경로·기대 출력·수용 기준을 전달합니다.
- 선행 계약을 먼저 확인하고 담당 경로가 분리된 독립 작업만 병렬 실행합니다. 호스트의 실제 동시 실행 한도를 따릅니다.
- API·스키마·토큰 같은 공유 계약의 소유자를 지정하며 같은 파일을 동시에 수정하지 않습니다.
- 공통 원장·state·HANDOFF는 주 에이전트가 기존 엔진의 revision·잠금·증거 규칙으로 직렬 기록합니다. 하위 역할은 재귀 위임하지 않습니다.
- 주 에이전트가 반환된 파일·실행 결과·테스트 증거를 확인하고 미완료·다음 행동을 통합합니다. 같은 원인으로 수정·재검토가 반복 실패하면 실패 근거와 해결 조건을 보고합니다.
- AI 검토는 `accept / revise / blocked` 의견입니다. 이를 사람의 승인, 원장의 `approved`, 병합·배포 권한으로 사용하지 않습니다.

## Claude Code 연결

플러그인 루트 `agents/`에 18개 네이티브 역할 정의가 있습니다. 예를 들어 백엔드는 `project-lifecycle-agent:ieum-backend`, 리뷰는 `project-lifecycle-agent:ieum-reviewer`입니다. 업데이트된 플러그인을 활성화하고 새 세션에서 `/agents` 또는 실제 역할 목록으로 등록 여부를 확인합니다. 사용자가 매번 역할 이름을 입력할 필요는 없습니다.

`security`, `reviewer`는 읽기 도구만 허용합니다. 다른 역할은 현재 호스트의 권한을 상속하며 담당 경로 제한은 작업 계약입니다. 호스트가 역할을 찾지 못하면 실제 로딩 경로·버전·활성화 상태를 진단하고 수행한 범위와 제한을 보고합니다.

## Codex 연결

Codex가 Claude용 `agents/`를 자동으로 읽는다고 가정하지 않습니다. 네이티브 역할이 필요한 작업에서는 에이전트가 실제 고객 프로젝트의 Codex 전용 설정을 확인하고 필요한 설정을 준비합니다. 기존 `.codex/config.toml`과 다른 역할 파일·전역 설정은 변경하지 않습니다.

같은 이름의 이음 설정이 수정돼 있으면 덮어쓰지 않고 충돌·차이·보존 방법을 확인합니다. 설정 파일 추가 직후 결과의 `host_loaded`는 `false`입니다. 새 세션에서 실제 인식·호출 여부를 확인해야 합니다. 커스텀 역할을 지원하지 않는 호스트는 사용 가능한 기본 작업자에게 역할 계약을 전달하고 그 방식을 명시합니다.

Codex와 Claude Code의 대화는 자동 동기화되지 않습니다. 같은 고객 폴더와 HANDOFF로 이어갑니다.

## 개발자 확인용 명령

아래 명령은 에이전트·기여자용입니다. 일반 사용자가 매번 실행할 절차가 아닙니다.

```sh
python tools/role_agents.py list
python tools/role_agents.py show "PO"
python tools/role_agents.py build
python tools/role_agents.py build --check
python tools/role_agents.py claude-preview /actual/customer/project
python tools/role_agents.py claude-install /actual/customer/project --file /actual/preview.json
python tools/role_agents.py codex-preview /actual/customer/project
python tools/role_agents.py codex-install /actual/customer/project --file /actual/preview.json
```

호스트별 preview 결과 전체를 JSON 파일로 저장해 install에 전달합니다. 도구는 프로젝트 revision·대상 파일 해시·생성할 설정 해시를 다시 대조하고 기존 트랜잭션·복구 기록을 사용합니다. 동일 내용의 재실행은 변경하지 않습니다.

## 확인 범위와 한계

구조·계약·패키지·임시 고객 프로젝트 설치 검사는 실제 모델 품질이나 호스트 실행 검증과 별개입니다. 실제 로딩·위임·테스트·산출물은 해당 호스트의 도구 호출과 파일·로그 근거로 확인합니다. 설정의 지침은 OS 수준 권한 격리를 대신하지 않습니다. 인증·기능 지원·동시 실행 한도·제작 도구가 제한될 수 있으며 에이전트는 제한과 수행 방식을 보고합니다.

beta.2에서 전체 회귀 114개를 통과했습니다. 실제 Codex CLI 0.170.0과 Claude Code 2.1.327에서 팀 지시 없는 구현·자동 전문 작업자 실행·새 세션 이어하기를 확인했습니다. 생성 프로젝트 테스트는 각각 12개·9개 통과했고 독립 재실행도 성공했습니다. [실제 요청·근거·호스트 제한](HOST-VALIDATION.md)을 확인하세요.

공식 형식 근거: [Codex 커스텀 에이전트](https://learn.chatgpt.com/docs/agent-configuration/subagents), [Claude Code 서브에이전트](https://code.claude.com/docs/en/sub-agents), [Claude Code 스킬](https://code.claude.com/docs/en/skills).
