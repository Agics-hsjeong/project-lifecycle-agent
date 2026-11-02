# 기여 안내

브랜드: 프로젝트이음(Project Ieum). 설치 ID: project-lifecycle-agent. 라이선스: MIT. GitHub 저장소 루트에 두 호스트의 marketplace와 플러그인 manifest를 유지합니다.

## 개발 기준

- 공통 스킬과 프로세스는 Codex·Claude Code에서 같은 동작을 목표로 합니다.
- 사용자에게 파일 편집이나 셸 명령을 일상 작업으로 넘기지 않습니다.
- 고객 프로젝트, 원본 RFP, 자격증명은 커밋·배포 패키지에 포함하지 않습니다.
- 스킬만 바꿔 충분하면 불필요한 서버·모델 API 의존성을 추가하지 않습니다.
- 사용자 승인·테스트 성공·배포 결과는 실제 근거가 있을 때만 기록합니다.

## 검증과 배포

개발자는 `python -m unittest discover -s tests -v`로 테스트하고 `python tools/package_plugin.py`로 배포 ZIP을 만듭니다. 이 명령들은 플러그인 사용자에게 요구하는 일상 절차가 아닙니다.

ZIP은 공개 가능한 파일만 허용 목록으로 선택합니다. 저장소와 배포 ZIP에는 Codex marketplace와 Claude Code marketplace가 함께 들어 있습니다.

개별 파일 허용 목록은 tools/package-files.json입니다. 새 런타임·테스트·공개 문서를 추가할 때만 검토 후 갱신합니다. 허용 폴더에 있다는 이유로 미등록 파일·환경 파일·고객 자료를 자동 포함하지 않습니다.

manifest 구조 검사와 로컬 도구 테스트는 실제 호스트 설치 시험을 대신하지 않습니다. 릴리스 전 두 호스트에서 설치·활성화·RFP 입력·작업 이어하기를 확인합니다.

## 문서 사이트

웹 소개·가이드 소스와 빌드·링크 검사는 [website/README.md](website/README.md)를 참고하세요. 공개용 가상 예제는 examples/customer-portal/에만 작성합니다. 실제 고객의 RFP·제안·참고 원본을 예제로 복사하지 않습니다. 로고·PDF·웹 의존성과 사이트 workflow는 Python 플러그인 ZIP에 포함하지 않습니다.
