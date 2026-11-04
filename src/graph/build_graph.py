"""객체를 GeoSPARQL 1.1 그래프(Turtle)로 만든다.

실행: python -m src.graph.build_graph
입력: src.model.build_objects 결과 (data/processed/objects.gpkg 등), src.model.build_business 결과 (business.csv)
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
SOSA = Namespace("http://www.w3.org/ns/sosa/")
PROV = Namespace("http://www.w3.org/ns/prov#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")

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
DATASET_SDOT = JGK["dataset-sdot"]
DATASET_SBIZ = JGK["dataset-sbiz-store"]
DATASET_CADASTRAL = JGK["dataset-cadastral"]
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
        method = SURVEY_METHOD.get(getattr(r, "survey_method", None))
        if method is not None:
            g.add((s, JGK.surveyMethod, method))
        g.add((s, DCTERMS.source, DATASET_NGII))
        add_geometry(g, s, r.geometry)
    for name, prop in (("basemap", JGK.basemapFeature), ("digital_map", JGK.digitalMapFeature)):
        path = proc / f"building_{name}.csv"
        if not path.exists():
            continue
        rows = pd.read_csv(path, dtype=str, keep_default_na=False)
        for r in rows.itertuples():
            g.add((URIRef(r.building_uri), prop, URIRef(r.map_uri)))
        if name != "basemap":
            continue
        # 견준 결과는 지도 쪽 객체에 적는다 (공개 보류가 같이 걸린다). 폴리곤을 함께 쓰는 건물은 주소가 서로 다르므로 하나라도 같으면 같다고 적는다
        for map_uri, same in rows.groupby("map_uri"):
            m = URIRef(map_uri)
            g.add((m, JGK.pnuAgrees, Literal(bool(same["pnu_same"].eq("True").any()))))
            compared = same["address_same"].isin(["True", "False"])
            if compared.any():
                g.add((m, JGK.addressAgrees, Literal(bool(same["address_same"].eq("True").any()))))


# 수치지형도 '측량방법' 값 -> 측량 방법 분류 (ontology.ttl jgk:SurveyMethodScheme)
SURVEY_METHOD = {"사진측량": JGK["method-aerial-photogrammetry"], "항공레이저측량": JGK["method-airborne-lidar"],
                 "레이저측량": JGK["method-airborne-lidar"], "현지측량": JGK["method-field-survey"], "지상측량": JGK["method-field-survey"],
                 "MMS": JGK["method-mms"]}


def add_survey(g: Graph, proc: Path) -> None:
    """측량 성과의 계보: 도엽(측량 성과, dcat:Dataset)과 그 성과를 낸 작업(prov:Activity), 수치지형도 건물에서 도엽으로 가는 연결.

    값은 국토정보플랫폼이 성과와 함께 주는 ISO 19115-3 메타데이터(src.model.survey_meta)에서 온다. 기본측량성과라 출처를
    수치지형도로 적어 공개 사이트에서 빠진다. 측량 방법은 성과에 든 지형지물의 '측량방법' 값에서 모은다.
    """
    import shapely.geometry as sg
    path = proc / "survey_sheet.csv"
    if not path.exists():
        return
    sheets = pd.read_csv(path, dtype=str, keep_default_na=False)
    maps = read_optional(proc, "digital_map_building")
    methods = {}
    if len(maps) and "survey_method" in maps.columns:
        for code, m in zip(maps["sheet_code"], maps["survey_method"]):
            if SURVEY_METHOD.get(m) is not None:
                methods.setdefault(code, set()).add(SURVEY_METHOD[m])
        for r in maps.itertuples():
            if r.sheet_code in set(sheets["sheet"]):
                g.add((URIRef(r.uri), PROV.wasDerivedFrom, URIRef(uri.survey_sheet(r.sheet_code))))
    for r in sheets.itertuples():
        s, a = URIRef(uri.survey_sheet(r.sheet)), URIRef(uri.survey_activity("ngii", r.notice_no))
        add_feature(g, s, JGK.SurveyProduct, "Active")
        g.add((s, RDF.type, DCAT.Dataset))
        add(g, s, RDFS.label, f"수치지형도 도엽 {r.sheet} (1:{int(r.scale):,})" if r.scale else f"수치지형도 도엽 {r.sheet}", lang="ko")
        add(g, s, JGK.sheetCode, r.sheet)
        add(g, s, JGK.mapScale, int(r.scale) if r.scale else None, XSD.integer)
        add(g, s, DCTERMS.rights, r.use_limit, lang="ko")
        add(g, s, DCTERMS.accrualPeriodicity, r.maintenance, lang="ko")
        add(g, s, JGK.crsCode, r.crs)
        g.add((s, DCTERMS.conformsTo, JGK["spec-digital-map-work-rule"]))
        g.add((s, PROV.wasGeneratedBy, a))
        g.add((s, DCTERMS.source, DATASET_NGII))
        if all(getattr(r, k) for k in ("west", "east", "south", "north")):
            add_geometry(g, s, sg.box(float(r.west), float(r.south), float(r.east), float(r.north)))
        add_feature_activity(g, a, r, methods.get(r.sheet, set()))


def add_feature_activity(g: Graph, a: URIRef, r, methods: set) -> None:
    """측량 작업 노드. 같은 고시로 낸 도엽은 같은 작업을 가리킨다."""
    g.add((a, RDF.type, PROV.Activity))
    g.add((a, RDF.type, JGK.SurveyActivity))
    add(g, a, RDFS.label, r.project, lang="ko")
    add(g, a, JGK.noticeNo, r.notice_no)
    add(g, a, JGK.workKind, r.work_kind, lang="ko")
    add(g, a, JGK.photoYear, int(r.photo_year) if r.photo_year else None, XSD.integer)
    add(g, a, JGK.fieldSurveyYear, int(r.field_year) if r.field_year else None, XSD.integer)
    add(g, a, JGK.productionYear, int(r.made_year) if r.made_year else None, XSD.integer)
    add(g, a, PROV.startedAtTime, f"{r.begin}T00:00:00+09:00" if r.begin else None, XSD.dateTime)
    add(g, a, PROV.endedAtTime, f"{r.end}T00:00:00+09:00" if r.end else None, XSD.dateTime)
    g.add((a, PROV.wasAssociatedWith, JGK["agent-ngii"]))
    for m in sorted(methods):
        g.add((a, JGK.surveyMethod, m))
    g.add((a, DCTERMS.source, DATASET_NGII))


def add_sensors(g: Graph, proc: Path) -> None:
    """S-DoT 센서(src.model.build_sensors 결과)와 1시간 평균 관측값을 SOSA로."""
    path = proc / "sensor.csv"
    if not path.exists():
        return
    for r in pd.read_csv(path, dtype=str, keep_default_na=False).itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.Sensor, "Active")
        g.add((s, RDF.type, SOSA.Sensor))
        add(g, s, RDFS.label, f"S-DoT 센서 {r.serial}", lang="ko")
        add(g, s, JGK.sensorSerial, r.serial)
        add(g, s, JGK.sensorModel, r.model)
        add(g, s, JGK.installAddress, r.address)
        add(g, s, JGK.installArea, r.area_type)
        if r.building_uri:
            g.add((s, JGK.addressBuilding, URIRef(r.building_uri)))
            add(g, s, JGK.addressBuildingDistance, r.building_dist_m, XSD.decimal)
        g.add((s, DCTERMS.source, DATASET_SDOT))
        add_geometry(g, s, shapely.Point(float(r.lon), float(r.lat)))
    obs = proc / "sensor_obs.csv"
    if not obs.exists():
        return
    observed = set()
    prop_ko = {"temperature": "기온", "humidity": "습도", "noise": "소음", "illuminance": "조도", "uv": "자외선"}
    for r in pd.read_csv(obs, dtype=str, keep_default_na=False).itertuples():
        s, sensor, prop = URIRef(r.uri), URIRef(r.sensor_uri), JGK[r.prop]
        g.add((s, RDF.type, SOSA.Observation))
        add(g, s, RDFS.label, f"{r.sensor_uri.rsplit('/', 1)[-1]} {prop_ko[r.prop]} {r.time.replace('T', ' ')[:16]}", lang="ko")
        g.add((s, SOSA.madeBySensor, sensor))
        g.add((s, SOSA.observedProperty, prop))
        g.add((s, SOSA.hasSimpleResult, Literal(r.value, datatype=XSD.decimal)))
        g.add((s, SOSA.resultTime, Literal(r.time, datatype=XSD.dateTime)))
        g.add((s, DCTERMS.source, DATASET_SDOT))
        observed.add((sensor, prop))
    for sensor, prop in observed:
        g.add((sensor, SOSA.observes, prop))


def add_business(g: Graph, proc: Path) -> None:
    """상호(소상공인 상가정보, src.model.build_business 결과). 원천 행 하나가 객체 하나다.

    공개 여부(주택 건물 안 업체)는 그래프에 적지 않는다. 공개 사이트가 건물 용도, 층, 대분류로 그때 고른다 (src.graph.publish).
    """
    path = proc / "business.csv"
    if not path.exists():
        return
    for r in pd.read_csv(path, dtype=str, keep_default_na=False).itertuples():
        s = URIRef(r.uri)
        add_feature(g, s, JGK.Business, "Active")
        add(g, s, JGK.businessNo, r.store_no)
        add(g, s, RDFS.label, r.name, lang="ko")
        add(g, s, JGK.branchName, r.branch)
        add(g, s, JGK.categoryLarge, r.cat_large)
        add(g, s, JGK.categoryMiddle, r.cat_middle)
        add(g, s, JGK.categorySmall, r.cat_small)
        add(g, s, JGK.floorInfo, r.floor)
        add(g, s, JGK.roadAddress, r.road_address)
        if r.building_uri:
            g.add((s, JGK.inBuilding, URIRef(r.building_uri)))
        if r.parcel_uri:
            g.add((s, JGK.onParcel, URIRef(r.parcel_uri)))
        add(g, s, JGK.linkedBy, r.linked_by)
        g.add((s, DCTERMS.source, DATASET_SBIZ))
        add_geometry(g, s, shapely.Point(float(r.lon), float(r.lat)))


def add_catalog(g: Graph) -> None:
    """원천 자료 목록(DCAT): 어휘에 적은 자료마다 목록에 넣고, 공개 등급을 config/area.yaml의 공개 보류 목록에서 적는다.
    공개 등급을 어휘에 손으로 적지 않는 것은 공개 사이트를 거르는 목록(src.graph.public.hold_sources)과 어긋나지 않게 하려는 것이다."""
    import yaml
    onto = Graph().parse(ROOT / "src" / "graph" / "ontology.ttl", format="turtle")
    with open(ROOT / "config" / "area.yaml", encoding="utf-8") as f:
        hold = {JGK[d] for d in yaml.safe_load(f)["publish"]["hold_sources"]}
    for d in sorted(onto.subjects(RDF.type, JGK.Dataset)):
        g.add((JGK.catalog, DCAT.dataset, d))
        g.add((d, DCTERMS.accessRights, Literal("공개 보류" if d in hold else "공개", lang="ko")))


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
    g.bind("sosa", SOSA)

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
    # 필지 경계와 지목 (연속지적도). 모양 노드와 필지 객체 모두에 출처를 단다 (지목, 기준일이 필지 객체에 있다)
    for r in read_optional(proc, "parcel_shape").itertuples():
        s = URIRef(r.uri)
        add_geometry(g, s, r.geometry, source=DATASET_CADASTRAL)
        g.add((s, DCTERMS.source, DATASET_CADASTRAL))
        add(g, s, JGK.landCategory, r.land_category, lang="ko")
        add(g, s, JGK.cadastralDate, r.cadastral_date, XSD.date)

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
        if "height_m" in buildings.columns and not pd.isna(r.height_m):
            add(g, s, JGK.heightM, f"{float(r.height_m):.2f}", XSD.decimal)
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
    add_sensors(g, proc)
    add_business(g, proc)
    add_survey(g, proc)
    add_catalog(g)
    return g


def main() -> None:
    g = build()
    g.serialize(OUT_TTL, format="turtle", encoding="utf-8")
    print(f"triples: {len(g)} -> {OUT_TTL.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
