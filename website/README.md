# 프로젝트이음 공개 사이트

Astro + Starlight 기반의 소개·문서·사용 가이드입니다. GitHub Pages에서 `/project-lifecycle-agent/` 경로로 배포합니다.

## 로컬 개발

Node.js는 package.json의 지원 범위를 따릅니다. Python 3.10 이상은 공개 예제 ZIP을 만드는 데 사용하며 표준 라이브러리만 필요합니다.

```sh
npm ci
npm run build
npm run check:links
npm run dev
```

콘솔에 출력된 Local 주소에 `/project-lifecycle-agent/`가 포함됩니다. 검색은 프로덕션 빌드에서 생성되므로 검색 검수에는 `npm run preview`를 사용합니다.

## 콘텐츠

- `src/pages/index.astro`: 브랜드 소개와 업무 예시·설치 탭·복사 기능
- `src/content/docs/docs/`: 설치·업무 가이드·예제·참고 문서
- `public/brand/`: 사용자 승인 로고 PNG와 제작 기준
- `public/downloads/`: 사용자 매뉴얼 PDF와 빌드 시 생성하는 가상 예제 ZIP
- `../examples/customer-portal/`: 가상 사례의 원본·텍스트 결과 기준안

설치 명령은 기존 `project-lifecycle-agent` ID를 사용합니다. 로고와 설명 문구의 표시 브랜드는 프로젝트이음입니다. 고객 자료 업로드나 AI 실행 기능을 사이트가 제공하지 않습니다.

## 문서 관리

새로운 상세 사용법은 이 사이트의 Markdown에 작성합니다. README는 설치와 주요 가이드 진입점을 담당합니다. 기존 `docs/`는 플러그인에 동봉되는 엔진·설치 규약으로 유지하며, 설치 방식이나 동작을 바꾸면 두 위치의 해당 설명을 함께 확인합니다. 변경 이력은 저장소 CHANGELOG를 원본으로 확인합니다.

PDF 다운로드에는 문서 작성일과 기준 엔진 버전을 표시합니다. 현재 PDF는 기존 사용자 매뉴얼을 새 브랜드로 갱신한 독립 배포본이며 사이트 Markdown에서 자동 생성하지 않습니다. 사용법 변경 시 PDF도 갱신해야 합니다.

PDF 재생성 소스는 `scripts/build-manual.py`입니다. 선택적 제작 의존성은 reportlab·pypdf이며 사이트 빌드에는 필요하지 않습니다. 기본 한글 폰트는 Windows의 맑은 고딕을 사용합니다. 다른 환경에서는 `PDF_FONT_REGULAR`, `PDF_FONT_BOLD`에 한글 TrueType 폰트 경로를 지정합니다. 저장소 루트에서 `python website/scripts/build-manual.py`를 실행하면 output/pdf와 사이트 다운로드 파일을 갱신합니다. 생성 후 페이지 렌더링·비교 검수를 수행해야 합니다.

## 배포

`.github/workflows/website.yml`이 빌드와 내부 링크·파일 검사를 수행합니다. main 반영 시 GitHub Pages에 배포하고, PR에서는 빌드만 검증합니다. 저장소 Settings → Pages의 Source는 **GitHub Actions**여야 합니다.

웹 소스·Node 의존성·예제·PDF는 Python 플러그인 배포 패키지의 허용 목록에 추가하지 않습니다. 고객 데이터와 별개로 관리합니다.
