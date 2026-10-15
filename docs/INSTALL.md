# 설치·업데이트 상세 안내

처음 사용하는 경우 [README의 설치부터 업무까지 안내](../README.md)를 순서대로 따라가세요. 이 문서는 최초 설치와 업데이트 명령을 모아 둔 참고 자료입니다. 일상 업무는 자연어로 요청하며 에이전트가 내부 도구를 실행합니다.

## 설치 전

- Codex 또는 Claude Code가 설치되고 로그인되어 있어야 합니다.
- 저장소 설치에는 Git, 내부 도구 실행에는 Python 3.10 이상 또는 호스트 제공 런타임이 필요합니다.
- 에이전트가 실행 환경을 점검하고 사용 가능한 런타임을 찾습니다. 해결하지 못한 환경 제한만 구체적으로 보고합니다.
- 문서·발표자료 생성·렌더링은 호스트의 제작 도구 지원에 따릅니다.
- 고객 업무 폴더는 플러그인 소스·설치 캐시 밖에 둡니다.

## Codex 최초 설치

Codex에 “Agics-hsjeong/project-lifecycle-agent의 main marketplace를 등록하고 project-lifecycle-agent를 설치·확인해줘”라고 요청할 수 있습니다.

직접 실행한다면 **터미널**에서 한 줄씩 입력합니다.

```sh
codex plugin marketplace add Agics-hsjeong/project-lifecycle-agent --ref main
codex plugin add project-lifecycle-agent@project-lifecycle-marketplace
```

설치 후 새 채팅을 시작하거나 앱을 다시 엽니다. 자동 선택되지 않으면 대화 입력창에서 `$project-lifecycle`을 호출하세요. `codex` 명령이 없으면 에이전트가 CLI 위치와 설치 가능한 방법을 진단하게 하세요.

Marketplace 등록 방식: [OpenAI 공식 문서](https://developers.openai.com/plugins/build/plugins).

## Claude Code 최초 설치

**Claude Code 대화 입력창**에서 한 줄씩 입력합니다.

```text
/plugin marketplace add Agics-hsjeong/project-lifecycle-agent
/plugin install project-lifecycle-agent@project-lifecycle-marketplace
```

여러 프로젝트에서 개인적으로 사용할 경우 사용자 범위(User)에 설치합니다. 새 세션에서 사용하며, `/plugin`으로 설치·활성화 상태를 볼 수 있습니다. 명시적 스킬 호출은 `/project-lifecycle-agent:project-lifecycle`입니다.

설치 방식: [Claude Code 공식 문서](https://code.claude.com/docs/en/discover-plugins).

## 첫 실행에서 에이전트가 처리하는 일

업무 폴더를 열고 다음처럼 요청하세요.

```text
Project Lifecycle Agent로 첨부한 RFP의 외주 사업을 시작해줘.
현재 작업 폴더에 새 프로젝트와 참고 템플릿 폴더를 준비해줘.
환경 점검, 원본 등록, 요구사항·고객 질문·제안 준비 초안과 기록 관리를 처리해줘.
```

에이전트가 폴더 생성·원본 보존·입력 출처·미확정 사항·현재 상태를 확인합니다. 사용자에게 기능 테스트나 관리 파일 수동 편집을 넘기지 않습니다. 실제 업무 자료와 사업 결정은 사용자가 제공합니다.

자기 템플릿 배치, 제안서·발표자료 제작, 수주 후 관리와 AI 간 이어하기는 [README](../README.md)에 요청문과 폴더 예시가 있습니다.

## 업데이트

“설치 소스와 버전을 확인하고 최신 main으로 업데이트해줘. 기존 고객 프로젝트는 보존해줘”라고 에이전트에게 요청할 수 있습니다.

직접 관리하는 경우 다음은 모두 **터미널 명령**입니다.

Codex:

```sh
codex plugin marketplace upgrade project-lifecycle-marketplace
codex plugin list --marketplace project-lifecycle-marketplace --json
```

Claude Code 사용자 범위 설치:

```sh
claude plugin marketplace update project-lifecycle-marketplace
claude plugin update project-lifecycle-agent@project-lifecycle-marketplace --scope user
claude plugin list --json
```

다른 범위에 설치했다면 실제 설치 범위에 맞춰 업데이트합니다. 두 제품을 사용하면 각각 갱신하고 새 세션에서 로딩 버전을 확인합니다.

옛 태그(예: v0.2.1)로 설치한 marketplace는 해당 참조에 고정됩니다. 단순 새로고침만으로 main으로 전환되지 않습니다. 에이전트가 실제 설치 소스·참조를 확인한 뒤 호스트의 marketplace 설정을 전환하게 하세요. 고객 프로젝트나 원본을 삭제하는 방식으로 해결하지 않습니다.

플러그인 업데이트가 기존 프로젝트의 문서·원장을 자동 교체하지는 않습니다. 새 기능이 필요하면 [엔진 운영 안내](ENGINE.md)에 따라 차이·적용 범위·이행 방법을 기록하고 보존하며 적용합니다.

[GitHub Releases](https://github.com/Agics-hsjeong/project-lifecycle-agent/releases)의 태그·ZIP은 고정 배포본입니다. 최신 main 코드와 버전이 다를 수 있습니다.
