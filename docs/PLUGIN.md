# 자연어 기반 플러그인

이 버전의 사용 흐름은 `플러그인 설치 → 자료 제공 → 자연어 요청 → 에이전트가 분석·생성·점검·기록`입니다. PowerShell은 Windows에서 내부 도구를 실행할 수 있는 수단 중 하나이며, 사용자가 매 사업마다 명령을 입력할 필요는 없습니다.

## 일상 사용

설치·활성화한 뒤 프로젝트를 저장할 작업 폴더에서 다음처럼 요청합니다.

```text
이 RFP로 A사 고객 포털 사업을 시작해줘.
외주 사업으로 등록하고 요구사항·질문·제안 준비 초안을 작성해줘.
폴더 생성과 원장 관리는 네가 처리해줘.
```

```text
A사 프로젝트를 이어서 진행해줘.
고객 답변을 반영하고 제안서와 공수 산정을 갱신해줘.
```

스킬이 선택되지 않으면 Codex에서는 `$project-lifecycle`, Claude Code에서는 `/project-lifecycle-agent:project-lifecycle`로 명시적으로 호출할 수 있습니다. 자동 선택은 호스트와 설정에 따라 다르므로 플러그인 설치·활성화와 현재 작업 폴더를 먼저 확인합니다.

## 구성

- 공통 스킬: skills/project-lifecycle/SKILL.md
- 공통 프로세스·템플릿: blueprint/
- 공통 자동화: tools/starter.py
- portable manifest: plugin.json; Codex 호환 manifest: .codex-plugin/plugin.json
- Claude Code manifest·카탈로그: .claude-plugin/
- Codex 카탈로그: .agents/plugins/marketplace.json

저장소 루트의 `.agents/plugins/marketplace.json`과 `.claude-plugin/marketplace.json`을 각각 사용합니다. 같은 GitHub 저장소를 두 호스트의 설치 소스로 등록할 수 있도록 구성했습니다. 배포 ZIP도 같은 루트 구조를 포함합니다.

현재 패키지는 전체 폴더를 설치하는 로컬 실행용 플러그인입니다. 스킬 폴더만 따로 설치하는 방식, 공개 디렉터리 제출용 skills-only 업로드는 검증 범위가 아닙니다.

## 최초 설치와 공개

최초 설치에는 사용하는 호스트에서 플러그인 소스를 등록하고 설치·활성화하는 과정이 필요합니다. GitHub 공개 저장소에 올린 것만으로 모든 사용자의 플러그인이 자동 설치되거나 공식 디렉터리에 등록되지는 않습니다.

Claude Code는 자체 marketplace를 등록한 뒤 `/plugin`에서 설치할 수 있습니다. Codex는 지원되는 로컬/저장소 marketplace를 등록한 뒤 앱의 플러그인 목록에서 설치·시험합니다. 등록·설치 자동화가 가능한 환경에서는 에이전트가 도와줄 수 있고, 로그인과 호스트가 요구하는 신뢰 확인은 사용자 계정에서 진행합니다.

저장소 이름은 project-lifecycle-agent, 연결된 GitHub 계정은 Agics-hsjeong, 라이선스는 MIT입니다. 실제 공개·호스트 설치 결과는 VALIDATION.md에서 확인합니다. 공식 디렉터리 제출은 별도 후속 절차입니다.

## 다른 방식

| 방식 | 맞는 상황 | 이 프로젝트에서의 위치 |
| --- | --- | --- |
| 스킬만 배포 | 간단한 반복 업무, 최소 구성 | 지침만 재사용할 때 |
| 스킬 기반 플러그인 | 여러 프로젝트에 설치·업데이트·재사용 | 현재 구현 방향 |
| MCP 추가 | 여러 AI가 동일한 DB·프로젝트 상태·외부 서비스를 조작 | 조직 공유 관리가 필요할 때 확장 |
| 독립 서비스·웹 화면 | AI를 켜지 않아도 실행, 다중 사용자·알림·대시보드 | 서버·인증·작업 큐를 포함한 후속 제품 |

플러그인과 MCP는 함께 사용할 수 있습니다. 플러그인은 업무 흐름을 제공하고 MCP는 실제 도구와 공유 데이터를 제공하는 식입니다. 현재 버전은 호스트 AI가 활성화된 세션에서 실행하며 상시 백그라운드 작업을 자체 제공하지 않습니다.

공식 참고: [OpenAI 플러그인 패키징](https://developers.openai.com/plugins/build/plugins), [Claude Code 플러그인](https://code.claude.com/docs/en/plugins), [Claude marketplace](https://code.claude.com/docs/en/plugin-marketplaces).
