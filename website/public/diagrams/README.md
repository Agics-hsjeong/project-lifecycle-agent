# 사이트 시각 도식

직접 작성한 SVG 원본이며 저장소의 MIT 라이선스를 따른다. 외부 이미지나 특정 기술 공급자의 로고는 포함하지 않는다.

| 파일 | 내용 |
| --- | --- |
| project-fabric.svg | RFP·템플릿에서 제안·구축·운영으로 이어지는 개념 시각화 |
| architecture.svg | 호스트 → 이음의 관리 규칙·엔진 → 고객 프로젝트 파일 |
| target-system.svg | 공개 가상 고객 포털 RFP에 대한 목표 시스템 개념 설계 |
| *-mobile.svg | 같은 내용을 모바일에서 읽기 쉽게 정리한 세로형 버전 |

각 SVG는 title·desc를 포함한다. 홈페이지는 모바일과 태블릿에서 세로형을 선택하며, 목표 시스템의 .system-access / .system-data와 .node-box를 사용해 강조를 표시한다. 색만으로 의미를 전달하지 않도록 선택 버튼과 설명을 함께 갱신한다.

## 수정과 재생성

저장소 루트에서 다음을 실행한다. Python 표준 라이브러리만 사용한다.

```sh
python website/scripts/generate-diagrams.py
```

생성 소스의 노드·문구·색상·연결 경로를 수정하고 SVG 6개를 함께 커밋한다. 빌드는 커밋된 SVG를 사용한다. 기존 public/brand의 브랜드 자산은 변경하지 않는다.

실제 동작 설명은 docs/V2-IMPLEMENTATION.md와 tools/workflow.py, 목표 시스템은 examples/customer-portal/RFP.md에 대조한다. 고객 포털은 설명용이며 구축 완료·인증 제품 선정·운영 승인을 의미하지 않는다.
