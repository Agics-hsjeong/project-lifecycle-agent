---
title: 업데이트와 문제 해결
description: 설치 소스·버전, 파일 접근, 제작 도구와 복구 문제를 에이전트와 해결합니다.
---

## 업데이트도 대화로 요청할 수 있습니다

```text
Project Lifecycle Agent의 설치 소스와 버전을 확인하고 최신 main으로 업데이트해줘.
기존 고객 프로젝트와 원본 자료는 보존해줘.
새 양식이 필요하면 차이·적용 범위·이행 방법을 먼저 정리해줘.
```

두 제품은 각각 업데이트하고 새 세션에서 사용합니다. 옛 태그로 설치된 marketplace는 단순 새로고침만으로 main으로 전환되지 않습니다. 실제 설치 참조·로딩 버전을 확인하게 하세요.

## 증상별 요청

| 증상 | 요청할 내용 |
| --- | --- |
| 스킬이 안 보임 | 설치·활성화·로딩 버전 진단. 새 세션에서 명시적 스킬 호출 |
| 옛 버전만 보임 | marketplace 태그 고정과 main 전환 방법 확인 |
| RFP·템플릿을 못 읽음 | 실제 경로·파일 권한·형식·OCR 도구와 읽지 못한 부분 확인 |
| 스타일이 기대와 다름 | 대표 파일·페이지·슬라이드와 원하는 특징을 지정해 재비교·수정 |
| 작업 중 종료됨 | 현재 상태·복구 필요를 진단하고 사용자 변경을 보존하며 해결 |
| 다음 단계로 이동 못함 | 미완료 산출물·검토·변경된 승인 근거를 확인 |

## 직접 관리하는 경우의 터미널 명령

아래는 선택적인 설치 관리자용 참고입니다. 일상 업무에서는 에이전트가 처리합니다.

Codex:

```sh
codex plugin marketplace upgrade project-lifecycle-marketplace
codex plugin list --marketplace project-lifecycle-marketplace --json
```

Claude Code 사용자 범위:

```sh
claude plugin marketplace update project-lifecycle-marketplace
claude plugin update project-lifecycle-agent@project-lifecycle-marketplace --scope user
claude plugin list --json
```

실제 설치 범위에 맞춰 갱신합니다. Releases의 ZIP은 특정 시점의 고정 배포본으로 main보다 이전일 수 있습니다.

## 안전하게 진단하기

오류 해결을 위해 고객 프로젝트나 캐시를 임의로 삭제할 필요는 없습니다. 실제 오류와 작업 경로를 전달해 진단하게 하세요. 에이전트가 해결 가능한 환경 준비를 먼저 처리하고 해결하지 못한 제한을 구체적으로 보고합니다.

여전히 문제가 있으면 [GitHub 이슈](https://github.com/Agics-hsjeong/project-lifecycle-agent/issues/new/choose)에 제품·버전·재현 단계·오류를 남겨주세요. 고객 원본·개인 정보·인증 정보는 제외합니다.
