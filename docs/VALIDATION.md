# 검증 기록

검증일: 2026-10-15 (Asia/Seoul). 대상: 플러그인 v0.2.1.

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

로컬 자동 테스트는 Windows/Python 3.12에서 수행했습니다. Linux/Python 3.10 검증은 GitHub Actions 설정에 포함했지만 원격 실행 결과가 확인되기 전에는 통과로 주장하지 않습니다.

공개 GitHub 저장소 Agics-hsjeong/project-lifecycle-agent를 생성했고 visibility=public, 기본 브랜치 main을 확인했습니다. 고객 프로젝트·원본 자료·테스트 실행 환경은 공개 소스와 배포 패키지에서 제외합니다.
