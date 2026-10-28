---
title: 기여와 피드백
description: 업무 사용 경험, 오류 제보, 문서와 코드 개선으로 프로젝트이음에 참여하세요.
---

프로젝트이음은 **MIT 라이선스**의 오픈소스입니다. 실제 업무에서 얻은 사용 경험과 개선 의견을 환영합니다.

## 피드백 남기기

- [오류 제보](https://github.com/Agics-hsjeong/project-lifecycle-agent/issues/new?template=bug_report.yml): 제품·버전, 재현 단계, 기대한 결과와 실제 오류를 적어주세요.
- [개선 제안](https://github.com/Agics-hsjeong/project-lifecycle-agent/issues/new?template=feature_request.yml): 해결하려는 업무와 현재의 불편을 알려주세요.
- 문서의 “페이지 수정” 링크로 오탈자·설명·사용 예시를 제안할 수 있습니다.

고객 제안서·계약서·실명·가격·인증 정보가 포함된 자료는 공개 이슈에 첨부하지 마세요. 가상 자료나 익명화한 재현 예제를 사용합니다.

## 소스 구조

| 위치 | 역할 |
| --- | --- |
| blueprint/ | 공통 프로세스와 산출물 양식 |
| skills/project-lifecycle/ | 사용자 진입점과 작업 규약 |
| tools/ | Python 표준 라이브러리 기반 생성·점검 도구 |
| tests/ | 엔진·제안·템플릿·패키징 검증 |
| website/ | Astro·Starlight 소개·문서 사이트 |
| examples/customer-portal/ | 공개용 가상 사업 예제 |

## 개발 검증

```sh
python -X utf8 -m unittest discover -s tests -v
python -X utf8 tools/package_plugin.py
```

사이트 작업은 `website/`에서 `npm ci`, `npm run build`, `npm run check:links`로 검증합니다. Node.js는 package.json의 지원 범위를 따릅니다. 기존 고객 데이터를 양식·예제·패키지에 복사하지 않습니다.

## 브랜드와 호환성

표시 브랜드는 프로젝트이음(Project Ieum)입니다. 설치 ID·marketplace·스킬 호출은 기존 이름을 유지해 업데이트와 인수인계를 연결합니다. 로고 원본과 제작 기준은 저장소의 `website/public/brand/`에 보관합니다.

[GitHub 저장소](https://github.com/Agics-hsjeong/project-lifecycle-agent) · [라이선스](https://github.com/Agics-hsjeong/project-lifecycle-agent/blob/main/LICENSE)
