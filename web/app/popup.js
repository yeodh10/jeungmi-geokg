// 지도 말풍선 (명세 3.6절 1단): 장소를 고르면 이름, 구분, 자세히가 든 말풍선을 그 자리에 띄운다.
// '자세히'는 패널(휴대폰은 시트)의 카드로 보낸다. 점이 없는 유형(길, 동네)은 말풍선이 없다.

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
  const { bus, panel, data, map } = ctx;
  if (!map) return;
  ctx.css("card.css");
  let popup = null;
  const close = () => { if (popup) { const p = popup; popup = null; p.remove(); } };

  // 짧은 요약: 건물은 지상 층수와 사용승인일, 단지는 동 수와 층수, 사용승인 연도의 범위 (값이 있는 것만)
  const nf = n => Number(n).toLocaleString("ko-KR");
  const span = (a, b, unit) => (a === b ? `${a}${unit}` : `${a}~${b}${unit}`);
  function summary(sel) {
    if (sel.group != null) {
      const g = data.groupById.get(Number(sel.group));
      if (!g) return "";
      const ms = g.m.map(i => data.byIndex[i]).filter(Boolean);
      const fa = ms.map(m => m.fa).filter(x => x > 0), ys = ms.map(m => Number(String(m.ad || "").slice(0, 4))).filter(Number.isFinite).filter(y => y > 0);
      return [`${nf(ms.length)}동`, fa.length ? `지상 ${span(Math.min(...fa), Math.max(...fa), "층")}` : "",
        ys.length ? `사용승인 ${span(Math.min(...ys), Math.max(...ys), "년")}` : ""].filter(Boolean).join(", ");
    }
    const p = sel.path ? data.byPath.get(sel.path) : null;
    if (!p || !/^building/.test(sel.path)) return "";
    return [p.fa > 0 ? `지상 ${p.fa}층` : "", p.ad ? `사용승인 ${p.ad}` : ""].filter(Boolean).join(", ");
  }

  function where(sel) {
    if (sel.group != null) { const g = data.groupById.get(Number(sel.group)); return g ? { pt: g.pt, name: g.n, kind: "단지" } : null; }
    const p = sel.path ? data.byPath.get(sel.path) : null;
    const kind = String(sel.path || "").split("/")[0];
    const label = { building: "건물", "building-gis": "건물", station: "역", sensor: "센서", parcel: "필지", entrance: "입구", business: "상호",
      "public-facility": "공공시설" }[kind] || "장소";
    if (kind === "parcel") {
      const first = p && p.m && p.m.length ? data.byIndex[p.m[0]] : null;
      return first ? { pt: first.pt, name: p.j || sel.path, kind: label } : null;
    }
    const pt = (p && p.pt) || (sel.card && sel.card.pt);
    if (!pt) return null;
    return { pt, name: (p && (p.n || p.j)) || (sel.card && sel.card.name) || sel.path, kind: label };
  }

  function detail() {
    if (panel.isCollapsed()) panel.collapse(false);
    if (ctx.sheet && ctx.sheet.active() && ctx.sheet.current() === "peek") ctx.sheet.snap("half");
    const h = panel.el.querySelector(".view-place h2");
    if (h) h.focus({ preventScroll: false });
  }

  bus.on("select", sel => {
    close();
    const w = where(sel || {});
    if (!w) return;
    const box = document.createElement("div");
    box.className = "pp-box";
    const sub = summary(sel || {});
    box.innerHTML = `<p class="pp-kind"><span class="chip type">${esc(w.kind)}</span></p><p class="pp-name">${esc(w.name)}</p>
      ${sub ? `<p class="pp-sub">${esc(sub)}</p>` : ""}<button type="button" class="btn ghost mini pp-more">자세히</button>`;
    box.querySelector(".pp-more").addEventListener("click", detail);
    popup = new maplibregl.Popup({ className: "pp", closeButton: true, closeOnClick: false, focusAfterOpen: false, offset: 14, maxWidth: "260px" })
      .setLngLat(w.pt).setDOMContent(box).addTo(map);
    const mine = popup;
    popup.on("close", () => { if (popup === mine) popup = null; });
    keepVisible(map, popup);
  });
  bus.on("deselect", close);
  bus.on("map:blank", close);
  bus.on("popup:close", close);
}
