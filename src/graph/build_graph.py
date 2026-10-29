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
DATASET_EMAP = JGK["dataset-juso-emap"]
DATASET_NGII = JGK["dataset-ngii-digital-map"]
DATASET_KBM = JGK["dataset-ngii-basemap"]
SOURCE_OF = {"navi": DATASET_NAVI, "emap": DATASET_EMAP, "gis": DATASET_GIS, "register": DATASET_REGISTER}


def present(v) -> bool:
    return v is not None and not (isinstance(v, float) and pd.isna(v)) and v != ""


def add(g: Graph, s, p, v, datatype=None, lang=None) -> None:
    if present(v):
        g.add((s, p, Literal(v, datatype=datatype, lang=lang)))


def add_feature(g: Graph, s: URIRef, cls, status: str) -> None:
    g.add((s, RDF.type, cls))
    g.add((s, RDF.type, GEO.Feature))
    g.add((s, JGK.status, STATUS[status]))


def add_geometry(g: Graph, s: URIRef, geom, source_gis_id=None, source=None) -> None:
    """source를 주면 모양 노드에 출처를 단다. 객체와 모양의 출처가 다를 때 쓴다 (공개 보류도 모양만 걸린다)."""
    # 좌표를 경도, 위도 순서로 쓰고 CRS를 적지 않으면 GeoSPARQL 기본값인 CRS84로 읽힌다
    gu = URIRef(uri.geometry(str(s)))
    g.add((s, GEO.hasGeometry, gu))
    g.add((s, GEO.hasDefaultGeometry, gu))
    g.add((gu, RDF.type, GEO.Geometry))
    g.add((gu, GEO.asWKT, Literal(shapely.to_wkt(geom, rounding_precision=7), datatype=GEO.wktLiteral)))
    add(g, gu, JGK.sourceGisBuildingId, source_gis_id)
    if source is not None:
        g.add((gu, DCTERMS.source, source))


def read_optional(proc: Path, name: str):
    """전자지도가 없을 때 만들어지지 않는 결과는 빈 표로 읽는다."""
    import pyogrio
    gpkg = proc / "objects.gpkg"
    if name in set(pyogrio.list_layers(gpkg)[:, 0]):
        return gpd.read_file(gpkg, layer=name)
    return gpd.GeoDataFrame({"uri": []}, geometry=[])


def add_emap(g: Graph, proc: Path) -> None:
    """도로, 도로 구간, 법정동, 기초구역과 그 연결."""
    for r in pd.read_csv(proc / "road.csv", dtype=str, keep_default_na=False).itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.Road, "Active")
        add(g, s, JGK.roadCode, r.road_code)
        add(g, s, RDFS.label, r.name, lang="ko")
        g.add((s, DCTERMS.source, SOURCE_OF[r.source]))
    for r in read_optional(proc, "road_segment").itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.RoadSegment, "Active")
        add(g, s, JGK.roadSegmentNo, f"{r.sigungu_code}-{r.segment_no}")
        add(g, s, RDFS.label, r.name, lang="ko")
        add(g, s, JGK.roadClass, r.road_class)
        add(g, s, JGK.roadWidth, r.width_m, XSD.decimal)
        add(g, s, JGK.roadLength, r.length_m, XSD.decimal)
        g.add((s, JGK.segmentOf, URIRef(r.road_uri)))
        g.add((s, DCTERMS.source, DATASET_EMAP))
        add_geometry(g, s, r.geometry)
    for layer, cls, code_prop, code_col in (("legal_dong", JGK.LegalDong, JGK.legalDongCode, "legal_dong_code"),
                                            ("basic_zone", JGK.BasicZone, JGK.basicZoneNo, "zone_no")):
        for r in read_optional(proc, layer).itertuples():
            s = URIRef(r.uri)
            add_feature(g, s, cls, "Active")
            add(g, s, code_prop, getattr(r, code_col))
            add(g, s, RDFS.label, r.name if layer == "legal_dong" else f"기초구역 {r.zone_no}", lang="ko")
            if r.source in SOURCE_OF:
                g.add((s, DCTERMS.source, SOURCE_OF[r.source]))
            else:  # 번호만 PNU에서 얻은 법정동
                g.add((s, DCTERMS.source, DATASET_GIS))
            if r.geometry is not None:
                add_geometry(g, s, r.geometry, source=SOURCE_OF[r.geometry_source])
    for r in pd.read_csv(proc / "entrance_road.csv", dtype=str).itertuples():
        g.add((URIRef(r.entrance_uri), JGK.facesRoad, URIRef(r.segment_uri)))
    for r in pd.read_csv(proc / "building_zone.csv", dtype=str, keep_default_na=False).itertuples():
        b = URIRef(r.building_uri)
        g.add((b, JGK.inLegalDong, URIRef(r.legal_dong_uri)))
        if r.zone_uri:
            g.add((b, JGK.inBasicZone, URIRef(r.zone_uri)))
        if r.road_uri:
            g.add((b, JGK.addressRoad, URIRef(r.road_uri)))
    for r in pd.read_csv(proc / "parcel_legal_dong.csv", dtype=str).itertuples():
        g.add((URIRef(r.parcel_uri), JGK.inLegalDong, URIRef(r.legal_dong_uri)))


