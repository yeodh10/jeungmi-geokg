# 상호운용성: jgk를 모르는 시스템도 읽을 수 있나

공간 지식 그래프는 만든 곳 밖에서 읽혀야 쓸모가 있다. 다른 기관, 검색엔진, 다른 GIS가 우리 어휘(jgk)를 따로 배우지 않고도 이 그래프를 읽을 수 있는지 재 봤다.

- 정렬 파일: [src/graph/alignment.ttl](../src/graph/alignment.ttl) (공개 사이트 `def/alignment.ttl`)
- 검사: `python -m src.graph.interop` (바깥 주소 확인까지, 인터넷 필요), 결과는 `data/processed/interop_report.md`
- 테스트: `python -m tests.test_interop` (인터넷 없이)

## 무엇과 이었나

| 바깥 어휘 | 쓰는 곳 | 잇는 방법 | 예 |
|---|---|---|---|
| GeoSPARQL 1.1 (OGC) | 공간 객체와 모양 | 처음부터 그 위에 세움 | 모든 객체가 `geo:Feature`, 모양은 `geo:asWKT` |
| SOSA/SSN (W3C, OGC) | 센서와 관측 | 처음부터 그 위에 세움 | `jgk:Sensor`는 `sosa:Sensor`, 관측은 `sosa:Observation` |
| QUDT 단위 | 관측 단위 | 관측 항목에 단위 주소 | 기온은 `unit:DEG_C`, 소음은 `unit:DeciB` |
| schema.org | 웹, 일반 시스템 | `rdfs:subClassOf`, `rdfs:subPropertyOf` | 역은 `schema:SubwayStation`, 도로명주소는 `schema:address`, 건물관리번호와 PNU는 `schema:identifier` |
| INSPIRE 지형지물 개념 등록부 | 유럽 공간정보 기관 | `skos:closeMatch` | 건물은 `inspire:Building`, 필지는 `inspire:CadastralParcel`, 도로 구간은 `inspire:RoadLink` |
| 위키데이터 | 누구나 쓰는 개념 번호 | 종류는 `skos:closeMatch`, 같은 실물은 `owl:sameAs` | 건물 Q41176, 도시철도역 Q928830, 증미역 Q59989 |
| BOT (W3C Linked Building Data) | BIM | `rdfs:subClassOf` | 우리 건물은 `bot:Building`. BIM 모델 건물과는 `jgk:bimBuilding` ([bim.md](bim.md)) |

schema.org는 하위 종류, 하위 속성으로 이었다. 그러면 RDFS 추론만 돌려도 바깥 이름으로 물을 수 있다. INSPIRE와 위키데이터는 정의 범위가 우리와 꼭 같지 않다. 예를 들어 INSPIRE의 Building은 유럽 건물 정의를 따른다. 그래서 '가까운 개념'으로만 이었다.

OGC CityGML 3.0의 주소(`https://www.opengis.net/ont/citygml/building/Building`)는 넣지 않았다. 없는 이름에도 200을 돌려줘서 정의가 정말 있는지 확인할 수 없기 때문이다. 바깥 주소는 없는 이름에 404를 주는 곳만 쓴다.

## 결과 (2026-10-30)

| 검사 | 결과 |
|---|---|
| 바깥 주소가 열리나 | 17개 모두 열림 (schema.org 5, INSPIRE 5, 위키데이터 6, BOT 1). BOT는 문서를 받아 이름이 정의돼 있는지까지 봄. 위키데이터는 이름까지 읽음: 건물, 도로, 대지, 센서, 도시철도역, 증미역 |
| 바깥 어휘로만 묻기 | 그래프 일부(1,135 트리플)에 owlrl로 RDFS 추론을 돌리니 3,372 트리플이 됨. 그 뒤 schema.org와 GeoSPARQL 이름만으로 지하철역 1곳(위키데이터 Q59989와 같은 실물), 주소가 있는 장소 51곳, 식별자가 있는 장소 53곳, 역이 속한 장소(역사 건물, 행정동)를 찾음. SOSA 이름만으로 센서 4대와 관측 항목도 찾음 |
| 형식 바꾸기 | 전체 184,637 트리플을 JSON-LD(30MB)와 N-Triples(38MB)로 쓰고 다시 읽음. 두 형식 모두 트리플이 하나도 바뀌지 않음 |
| GeoSPARQL 모양 | WKT 9,760개 모두 읽힘. 모두 경도, 위도 순서(CRS84)로 한국 범위 안. 모양이 없는 객체 1,844개는 행정동, 필지처럼 경계 자료를 아직 넣지 않은 것 |
| 위키데이터와 견주기 | 이름 같음(증미역). 좌표 차이 44.8m. 우리 점은 내비게이션용DB의 역사 건물 중심점이고, 위키데이터 점은 역 위치를 사람이 찍은 값이라 이 정도 차이는 자연스럽다. 위키데이터에는 역 번호 908도 있다 |

'장소' 수(170)가 떼어 낸 객체 수보다 많은 것은 RDFS 추론 때문이다. 예를 들어 건물의 `jgk:locatedOn` 값은 범위(range)가 필지라서, 추론이 그 필지를 필지이자 장소로 알아낸다. 바깥 시스템이 받는 것도 이 추론 결과다.

## 국가기본도 대전환과 이어지는 점

- **영구 식별자를 바깥과 잇기**: 우리 URI는 공식 번호로 만들고, 같은 실물을 가리키는 바깥 번호(위키데이터 Q59989)는 `owl:sameAs`로 덧붙인다. 바깥 번호가 바뀌어도 우리 URI는 그대로다.
- **같은 그래프, 여러 소비자**: 국토부, 지자체, 민간이 각자 아는 어휘(schema.org, INSPIRE, 위키데이터)로 같은 그래프를 읽는다. 정렬은 파일 하나로 따로 두어, 원천 그래프를 건드리지 않고 고칠 수 있다.
- **형식에 묶이지 않기**: Turtle, JSON-LD, N-Triples 어느 쪽으로 내보내도 내용이 같다. 그래서 받는 쪽 도구에 맞춰 형식을 고를 수 있다.
