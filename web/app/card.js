// 장소 카드 (명세 3.6절): 패널 카드, 행동 줄, 이어진 것과 번짐, 입구 표시, 링크 복사, 카드 실패 (3.11절)
// 카드 순서는 모든 유형이 같다: 머리, 주소, 행동 줄, 정보, 이어진 것(보라), 없는 정보, 출처(보라), 끝.
// 단지는 card.json이 없어서 places.json의 묶음 값으로 만든다. 번짐(spread)은 places.json에서 조건으로 찾는다.

const SITE = "https://yeodh10.github.io/jeungmi-geokg";
const ISSUES = "https://github.com/yeodh10/jeungmi-geokg/issues/new";
const AREA = ["road", "admin-dong", "legal-dong", "basic-zone"];
const BLDG = ["building", "building-gis"];
const STATUS = { "주소 미연결": "도로명주소 없는 건물" };
const SPREAD_KEY = { pnu: "pnu", ld: "ld", dong: "dong", road: "road", zip: "zip", g: "g" };
const MORE = {
  code: '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5"/>',
  near: '<circle cx="12" cy="12" r="3"/><circle cx="12" cy="12" r="8" stroke-dasharray="3 3"/>',
  addr: '<path d="M12 21s-6-5.4-6-10a6 6 0 0 1 12 0c0 4.6-6 10-6 10z"/><path d="M9.5 11h5"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
};
const nf = n => Number(n).toLocaleString("ko-KR");
const short = a => String(a || "").replace(/^서울특별시\s+/, "");
// 받침이 있으면 '은', 없으면 '는'
const eunNeun = w => { const c = String(w).trim().slice(-1).charCodeAt(0); return c >= 0xac00 && c <= 0xd7a3 && (c - 0xac00) % 28 ? "은" : "는"; };
// 두 점 사이 직선 거리 (m)
export const meters = (a, b) => {
  const R = 6371008.8, r = Math.PI / 180;
  const dLat = (b[1] - a[1]) * r, dLng = (b[0] - a[0]) * r;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * r) * Math.cos(b[1] * r) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
};
const zeroFloor = v => /(^|[^0-9])0층/.test(String(v));