def add_maps(g: Graph, proc: Path) -> None:
    """국가기본도와 수치지형도 건물, 그리고 우리 건물에서 그 건물로 가는 연결."""
    for r in read_optional(proc, "basemap_building").itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.BaseMapFeature, "Active")
        g.add((s, JGK.featureCode, JGK["basemap-B0010000"]))
        add(g, s, JGK.basemapId, r.nf_id)
        add(g, s, JGK.molitUfid, r.molit_ufid)
        add(g, s, RDFS.label, r.name, lang="ko")
        add(g, s, JGK.mapKindCode, r.kind_code)
        add(g, s, JGK.mapUseCode, r.use_code)
        add(g, s, JGK.mapFloors, None if pd.isna(r.floors) else int(r.floors), XSD.integer)
        add(g, s, JGK.pnu, r.pnu)
        add(g, s, JGK.matchIoU, None if pd.isna(r.match_iou) else round(float(r.match_iou), 3), XSD.decimal)
        g.add((s, DCTERMS.source, DATASET_KBM))
        add_geometry(g, s, r.geometry)
    for r in read_optional(proc, "digital_map_building").itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.DigitalMapFeature, "Active")
        g.add((s, JGK.featureCode, JGK["basemap-B0010000"]))
        add(g, s, JGK.ufid, r.ufid)
        add(g, s, JGK.sheetCode, r.sheet_code)
        add(g, s, RDFS.label, r.name, lang="ko")
        add(g, s, JGK.mapKind, r.kind)
        add(g, s, JGK.mapUse, r.use)
        add(g, s, JGK.mapFloors, None if pd.isna(r.floors) else int(r.floors), XSD.integer)
        add(g, s, JGK.matchIoU, None if pd.isna(r.match_iou) else round(float(r.match_iou), 3), XSD.decimal)
        g.add((s, DCTERMS.source, DATASET_NGII))
        add_geometry(g, s, r.geometry)
    for name, prop in (("basemap", JGK.basemapFeature), ("digital_map", JGK.digitalMapFeature)):
        path = proc / f"building_{name}.csv"
        if not path.exists():
            continue
        for r in pd.read_csv(path, dtype=str, keep_default_na=False).itertuples():
            g.add((URIRef(r.building_uri), prop, URIRef(r.map_uri)))
            if name == "basemap":  # 견준 결과는 지도 쪽 객체에 적는다 (공개 보류가 같이 걸린다)
                m = URIRef(r.map_uri)
                g.add((m, JGK.pnuAgrees, Literal(r.pnu_same == "True")))
                if r.address_same in ("True", "False"):
                    g.add((m, JGK.addressAgrees, Literal(r.address_same == "True")))


def build(proc: Path = PROC) -> Graph:
    """proc 폴더의 객체 파일(src.model.build_objects 결과)로 그래프를 만든다."""
    gpkg = proc / "objects.gpkg"
    buildings = gpd.read_file(gpkg, layer="building")
    entrances = gpd.read_file(gpkg, layer="entrance")
    stations = gpd.read_file(gpkg, layer="station")
    facilities = gpd.read_file(gpkg, layer="public_facility")
    facility_links = pd.read_csv(proc / "facility_building.csv", dtype=str)
    parcels = pd.read_csv(proc / "parcel.csv", dtype=str, keep_default_na=False)
    dongs = pd.read_csv(proc / "admin_dong.csv", dtype=str, keep_default_na=False)
    links = pd.read_csv(proc / "building_entrance.csv", dtype=str)

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
        source_of = {"gis": DATASET_GIS, "navi": DATASET_NAVI, "register": DATASET_REGISTER}
        for k in filter(None, r.sources.split(";")):
            g.add((s, DCTERMS.source, source_of[k]))

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
    add_emap(g, proc)
    add_maps(g, proc)
    return g


def main() -> None:
    g = build()
    g.serialize(OUT_TTL, format="turtle", encoding="utf-8")
    print(f"triples: {len(g)} -> {OUT_TTL.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
