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


def road(road_code: str) -> str:
    """도로명코드 12자리 (시군구코드 5 + 도로명번호 7)."""
    assert len(road_code) == 12 and road_code.isdigit(), road_code
    return f"{BASE}/road/{road_code}"


def road_segment(sigungu_code: str, segment_no) -> str:
    """도로구간 일련번호는 시군구 안에서만 겹치지 않아서 시군구코드를 앞에 붙인다 (보조출입구와 같은 꼴)."""
    assert len(sigungu_code) == 5 and str(segment_no).isdigit(), (sigungu_code, segment_no)
    return f"{BASE}/road-segment/{sigungu_code}-{int(segment_no)}"


def legal_dong(legal_dong_code: str) -> str:
    assert len(legal_dong_code) == 10 and legal_dong_code.isdigit(), legal_dong_code
    return f"{BASE}/legal-dong/{legal_dong_code}"


def basic_zone(zone_no: str) -> str:
    """국가기초구역번호 5자리 (우편번호와 같은 값)."""
    assert len(zone_no) == 5 and zone_no.isdigit(), zone_no
    return f"{BASE}/basic-zone/{zone_no}"


def basemap_feature(nf_id: str) -> str:
    """국가기본도 지형지물의 객체ID(nf_id) 17자리. 예: BLD01000000GFQR7T"""
    assert len(nf_id) == 17 and nf_id.isalnum(), nf_id
    return f"{BASE}/basemap/{nf_id}"


def digital_map_feature(ufid: str) -> str:
    """수치지형도 Ver2.0 지형지물의 유일식별자(UFID) 34자리."""
    assert len(ufid) == 34 and ufid.isalnum(), ufid
    return f"{BASE}/digital-map/{ufid}"


def geometry(feature_uri: str) -> str:
    return f"{feature_uri}/geometry"
