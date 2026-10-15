# 검증 기록

검증일: 2026-10-16 (Asia/Seoul). 대상: 플러그인 v0.5.1.

## 0.5.1 사용자 초기 준비 검증

template-onboard는 사용자 프로젝트의 폴더·환경·자료/선택/분석 상태를 조회하고 에이전트의 다음 작업을 반환합니다. 검증 책임은 agent로 기록하며 자료가 없다는 이유로 개발 테스트를 사용자에게 요구하지 않습니다. 환경·복구 문제가 있으면 먼저 에이전트 진단 대상으로 반환합니다. 고객 폴더 분리·자료 상태 전환·오래된 분석/원본 탐지·복구 상황의 기존 파일 보존을 테스트합니다.

Windows/Python 3.12에서 전체 59개 테스트를 통과했습니다. 공식 스킬 검증과 Claude Code marketplace/plugin strict 검증도 통과했습니다. 이 검증은 개발자가 수행하는 제품 검증이며 사용자의 개인 템플릿 제출·기능 테스트를 요구하지 않습니다. 실제 업무 산출물의 렌더링·비교 검수는 해당 작업을 수행하는 호스트 에이전트의 책임입니다.

## 0.5 참고 템플릿 검증

Windows/Python 3.12에서 56개 자동 테스트를 통과했습니다. 신규 10개 항목은 DOCX 서체·색·섹션·표 관측, PPTX 실제 관계 순서·도형·테마 관측과 외부 링크 비실행, 폴더 자료의 원본·중복·버전 보존, 복수 스타일 선택, 읽기 실패 원본 보존, 시각 근거 요구, 편집 작업 사본의 경로·형식·덮어쓰기 방지, 분석/출력/비교 근거 변경과 승인 무효화, 회사 폴더 등록·기존 안내 보존, DTD 거부·내부 제어 경로 보호를 다룹니다.

공식 스킬 검증과 Claude Code marketplace/plugin strict 검증을 통과했습니다. 회사 reference-templates/ 안내 파일이 Git 추적에서 제외되는지 확인했으며 패키지 검사에서도 회사 참고자료 제외를 확인했습니다.

검증 자료는 테스트에서 만든 가상 DOCX/PPTX와 작은 이미지입니다. 실제 회사 템플릿이 아직 없으므로 실제 디자인 재현·작성 노하우 분석·문서/발표 품질을 검증했다고 주장하지 않습니다. 자동 구조 관측과 비교 기록의 유효성을 검증했으며, 실제 원본을 받은 뒤 호스트 도구의 렌더링과 시각 대조를 수행해야 합니다.

