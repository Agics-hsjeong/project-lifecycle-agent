# 검증 기록

검증일: 2026-10-16 (Asia/Seoul). 대상: 플러그인 v0.3.0.

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
