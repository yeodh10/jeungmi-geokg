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

두 번째 명령 뒤 브라우저에서 http://localhost:8000 을 열면 검토 화면이, http://localhost:8000/map 을 열면 지도 화면이 나옵니다. 지도 화면은 그래프(원천 + 승인된 변경)에서 건물, 출입구, 공공시설, 역을 꺼내 그리고, 객체를 누르면 어휘의 한국어 이름으로 속성과 연결된 객체를 보여 줍니다. 세 번째는 승인과 반려 흐름을 화면 없이 확인합니다(제안 파일의 임시 복사본으로 돌아서 실제 기록은 바뀌지 않습니다).

데이터 규칙 검사 (SHACL):

```bash
.venv/Scripts/python -m src.graph.validate
.venv/Scripts/python -m tests.test_shacl
```

`src/graph/shapes.ttl`에 객체 종류마다 규칙을 적었습니다. 상태 값, 출처, 기본 모양, 건물 식별 번호(건물관리번호 25자리와 GIS 번호 28자리 중 정확히 하나), PNU 형식, 기관코드 7자리, 폐지하면 폐지 확인일, 출입구는 건물과 이어질 것 등입니다. 전체 검사 결과는 `data/processed/validation_report.md`에 남습니다. 검토 화면은 제안을 승인하기 전에 반영 결과를 이 규칙으로 검사하고, 어기면 승인을 막습니다.

URI를 실제로 열리는 페이지로 만들기:

```bash
.venv/Scripts/python -m src.graph.publish
```

`data/processed/site/`에 객체마다 `index.html`(한국어 이름표로 본 속성과 연결)과 `index.ttl`(그 URI가 주어인 트리플), 어휘 페이지(`def/`)가 생깁니다. 이 폴더를 GitHub Pages에 올리면 `https://yeodh10.github.io/jeungmi-geokg/id/...` URI가 그대로 열립니다. 이용 조건을 확인 중인 출처(`config/area.yaml`의 `publish.hold_sources`)에서 온 객체는 페이지도 연결도 만들지 않습니다.

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

### 고시공고에서 제안 만들기 (LLM)

```bash
.venv/Scripts/python -m src.llm.notice data/raw/notices/<고시공고>.txt
.venv/Scripts/python -m tests.test_notice            # API 키 없이 가짜 클라이언트로 흐름 확인
```

- LLM(`claude-opus-5-5`, 구조화 출력)은 문서에서 바뀌는 내용만 뽑습니다. 변경 종류, 대상의 주소나 이름, 새 값, 원문 문장입니다.
- 뽑힌 주소, 지번, 이름을 그래프 객체에 맞추는 일은 코드가 정해진 순서(이름, 도로명주소, 지번)로 합니다. LLM이 URI를 정하지 않습니다.
- 객체를 정확히 하나로 찾은 폐지, 용도 변경, 이름 변경, 주소 변경만 제안이 됩니다. 후보가 여럿이거나 신축처럼 모양이 필요한 것은 `data/processed/review/notices/`의 보고서에만 남습니다.
- 안전 분류기가 거절하면 서버가 다른 모델로 다시 돌리도록 `fallbacks: "default"`를 켰습니다.

### 자연어 질의 (LLM)

```bash
.venv/Scripts/python -m src.query.nl "증미역 300m 안에 있는 학교는?"
.venv/Scripts/python -m tests.test_nl                # 가짜 클라이언트로 흐름 확인
```

지도 화면의 "질문하기" 칸에서도 쓸 수 있습니다. 결과는 지도에 빨간색으로 표시되고, 실행한 SPARQL이 함께 보입니다.

- LLM은 어휘(ontology.ttl), 그래프의 주요 이름과 값, 예시 쿼리(`src/graph/queries/`)를 보고 SPARQL 하나를 씁니다.
- 실행 전에 코드가 읽기 쿼리(SELECT, ASK)만 통과시키고 결과 수를 500행으로 제한합니다.
- 거리 조건은 LLM이 `{{NEAR:<객체 URI>:<미터>}}`를 쓰면 코드가 그 객체 둘레의 원(EPSG:5186에서 계산)으로 바꿉니다.
- 쿼리가 실패하면 오류 내용을 붙여 한 번 더 쓰게 합니다.

### 자동 갱신 (새 자료가 오면)

```bash
.venv/Scripts/python -m src.ingest.unpack            # 새 압축 파일을 _archives에 넣은 뒤 다른 폴더로 풀어도 된다
.venv/Scripts/python -m src.review.diff_proposals --raw <새 원본 폴더> --label 202610
.venv/Scripts/python -m tests.test_diff              # 시험용 다음 자료로 비교 흐름 확인
```

새 자료로 그래프를 통째로 바꾸지 않습니다. 새 자료를 따로 객체로 만든 뒤 지금 그래프와 객체마다 비교해 제안을 만듭니다.

| 제안 | 언제 | 승인하면 |
|---|---|---|
| create | 새 자료에만 있는 객체 | 객체와 모양을 추가 |
| update | 값이 달라진 객체 | 이전 값을 빼고 새 값을 넣음 |
| retire | 새 자료에서 사라진 객체 | 지우지 않고 상태를 폐지로, 폐지 확인일 기록 (URI 유지) |
| link | 주소 미연결 건물이 새 건물관리번호 건물의 모양으로 쓰임 | 상태를 주소 연결됨으로, `owl:sameAs`로 새 URI와 연결 |

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
- [x] 데이터 갱신 비교로 제안 만들기 (10/29, 시험용 다음 자료로 확인)
- [x] 지도 화면: 그래프 전체, 상태별 색, 객체 속성과 연결 따라가기, 이름과 주소 찾기 (10/29)
- [ ] 새 기준일 실제 자료로 자동 갱신 돌려 보기 (내비게이션용DB 10월분 또는 새 GIS건물통합정보)
- [x] 고시공고에서 LLM으로 제안 만들기 (10/29, 가짜 클라이언트로 확인)
- [x] 자연어 질의와 지도 표시 (10/29, 가짜 클라이언트로 확인)
- [ ] 실제 API 키로 고시공고, 자연어 질의 돌려 보기 (API 키 받은 뒤)
- [x] URI 정적 페이지 만들기 (10/29, 11,710쪽, 내부 연결 60,674개 모두 열림)
- [x] SHACL 데이터 규칙과 승인 전 검사 (10/29, 전체 그래프에서 원천 자료 문제 1건만 남음)
- [ ] GitHub Pages로 배포 (공개 전 확인)
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
