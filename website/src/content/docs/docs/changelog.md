---
title: 변경 이력
description: 프로젝트이음의 브랜드·문서 사이트와 플러그인 기능 변경을 확인합니다.
---

## 2.0.0-beta.7 — Windows 관측 파일 경합 처리

2026-11-22. 관리 native 읽기·쓰기를 같은 잠금으로 조율하고 관측 저장을 업무 변경 저널과 분리합니다. 짧은 Windows 공유 충돌은 유한 재시도하며 장기 오류는 보존·보고합니다. 실패 이벤트·로그 커서는 후속 수신에서 다시 처리합니다. 업무 커밋 마지막의 저널 삭제도 정확한 경로의 짧은 Windows 공유 충돌만 재시도하고 지속 오류에는 저널을 보존합니다. 실제 역할 실행·작업자 ID·원장 검증의 구분은 유지합니다.

beta.6 실제 할인 기능은 4/4 작업을 완료했지만 관제 갱신은 두 차례 끊겼습니다. 기능 성공과 관제 연속성을 구분하며 [원인 재현·후속 검증 기록](https://github.com/Agics-hsjeong/project-lifecycle-agent/blob/main/docs/WORKSPACE-OBSERVATION-VALIDATION.md)을 확인하세요.

첫 beta.7 실제 재검증에서는 저널 삭제 경합과 개별 산출물 읽기 거부로 HTTP 400이 총 78회 발생했습니다. 해당 실행은 관제 연속성 통과로 계산하지 않습니다. 저널 정리는 짧은 충돌만 재시도하고, 읽을 수 없는 산출물은 접근 불가·버전 미확인으로 표시해 다른 작업과 로그 조회를 유지합니다. 완료·승인 검증은 완화하지 않습니다.

[관제 사용 가이드](../guides/workspace/)

최종 실제 Codex 후속 작업은 세 역할 자동 실행과 3/3 검증 완료를 확인했습니다. 파일 읽기 부하 중 622회 조회에서 621회가 200이고, 업무 잠금 409 한 번은 1.329초 뒤 자동 회복했습니다. 400·5xx·전송 오류와 수동 복구는 없었습니다. Windows 전체 회귀 572개(플랫폼·권한 생략 7개)와 실제 관제 화면 대조 110개를 통과했습니다.

## 2.0.0-beta.6 — 실제 작업자·실행 범위 연결

2026-11-22. 호스트의 실제 native 작업자 ID와 시작·종료 이벤트를 TASK 위임에 연결합니다. 실행 종료와 검증 완료를 구분하며 최근 실행·전체 기록을 나눕니다. 이전 실패·변경된 증거는 남겨 두고 다음 행동이 완료 메모로 바뀌어도 작업 제목은 유지합니다. 새 RUN·TASK 바인딩·실행 범위는 원자적으로 저장합니다.

[관제 사용 가이드](../guides/workspace/) · [beta.6 다운로드](https://github.com/Agics-hsjeong/project-lifecycle-agent/releases/tag/v2.0.0-beta.6)

## 2.0.0-beta.5 — 검증·보호·관제 품질 개선

2026-11-22. 검증 대상과 반영 파일의 해시 계약, 읽기 전용 코드 검증, 최소 입력·민감정보 보호, 승인 의존성 환경을 적용했습니다. 관제의 실제 실행·실패·제외·검증 출처·부분 사용량 표시와 산출물 받기·모바일 세 가지 뷰를 개선했습니다.

실제 Codex·Claude 생성 경계, FastAPI 격리 스키마 5개·별도 로컬 ASGI 5개, TypeScript 컴파일과 실제 서버 UI를 확인했습니다. Windows 격리의 ASGI 지원 한계와 관측 기반 예산 범위는 [검증 기록](https://github.com/Agics-hsjeong/project-lifecycle-agent/blob/main/docs/QUALITY-IMPROVEMENTS.md)에 구분했습니다.

[beta.5 다운로드](https://github.com/Agics-hsjeong/project-lifecycle-agent/releases/tag/v2.0.0-beta.5)

## 2.0.0-beta.4 — 관제와 자동 실행 안정화

역할별 실제 활동·워크플로우·작업 지도·타임라인을 제공하고, OS 격리 검증과 실행 복구·입력 무결성·관제 갱신을 강화했습니다. 실제 Codex·Claude 백엔드→QA 자동 실행을 검증했습니다. Windows 독립 검증은 MXC가 필요합니다.

[beta.4 다운로드](https://github.com/Agics-hsjeong/project-lifecycle-agent/releases/tag/v2.0.0-beta.4)

## 2.0.0-beta.2 — 자동 역할 실행

2026-11-19. 팀이라는 추가 요청 없이 필요한 역할·작업자를 선택해 실행합니다. 18개 역할 계약, Codex `$ieum`과 Claude 프로젝트별 `/ieum` 별칭, 기존 설정 보존·실행 증거·인수인계를 제공합니다.

Codex CLI 0.170.0과 Claude Code 2.1.327에서 자동 전문 작업자 실행과 새 세션 이어하기를 확인했습니다. 생성 프로젝트 테스트는 각각 12개·9개, 저장소 전체 회귀는 114개 통과했습니다. [역할 사용 가이드](../guides/agents/)와 [실제 검증 범위·제한](https://github.com/Agics-hsjeong/project-lifecycle-agent/blob/main/docs/HOST-VALIDATION.md)을 확인하세요.

[beta.2 다운로드](https://github.com/Agics-hsjeong/project-lifecycle-agent/releases/tag/v2.0.0-beta.2)

## 2.0.0-beta.1 — 베타

2026-11-03. 실행 프로필·릴리스 범위·작업 재개·관리 변경 복원·출력 검수·로컬 현황, 기존 프로젝트의 이행 미리보기·관리정보 백업·되돌리기를 제공합니다. 설치본의 경로·버전·패키지 무결성을 진단합니다. [v2 사용과 업데이트](../guides/v2/)를 참고하세요.

[베타 릴리스와 다운로드](https://github.com/Agics-hsjeong/project-lifecycle-agent/releases/tag/v2.0.0-beta.1)를 확인하세요. 기존 안정판과 구분해 사용하고 실제 설치 버전은 에이전트가 확인합니다.

## 프로젝트이음 브랜드와 공개 사이트

2026-10-28 작성 기준입니다. 표시 브랜드를 프로젝트이음(Project Ieum)으로 정리하고 소개, 설치·업무 가이드, 고객 포털 공개 예제, 매뉴얼 다운로드와 피드백 경로를 구성했습니다.

설치 ID와 스킬 호출은 `project-lifecycle-agent` / `project-lifecycle`을 사용합니다. 당시 플러그인 엔진의 기준 버전은 **0.5.1**이었습니다. 사이트의 설명과 실제 설치 버전이 다르면 에이전트에게 로딩 버전을 확인하게 하세요.

## 0.5.1 - 첫 사용 준비

사용자 프로젝트의 참고 폴더·환경·자료 상태와 다음 작업을 준비합니다. 원본·분석 근거의 변경과 복구 필요를 확인합니다. 환경 점검과 문서·발표 비교 검수는 에이전트가 수행합니다.

## 0.5.0 - 과거 템플릿 활용

정성제안서·발표자료의 원본·버전·선택·스타일 분석·작업 사본과 생성본 비교 검수를 관리합니다.

## 0.4.0 - 제안 패키지

정성제안서, 평가 대응표, 발표자료, 대본·예상 Q&A와 제출 점검을 별도 산출물로 관리합니다.

## 0.3.0 - 실행 엔진

원본 입력, 원장·ID·관계, 검토·단계 이동, 변경 기록·복구와 인수인계 기능을 추가했습니다.

전체 이력과 실제 배포본은 [저장소 CHANGELOG](https://github.com/Agics-hsjeong/project-lifecycle-agent/blob/main/CHANGELOG.md), [Releases](https://github.com/Agics-hsjeong/project-lifecycle-agent/releases)를 확인하세요. 태그 배포본과 main의 버전은 다를 수 있습니다.