export function init(ctx) {
  ctx.css("card.css");
  ctx.css("graph.css");
  const { bus, state, ui, panel, data, cfg } = ctx;
  const map = ctx.map;
  const icon = (name, size = 18) => (MORE[name]
    ? `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${MORE[name]}</svg>`
    : ui.icon(name, size));
  const pageBase = cfg.mode === "public" ? cfg.root : SITE;
  let current = null;        // 지금 카드의 고른 값
  let spreadOwner = null;    // 지금 번짐을 누가 그렸는지 (card, ask, nearby)
  let entrancesP = null;
  let refit = null;

  // ---- 지도 화면 링크: 지금 배율과 위치를 함께 적는다 (새 창에서 같은 장소, 같은 배율)
  function viewUrl(extra = {}) {
    const v = {};
    if (map) {
      const c = map.getCenter();
      v.z = map.getZoom().toFixed(2);
      v.c = `${c.lng.toFixed(6)},${c.lat.toFixed(6)}`;
    }
    return state.url({ ...v, ...extra });
  }
  ui.toolButton({ id: "tool-link", icon: "link", label: "지금 화면 링크 복사", group: "link", order: 0,
    onClick: () => ui.copy(viewUrl(), "지금 화면 링크를 복사했습니다") });

  // ---- 번짐: 조건으로 places.json에서 찾는다
  function spreadPaths(sp) {
    const key = sp && SPREAD_KEY[sp.by];
    if (!key) return [];
    return data.places.filter(p => p[key] != null && String(p[key]) === String(sp.value)).map(p => p.p);
  }
  function spread(paths, label, { fit = true, owner = "card" } = {}) {
    bus.emit("spread", { paths, label, owner });
    if (fit && paths.length) ctx.mapApi.fitPlaces(paths, { maxZoom: 17 });
    if (!map) ui.live(`${label} ${nf(paths.length)}곳을 지도에 표시했습니다`);
  }
  // 지도 위 번짐 안내 띠 (누가 그렸든 같은 자리에 보인다)
  const note = document.createElement("div");
  note.className = "spread-note";
  note.hidden = true;
  note.innerHTML = `<span class="sw-graph" aria-hidden="true"></span><span class="spread-text"></span>
    <button type="button" class="spread-clear" aria-label="보라색 표시 지우기">${ui.icon("close", 16)}<span>지우기</span></button>`;
  document.body.appendChild(note);
  note.querySelector(".spread-clear").addEventListener("click", () => bus.emit("spread:clear"));
  bus.on("spread", ({ paths = [], label = "", owner = null } = {}) => {
    spreadOwner = owner || "other";
    note.querySelector(".spread-text").textContent = `보라색: ${label} ${nf(paths.length)}곳`;
    note.hidden = false;
    ui.live(`지도에 ${label} ${nf(paths.length)}곳을 보라색으로 표시했습니다`);
  });
  bus.on("spread:clear", () => { spreadOwner = null; note.hidden = true; });
  const clearCardSpread = () => { if (spreadOwner === "card") bus.emit("spread:clear"); };

  // ---- 배경지도의 같은 이름 길 (있으면 좋음 8). 우리 층 'card-road'만 쓴다
  function roadHighlight(name) {
    if (!map) return;
    ctx.mapApi.whenReady(() => {
      if (!map.getLayer("card-road") && map.getSource("base")) {
        map.addLayer({ id: "card-road", type: "line", source: "base", "source-layer": "transportation",
          filter: ["==", ["get", "name"], "__none__"], paint: { "line-color": css("--graph"), "line-width": 6, "line-opacity": 0.55 } },
        map.getLayer("base-road-label") ? "base-road-label" : undefined);
        ctx.mapApi.onTheme(() => map.getLayer("card-road") && map.setPaintProperty("card-road", "line-color", css("--graph")));
      }
      if (!map.getLayer("card-road")) return;
      const f = name ? ["any", ["==", ["get", "name"], name], ["==", ["get", "name:ko"], name]] : ["==", ["get", "name"], "__none__"];
      map.setFilter("card-road", f);
    });
  }

  // ---- 장소 열기 (이어진 것, 입점 상호, 단지 동 목록)
  async function openPath(path, { group, from = "list" } = {}) {
    if (group != null) { bus.emit("select", { path, group, from, fit: true }); return; }
    if (data.byPath.has(path)) { bus.emit("select", { path, from, fit: true }); return; }
    try {
      const card = await ctx.source.card(path);
      bus.emit("select", { path, card, from, fit: true });
    } catch (e) { ui.toast("이 장소를 열지 못했습니다. 잠시 뒤에 다시 해 보세요."); }
  }

  // ---- 카드 그리기 (panel이 장소 화면을 열 때 부른다)
  panel.setPlaceRenderer((el, sel) => {
    current = sel;
    // 휴대폰: 시트가 올라가는 움직임이 지도 맞춤을 끊을 수 있어, 시트가 멎은 뒤 한 번 더 맞춘다 (아래 sheet.onChange)
    refit = sel.fit && ctx.sheet && ctx.sheet.active() ? { sel, at: performance.now() } : null;
    clearCardSpread();
    roadHighlight(null);
    const token = Symbol("card");
    el.__card = token;
    const alive = () => el.__card === token && el.isConnected;
    if (sel.kind === "group") { drawGroup(el, sel); afterOpen(sel, null); return; }
    const p = sel.place || {};
    el.innerHTML = `<article class="cd">${head(sel, { status: p.s })}<div class="cd-rest">${ui.skeleton(5)}</div></article>`;
    const got = sel.card ? Promise.resolve(sel.card) : ctx.source.card(sel.path);
    got.then(card => {
      if (!alive()) return;
      sel.card = card;
      fill(el, sel, card);
      afterOpen(sel, card);
    }).catch(() => {
      if (!alive()) return;
      reduced(el, sel);
      afterOpen(sel, null);
    });
  });

  function kindLine(sel, { status, kindLabel } = {}) {
    const st = status && status !== "사용" ? `<span class="chip cd-status">${esc(STATUS[status] || status)}</span>` : "";
    return `<p class="cd-kind"><span class="chip type">${esc(kindLabel || sel.kindLabel)}</span>${st}</p>`;
  }
  function head(sel, opts = {}) {
    return `<header class="cd-head">${kindLine(sel, opts)}<h2 class="cd-name" tabindex="-1">${esc(sel.name)}</h2></header>`;
  }

  // 주소 줄: 도로명, 지번, 복사, 역에서 직선 거리
  function addrBlock(addr, jibun, d) {
    const rows = [];
    if (addr) rows.push(`<p class="cd-addr">${esc(short(addr))}</p>`);
    if (jibun) rows.push(`<p class="cd-jibun"><span class="muted">지번</span> ${esc(jibun)}</p>`);
    if (Number.isFinite(d) && d > 0) rows.push(`<p class="cd-dist">${icon("station", 14)}역에서 직선 ${nf(d)}m</p>`);
    return rows.length ? `<div class="cd-addrs">${rows.join("")}</div>` : "";
  }

  // 행동 줄: 주변 찾기, 링크 복사, 주소 복사, 기계용 원본. 쓸 수 없는 단추도 자리를 지키고 까닭을 알린다
  function actions({ pt, label, link, addr, ttl, why = {} }) {
    const btn = (id, ic, text, off, reason) =>
      `<button type="button" class="cd-act" data-act="${id}"${off ? ` aria-disabled="true" data-why="${esc(reason)}" title="${esc(reason)}"` : ""}>${icon(ic)}<span>${text}</span></button>`;
    return `<div class="cd-actions" role="group" aria-label="이 장소로 할 수 있는 일">
      ${btn("near", "near", "주변 찾기", !pt, why.near || "위치 점이 없는 장소라 주변을 잴 수 없습니다")}
      ${btn("link", "link", "링크 복사", !link, why.link || "")}
      ${btn("addr", "addr", "주소 복사", !addr, why.addr || "주소가 없는 장소입니다")}
      ${btn("ttl", "code", "기계용 원본", !ttl, why.ttl || "공개 사이트에 원본이 없는 장소입니다")}
    </div>`;
  }
  function bindActions(el, { pt, label, link, addr, ttl }) {
    el.querySelectorAll(".cd-act").forEach(b => b.addEventListener("click", () => {
      if (b.getAttribute("aria-disabled") === "true") { ui.toast(b.dataset.why); return; }
      const a = b.dataset.act;
      if (a === "near") {
        if (ctx.features.has("nearby")) bus.emit("nearby:open", { center: pt, label, radius: 300 });
        else ui.toast("주변 찾기 기능을 불러오지 못했습니다.");
      } else if (a === "link") ui.copy(typeof link === "function" ? link() : link, "이 장소의 지도 링크를 복사했습니다");
      else if (a === "addr") ui.copy(addr, "주소를 복사했습니다");
      else if (a === "ttl") window.open(ttl, "_blank", "noopener");
    }));
  }

  // 정보: 값이 없는 칸은 두지 않는다. '0층'은 만들지 않는다
  function factsBlock(facts) {
    const rows = (facts || []).filter(f => f && f.v != null && f.v !== "" && !zeroFloor(f.v)).map(f => {
      const raw = f.raw && f.raw !== f.v ? ` <span class="muted">(원래 값 ${esc(f.raw)})</span>` : "";
      const nt = f.note ? `<span class="cd-note">${esc(f.note)}</span>` : "";
      return `<div class="cd-fact"><dt>${esc(f.k)}</dt><dd>${esc(f.v)}${raw}${nt}</dd></div>`;
    });
    return rows.length ? `<section class="cd-sec"><h3 class="cd-h">정보</h3><dl class="cd-facts">${rows.join("")}</dl></section>` : "";
  }

  // 입점 상호: 층 순서(원본 순서 그대로), 업종, 이어진 방법
  function tenantsBlock(card) {
    const list = card.tenants || [];
    if (!list.length) return "";
    const LIMIT = 30;
    const row = t => {
      const name = t.path ? `<button type="button" class="linkish cd-open" data-path="${esc(t.path)}">${esc(t.name)}</button>` : `<span class="cd-tn">${esc(t.name)}</span>`;
      const bits = [t.floor || "층 정보 없음", t.cat, t.by ? `${t.by}로 이음` : "", t.rows > 1 ? `원천 ${t.rows}행을 한 줄로` : "", t.src || ""].filter(Boolean);
      return `<li>${name}<span class="cd-tmeta">${esc(bits.join(", "))}</span></li>`;
    };
    return `<section class="cd-sec"><h3 class="cd-h">입점 상호 <span class="muted">${nf(list.length)}곳</span></h3>
      <ul class="cd-tenants">${list.slice(0, LIMIT).map(row).join("")}</ul>
      ${list.length > LIMIT ? `<ul class="cd-tenants" hidden>${list.slice(LIMIT).map(row).join("")}</ul><button type="button" class="btn ghost mini cd-more">${nf(list.length - LIMIT)}곳 더 보기</button>` : ""}
      <p class="cd-note">${esc(card.tenantsNote || "")}</p></section>`;
  }

  // 센서: 항목별 평균, 범위, 시간별 꺾은선, 기간
  function sensorBlock(s) {
    if (!s) return "";
    const items = (s.items || []).filter(it => it.series && it.series.length);
    const chart = it => {
      const W = 300, H = 64, ys = it.series.map(x => x[1]);
      const lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
      const pts = it.series.map((x, k) => `${(k / Math.max(1, it.series.length - 1) * W).toFixed(1)},${(H - 4 - (x[1] - lo) / span * (H - 8)).toFixed(1)}`).join(" ");
      return `<svg class="cd-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(it.name)} 시간별 값, 가장 낮은 값 ${it.min}${esc(it.unit)}, 가장 높은 값 ${it.max}${esc(it.unit)}"><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
    };
    const body = items.length ? items.map(it => `<div class="cd-sensor">
        <p class="cd-sname"><b>${esc(it.name)}</b> <span class="muted">평균 ${esc(it.avg)}${esc(it.unit)}, 범위 ${esc(it.min)} ~ ${esc(it.max)}${esc(it.unit)}, 값 ${nf(it.n || it.series.length)}개</span></p>
        ${chart(it)}</div>`).join("") : `<p class="muted">관측값 없음. 이 센서의 관측값이 원천 자료에 없습니다.</p>`;
    return `<section class="cd-sec"><h3 class="cd-h">관측값 <span class="muted">${esc(s.period || "")}</span></h3>${body}
      <p class="cd-note">${esc(s.note || "실시간 값이 아닙니다.")}</p></section>`;
  }

  // 이어진 것 (보라). 이름을 누르면 그 장소로 가고, 번지기를 누르면 소속 건물이 보라색으로 번진다
  function linksBlock(links, external) {
    const rows = [];
    const ents = (links || []).filter(l => l.kind === "entrance");
    for (const l of links || []) {
      if (l.kind === "entrance") continue;
      const n = l.spread ? spreadPaths(l.spread).length : 0;
      const sp = l.spread && n ? `<button type="button" class="g-spread" data-by="${esc(l.spread.by)}" data-value="${esc(l.spread.value)}" data-label="${esc(l.rel === "같은 단지" ? l.name.replace(/\s*\(\d+동\)$/, "") : l.name)}">${l.kind === "spread" ? esc(l.name) : `${nf(n)}동`} 번지기</button>` : "";
      const main = l.kind === "spread" ? `<span class="g-name muted">지도에서 보기</span>`
        : `<button type="button" class="linkish g-open" data-path="${esc(l.path)}"${l.kind === "group" ? ` data-group="${esc(l.spread && l.spread.value)}"` : ""}>${esc(l.name)}</button>`;
      rows.push(`<li><span class="g-rel">${esc(l.rel)}</span><span class="g-main">${main}</span>${sp}</li>`);
    }
    if (ents.length) {
      const one = ents.length === 1;
      rows.push(`<li><span class="g-rel">입구</span><span class="g-main">${one
        ? `<button type="button" class="linkish g-open" data-path="${esc(ents[0].path)}">${esc(ents[0].name)}</button>`
        : `<span>${nf(ents.length)}곳</span> ${ents.map((e, k) => `<button type="button" class="linkish g-open" data-path="${esc(e.path)}">${k + 1}</button>`).join(" ")}`}</span></li>`);
    }
    for (const x of external || []) {
      rows.push(`<li><span class="g-rel">${esc(x.rel)}</span><span class="g-main"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name)} ${ui.icon("external", 14)}</a></span></li>`);
    }
    return rows.length ? `<section class="cd-sec g-sec"><h3 class="cd-h g-h">이어진 것</h3><ul class="g-links">${rows.join("")}</ul></section>` : "";
  }

  function missingBlock(list) {
    const m = (list || []).filter(Boolean);
    if (!m.length) return "";
    const s = m.join(", ");
    return `<section class="cd-sec cd-missing"><h3 class="cd-h">없는 정보</h3><p>${esc(s)}${eunNeun(m[m.length - 1])} 없습니다.</p></section>`;
  }

  function sourcesBlock(src) {
    if (!src) return "";
    const o = (src.object || []).join(", "), s = (src.shape || []).join(", ");
    return `<section class="cd-sec g-sec"><h3 class="cd-h g-h">출처</h3><dl class="g-src">
      <div><dt>이 장소의 출처</dt><dd>${esc(o || "적힌 출처가 없습니다")}</dd></div>
      <div><dt>이 모양의 출처</dt><dd>${esc(s || "모양이 없는 장소입니다")}</dd></div></dl></section>`;
  }

  function footBlock(card) {
    const showPage = (cfg.mode === "public" || card.public === true) && card.path;
    const page = showPage ? `${pageBase}/id/${card.path}/` : null;
    const uri = card.uri || "";
    const issue = uri ? `${ISSUES}?title=${encodeURIComponent("틀린 곳 알리기")}&body=${encodeURIComponent(uri)}` : null;
    return `<footer class="cd-foot g-sec">
      ${uri ? `<div class="cd-uri"><p class="cd-label">고유 주소 (URI)</p><p class="uri">${esc(uri)}</p>
        <button type="button" class="btn ghost mini cd-copy-uri">${ui.icon("copy", 14)}고유 주소 복사</button></div>` : ""}
      ${card.officialNo ? `<p class="cd-off"><span class="cd-label">${esc(card.officialNo.label)}</span> <span class="mono">${esc(card.officialNo.value)}</span></p>` : ""}
      ${card.factCount ? `<p class="cd-count">그래프의 사실 ${nf(card.factCount)}개에서 왔습니다.</p>` : ""}
      <p class="cd-pagelinks">${page ? `<a href="${esc(page)}">장소 페이지 열기</a>` : ""}
      ${issue && showPage ? `<a href="${esc(issue)}" target="_blank" rel="noopener">${icon("flag", 14)}틀린 곳 알리기</a>` : ""}</p>
    </footer>`;
  }

  function othersBlock(sel) {
    const others = (sel.others || []).filter(o => o.path !== sel.path || o.group !== sel.group);
    if (!others.length) return "";
    return `<section class="cd-sec"><h3 class="cd-h">여기 있는 다른 곳</h3><ul class="cd-others">${others.map(o => {
      const g = o.group != null ? data.groupById.get(Number(o.group)) : null;
      const name = g ? g.n : data.nameOf(o.path) || o.path;
      return `<li><button type="button" class="linkish cd-open" data-path="${esc(o.path)}" data-group="${g ? g.g : ""}">${esc(name)}</button></li>`;
    }).join("")}</ul></section>`;
  }

  function bindCommon(el, sel) {
    el.querySelectorAll(".cd-open, .g-open").forEach(b => b.addEventListener("click", () =>
      openPath(b.dataset.path, { group: b.dataset.group ? Number(b.dataset.group) : undefined })));
    el.querySelectorAll(".g-spread").forEach(b => b.addEventListener("click", () => {
      const paths = spreadPaths({ by: b.dataset.by, value: b.dataset.value });
      spread(paths, b.dataset.label);
    }));
    const more = el.querySelector(".cd-more");
    if (more) more.addEventListener("click", () => { more.previousElementSibling.hidden = false; more.remove(); });
    const cu = el.querySelector(".cd-copy-uri");
    if (cu) cu.addEventListener("click", () => ui.copy(sel.card.uri, "고유 주소를 복사했습니다"));
  }

  // card.json을 받은 뒤의 전체 카드
  function fill(el, sel, card) {
    const p = sel.place || {};
    const name = card.name || sel.name;
    const ttl = (cfg.mode === "public" || card.public === true) ? `${pageBase}/id/${card.path}/index.ttl` : null;
    const addr = card.addr ? short(card.addr) : card.jibun ? `${card.jibun} (지번)` : null;
    const pt = card.pt || sel.pt || null;
    const link = () => viewUrl({ focus: sel.path, unit: null });   // 누를 때의 배율과 위치로 만든다
    const why = AREA.includes(sel.kind) ? { near: "길과 동네는 위치 점이 없어 주변 찾기를 쓸 수 없습니다" }
      : sel.kind === "parcel" && !pt ? { near: "위에 건물이 없는 필지라 위치 점이 없습니다" } : {};
    if (!ttl) why.ttl = "공개하지 않는 장소라 공개 사이트에 원본이 없습니다";
    const lead = card.lead ? `<p class="cd-lead">${esc(card.lead)}</p>` : "";
    const roadBg = sel.kind === "road" && map ? `<section class="cd-sec"><button type="button" class="btn ghost mini cd-roadbg" aria-pressed="false">배경지도의 같은 이름 길 보기</button>
      <p class="cd-note">배경지도(OpenStreetMap)의 같은 이름 길을 칠합니다. 이 지도의 도로선 자료는 아닙니다.</p></section>` : "";
    const rest = el.querySelector(".cd-rest");
    if (!rest) return;
    // 머리의 구분 줄만 바꾼다. 제목(h2)은 그대로 두어야 목록에서 열 때 옮긴 포커스가 남는다
    el.querySelector(".cd-kind").outerHTML = kindLine(sel, { status: card.status || p.s, kindLabel: sel.kind === "entrance" ? "입구" : sel.kindLabel });
    // 이름은 화면 머리에도 같다 (카드 이름이 더 정확하면 바꾼다)
    const h = el.querySelector(".cd-name");
    if (h && name !== sel.name) h.textContent = name;
    rest.innerHTML = `${addrBlock(card.addr, card.jibun || (sel.kind === "parcel" ? null : p.j), card.d ?? p.d)}
      ${actions({ pt, label: name, link, addr, ttl, why })}
      ${lead}${factsBlock(card.facts)}${sensorBlock(card.sensor)}${tenantsBlock(card)}${roadBg}
      ${linksBlock(card.links, card.external)}${othersBlock(sel)}${missingBlock(card.missing)}${sourcesBlock(card.sources)}${footBlock(card)}`;
    bindActions(rest, { pt, label: name, link, addr, ttl });
    bindCommon(el, sel);
    const rb = rest.querySelector(".cd-roadbg");
    if (rb) rb.addEventListener("click", () => {
      const on = rb.getAttribute("aria-pressed") !== "true";
      rb.setAttribute("aria-pressed", String(on));
      rb.textContent = on ? "배경지도의 같은 이름 길 지우기" : "배경지도의 같은 이름 길 보기";
      roadHighlight(on ? name : null);
    });
    if (sel.from === "road-label" && rb) rb.click();
  }

  // card.json을 받지 못했을 때: places.json의 이름과 주소로 줄인 카드와 다시 시도
  function reduced(el, sel) {
    const p = sel.place || {};
    const rest = el.querySelector(".cd-rest");
    if (!rest) return;
    const addr = p.a ? short(p.a) : p.j ? `${p.j} (지번)` : null;
    rest.innerHTML = `${addrBlock(p.a, p.j, p.d)}
      ${actions({ pt: sel.pt, label: sel.name, link: true, addr, ttl: null, why: { ttl: "자세한 정보를 받지 못해 원본 주소를 확인하지 못했습니다" } })}
      <div class="cd-fail" role="alert"><p>이 장소의 자세한 정보를 받지 못했습니다.</p>
        <button type="button" class="btn mini cd-retry">${ui.icon("refresh", 14)}다시 시도</button></div>`;
    bindActions(rest, { pt: sel.pt, label: sel.name, link: () => viewUrl({ focus: sel.path, unit: null }), addr, ttl: null });
    rest.querySelector(".cd-retry").addEventListener("click", () => {
      if (current !== sel) return;
      rest.innerHTML = ui.skeleton(5);
      const token = el.__card;
      ctx.source.card(sel.path).then(card => {
        if (el.__card !== token || !el.isConnected) return;
        sel.card = card;
        fill(el, sel, card);
        afterOpen(sel, card);
        ui.live("장소 정보를 다시 받았습니다");
      }).catch(() => { if (el.__card === token && el.isConnected) { reduced(el, sel); ui.toast("아직 받지 못했습니다. 잠시 뒤에 다시 해 보세요."); } });
    });
  }

  // 단지 카드: 화면용 묶음이라 card.json과 고유 주소가 없다
  function drawGroup(el, sel) {
    const g = sel.group;
    const members = g.m.map(i => data.byIndex[i]).filter(Boolean);
    const rep = members[0] || {};
    const part = new Set(g.part || []);
    const facts = [{ k: "동 수", v: `${nf(members.length)}동` }];
    if (g.hh) facts.push({ k: "세대수", v: `${nf(g.hh)}세대`, note: part.has("hh") ? "일부 필지만 값이 있습니다" : "건축물대장 총괄표제부의 필지 값을 더했습니다" });
    if (g.site) facts.push({ k: "대지면적", v: `${nf(g.site)}㎡`, note: part.has("site") ? "일부 필지만 값이 있습니다" : "필지 값을 더했습니다" });
    const pnus = (g.pnu || []).map(x => data.byPath.get("parcel/" + x)).filter(Boolean);
    const link = () => viewUrl({ focus: rep.p, unit: "group" });
    const addr = rep.a ? short(rep.a) : null;
    const memberRows = members.map(m => `<li><button type="button" class="linkish cd-open" data-path="${esc(m.p)}">${esc(m.n)}</button>${m.fa ? ` <span class="muted">${nf(m.fa)}층</span>` : ""}</li>`).join("");
    el.innerHTML = `<article class="cd">${head(sel, { kindLabel: "단지" })}<div class="cd-rest">
      ${addrBlock(rep.a ? `${rep.a} (대표 동)` : null, null, g.pt && data.station && data.station.pt ? Math.round(meters(g.pt, data.station.pt)) : null)}
      ${actions({ pt: g.pt, label: g.n, link, addr, ttl: null, why: { addr: "단지에는 주소가 없고 대표 동에도 도로명주소가 없습니다", ttl: "단지는 화면용 묶음이라 기계용 원본이 없습니다" } })}
      ${factsBlock(facts)}
      <section class="cd-sec"><h3 class="cd-h">동 목록 <span class="muted">${nf(members.length)}동</span></h3><ul class="cd-members">${memberRows}</ul></section>
      <section class="cd-sec g-sec"><h3 class="cd-h g-h">이어진 것</h3><ul class="g-links">
        <li><span class="g-rel">단지 전체</span><span class="g-main muted">지도에서 보기</span><button type="button" class="g-spread" data-by="g" data-value="${g.g}" data-label="${esc(g.n)}">${nf(members.length)}동 번지기</button></li>
        ${pnus.map(x => `<li><span class="g-rel">필지</span><span class="g-main"><button type="button" class="linkish g-open" data-path="${esc(x.p)}">${esc(x.j || x.p)}</button></span></li>`).join("")}
      </ul></section>
      ${othersBlock(sel)}
      <section class="cd-sec cd-missing"><h3 class="cd-h">없는 정보</h3><p>단지는 건물 이름과 필지로 화면에서 묶은 것이라 고유 주소가 없습니다. 링크는 대표 동과 단지 표시(unit=group)로 잇습니다.</p></section>
      <section class="cd-sec g-sec"><h3 class="cd-h g-h">출처</h3><dl class="g-src">
        <div><dt>이 장소의 출처</dt><dd>동마다 다릅니다. 동을 눌러 동 카드에서 봅니다.${g.hh || g.site ? " 세대수와 대지면적은 서울시 건축물대장 총괄표제부입니다." : ""}</dd></div>
        <div><dt>이 모양의 출처</dt><dd>동마다 다릅니다.</dd></div></dl></section>
    </div></article>`;
    bindActions(el, { pt: g.pt, label: g.n, link, addr, ttl: null });
    bindCommon(el, sel);
  }

  // 카드를 연 뒤: 입구 표시, 동네와 길은 소속 건물 번짐
  function afterOpen(sel, card) {
    if (current !== sel) return;
    let owner = null;
    if (BLDG.includes(sel.kind)) owner = sel.path;
    else if (sel.kind === "entrance" && card) { const b = (card.links || []).find(l => l.rel === "소속 건물"); owner = b && b.path; }
    if (owner) {
      entrancesP = entrancesP || ctx.source.entrances().catch(e => { entrancesP = null; throw e; });
      entrancesP.then(all => { if (current === sel) ctx.mapApi.setEntrances(all[owner] || []); }).catch(() => {});
    } else ctx.mapApi.setEntrances([]);
    if (AREA.includes(sel.kind) && card) {
      const l = (card.links || []).find(x => x.kind === "spread" && x.spread);
      if (l) spread(spreadPaths(l.spread), `${card.name}, ${l.rel}`, { fit: true });
    }
  }

  bus.on("deselect", () => { current = null; clearCardSpread(); roadHighlight(null); });

  // 휴대폰: 시트가 올라가 고른 곳을 가리면 그곳을 시트 위로 옮긴다 (padding 움직임이 끝난 뒤)
  if (map && ctx.sheet) {
    let t = 0;
    ctx.sheet.onChange(name => {
      clearTimeout(t);
      if (!name || name === "full") return;
      t = setTimeout(() => {
        const s = current;
        const pt = s && (s.pt || (s.card && s.card.pt));
        if (refit && refit.sel === s && performance.now() - refit.at < 2000) {
          refit = null;
          if (s.paths && s.paths.length && ctx.mapApi.fitPlaces(s.paths)) return;
          if (pt) { map.easeTo({ center: pt, zoom: Math.max(map.getZoom(), 17), duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 300 }); return; }
        }
        refit = null;
        if (!pt) return;
        const q = map.project(pt);
        const bottom = window.innerHeight - ctx.sheet.height();
        if (q.y > bottom - 24 || q.y < 120) map.easeTo({ center: pt, duration: matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 300 });
      }, 260);
    });
  }

  // 다른 모듈이 쓸 수 있게 (묻기 표, 주변 목록에서 장소를 열 때)
  ctx.openPath = openPath;
  ctx.spreadPaths = spreadPaths;
}
