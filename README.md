# 증미역 공간 지식 그래프 (Jeungmi GeoKG)

서울 9호선 증미역 반경 약 1km 안의 건물, 출입구, 도로, 필지, 공공시설을 "의미 있는 객체"로 쪼개고, 객체마다 바뀌지 않는 고유 주소(URI)를 붙인 뒤, 객체끼리의 관계를 지식 그래프로 엮는 프로토타입입니다.

국토교통부 "국가기본도 대전환" 방향(객체 원자화, 영구 식별자, 자동 갱신, AI 제안 후 사람 승인)을 작은 지역에서 실제로 돌아가게 만들어 보는 것이 목표입니다.

## 만들 것

1. 공공 데이터를 받아 대상 범위만 잘라내는 수집 단계
2. 객체 쪼개기와 URI 부여
3. GeoSPARQL 1.1 기준의 지식 그래프 구축과 조회
4. 문서(구청 고시공고 등)를 넣으면 LLM이 객체와 변경 내용을 뽑아 "변경 제안"을 만드는 단계
5. 변경 제안을 사람이 승인 또는 반려하는 검토 화면
6. 자연어 질문을 그래프 조회로 바꿔 지도에 보여주는 질의 기능

## 대상 범위

- 중심: 증미역 역사 건물 중심점 (경도 126.861247, 위도 37.557719)
- 반경: 1km
- 중심 좌표의 출처와 원본 값은 [config/area.yaml](config/area.yaml)에 있습니다.

## 객체 모델 (초안)

객체 종류: Building(건물), BuildingEntrance(건물 출입구), RoadSegment(도로 구간), Parcel(필지), PublicFacility(공공시설), Station(역), AdminDong(행정동)

관계:

- Building `hasEntrance` BuildingEntrance
- BuildingEntrance `facesRoad` RoadSegment
- Building `locatedOn` Parcel
- 모든 객체 `within` AdminDong
- 공간 관계(접함, 포함, 겹침)는 GeoSPARQL의 위상 관계 함수로 계산합니다.

모든 객체는 GeoSPARQL의 Feature로 두고, 모양은 Geometry로 따로 분리합니다. URI 규칙은 [docs/uri-policy.md](docs/uri-policy.md)에 있습니다.

## 데이터

원본 데이터는 저장소에 올리지 않습니다. 출처, 이용 조건, 받는 방법은 [docs/data-sources.md](docs/data-sources.md)에 있습니다.

## 실행

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS, Linux는 .venv/bin/python
cp .env.example .env                                      # 키 값을 채운다
```

원본 압축 파일을 `data/raw/_archives/`에 넣고 풀기, 범위 자르기 (7-Zip이 없으면 먼저 `pip install py7zr`):

```bash
.venv/Scripts/python -m src.ingest.unpack
.venv/Scripts/python -m src.ingest.clip_area
```

결과는 `data/processed/jeungmi_area.gpkg`(EPSG:4326)와 `data/processed/clip_report.json`에 생깁니다.

## 기술 스택

Python 3.12, geopandas, shapely, pyproj, pyogrio, rdflib, pyoxigraph, Claude API, FastAPI, MapLibre GL JS. 저장 좌표계는 EPSG:4326, 거리 계산은 EPSG:5186입니다. 기본값에서 바꾼 것은 [docs/decisions.md](docs/decisions.md)에 적습니다.

## 폴더 구조

```
config/          대상 범위, 설정
data/raw/        원본 데이터 (커밋 안 함)
data/processed/  잘라내고 정리한 데이터
src/ingest/      데이터 수집
src/model/       객체 쪼개기, URI 부여
src/graph/       그래프 생성, 조회
src/llm/         문서에서 변경 제안 추출
src/review/      사람 검토 화면
src/query/       자연어 질의
web/             지도 화면
docs/            설계 문서, 결정 기록
```

## 일정과 진행 상황

| 기간 | 할 일 | 상태 |
|---|---|---|
| 10/28 ~ 11/3 | 데이터 받기, 범위 자르기, 이용 조건 기록 | 진행 중 |
| 11/4 ~ 11/10 | 객체 쪼개기, URI 부여, 그래프 구축, SPARQL 조회 확인 | |
| 11/11 ~ 11/17 | LLM 변경 제안, 자연어 질의 | |
| 11/18 ~ 11/24 | 검토 화면, 지도 화면, 배포, README와 시연 영상 | |

- [x] 저장소 생성
- [x] GIS건물통합정보, 건축물대장 총괄표제부 받기 (10/28)
- [x] 내비게이션용DB, 민원행정기관 전자지도 받기 (10/28)
- [x] 증미역 중심 좌표 확인, 반경 1km 자르기 (10/28)
- [x] GIS건물통합정보, 내비게이션용DB 이용 조건 기록 (10/28)
- [x] 건물, 필지 URI 규칙 확정 (10/28)
- [ ] 도로명주소 전자지도 받기 (승인 대기)
- [ ] 민원행정기관, 전자지도 이용 조건 확인
- [ ] 필지 경계(연속지적도) 붙이기 (나중에)
- [x] 다른 컴퓨터에서 원본을 다시 풀 수 있게 `src/ingest/unpack.py` 추가 (10/28)
- [ ] 2주차: 객체 쪼개기, URI 부여, 그래프 구축 (진행 중, 아래 "이어서 할 일")

## 이어서 할 일 (2026-10-28 퇴근 시점)

다른 컴퓨터에서 이어 할 때:

1. `git clone` 후 위 "실행" 순서대로 가상환경을 만든다.
2. 원본 압축 파일 5개(`data/raw/_archives/`, 약 570MB)를 옮겨 와 같은 자리에 둔다.
3. `unpack`, `clip_area`를 차례로 실행하면 `data/processed/jeungmi_area.gpkg`가 다시 생긴다.

2주차 작업 상태:

- 끝남: 어휘 정의 `src/graph/ontology.ttl`, URI 함수 `src/model/uri.py`
- 다음: `src/model/build_objects.py`로 건물, 출입구, 필지, 행정동 객체를 만든다. 규칙은 docs/uri-policy.md.
  - 건물관리번호 건물: 중심점을 포함하는 GIS 폴리곤을 모양으로, 없으면 중심점을 점 모양으로
  - 주소 미연결 건물: GIS 폴리곤 628개, `building-gis/{A1}`
  - A1 중복은 A0를 뺀 속성과 모양이 같으면 하나로 합친다 (A0까지 비교하면 안 합쳐진다)
  - 필지 PNU: GIS A2, 없으면 내비 지번의 대표지번(jibun_seq 0)으로 만든다. 산여부 0은 1, 1은 2
  - 보조출입구 19개는 주소(도로명코드, 지하여부, 본번, 부번)가 같은 건물 6 ~ 25개에 모두 잇는다
  - 행정동은 내비 행정동코드로 잇는다. 주소 미연결 건물은 행정동 경계(전자지도)가 와야 이을 수 있다
- 그다음: `src/graph/build_graph.py`로 Turtle을 만들고, pyoxigraph에 올려 SPARQL로 확인한다.
- 공공시설, 역 URI 규칙은 아직 초안이라 정한 뒤 넣는다.
