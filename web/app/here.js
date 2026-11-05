// 여기 보기 (명세 3.4절 5번, WP5): 빈 곳 누름, 우클릭, 길게 누르기(500ms)에 말풍선을 띄운다.
// 30m 안 건물, 고른 거리 기준점에서 직선 거리(있을 때만), 좌표, 여기서 주변 찾기, 여기서 재기(재기 기능이 있을 때만), 여기를 거리 기준으로.
// 범위(증미역 반경 1km) 밖이면 범위 안내를 낸다. 배경 길 이름을 누르면 같은 이름 길의 카드를 연다 (있으면 좋음 8).

const NEAR_M = 30;
const nf = n => Number(n).toLocaleString("ko-KR");
const meters = (a, b) => {
  const R = 6371008.8, r = Math.PI / 180;
  const dLat = (b[1] - a[1]) * r, dLng = (b[0] - a[0]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
};

// 말풍선이 찾기창, 칩 줄, 아래 시트에 가리면 지도를 조금 옮겨 보이게 한다
function keepVisible(map, popup) {
  requestAnimationFrame(() => {
    const el = popup.getElement && popup.getElement();
    if (!el || !el.isConnected) return;
    const r = el.getBoundingClientRect();
    const tops = ["searchbar", "map-top"].map(id => document.getElementById(id)).filter(x => x && x.getClientRects().length && x.childElementCount && getComputedStyle(x).display !== "none")
      .map(x => x.getBoundingClientRect().bottom);
    const top = Math.max(0, ...tops) + 8;
    const sheet = document.getElementById("panel");
    const bottom = innerWidth <= 900 && sheet ? sheet.getBoundingClientRect().top - 8 : innerHeight - 8;
    let dy = 0;
    if (r.top < top) dy = -(top - r.top);
    else if (r.bottom > bottom) dy = Math.min(r.bottom - bottom, r.top - top);
    if (Math.abs(dy) > 2) map.panBy([0, dy], { duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 250 });
  });
}

export function init(ctx) {
  const { bus, ui, data, map } = ctx;
  if (!map) return;
  ctx.css("card.css");
  const station = data.station && data.station.pt ? data.station.pt : data.center;
  let popup = null;
  let lastAt = 0;
  const close = () => { if (popup) { const p = popup; popup = null; p.remove(); } };

  // 평면 근사 (반경 1km 안이라 충분하다): 경위도를 m로
  const kx = 111320 * Math.cos(data.center[1] * Math.PI / 180), ky = 110574;
  const toXY = c => [c[0] * kx, c[1] * ky];
  function distToRing(p, ring) {
    let best = Infinity, inside = false;
    for (let k = 0, j = ring.length - 1; k < ring.length; j = k++) {
      const a = toXY(ring[j]), b = toXY(ring[k]);
      if ((b[1] > p[1]) !== (a[1] > p[1]) && p[0] < ((a[0] - b[0]) * (p[1] - b[1])) / (a[1] - b[1]) + b[0]) inside = !inside;
      const dx = b[0] - a[0], dy = b[1] - a[1], L = dx * dx + dy * dy;
      const t = L ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L)) : 0;
      best = Math.min(best, Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy)));
    }
    return { best, inside };
  }
  function polyDist(p, geom) {
    const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
    let d = Infinity;
    for (const poly of polys) {
      const outer = distToRing(p, poly[0]);
      if (outer.inside) return 0;
      d = Math.min(d, outer.best);
    }
    return d;
  }
  const shapeByI = new Map((data.shapes.features || []).map(f => [f.properties.i, f.geometry]));
  function nearBuildings(pt) {
    const p = toXY(pt);
    const out = [];
    for (const b of data.places) {
      if (!b.pt || meters(pt, b.pt) > 250) continue;
      const g = b.sh === 1 ? shapeByI.get(b.i) : null;
      const d = g ? polyDist(p, g) : Math.hypot(p[0] - toXY(b.pt)[0], p[1] - toXY(b.pt)[1]);
      if (d <= NEAR_M) out.push({ b, d });
    }
    return out.sort((a, b) => a.d - b.d);
  }

  // 배경 길 이름을 눌렀는지 본다. 이름이 같은 길이 places.json에 있으면 그 길 카드를 연다
  function roadAt(point) {
    if (!map.getLayer("base-road-label")) return null;
    const box = [[point.x - 6, point.y - 6], [point.x + 6, point.y + 6]];
    for (const f of map.queryRenderedFeatures(box, { layers: ["base-road-label"] })) {
      const pr = f.properties || {};
      const names = [pr["name:ko"], pr.name].filter(Boolean);
      const road = (data.roads || []).find(r => names.includes(r.n));
      if (road) return road;
    }
    return null;
  }

  function show({ lngLat, point, context }) {
    const now = performance.now();
    if (now - lastAt < 400) return;   // 길게 누르기와 우클릭이 함께 오는 경우
    lastAt = now;
    close();
    const pt = [lngLat.lng, lngLat.lat];
    if (!context && point) {
      const road = roadAt(point);
      if (road) { bus.emit("select", { path: road.p, from: "road-label", fit: false }); return; }
    }
    const dSt = meters(pt, station);
    const dC = meters(pt, data.center);
    const box = document.createElement("div");
    box.className = "pp-box";
    const coord = `${pt[1].toFixed(6)}, ${pt[0].toFixed(6)}`;
    if (dC > 1000) {
      box.innerHTML = `<p class="pp-name">지도 범위 밖입니다</p>
        <p class="pp-sub">이 지도는 증미역 반경 1km만 다룹니다. 이곳은 역에서 직선 ${nf(Math.round(dSt))}m 떨어져 있습니다.</p>
        <div class="pp-acts"><button type="button" class="btn ghost mini" data-a="station">증미역으로 가기</button></div>`;
    } else {
      const near = nearBuildings(pt);
      const list = near.slice(0, innerWidth <= 900 ? 3 : 5).map(x => `<li><button type="button" class="linkish" data-path="${esc(x.b.p)}">${esc(x.b.n)}</button>
        <span class="muted small">${x.d < 1 ? "누른 곳" : `${nf(Math.round(x.d))}m`}</span></li>`).join("");
      const measure = ctx.features.has("measure") ? `<button type="button" class="btn ghost mini" data-a="measure">여기서 재기</button>` : "";
      const nearby = ctx.features.has("nearby") ? `<button type="button" class="btn ghost mini" data-a="nearby">여기서 주변 찾기</button>` : "";
      const basisBtn = ctx.basis ? `<button type="button" class="btn ghost mini" data-a="basis">여기를 거리 기준으로</button>` : "";
      const b0 = ctx.basis && ctx.basis.get(), dB = b0 ? ctx.basis.dist(pt) : null;
      box.innerHTML = `<p class="pp-kind"><span class="chip type">여기 보기</span></p>
        ${near.length ? `<p class="pp-sub">${NEAR_M}m 안 건물 ${nf(near.length)}동</p><ul class="pp-list">${list}</ul>`
          : `<p class="pp-name">여기에는 건물 자료가 없습니다</p>`}
        ${Number.isFinite(dB) && dB > 0 ? `<p class="pp-sub">${esc(b0.n)}에서 직선 ${nf(dB)}m</p>` : ""}
        <p class="pp-sub">좌표 <span class="mono">${coord}</span> <button type="button" class="linkish small" data-a="copy" aria-label="좌표 복사">복사</button></p>
        <div class="pp-acts">${nearby}${measure}${basisBtn}</div>`;
    }
    box.querySelectorAll("[data-path]").forEach(b => b.addEventListener("click", () => {
      close();
      bus.emit("select", { path: b.dataset.path, from: "list", fit: false });
    }));
    box.querySelectorAll("[data-a]").forEach(b => b.addEventListener("click", () => {
      const a = b.dataset.a;
      if (a === "copy") { ui.copy(coord, "좌표를 복사했습니다"); return; }
      close();
      if (a === "nearby") bus.emit("nearby:open", { center: pt, label: "누른 곳", radius: 300 });
      else if (a === "measure") bus.emit("measure:start", { mode: "distance", from: pt });
      else if (a === "basis") { ctx.basis.set({ pt, n: "누른 곳", p: null }); ui.toast("누른 곳을 거리 기준으로 정했습니다. 카드와 목록의 거리가 여기서 잽니다"); }
      else if (a === "station") map.easeTo({ center: station, zoom: Math.max(map.getZoom(), 15), duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 400 });
    }));
    popup = new maplibregl.Popup({ className: "pp", closeButton: true, closeOnClick: false, focusAfterOpen: false, offset: 8, maxWidth: "280px" })
      .setLngLat(pt).setDOMContent(box).addTo(map);
    const mine = popup;
    popup.on("close", () => { if (popup === mine) popup = null; });
    keepVisible(map, popup);
    ui.live(dC > 1000 ? "누른 곳은 지도 범위 밖입니다" : "누른 곳의 정보를 말풍선으로 열었습니다");
  }

  bus.on("map:blank", e => { if (e && e.lngLat) show(e); });
  bus.on("select", sel => { if (!sel || sel.from !== "road-label") close(); });
  bus.on("deselect", close);

  // 길게 누르기 (손가락 하나, 500ms, 8px 안에서만)
  const canvas = map.getCanvasContainer();
  let hold = null;
  canvas.addEventListener("touchstart", e => {
    if (e.touches.length !== 1) { hold && clearTimeout(hold.t); hold = null; return; }
    const t = e.touches[0], r = canvas.getBoundingClientRect();
    const point = { x: t.clientX - r.left, y: t.clientY - r.top };
    hold = { x: t.clientX, y: t.clientY, t: setTimeout(() => {
      hold = null;
      const ll = map.unproject([point.x, point.y]);
      show({ lngLat: ll, point, context: true });
    }, 500) };
  }, { passive: true });
  canvas.addEventListener("touchmove", e => {
    if (!hold) return;
    const t = e.touches[0];
    if (Math.hypot(t.clientX - hold.x, t.clientY - hold.y) > 8) { clearTimeout(hold.t); hold = null; }
  }, { passive: true });
  const cancel = () => { if (hold) { clearTimeout(hold.t); hold = null; } };
  canvas.addEventListener("touchend", cancel, { passive: true });
  canvas.addEventListener("touchcancel", cancel, { passive: true });
}
