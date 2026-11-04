# 측량 성과와 계보

지도 위의 건물 모양은 누군가 언제 어떤 방법으로 잰 결과입니다. 같은 건물이라도 항공사진으로 도화한 외곽과 레이저로 잰 외곽은 정확도와 갱신 시기가 다릅니다. 그래서 이 그래프는 객체의 값만이 아니라, 그 값을 낸 측량 성과와 측량 작업까지 이어 둡니다.

## 1. 지금 그래프에 들어 있는 것

국토정보플랫폼에서 수치지형도를 받으면 도엽마다 ISO 19115-3 메타데이터(XML)가 함께 옵니다. 반경 1km를 덮는 도엽 2장의 메타데이터를 읽어 그래프에 넣었습니다(`src/model/survey_meta.py`, `src/graph/build_graph.py`의 `add_survey`).

| 도엽 | 사업 | 제작 구분 | 성과 고시 | 촬영, 조사, 제작 | 작업 기간 | 측량 방법 | 이은 우리 건물 |
|---|---|---|---|---|---|---|---|
| 37608075 | 2025년 1대 1000 및 국가기본도 통합구축(수도권북부1지구) | 수정 | 2026-1397 | 2025, 2025, 2025 | 2025-03-25 ~ 2025-12-30 | 항공사진측량 | 1,726동 |
| 37608085 | 같은 사업 | 수정 | 2026-1397 | 2025, 2025, 2025 | 2025-03-25 ~ 2025-12-30 | 항공사진측량 | 119동 |

- 측량 방법은 성과에 든 지형지물의 '측량방법' 값에서 모았습니다. 두 도엽의 건물 20,091개가 모두 '사진측량'입니다.
- 우리 건물에서 측량 작업까지 SPARQL 한 번으로 거슬러 올라갑니다(`src/graph/queries/17_survey_lineage.rq`).

```
우리 건물 --jgk:digitalMapFeature--> 수치지형도 건물 --prov:wasDerivedFrom--> 측량 성과(도엽)
  --prov:wasGeneratedBy--> 측량 작업 --jgk:surveyMethod--> 항공사진측량 (SKOS)
                                   --prov:wasAssociatedWith--> 국토지리정보원
```

- 기본측량성과라 이 계보는 로컬 그래프에만 있고 공개 사이트에는 나가지 않습니다(`docs/governance.md`).
- 받은 XML 가운데에는 태그 짝이 맞지 않는 파일이 있어, XML 해석기 대신 필요한 요소만 글로 찾습니다. 값을 찾지 못하면 비워 둡니다.

## 2. ISO 19115-3과 그래프의 대응

메타데이터 표준(ISO 19115-3)의 칸을 W3C 어휘(DCAT, PROV-O, Dublin Core)로 옮긴 표입니다. GeoDCAT-AP의 대응을 따르되, 국토지리정보원 메타데이터가 요약문 안에 글로 적어 둔 값(고시번호, 촬영년도 등)은 우리 속성으로 받았습니다.

| ISO 19115-3 요소 | 값 (도엽 37608075) | 그래프 |
|---|---|---|
| `citation/title` | 2025년 1대 1000 및 국가기본도 통합구축(수도권북부1지구) - 도엽번호 : 37608075 | 작업 `rdfs:label`, 성과 `jgk:sheetCode` |
| `abstract`의 '제작구분' | 수정 | 작업 `jgk:workKind` |
| `abstract`의 '고시번호' | 2026-1397 | 작업 `jgk:noticeNo` (작업 URI에도 씀) |
| `abstract`의 '촬영년도, 조사년도, 제작년도' | 2025, 2025, 2025 | `jgk:photoYear`, `jgk:fieldSurveyYear`, `jgk:productionYear` |
| `extent/temporalElement` | 2025-03-25 ~ 2025-12-30 | 작업 `prov:startedAtTime`, `prov:endedAtTime` |
| `extent/geographicElement` (경위도 범위) | 126.85 ~ 126.875, 37.5499 ~ 37.5751 | 성과 `geo:hasGeometry` (범위 사각형) |
| `spatialResolution/denominator` | 5000 | 성과 `jgk:mapScale` |
| `referenceSystemInfo` | EPSG:5186 | 성과 `jgk:crsCode` |
| `resourceConstraints` | 재배포 금지 | 성과 `dcterms:rights` |
| `resourceMaintenance` | 격년, 지형지물 변동에 따라 수시 | 성과 `dcterms:accrualPeriodicity` |
| `contentInfo`의 규정 | 수치지형도 작성 작업규정 (2019-05-22) | 성과 `dcterms:conformsTo` |
| `pointOfContact` | 국토지리정보원 | 작업 `prov:wasAssociatedWith` |

