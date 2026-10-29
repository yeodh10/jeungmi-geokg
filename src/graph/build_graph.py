"""객체를 GeoSPARQL 1.1 그래프(Turtle)로 만든다.

실행: python -m src.graph.build_graph
입력: src.model.build_objects 결과 (data/processed/objects.gpkg 등)
출력: data/processed/jeungmi_geokg.ttl (어휘는 src/graph/ontology.ttl에 따로 있다)
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS

from src.model import uri

ROOT = Path(__file__).resolve().parents[2]
PROC = ROOT / "data" / "processed"
OUT_TTL = PROC / "jeungmi_geokg.ttl"

JGK = Namespace("https://yeodh10.github.io/jeungmi-geokg/def#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

STATUS = {"Active": JGK.Active, "AddressUnlinked": JGK.AddressUnlinked,
          "AddressLinked": JGK.AddressLinked, "Retired": JGK.Retired}
DATASET_GIS = JGK["dataset-gis-building"]
DATASET_NAVI = JGK["dataset-navi-db"]
DATASET_REGISTER = JGK["dataset-building-register"]
DATASET_PUBLIC_OFFICE = JGK["dataset-public-office"]
DATASET_AGENCY_CODE = JGK["dataset-agency-code"]


def present(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and v != ""


def add(g: Graph, s, p, v, datatype=None, lang=None) -> None:
    if present(v):
        g.add((s, p, Literal(v, datatype=datatype, lang=lang)))


def add_feature(g: Graph, s: URIRef, cls, status: str) -> None:
    g.add((s, RDF.type, cls))
    g.add((s, RDF.type, GEO.Feature))
    g.add((s, JGK.status, STATUS[status]))


def add_geometry(g: Graph, s: URIRef, geom, source_gis_id=None) -> None:
    # 좌표를 경도, 위도 순서로 쓰고 CRS를 적지 않으면 GeoSPARQL 기본값인 CRS84로 읽힌다
    gu = URIRef(uri.geometry(str(s)))
    g.add((s, GEO.hasGeometry, gu))
    g.add((s, GEO.hasDefaultGeometry, gu))
    g.add((gu, RDF.type, GEO.Geometry))
    g.add((gu, GEO.asWKT, Literal(shapely.to_wkt(geom, rounding_precision=7), datatype=GEO.wktLiteral)))
    add(g, gu, JGK.sourceGisBuildingId, source_gis_id)


def main() -> None:
    gpkg = PROC / "objects.gpkg"
    buildings = gpd.read_file(gpkg, layer="building")
    entrances = gpd.read_file(gpkg, layer="entrance")
    stations = gpd.read_file(gpkg, layer="station")
    facilities = gpd.read_file(gpkg, layer="public_facility")
    facility_links = pd.read_csv(PROC / "facility_building.csv", dtype=str)
    parcels = pd.read_csv(PROC / "parcel.csv", dtype=str, keep_default_na=False)
    dongs = pd.read_csv(PROC / "admin_dong.csv", dtype=str, keep_default_na=False)
    links = pd.read_csv(PROC / "building_entrance.csv", dtype=str)

    g = Graph()
    g.bind("jgk", JGK)
    g.bind("geo", GEO)
    g.bind("dcterms", DCTERMS)

    dong_uri = {r.adm_dong_code: URIRef(r.uri) for r in dongs.itertuples()}
    for r in dongs.itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.AdminDong, "Active")
        add(g, s, JGK.admDongCode, r.adm_dong_code)
        add(g, s, RDFS.label, r.adm_dong_name, lang="ko")
        g.add((s, DCTERMS.source, DATASET_NAVI))

    for r in parcels.itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.Parcel, "Active")
        add(g, s, JGK.pnu, r.pnu)
        add(g, s, JGK.siteArea, r.site_area, XSD.decimal)
        add(g, s, JGK.totalFloorArea, r.total_floor_area, XSD.decimal)
        add(g, s, JGK.households, r.households, XSD.integer)
        add(g, s, JGK.mainBuildingCount, r.main_building_count, XSD.integer)
        add(g, s, JGK.complexMainUse, r.complex_main_use)
        if present(r.site_area):
            g.add((s, DCTERMS.source, DATASET_REGISTER))

    for r in buildings.itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.Building, r.status)
        add(g, s, JGK.buildingMgtNo, r.bldg_mgt_no)
        if r.kind == "building-gis":
            add(g, s, JGK.gisBuildingId, r.gis_building_id)
        add(g, s, RDFS.label, r.label, lang="ko")
        add(g, s, JGK.roadAddress, r.road_address)
        add(g, s, JGK.useName, r.use_name)
        add(g, s, JGK.structureName, r.structure_name)
        add(g, s, JGK.approvalDate, r.approval_date, XSD.date)
        add(g, s, JGK.floorsAbove, None if pd.isna(r.floors_above) else int(r.floors_above), XSD.integer)
        add(g, s, JGK.floorsBelow, None if pd.isna(r.floors_below) else int(r.floors_below), XSD.integer)
        if present(r.pnu):
            g.add((s, JGK.locatedOn, URIRef(uri.parcel(r.pnu))))
        if present(r.adm_dong_code):
            g.add((s, JGK.within, dong_uri[r.adm_dong_code]))
        if r.kind == "building":
            g.add((s, DCTERMS.source, DATASET_NAVI))
        if r.attr_source == "gis":
            g.add((s, DCTERMS.source, DATASET_GIS))
        add_geometry(g, s, r.geometry, r.gis_building_id if r.attr_source == "gis" else None)

    for r in entrances.itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.BuildingEntrance, "Active")
        add(g, s, JGK.entranceType, r.entrance_type)
        add(g, s, JGK.subEntranceSeq, r.sub_entrance_seq)
        if present(r.adm_dong_code):
            g.add((s, JGK.within, dong_uri[r.adm_dong_code]))
        g.add((s, DCTERMS.source, DATASET_NAVI))
        add_geometry(g, s, r.geometry)

    for r in links.itertuples():
        g.add((URIRef(r.building_uri), JGK.hasEntrance, URIRef(r.entrance_uri)))

    for r in stations.itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.Station, "Active")
        add(g, s, RDFS.label, r.name, lang="ko")
        add(g, s, JGK.lineName, r.line)
        g.add((s, JGK.housedIn, URIRef(r.building_uri)))
        if present(r.adm_dong_code):
            g.add((s, JGK.within, dong_uri[r.adm_dong_code]))
        g.add((s, DCTERMS.source, DATASET_NAVI))
        add_geometry(g, s, r.geometry)

    for r in facilities.itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.PublicFacility, "Active")
        add(g, s, RDFS.label, r.label, lang="ko")
        add(g, s, JGK.agencyCode, r.agency_code)
        add(g, s, JGK.agencyName, r.agency_name)
        add(g, s, JGK.facilityType, r.facility_type)
        if present(r.adm_dong_code):
            g.add((s, JGK.within, dong_uri[r.adm_dong_code]))
        g.add((s, DCTERMS.source, DATASET_PUBLIC_OFFICE))
        g.add((s, DCTERMS.source, DATASET_AGENCY_CODE))
        add_geometry(g, s, r.geometry)
    for r in facility_links.itertuples():
        g.add((URIRef(r.facility_uri), JGK.housedIn, URIRef(r.building_uri)))

    g.serialize(OUT_TTL, format="turtle", encoding="utf-8")
    print(f"triples: {len(g)} -> {OUT_TTL.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
