// 공개 장소 페이지, 목록, 소개(src/graph/publish.py가 만든 페이지)의 동작: 복사, 목록 거르기, 화면 밝기, 작은 지도
// 지도 앱은 이 파일을 쓰지 않는다 (web/app/). 배경 스타일, 밝기, 한국어 문구는 common.js에 있다
(() => {
  const $ = id => document.getElementById(id);

  // 복사: 실패하면 값을 선택된 입력칸으로 보여 줘 직접 복사하게 한다
  document.querySelectorAll("button.copy").forEach(b => b.onclick = async () => {
    const old = b.innerHTML, value = b.dataset.copy;
    try {
      await navigator.clipboard.writeText(value);
      b.textContent = b.dataset.done || "복사했습니다";
      setTimeout(() => { b.innerHTML = old; }, 1400);
    } catch (e) {
      let box = b.parentElement.querySelector("input.copy-fallback");
      if (!box) {
        box = Object.assign(document.createElement("input"), { className: "field copy-fallback", readOnly: true, value });
        box.setAttribute("aria-label", "자동으로 복사하지 못했습니다. 이 칸의 값을 직접 복사해 주세요");
        b.insertAdjacentElement("afterend", box);
      }
      box.focus(); box.select();
    }
  });

  // 목록 거르기 (공백 무시)
  const tight = s => String(s).toLowerCase().replace(/\s+/g, "");
  const filter = $("filter");
  if (filter) {
    const rows = [...document.querySelectorAll("#listing tbody tr")].map(tr => [tr, tight(tr.textContent)]);
    filter.oninput = () => {
      const q = tight(filter.value);
      let shown = 0;
      for (const [tr, t] of rows) { const on = !q || t.includes(q); tr.hidden = !on; shown += on; }
      $("nohit").hidden = shown > 0;
    };
  }

  // 화면 밝기: 지도 앱에서 고른 값과 같은 곳(localStorage jgk-theme)에 둔다
  const pick = $("theme-pick");
  if (pick) {
    pick.value = themeChoice();
    pick.onchange = () => setTheme(pick.value);
  }

  // 작은 지도: 페이지에 실린 모양을 구운 배경 위에 그린다. 페이지 스크롤을 막지 않게 두 손가락(또는 Ctrl+휠)으로만 움직인다
  const el = $("objmap");
  if (el && window.maplibregl) {
    const geom = JSON.parse(el.dataset.geom);
    const feature = { type: "Feature", geometry: geom, properties: {} };
    let map;
    try {
      map = new maplibregl.Map(mapOptions({ container: el, style: baseStyle(), attributionControl: { compact: true },
        cooperativeGestures: true, center: MAP_CENTER, zoom: 16 }));
    } catch (e) {  // WebGL을 못 쓰면 지도 자리에 안내만 둔다
      el.innerHTML = '<p class="small muted map-off">이 브라우저에서는 지도를 표시할 수 없습니다. 위치는 기계용 원본의 좌표에서 확인할 수 있습니다.</p>';
      return;
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    const paint = () => {
      const color = css("--select");
      map.setPaintProperty("obj-fill", "fill-color", color);
      map.setPaintProperty("obj-line", "line-color", color);
      map.setPaintProperty("obj-pt", "circle-color", color);
      map.setPaintProperty("obj-pt", "circle-stroke-color", css("--panel"));
    };
    whenStyleReady(map, () => {
      map.addSource("obj", { type: "geojson", data: feature });
      // 글자 아래에 그려 도로 이름이 가려지지 않게 한다
      map.addLayer({ id: "obj-fill", type: "fill", source: "obj", filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]],
        paint: { "fill-opacity": 0.25 } }, "base-road-label");
      map.addLayer({ id: "obj-line", type: "line", source: "obj", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-width": 2.5 } }, "base-road-label");
      map.addLayer({ id: "obj-pt", type: "circle", source: "obj", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 8, "circle-stroke-width": 3 } });
      paint();
      const b = boundsOf([feature]);
      if (geom.type === "Point") map.jumpTo({ center: b.getCenter(), zoom: 16 });
      else map.fitBounds(b, { padding: 40, maxZoom: 17.5, duration: 0 });
      // 작은 지도라 표기는 접어 둔다 (ⓘ 단추로 펴고, 같은 표기가 페이지 바닥글에도 있다)
      map.once("idle", () => {
        const attrib = el.querySelector(".maplibregl-ctrl-attrib");
        if (attrib) attrib.classList.remove("maplibregl-compact-show");
      });
    });
    onThemeChange(() => { restyleBase(map); if (map.getLayer("obj-fill")) paint(); });
    window.__objmap = map;  // 확인용 (헤드리스 시험이 읽는다)
  }
})();
