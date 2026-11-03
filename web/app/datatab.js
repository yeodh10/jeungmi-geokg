// 자료 탭 (명세 3.8절)과 첫 패널 홈 (3.9절).
// 자료 탭: 출처 목록(이름, 기준 시점, 공개 여부, 공개하지 않는 까닭), 이 지도에 없는 것, 공개 범위, 건물 색, 지도 자료 표기.
// 공개 화면에는 승인, 반려 건수를 싣지 않고 까닭만 적는다.

const nf = n => Number(n).toLocaleString("ko-KR");

export function init(ctx) {
  ctx.css("graph.css");
  const { bus, ui, panel, data, source, cfg } = ctx;
  let srcP = null;
  const load = () => (srcP = srcP || source.sources().catch(e => { srcP = null; throw e; }));

  const legend = () => `<ul class="legend">${data.useGroups.map(g =>
    `<li class="legend-row"><span class="sw" style="background:var(${esc(g.color)});box-shadow:inset 0 0 0 1px var(--map-bldg-line)"></span>${esc(g.label)} <span class="muted">${nf(g.n)}동</span></li>`).join("")}
    <li class="legend-row"><span class="sw" style="background:var(--panel);box-shadow:inset 0 0 0 3px var(--select)"></span>고른 장소 (파랑)</li>
    <li class="legend-row"><span class="sw" style="background:var(--graph)"></span>이어진 것, 질문 답 (보라)</li>
    <li class="legend-row"><span class="sw dot" style="background:var(--station-mark)"></span>증미역 (9호선)</li></ul>`;

  // ---- 자료 탭
  function render(el) {
    el.innerHTML = ui.skeleton(6);
    load().then(s => {
      const srcRow = x => `<li><p class="nm">${esc(x.name)} <span class="chip ${x.public ? "pub" : "held"}">${x.public ? "공개" : "공개하지 않음"}</span></p>
        <p>${esc(x.by || "")}${x.as_of ? (/기준|받은 날|내려받기|제작/.test(x.as_of) ? `, ${esc(x.as_of)}` : `, ${esc(x.as_of)} 기준`) : ""}</p>
        ${x.public ? `<p>쓴 곳: ${esc(x.used_for || "")}</p>${x.license ? `<p>이용 조건: ${esc(x.license)}</p>` : ""}${x.note ? `<p>${esc(x.note)}</p>` : ""}
          ${x.url ? `<p><a href="${esc(x.url)}" target="_blank" rel="noopener">자료 안내 ${ui.icon("external", 12)}</a></p>` : ""}`
          : `<p>까닭: ${esc(x.reason || "")}</p>`}</li>`;
      const pub = s.sources.filter(x => x.public), held = s.sources.filter(x => !x.public);
      el.innerHTML = `
        <section class="dt-sec"><h2 class="dt-h" tabindex="-1">이 지도의 자료</h2>
          <p class="dt-p">${esc(s.principle || "")}</p><p class="dt-p">${esc(s.basis || "")}</p></section>
        <section class="dt-sec" data-tour="sources"><h3 class="dt-h">출처 <span class="muted">${nf(pub.length)}종 공개</span></h3><ul class="dt-src">${pub.map(srcRow).join("")}</ul>
          ${s.hiddenBusinessesNote ? `<p class="dt-p" style="margin-top:8px">${esc(s.hiddenBusinessesNote)}</p>` : ""}</section>
        <section class="dt-sec" data-tour="held"><h3 class="dt-h">공개하지 않은 자료 <span class="muted">${nf(held.length)}종</span></h3><ul class="dt-src">${held.map(srcRow).join("")}</ul></section>
        <section class="dt-sec"><h3 class="dt-h">이 지도에 없는 것</h3><ul class="dt-absent">${(s.absent || []).map(x => `<li>${esc(x)}</li>`).join("")}</ul>
          <p class="dt-p" style="margin-top:8px">값이 없는 칸은 빈칸으로 두지 않고 카드의 '없는 정보'에 적습니다.</p></section>
        <section class="dt-sec"><h3 class="dt-h">공개 범위</h3><p class="dt-p">${esc(s.scope || "")}</p><p class="dt-p">${esc(s.history || "")}</p>
          <p class="dt-p">장소마다 고유 주소 (URI)가 있습니다. 카드 맨 아래의 고유 주소를 열면 같은 장소의 페이지와 기계용 원본 (Turtle)이 나옵니다.</p></section>
        <section class="dt-sec"><h3 class="dt-h">건물 색 (용도)</h3>${legend()}</section>
        <section class="dt-sec dt-credits"><h3 class="dt-h">지도 자료 표기</h3>
          <p class="dt-p">${(s.credits || []).map(c => `<a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.name)}</a>`).join("")}</p>
          <p class="dt-p">${esc(s.creditsNote || "")}</p>
          <p class="dt-p"><a href="${esc(cfg.mode === "public" ? cfg.root : "https://yeodh10.github.io/jeungmi-geokg")}/about/">소개 페이지</a>에 그래프 쓰는 법이 있습니다.</p></section>`;
    }).catch(() => {
      el.innerHTML = `<div class="cd-fail" role="alert"><p>자료 탭 내용을 받지 못했습니다.</p><button type="button" class="btn mini">${ui.icon("refresh", 14)}다시 시도</button></div>`;
      el.querySelector("button").addEventListener("click", () => render(el));
    });
  }
  panel.addTab({ id: "data", label: "자료", order: 20, render });

  // ---- 첫 패널 (홈)
  function home(el) {
    const c = data.count || {};
    const total = data.places.length;
    const max = Math.max(...data.useGroups.map(g => g.n), 1);
    const bars = data.useGroups.map(g => `<li><span><span class="sw" style="background:var(${esc(g.color)});box-shadow:inset 0 0 0 1px var(--map-bldg-line);margin-right:6px"></span>${esc(g.label)}</span>
      <span class="bar"><span style="width:${(g.n / max * 100).toFixed(1)}%"></span></span><span class="num">${nf(g.n)}동</span></li>`).join("");
    // 지은 때: 사용승인일의 연대 (2018년까지 자료)
    const dec = new Map();
    let noDate = 0;
    for (const p of data.places) {
      const y = p.ad ? Number(String(p.ad).slice(0, 4)) : NaN;
      if (!Number.isFinite(y)) { noDate++; continue; }
      const d = Math.floor(y / 10) * 10;
      dec.set(d, (dec.get(d) || 0) + 1);
    }
    const decs = [...dec.entries()].sort((a, b) => a[0] - b[0]);
    const dmax = Math.max(...decs.map(x => x[1]), 1);
    const years = decs.map(([d, n]) => `<li><span>${d}년대</span><span class="bar"><span style="width:${(n / dmax * 100).toFixed(1)}%"></span></span><span class="num">${nf(n)}동</span></li>`).join("");
    const tour = ctx.features.has("tour") ? `<button type="button" class="btn ghost home-tour">${ui.icon("info", 18)}5분 둘러보기</button>` : "";
    el.innerHTML = `<div class="home">
      <p class="home-sum">증미역 반경 1km, 건물 ${nf(c.buildings || total)}동, 장소 ${nf(c.places || 0)}곳</p>
      ${tour}
      ${ctx.features.has("ask") ? `<section class="dt-sec home-qs"><h3 class="dt-h">대표 질문</h3><ul class="ask-list">${ui.skeleton(3)}</ul></section>` : ""}
      <section class="dt-sec"><h3 class="dt-h">건물 종류</h3><ul class="home-bars">${bars}</ul></section>
      <section class="dt-sec"><h3 class="dt-h">지은 때 <span class="muted">(사용승인일)</span></h3><ul class="home-bars">${years}</ul>
        <p class="small muted">사용승인일은 2018년까지의 자료입니다. 값이 없는 건물 ${nf(noDate)}동은 세지 않았습니다.</p></section>
      <section class="dt-sec"><h3 class="dt-h">이 지도에 없는 것</h3>
        <p class="home-missing">영업시간, 리뷰, 전화번호, 버스와 지하철 시간표, 실시간 교통은 없습니다. 상호는 공공데이터에 등록된 곳만 있습니다.
        ${ctx.features.has("datatab") ? `<button type="button" class="linkish home-data">자료 탭에서 자세히 보기</button>` : ""}</p></section>
    </div>`;
    const tb = el.querySelector(".home-tour");
    if (tb) tb.addEventListener("click", () => bus.emit("tour:start", { opener: tb }));
    const db = el.querySelector(".home-data");
    if (db) db.addEventListener("click", () => {
      panel.selectTab("data", { push: true });
      const h = panel.el.querySelector("#pane-data h2");
      if (h) h.focus({ preventScroll: true });
    });
    const qs = el.querySelector(".home-qs ul");
    if (qs && ctx.questions) ctx.questions().then(q => {
      const top = q.items.filter(x => x.featured).slice(0, 3);
      qs.innerHTML = top.map(x => `<li><button type="button" class="ask-q" data-id="${esc(x.id)}"><span>${esc(x.q)}</span><span class="n">${nf(x.n)}</span></button></li>`).join("");
      qs.querySelectorAll(".ask-q").forEach(b => b.addEventListener("click", () => bus.emit("ask:open", { id: b.dataset.id })));
    }).catch(() => { qs.innerHTML = `<li class="muted small">질문을 받지 못했습니다. 묻기 탭에서 다시 해 보세요.</li>`; });
  }
  panel.setHome(home);
}
