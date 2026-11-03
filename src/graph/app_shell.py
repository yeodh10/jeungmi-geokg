"""지도 앱의 화면 틀(web/app/app.html)에 자리표시 값을 채운다. 공개 사이트와 로컬 서버가 같은 틀을 쓴다.

  render_app_html("public", build=...)  -> 공개 사이트의 /index.html, /map/index.html (src.graph.publish가 쓴다)
  render_app_html("local", extra=[...]) -> 로컬 서버의 /map (src/review/app.py가 extra를 넘긴다)

자리표시: __ROOT__, __STATIC__, __VENDOR__, __APP__, __BUILD__, __CONFIG__ (명세 5.1절).
__BUILD__는 앱 파일과 자료 파일을 ?v=로 받아 재배포 직후 옛 파일과 새 파일이 섞이지 않게 한다.
공개판은 publish가 정한 빌드 시각이고 publish_report.json의 build에도 적는다 (places.json의 built와 같은 값을 쓴다).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.graph.public import SITE

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "web" / "app" / "app.html"
SITE_PATH = "/" + SITE.split("://", 1)[1].split("/", 1)[1]  # /jeungmi-geokg
STARTED = datetime.now().strftime("%Y%m%d%H%M%S")  # 로컬 서버는 켠 시각
LOCAL_SOURCE = "/static/local/source-api.js"


def build_stamp() -> str:
    """지금 시각으로 만든 빌드 값 (숫자 14자리). 주소창에 그대로 넣을 수 있다."""
    return datetime.now().strftime("%Y%m%d%H%M%S")


def paths(mode: str) -> dict[str, str]:
    if mode == "public":
        return {"root": SITE_PATH, "static": SITE_PATH + "/assets", "vendor": SITE_PATH + "/assets/vendor", "app": SITE_PATH + "/assets/app"}
    if mode == "local":
        return {"root": "", "static": "/static", "vendor": "/static/vendor", "app": "/static/app"}
    raise ValueError(f"모르는 모드: {mode}")


def app_config(mode: str, build: str, extra=()) -> dict:
    p = paths(mode)
    source = p["app"] + "/source-static.js" if mode == "public" else LOCAL_SOURCE
    return {"mode": mode, "root": p["root"], "app": p["app"], "build": build, "source": source,
            "extra": [] if mode == "public" else list(extra)}


def render_app_html(mode: str, extra=(), build: str | None = None) -> str:
    """화면 틀을 채운 HTML. 공개판은 extra를 받지 않는다 (로컬 전용 모듈이 공개 사이트로 새지 않게)."""
    if mode == "public" and extra:
        raise ValueError("공개판에는 로컬 모듈을 넣지 않는다")
    build = build or (STARTED if mode == "local" else build_stamp())
    p = paths(mode)
    # 인라인 스크립트 안이라 </script>가 생기지 않게 '<'를 이스케이프한다
    config = json.dumps(app_config(mode, build, extra), ensure_ascii=False, separators=(", ", ": ")).replace("<", "\\u003c")
    html = SHELL.read_text(encoding="utf-8")
    for key, value in (("__CONFIG__", config), ("__STATIC__", p["static"]), ("__VENDOR__", p["vendor"]), ("__APP__", p["app"]),
                       ("__ROOT__", p["root"]), ("__BUILD__", build)):
        html = html.replace(key, value)
    return html
