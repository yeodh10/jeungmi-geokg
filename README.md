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

객체 쪼개기, 그래프 만들기, SPARQL 확인:

```bash
.venv/Scripts/python -m src.model.build_objects
.venv/Scripts/python -m src.graph.build_graph
.venv/Scripts/python -m src.graph.check_queries
```

`data/processed/jeungmi_geokg.ttl`(그래프)과 `data/processed/query_check.md`(쿼리 결과)가 생깁니다. 예시 쿼리는 `src/graph/queries/`에 있습니다.

변경 제안 만들기와 검토 화면:

```bash
.venv/Scripts/python -m src.review.make_proposals
.venv/Scripts/python -m uvicorn src.review.app:app --port 8000
.venv/Scripts/python -m tests.test_review
```

두 번째 명령 뒤 브라우저에서 http://localhost:8000 을 열면 검토 화면이 나옵니다. 세 번째는 승인과 반려 흐름을 화면 없이 확인합니다(제안 파일의 임시 복사본으로 돌아서 실제 기록은 바뀌지 않습니다).

## 변경 제안과 사람 승인

```
원천 데이터 -> 객체, URI -> 원천 그래프 (언제든 다시 만들 수 있음)
                                  +
제안 (규칙 후보, 데이터 갱신 비교, LLM 문서 추출) -> 검토 화면 -> 승인한 것만 SPARQL UPDATE로 반영
```

- 제안은 출처와 상관없이 같은 형식입니다. 대상 URI, 근거, 그래프에 넣고 뺄 트리플, 위치를 담습니다.
- 승인하면 제안 자체도 그래프에 기록됩니다. 바뀐 객체는 `prov:wasInfluencedBy`로 제안을 가리키고, 제안에는 승인한 사람과 시각이 남습니다.
- 반려한 제안은 그래프에 들어가지 않지만 제안 기록에는 남습니다.
- 지금 들어 있는 제안: 공공시설 기관코드 후보 1건 (서울강서고용복지플러스센터 -> 서울강서고용센터 1494880, 대기 중)

## 지금 그래프에 들어 있는 것 (2026-10-29)

| 객체 | 개수 | 비고 |
|---|---|---|
| 건물 | 2,790 | 주소 연결 2,162 (폴리곤 1,838, 점 324), 주소 미연결 628 |
| 건물 출입구 | 2,180 | 주출입구 2,161, 보조출입구 19 |
| 필지 | 1,760 | 모양 없이 PNU만, 63개에 단지 정보 |
| 행정동 | 8 | 모양 없이 행정동코드만 |
| 역 | 1 | 증미역, 역사 건물과 `housedIn`으로 연결 |
| 공공시설 | 23 | 행정표준코드 기관코드로 식별, 22곳이 건물과 연결. 3곳은 검토 목록 |

트리플 81,996개. 공간 관계는 SPARQL에서 `geof:sfTouches`, `geof:sfIntersects` 같은 GeoSPARQL 위상 함수로 그때그때 계산합니다.

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
- [x] 2주차: 건물, 출입구, 필지, 행정동 객체와 URI, 그래프 구축, SPARQL 확인 (10/29)
- [x] 역 객체 (증미역, 10/29)
- [x] 공공시설 객체 23곳 (행정표준코드 기관코드, 10/29)
- [x] 변경 제안 형식, 승인 반영 장치, 검토 화면 (10/29, 원래 4주차 일을 당김)
- [ ] 데이터 갱신 비교로 제안 만들기 (새 기준일 자료 받은 뒤)
- [ ] 고시공고에서 LLM으로 제안 만들기 (API 키, 고시공고 받은 뒤)
- [ ] 도로 구간, 행정동 경계, facesRoad 관계 (전자지도 받은 뒤)

## 이어서 할 일

다른 컴퓨터에서 이어 할 때:

1. `git clone` 후 위 "실행" 순서대로 가상환경을 만든다.
2. 원본 압축 파일 5개(`data/raw/_archives/`, 약 570MB)를 옮겨 와 같은 자리에 둔다.
3. "실행"의 명령을 위에서부터 차례로 실행하면 `data/processed/`가 모두 다시 생긴다.

남은 일:

- 공공시설 검토 목록 3곳 중 고용센터 1곳은 제안으로 올라가 있다. 우편취급국 2곳은 기관코드가 없어 공공시설로 넣지 않는다.
- 전자지도가 오면: 도로 구간 객체, 출입구 `facesRoad`, 행정동 경계 모양을 넣는다. 그러면 주소 미연결 건물 628개도 `geof:sfWithin`으로 행정동에 이을 수 있다.
- 3주차: 강서구청 고시공고에서 변경 제안 뽑기, 자연어 질문을 SPARQL로 바꾸기. `src/graph/queries/`의 예시 쿼리를 프롬프트 예시로 쓴다.
- 거리 조건(역에서 몇 m 안)은 pyoxigraph에 `geof:distance`, `geof:buffer`가 없어서 파이썬에서 원을 만들어 `geof:sfIntersects`에 넣는다(`04_near_station_by_use.rq` 참고).
