"""URI 만들기. 규칙은 docs/uri-policy.md를 따른다."""

BASE = "https://yeodh10.github.io/jeungmi-geokg/id"


def building(bldg_mgt_no: str) -> str:
    assert len(bldg_mgt_no) == 25, bldg_mgt_no
    return f"{BASE}/building/{bldg_mgt_no}"


def building_gis(gis_building_id: str) -> str:
    assert len(gis_building_id) == 28, gis_building_id
    return f"{BASE}/building-gis/{gis_building_id}"


def parcel(pnu: str) -> str:
    assert len(pnu) == 19, pnu
    return f"{BASE}/parcel/{pnu}"


def admin_dong(adm_dong_code: str) -> str:
    assert len(adm_dong_code) == 10, adm_dong_code
    return f"{BASE}/admin-dong/{adm_dong_code}"


def main_entrance(bldg_mgt_no: str) -> str:
    assert len(bldg_mgt_no) == 25, bldg_mgt_no
    return f"{BASE}/entrance/{bldg_mgt_no}-main"


def sub_entrance(sigungu_code: str, entrance_seq: str) -> str:
    assert len(sigungu_code) == 5, sigungu_code
    return f"{BASE}/entrance/{sigungu_code}-{entrance_seq}"


def station(bldg_mgt_no: str) -> str:
    assert len(bldg_mgt_no) == 25, bldg_mgt_no
    return f"{BASE}/station/{bldg_mgt_no}"


def public_facility(agency_code: str) -> str:
    assert len(agency_code) == 7 and agency_code.isdigit(), agency_code
    return f"{BASE}/public-facility/{agency_code}"


def geometry(feature_uri: str) -> str:
    return f"{feature_uri}/geometry"
