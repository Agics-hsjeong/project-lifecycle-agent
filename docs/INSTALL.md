# 설치 후 사용

Project Lifecycle Agent는 Codex·Claude Code에서 자연어로 프로젝트를 운영하는 로컬 실행용 플러그인입니다. 설치 이후에는 프로젝트 작업 폴더에서 자료와 목표를 전달하면 됩니다.

## 최초 설치

공개 저장소: [Agics-hsjeong/project-lifecycle-agent](https://github.com/Agics-hsjeong/project-lifecycle-agent). 다음은 개발자/관리자를 위한 1회 설치 방법이며, 매 프로젝트마다 실행하는 명령이 아닙니다.

### Codex

```text
codex plugin marketplace add Agics-hsjeong/project-lifecycle-agent
codex plugin add project-lifecycle-agent@project-lifecycle-marketplace
```

앱에서는 등록된 marketplace를 플러그인 목록에서 선택해 설치할 수 있습니다. 설치 후 새 채팅에서 스킬 로딩을 확인합니다.

### Claude Code

```text
/plugin marketplace add Agics-hsjeong/project-lifecycle-agent
/plugin install project-lifecycle-agent@project-lifecycle-marketplace
```

설치 후 새 세션에서 스킬을 확인합니다. 회사의 호스트 설정·신뢰 정책에 따라 최초 설치 시 사용자 확인이 필요할 수 있습니다.

## 일상 사용

```text
이 RFP로 새 사업을 시작해줘.
외주 사업으로 등록하고 원본·요구사항·고객 질문·제안 준비 초안을 만들어줘.
프로젝트 생성과 기록 관리는 네가 처리해줘.
```

```text
이 프로젝트를 이어서 진행해줘.
고객 답변을 반영하고 다음 단계의 산출물을 작성해줘.
```

자동 선택이 되지 않으면 Codex에서 `$project-lifecycle`, Claude Code에서 `/project-lifecycle-agent:project-lifecycle`를 명시합니다.

## 처음 확인할 결과

- 현재 작업 공간 안에 새 프로젝트가 만들어졌는지 확인합니다.
- RFP 원본이 보존되고 입력 자료에 출처가 기록되었는지 확인합니다.
- 사업 검토서·요구사항 원장·HANDOFF.md가 실제 자료에 따라 작성되었는지 확인합니다.
- 미확정 가격·일정·담당자·범위를 AI가 임의로 승인하지 않았는지 확인합니다.

Python 3.10 이상이 있으면 생성·점검 도구를 에이전트가 내부 실행합니다. Python이 없는 환경에서는 스킬이 사용 가능한 파일 도구로 기본형을 복사하는 대체 흐름을 안내합니다. 파일 접근·외부 서비스 권한은 호스트 설정을 따릅니다.
