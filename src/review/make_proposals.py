"""규칙으로 맞추지 못한 공공시설에 기관코드 후보를 찾아 변경 제안으로 만든다.

실행: python -m src.review.make_proposals
입력: src.model.build_objects 결과 (review_public_facility.csv, objects.gpkg 등)
출력: data/processed/review/proposals.json 에 새 제안 추가 (이미 있는 제안은 그대로 둔다)

이름이 비슷한 기관을 기계가 고르지만, 같은 기관인지는 사람이 검토 화면에서 정한다.
"""
from __future__ import annotations

import difflib
import re

import geopandas as gpd
import pandas as pd
import shapely

from src.model import uri
from src.model.build_objects import (ADDRESS_KEY, AGENCY_DIR, AREA_GPKG, OUT_GPKG, PROC,
                                     housing_buildings)
from src.review.proposals import Change, Proposal, ProposalStore, Term, now

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
GEO = "http://www.opengis.net/ont/geosparql#"
JGK = "https://yeodh10.github.io/jeungmi-geokg/def#"
DCTERMS = "http://purl.org/dc/terms/"
MIN_SIMILARITY = 0.6  # 이보다 덜 비슷하면 후보로 내지 않는다


def load_agency_details() -> pd.DataFrame:
    path = next(AGENCY_DIR.glob("*유형분류 의미추가*.txt"))
    cols = ["기관코드", "전체기관명", "최하위기관명", "전화번호", "생성일자", "존폐여부"]
    ag = pd.read_csv(path, sep="\t", dtype=str, encoding="cp949", keep_default_na=False,
                     quoting=3, usecols=cols)
    return ag[ag["존폐여부"] == "0"]


def best_candidate(name: str, sido: str, sigungu: str, ag: pd.DataFrame):
    """같은 시군구 기관 중 이름이 가장 비슷한 하나와 유사도."""
    gu = sigungu.removesuffix("구").removesuffix("군").removesuffix("시")
    pool = ag[ag["전체기관명"].str.contains(sido[:2]) & ag["전체기관명"].str.contains(gu)]
    if pool.empty:
        return None, 0.0
    norm = re.sub(r"\s+", "", name)
    scores = pool["최하위기관명"].map(lambda n: difflib.SequenceMatcher(None, norm, re.sub(r"\s+", "", n)).ratio())
    i = scores.idxmax()
    return pool.loc[i], float(scores[i])


def facility_changes(target: str, office, agency, building_uris: list[str], dong_uri: str | None) -> list[Change]:
    lit = lambda v, **kw: Term(kind="literal", value=str(v), **kw)
    ref = lambda v: Term(kind="uri", value=v)
    geom = uri.geometry(target)
    adds = [
        (target, RDF_TYPE, ref(JGK + "PublicFacility")),
        (target, RDF_TYPE, ref(GEO + "Feature")),
        (target, JGK + "status", ref(JGK + "Active")),
        (target, RDFS_LABEL, lit(office["기관명"], lang="ko")),
        (target, JGK + "agencyCode", lit(agency["기관코드"])),
        (target, JGK + "agencyName", lit(agency["전체기관명"])),
        (target, JGK + "facilityType", lit(f"{office['유형']}/{office['상세분류']}")),
        (target, DCTERMS + "source", ref(JGK + "dataset-public-office")),
        (target, DCTERMS + "source", ref(JGK + "dataset-agency-code")),
        (target, GEO + "hasGeometry", ref(geom)),
        (target, GEO + "hasDefaultGeometry", ref(geom)),
        (geom, RDF_TYPE, ref(GEO + "Geometry")),
        (geom, GEO + "asWKT", lit(shapely.to_wkt(office.geometry, rounding_precision=7), datatype=GEO + "wktLiteral")),
    ]
    adds += [(target, JGK + "housedIn", ref(b)) for b in building_uris]
    if dong_uri:
        adds.append((target, JGK + "within", ref(dong_uri)))
    return [Change(op="add", s=s, p=p, o=o) for s, p, o in adds]


def main() -> None:
    review = pd.read_csv(PROC / "review_public_facility.csv", dtype=str, keep_default_na=False)
    offices = gpd.read_file(AREA_GPKG, layer="public_office")
    centers = gpd.read_file(AREA_GPKG, layer="navi_building_center")
    buildings = gpd.read_file(OUT_GPKG, layer="building").dropna(subset=["bldg_mgt_no"]).set_index("bldg_mgt_no")
    addr_index = centers.set_index(ADDRESS_KEY)["bldg_mgt_no"].sort_index()
    taken = set(gpd.read_file(OUT_GPKG, layer="public_facility")["agency_code"])
    agencies = load_agency_details()

    proposals = []
    for _, r in review.iterrows():
        office = offices[offices["기관명"] == r["기관명"]].iloc[0]
        sido, sigungu = office["도로명주소"].split()[:2]
        cand, score = best_candidate(r["기관명"], sido, sigungu, agencies)
        if cand is None or score < MIN_SIMILARITY or cand["기관코드"] in taken:
            print(f"[후보 없음] {r['기관명']}" + (f" (가장 비슷한 {cand['최하위기관명']} {score:.2f})" if cand is not None else ""))
            continue
        mgts = housing_buildings(office, addr_index, buildings["geometry"])
        dong = buildings.loc[mgts[0], "adm_dong_code"] if mgts else None
        target = uri.public_facility(cand["기관코드"])
        proposals.append(Proposal(
            id=f"rule-facility-{cand['기관코드']}",
            created_at=now(), source_kind="rule-candidate",
            source_detail="공공시설 기관코드 맞추기 규칙 1~3에 맞지 않아 이름이 가장 비슷한 기관을 후보로 냈다",
            action="create", target=target, target_type="PublicFacility",
            summary=f"'{r['기관명']}'을(를) 기관코드 {cand['기관코드']} '{cand['최하위기관명']}'(으)로 보고 공공시설로 등록한다",
            evidence=[
                f"민원행정기관 전자지도: {r['기관명']}, {office['도로명주소']}, 전화 {office['전화번호']}",
                f"행정표준코드: {cand['기관코드']} {cand['전체기관명']}, 전화 {cand['전화번호'] or '없음'}, 생성 {cand['생성일자']}",
                f"이름 유사도 {score:.2f} (기준 {MIN_SIMILARITY})",
                f"들어 있는 건물: {len(mgts)}개" + (f" ({buildings.loc[mgts[0], 'road_address']})" if mgts else ""),
            ],
            changes=facility_changes(target, office, cand, [buildings.loc[m, "uri"] for m in mgts],
                                     uri.admin_dong(dong) if dong else None),
            location=(office.geometry.x, office.geometry.y),
        ))
        print(f"[제안] {r['기관명']} -> {cand['기관코드']} {cand['최하위기관명']} ({score:.2f})")

    added = ProposalStore().add_new(proposals)
    print(f"새 제안 {added}건 (전체 후보 {len(proposals)}건)")


if __name__ == "__main__":
    main()
