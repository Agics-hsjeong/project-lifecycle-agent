---
title: Claude Code에 설치하기
description: Claude Code 대화 입력창에서 프로젝트이음을 설치하고 사용합니다.
---

일반 Claude 웹 채팅이 아닌 **Claude Code**에서 사용합니다. 로그인한 뒤 아래 명령을 실행하세요.

## 대화 입력창에 한 줄씩 입력하세요

```text
/plugin marketplace add Agics-hsjeong/project-lifecycle-agent
/plugin install project-lifecycle-agent@project-lifecycle-marketplace
```

위 명령은 PowerShell에 입력하는 명령이 아닙니다. **Claude Code의 대화 입력창**에서 실행합니다.

설치 범위를 선택하는 화면이 나오면, 여러 프로젝트에서 개인적으로 사용할 때에는 **User**를 선택합니다. 회사에서 범위를 관리하는 경우 해당 정책을 따릅니다.

## 설치 후 새 세션을 시작하세요

`/plugin`에서 설치·활성화 상태를 확인할 수 있습니다. 새 세션을 시작하고 프로젝트 업무를 요청합니다. 자동 선택되지 않을 때에는 다음 스킬을 명시합니다.

```text
/project-lifecycle-agent:project-lifecycle
```

[첫 프로젝트 만들기](../../first-project/)의 요청문으로 시작하세요.

## Codex와 같이 쓸 때

두 제품에 각각 설치하고 같은 고객 프로젝트 폴더를 열면 됩니다. 공유되는 것은 파일과 관리 기록이며 대화 내용은 자동 동기화되지 않습니다.

[AI 간 이어하기](../../guides/handoff/)

## 설치 이름

브랜드는 프로젝트이음이며 설치 ID는 `project-lifecycle-agent@project-lifecycle-marketplace`입니다. 기존 사용자는 같은 ID의 업데이트를 사용합니다.

[Claude Code 공식 설치 문서](https://code.claude.com/docs/en/discover-plugins) · [업데이트·문제 해결](../../reference/troubleshooting/)