## 3. 같은 구조로 받을 성과

측량 회사가 내는 성과도 같은 세 층(성과, 작업, 방법)으로 받을 수 있게 측량 방법 분류에 개념을 미리 정해 두었습니다(`jgk:SurveyMethodScheme`). 아래는 설계이고, 이 저장소에 그런 실제 자료는 아직 없습니다.

| 성과 | 성과 노드 | 작업과 장비 | 그래프에 붙는 값 |
|---|---|---|---|
| 항공 LiDAR 점군 타일 | `jgk:SurveyProduct` + `dcat:distribution`(LAS, COPC) | 작업 `jgk:surveyMethod` 항공 레이저측량, 장비는 `sosa:Platform`(항공기)과 `sosa:Sensor`(레이저 스캐너) | 건물마다 잰 높이를 `sosa:Observation`으로: 관측 대상은 건물, 결과는 높이(m), 절차는 'DSM-DEM 차분' |
| 수심 LiDAR | 같음 | 수심 레이저측량 (항공 레이저측량의 하위 개념) | 하천, 해안 객체의 바닥 높이 관측 |
| 정사영상 타일 | `jgk:SurveyProduct` + `dcat:distribution`(COG) | 항공사진측량 | 지상표본거리(GSD), 촬영일. 객체와는 범위가 겹치는 성과로 잇는다 |
| MMS 점군, 영상 | 같음 | 차량 이동형 측량 | 건물 전면, 출입구 위치 관측. 출입구 객체의 위치 검증에 쓴다 |
| 초분광 영상 | 같음 | 초분광 영상 | 지붕 재질, 식생 분류를 객체 속성 관측으로 |

이렇게 넣으면 바로 할 수 있는 검사가 있습니다.

- 건축물대장 높이(`jgk:heightM`, 지금 1,437동)와 LiDAR로 잰 높이를 건물마다 견주어, 차이가 큰 곳(증축, 대장 오류)을 변경 제안으로 낸다. 국가기본도 층수와 대장 층수를 견주는 지금의 쿼리 13과 같은 방식입니다.
- 성과마다 촬영 시점이 있으므로, 객체의 값이 언제 기준인지 답할 수 있다. 시점별 판(`src/graph/editions.py`)과 함께 쓰면 '2025년 촬영 성과로 본 이 건물'을 물을 수 있다.
- 같은 건물에 방법이 다른 성과가 여럿이면, 품질 규칙(SHACL)으로 우선순위를 정할 수 있다(예: 높이는 LiDAR, 외곽은 정사영상 도화).

예시 (설계 확인용 Turtle, 실제 자료가 아님):

```turtle
<id/survey-sheet/EXAMPLE-LIDAR-0001> a jgk:SurveyProduct ;
    prov:wasGeneratedBy <id/survey-activity/example-2026-0001> ;
    dcat:distribution [ dcat:mediaType "application/vnd.laszip+copc" ] .
<id/survey-activity/example-2026-0001> a jgk:SurveyActivity ;
    jgk:surveyMethod jgk:method-airborne-lidar ;
    prov:used [ a sosa:Sensor ; rdfs:label "항공 레이저 스캐너" ] .
[] a sosa:Observation ;
    sosa:hasFeatureOfInterest <id/building/1150010200106660040000001> ;
    sosa:observedProperty jgk:buildingHeight ;
    sosa:hasSimpleResult 18.4 ;
    prov:wasDerivedFrom <id/survey-sheet/EXAMPLE-LIDAR-0001> .
```
