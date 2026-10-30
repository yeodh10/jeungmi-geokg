// 공개 URI 사이트(src/graph/publish.py가 만든 페이지)의 동작: URI 복사, 목록 거르기, 전체 찾기, 객체 지도
(() => {
  const $ = id => document.getElementById(id);
  const root = (document.querySelector('link[href$="/assets/site.css"]').getAttribute("href") || "").replace(/\/assets\/site\.css$/, "");

  // URI 복사
  document.querySelectorAll("button.copy").forEach(b => b.onclick = async () => {
    const old = b.innerHTML;
    try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "복사했습니다"; }
    catch (e) { b.textContent = "복사하지 못했습니다"; }
    setTimeout(() => { b.innerHTML = old; }, 1400);
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

  // 전체 찾기: 처음 입력할 때 목록(assets/search.json)을 한 번 받는다
  const q = $("site-q"), box = $("site-hits");
  if (q && box) {
    let all = null, sel = -1;
    const load = async () => all || (all = await (await fetch(root + "/assets/search.json")).json());
    const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const render = async () => {
      const t = tight(q.value);
      if (t.length < 2) { box.innerHTML = ""; return; }
      const list = await load();
      const hits = [];
      for (const [label, path, kind] of list) {
        const a = tight(label), b = path.toLowerCase();
        const score = a === t ? 3 : a.startsWith(t) ? 2 : a.includes(t) || b.includes(t) ? 1 : 0;
        if (score) hits.push([score, label, path, kind]);
        if (hits.length > 400) break;
      }
      hits.sort((x, y) => y[0] - x[0] || x[1].length - y[1].length);
      sel = -1;
      box.innerHTML = hits.length
        ? hits.slice(0, 12).map(([, label, path, kind]) => `<a href="${root}/id/${esc(path)}/"><span>${esc(label)}</span><span class="k">${esc(kind)}</span></a>`).join("")
        : '<div class="none">맞는 객체가 없습니다.</div>';
    };
    q.oninput = render;
    q.onkeydown = ev => {
      const links = [...box.querySelectorAll("a")];
      if (!links.length) return;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        sel = (sel + (ev.key === "ArrowDown" ? 1 : -1) + links.length) % links.length;
        links.forEach((a, i) => a.classList.toggle("on", i === sel));
      } else if (ev.key === "Enter") {
        (links[sel] || links[0]).click();
      } else if (ev.key === "Escape") { box.innerHTML = ""; }
    };
    document.addEventListener("click", ev => { if (!ev.target.closest(".find")) box.innerHTML = ""; });
  }

  // 객체 지도: 페이지에 실린 모양(GeoJSON)을 회색 배경지도 위에 그린다
  const el = $("objmap");
  if (el && window.maplibregl) {
    const geom = JSON.parse(el.dataset.geom);
    const feature = { type: "Feature", geometry: geom, properties: {} };
    const map = new maplibregl.Map({ container: el, style: baseStyle(), attributionControl: { compact: true }, interactive: true,
      cooperativeGestures: true, center: [126.8612, 37.5577], zoom: 16 });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    whenStyleReady(map, () => {
      const accent = css("--accent");
      map.addSource("obj", { type: "geojson", data: feature });
      map.addLayer({ id: "obj-fill", type: "fill", source: "obj", filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]], paint: { "fill-color": accent, "fill-opacity": 0.25 } });
      map.addLayer({ id: "obj-line", type: "line", source: "obj", filter: ["!=", ["geometry-type"], "Point"], paint: { "line-color": accent, "line-width": 2.5 } });
      map.addLayer({ id: "obj-pt", type: "circle", source: "obj", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 8, "circle-color": accent, "circle-stroke-width": 3, "circle-stroke-color": "#fff" } });
      const b = boundsOf([feature]);
      if (geom.type === "Point") map.jumpTo({ center: b.getCenter(), zoom: 17 });
      else map.fitBounds(b, { padding: 40, maxZoom: 18, duration: 0 });
    });
  }
})();
