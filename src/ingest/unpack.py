"""data/raw/_archives/에 보관한 원본 압축 파일을 정해진 폴더로 푼다.

실행: python -m src.ingest.unpack
다른 컴퓨터에서 이어 할 때 _archives 폴더만 옮겨 오면 이 스크립트로 data/raw/를 다시 만들 수 있다.
.7z 파일은 7-Zip(7z.exe)이 필요하다. 없으면 `pip install py7zr`로 대신 푼다.
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# (압축 파일 이름 패턴, 풀 폴더, 풀 파일 패턴). 풀 파일 패턴이 None이면 전부 푼다.
# 범위가 서울이라 전국 파일 중 서울 것과 공통 파일만 푼다.
PLAN = [
    ("AL_D010_11_*.zip", "vworld_gis_building", None),
    ("민원행정기관전자지도_*.zip", "juso_public_office", None),
    ("*내비게이션용DB_전체분.7z", "juso_navi_db",
     ["match_build_seoul.txt", "match_jibun_seoul.txt", "match_rs_entrc.txt", "*.pdf"]),
    ("서울시 건축물대장 총괄표제부.csv", "seoul_building_register", None),
    ("*기관코드 전체자료.zip", "agency_code", ["*유형분류 의미추가*.txt"]),
    ("도로명주소전자지도_서울특별시*.zip", "juso_emap", None),
    # 국토정보플랫폼 수치지형도 Ver2.0 (도엽 하나에 압축 하나). {sheet}는 파일 이름의 도엽코드 8자리
    ("(B010)수치지도_*.zip", "ngii_digital_map/{sheet}", None),
    # 국토정보플랫폼 국가기본도 (사용자 정의 영역). 레이어마다 압축이 하나씩 들어 있어 레이어 이름 폴더로 푼다
    ("(B022)국가기본도*.zip", "ngii_basemap", "nested"),
    # 도로명주소 한글(주소DB)은 보류 중이라 풀지 않는다 (docs/decisions.md)
]


def zip_name(info: zipfile.ZipInfo) -> str:
    # UTF-8 표시가 없는 zip은 이름이 CP949로 들어 있다
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename  # 이미 제대로 읽힌 이름 (기관코드 전체자료 zip)


def wanted(name: str, patterns: list[str] | None) -> bool:
    return patterns is None or any(fnmatch.fnmatch(Path(name).name, p) for p in patterns)


def unpack_zip(src: Path, dest: Path, patterns: list[str] | None) -> list[str]:
    done = []
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            name = zip_name(info)
            if info.is_dir() or not wanted(name, patterns):
                continue
            with z.open(info) as fin, open(dest / Path(name).name, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            done.append(Path(name).name)
    return done


def unpack_nested_zip(src: Path, dest: Path) -> list[str]:
    """압축 안의 압축(레이어 이름.zip)을 dest/레이어 이름/ 으로 푼다."""
    import io
    done = []
    with zipfile.ZipFile(src) as outer:
        for info in outer.infolist():
            layer = Path(zip_name(info)).stem
            data = outer.read(info)
            if not zipfile.is_zipfile(io.BytesIO(data)):
                # 국토정보플랫폼이 가끔 레이어 압축 대신 오류 XML을 넣어 준다. 그 레이어만 건너뛴다
                print(f"[건너뜀] {src.name} 안의 {layer}: 압축 파일이 아님 ({data[:60]!r})")
                continue
            with zipfile.ZipFile(io.BytesIO(data)) as inner:
                for f in inner.infolist():
                    if f.is_dir():
                        continue
                    (dest / layer).mkdir(parents=True, exist_ok=True)
                    (dest / layer / Path(zip_name(f)).name).write_bytes(inner.read(f))
                    done.append(f"{layer}/{Path(zip_name(f)).name}")
    return done


def find_7z() -> str | None:
    for cand in [shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"]:
        if cand and Path(cand).exists():
            return cand
    return None


def unpack_7z(src: Path, dest: Path, patterns: list[str] | None) -> list[str]:
    exe = find_7z()
    if exe:
        args = [exe, "e", str(src), f"-o{dest}", "-y", "-bso0", "-bsp0"]
        subprocess.run(args + (patterns or []), check=True)
    else:
        try:
            import py7zr
        except ImportError:
            raise SystemExit("7-Zip이 없습니다. 7-Zip을 설치하거나 `pip install py7zr` 후 다시 실행하세요.")
        with py7zr.SevenZipFile(src) as z:
            names = [n for n in z.getnames() if wanted(n, patterns)]
            z.extract(path=dest, targets=names)
    return sorted(p.name for p in dest.iterdir() if wanted(p.name, patterns))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=ROOT / "data" / "raw")
    args = ap.parse_args()
    archives = ROOT / "data" / "raw" / "_archives"

    for pattern, folder, files in PLAN:
        found = sorted(archives.glob(pattern))
        if not found:
            print(f"[없음] {pattern} -> _archives에 넣어 주세요 (docs/data-sources.md)")
            continue
        if "{sheet}" in folder:  # 도엽마다 압축이 따로 있으면 모두 도엽 폴더로 푼다
            jobs = [(src, folder.format(sheet=re.search(r"_(\d{8})_", src.name).group(1))) for src in found]
        else:
            jobs = [(found[-1], folder)]  # 같은 자료가 여러 개면 이름순 마지막(가장 최근 기준일)
        for src, sub in jobs:
            dest = args.raw_dir / sub
            dest.mkdir(parents=True, exist_ok=True)
            if files == "nested":
                done = unpack_nested_zip(src, dest)
            elif src.suffix == ".zip":
                done = unpack_zip(src, dest, files)
            elif src.suffix == ".7z":
                done = unpack_7z(src, dest, files)
            else:
                shutil.copy2(src, dest / src.name)
                done = [src.name]
            print(f"[완료] {src.name} -> {sub}/ ({len(done)}개 파일)")


if __name__ == "__main__":
    main()
