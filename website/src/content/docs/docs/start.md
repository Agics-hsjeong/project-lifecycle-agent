---
title: 처음 시작하기
description: 프로젝트이음을 설치하고 자기 자료로 첫 프로젝트를 시작하는 전체 흐름.
---

프로젝트이음(Project Ieum)은 **제안부터 운영까지, 프로젝트의 모든 단계를 잇는 오픈소스 에이전트**입니다. Codex와 Claude Code에서 사용할 수 있고 외주/SI와 자체 서비스에 모두 적용합니다.

사용자는 업무 자료·목표·원하는 스타일과 사업 결정을 전달합니다. 에이전트가 폴더 생성, 원본 등록, 분석, 제작, 검수와 진행 기록을 처리합니다. 일상 업무마다 셸 명령이나 JSON·CSV를 직접 편집할 필요는 없습니다.

## 1. 사용하는 AI에 한 번 설치하세요

- [Codex 설치](../install/codex/)
- [Claude Code 설치](../install/claude/)

두 제품을 오가려면 각각 설치합니다. 설치 이름은 기존 사용자와 연결되는 `project-lifecycle-agent`, marketplace는 `project-lifecycle-marketplace`입니다.

## 2. 자기 업무 폴더를 여세요

플러그인 설치 폴더 밖에 `내 프로젝트/`처럼 업무용 폴더를 준비하고 Codex 또는 Claude Code에서 엽니다. 새 프로젝트는 그 안의 `projects/<프로젝트 식별자>/`에 생성됩니다.

## 3. RFP나 아이디어를 전달하세요

```text
Project Lifecycle Agent로 첨부한 RFP의 외주 사업을 시작해줘.
현재 작업 폴더에 프로젝트와 참고 템플릿 폴더를 준비해줘.
환경 점검, 원본 등록, 요구사항·고객 질문·제안 준비와 기록 관리를 처리해줘.
```

프로젝트이음의 스킬 이름은 `project-lifecycle`입니다. 자동으로 선택되지 않으면 Codex에서 `$project-lifecycle`, Claude Code에서 `/project-lifecycle-agent:project-lifecycle`를 호출합니다.

## 4. 과거 자료의 스타일을 활용하세요

프로젝트의 `templates/qualitative/`에 정성제안서를, `templates/presentation/`에 발표자료를 넣고 대표 파일을 알려주세요. 여러 예시가 있을 때에는 원하는 스타일을 각각 선택할 수 있습니다.

[첫 프로젝트 따라 하기](../first-project/) · [자기 템플릿 활용](../guides/templates/)

## 5. 같은 프로젝트에서 다음 단계로 이어가세요

정성제안서·발표 제작 후 수주하면 계약·협상 결과를 등록합니다. 착수부터 설계·구현·검증·배포·납품·유지보수까지 같은 기록을 사용합니다.

**사업 검토 → 제안 → 착수 → 기획·설계 → 디자인 → 퍼블리싱 → 구현 → 검증 → 배포 → 납품 → 유지보수**

## 알아두면 좋은 범위

- 호스트가 제공하는 Python 3.10 이상 런타임을 사용합니다. 에이전트가 실행 환경을 점검합니다.
- 실제 문서·발표 제작과 렌더링은 호스트의 도구 지원에 따릅니다. 지원하지 않는 형식은 미완료로 명시합니다.
- 실제 예산·인력·계약·검수 판단은 사용자가 제공합니다. AI가 사람의 승인을 만들어 기록하지 않습니다.
- 웹사이트는 소개·문서·예제를 제공합니다. 실제 고객 자료 작업은 사용자의 Codex·Claude Code 세션에서 수행합니다.

[공개 예제로 살펴보기](../examples/portal/) · [사용자 매뉴얼 PDF 다운로드](/project-lifecycle-agent/downloads/project-ieum-user-guide.pdf)