[GitHub Actions 0.5 검증](https://github.com/Agics-hsjeong/project-lifecycle-agent/actions/runs/38772715481)에서 Windows/Linux × Python 3.10/3.12 네 조합의 테스트·패키징이 모두 성공했습니다. 대상 커밋은 563e64a792d45974db0420b2dff1ffb3dec29c62입니다. Codex·Claude Code의 기존 설치를 호스트 명령으로 v0.5.0, enabled=true로 갱신했고 실제 설치본 doctor도 ready=true를 확인했습니다. 캐시를 직접 수정하지 않았습니다. 새 채팅/세션에서 새 스킬을 적용합니다.

## 0.4 정성제안·발표 패키지 검증

Windows/Python 3.12에서 46개 자동 테스트를 통과했습니다. 기존 11단계 통합 시나리오에도 제안 평가 프로필과 제출 컨테이너를 포함했습니다. 새 검증은 평가항목→제안서 절·EVAL↔SLIDE 누락, 발표 시간 초과·대본 누락, 없는/가짜 제출 파일·PPTX 장수 불일치, 승인 뒤 제출 파일·프로필 변경, 발표 제외 근거, 기존 프로젝트 보존·ID 충돌·잘못된 설정의 원자적 거부를 다룹니다.

테스트의 DOCX/PPTX는 기본 컨테이너 검사 전용 가상 파일입니다. 실제 고객 문서의 렌더링·시각 품질·내용 품질을 검증한 파일이 아닙니다. 새 스킬은 실제 작성 시 호스트의 문서/발표 제작 도구로 제출본 생성과 전 페이지/슬라이드 렌더링을 수행하도록 안내합니다. 고객 RFP가 아직 없으므로 실제 정성제안서나 발표자료를 고객 산출물로 생성하지 않았습니다.

[GitHub Actions 0.4 검증](https://github.com/Agics-hsjeong/project-lifecycle-agent/actions/runs/38767984479)에서 Windows/Linux × Python 3.10/3.12 네 조합의 46개 테스트·패키징이 모두 성공했습니다. 대상 커밋은 f8e8555f2cec53672bc3aa374e0227c80ef9f56a입니다. 공식 스킬 검증과 Claude Code marketplace/plugin strict 검증도 통과했습니다. 기존 사용자 설치는 두 호스트의 업데이트 명령으로 v0.4.0, enabled=true로 갱신했고 실제 설치본의 doctor도 ready=true를 확인했습니다. 캐시 파일을 직접 수정하지 않았습니다. 적용에는 새 Codex 채팅/Claude Code 세션이 필요합니다.

## 0.3 실행 엔진 검증

Windows/Python 3.12에서 36개 자동 테스트를 통과했습니다. 가상 프로젝트가 discovery부터 maintenance까지 실제 원장 갱신·검토 스냅샷·단계 이동을 거치고 구현 단계로 되돌아오는 통합 시나리오를 포함합니다.

승인 뒤 문서·근거·범위·입력 변경 차단, 정상 요구사항 상태 진행, 과거 승인 스냅샷 누락, 반려·단계 건너뛰기·인수인계 미작성 차단을 확인했습니다. 자동 ID·CSV 추가 열·revision 충돌·없는 연결·잘못된 경로·완료 근거를 검사했습니다.

관리 파일 쓰기 실패 롤백, 비정상 종료 기록 복구, 사용자 추가 편집 시 복구 중단, 살아 있는 잠금 소유자 보호를 확인했습니다. TXT(cp949)·DOCX·HWPX 추출, XML DTD 거부, 입력 중복 감지·버전 원본 보존을 시험했습니다.

같은 파일로 생성한 ZIP의 바이트가 일치하고 PACKAGE.json의 모든 파일 해시가 맞는지 확인했습니다. ZIP 압축 해제본을 고객 작업 공간에서 실행해 doctor를 통과했습니다. 링크 경로 거부 때 기존 배포 파일이 보존되는지도 확인했습니다.

테스트 승인·문서는 모두 가상 데이터이며 고객 프로젝트나 배포 기본형에 넣지 않습니다. 엔진 테스트는 모델이 작성하는 제안서의 품질이나 모든 호스트의 스킬 자동 선택을 보장하지 않습니다. PDF/OCR·실제 Git/Figma/배포 실행은 호스트 환경과 권한에 따라 별도 확인해야 합니다.

[GitHub Actions 0.3 엔진 검증](https://github.com/Agics-hsjeong/project-lifecycle-agent/actions/runs/38762532223)에서 Windows/Linux × Python 3.10/3.12 네 조합의 테스트·패키징이 모두 성공했습니다. 대상 커밋은 3b9f0c609287f54739493cb7d7dffa587d6a22bb입니다.

수정한 스킬은 공식 quick_validate.py를 통과했고 Claude Code 2.1.300의 marketplace·plugin --strict 검증도 통과했습니다. 기존 사용자 설치를 호스트 명령으로 갱신하여 Codex와 Claude Code 모두 v0.3.0, installed/enabled 상태를 확인했습니다. 두 실제 설치 캐시의 starter.py doctor도 고객 작업 위치에서 ready=true로 실행되었습니다. 설치 캐시를 직접 수정하지 않았습니다. 새 스킬을 적용하려면 새 Codex 채팅/Claude Code 세션을 시작해야 합니다.

## 0.2.1 이전 검증 기록

## 자동 검증

명령: `python -m unittest discover -s tests -v`

Python 3.12 환경에서 19개 테스트 통과. 기존 13개 항목(SI·Product 생성, 덮어쓰기 방지, 경로·미완성·11단계 점검, 필수 산출물, 최신 반려, 파일 참조, 검증 상태, 납품 추적, 적용 제외, ID, CSV)에 더해 RFP 원본 보존·등록, 없는 원본 사전 감지, 명시/기본 작업 공간 분리, 제품별 플러그인 ID·버전 일치, 배포 패키지의 실행 파일 포함·고객 자료 제외를 확인했습니다.

테스트는 .test-output/ 안의 임시 프로젝트를 사용합니다. 검증용 승인·산출물 내용은 가상 데이터이며 실제 프로젝트에 포함하지 않습니다.

## 연결과 제품 범위

Codex의 AGENTS.md와 Claude Code의 CLAUDE.md import 방식은 공식 문서를 확인해 구성했습니다. 실제 별도 Codex/Claude Code 세션을 자동 실행하여 지침 로딩을 시험하지는 않았습니다. 새 프로젝트에서 CONNECTIONS.md의 확인 요청으로 로딩을 확인하세요.

현재 관리 도구는 파일·원장의 구조를 점검합니다. AI 실행기, 권한 인증, 실제 배포, 테스트 실행 대행, 전자결재 기능은 구현 범위에 포함하지 않습니다.

Codex CLI로 고객·테스트 자료를 제외한 배포본 marketplace를 등록하고 플러그인을 설치했습니다. `codex plugin list --marketplace project-lifecycle-marketplace --json`에서 v0.2.1, installed=true, enabled=true를 확인했습니다. 설치 캐시에 projects/와 .test-output/이 없음을 확인했습니다. 별도 AI 세션에서 모델이 스킬을 자동 선택하는 행동 시험은 아직 수행하지 않았습니다.

Skill Creator의 quick_validate.py가 `Skill is valid!`로 통과했습니다. 필요한 PyYAML은 .test-output/validation-deps에만 설치했으며 배포 패키지와 전역 Python 환경에 포함하지 않았습니다.

Claude Code 2.1.300 공식 패키지를 .test-output/claude-host에만 설치했습니다. `plugin validate . --strict`와 `plugin validate .claude-plugin/plugin.json --strict`가 경고 없이 통과했습니다. 격리된 .test-output/claude-profile에서 실제 marketplace 등록·플러그인 설치를 수행했고 v0.2.1, enabled=true를 확인했습니다. 사용자의 기존 Claude 설정·인증은 변경하지 않았고 모델 호출은 하지 않았습니다.

로컬 자동 테스트는 Windows/Python 3.12에서 수행했습니다. [GitHub Actions 실행 #1](https://github.com/Agics-hsjeong/project-lifecycle-agent/actions/runs/38750433129)에서 Windows/Linux와 Python 3.10/3.12의 네 조합이 모두 성공했습니다. 해당 검증 대상 커밋은 ed3d6a943f2bf1ae9886403bb5e81a50e0a39370입니다.

공개 GitHub 저장소 Agics-hsjeong/project-lifecycle-agent를 생성했고 visibility=public, 기본 브랜치 main을 확인했습니다. 고객 프로젝트·원본 자료·테스트 실행 환경은 공개 소스와 배포 패키지에서 제외합니다.
