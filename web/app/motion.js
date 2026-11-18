// 지도 움직임 (키보드와 끌기의 부드러움)
// 키: W A S D와 방향키는 옮기기, Q와 E는 왼쪽 오른쪽으로 돌리기, R과 F는 더 기울이기와 덜 기울이기(입체 보기에서만),
// +와 -는 확대와 축소, Shift를 누르고 있으면 빨리 간다.
// 누르고 있는 동안 금방 빨라졌다가 같은 빠르기로 가고, 떼면 미끄러지듯 멎는다. 두 키를 같이 누르면 대각선으로 가고,
// 옮기면서 돌리기처럼 여러 키를 섞어도 된다.
// 누르고 있는 동안은 긴 easeTo 하나로 같은 빠르기로 간다. 키가 바뀔 때만 지금 자리에서 새로 시작해, 토막 사이에 멈칫하지 않는다.
// 프레임마다 jumpTo를 부르면 moveend가 프레임마다 나서 주소창 적기와 자료 받기가 몰리므로 쓰지 않는다.
// 글을 쓰는 칸에서는 키를 받지 않고, 방향키는 지도에 초점이 있을 때만 받는다(탭과 목록의 방향키 이동을 막지 않게).

const PAN = 560;      // 옮기기 빠르기, 화면 px/초
const TURN = 90;      // 돌리기 빠르기, 도/초
const TILT = 40;      // 기울이기 빠르기, 도/초
const ZOOM = 1.4;     // 확대 빠르기, 단계/초
const FAST = 2.5;     // Shift를 누르면 이만큼 빨리
const LONG = 6000;    // 같은 빠르기로 가는 한 번의 길이(ms). 다 가기 조금 전에 지금 자리에서 다시 이어 간다
const RAMP = 240;     // 멈춘 데서 빨라지는 시간(ms)
const STOP = 340;     // 떼고 멎는 시간(ms)
const MAX_TURN = 150; // 한 번에 돌리는 각도 상한. 180도를 넘기면 지도가 반대쪽으로 돌아간다
// [옆, 위아래, 돌리기, 확대, 기울이기]
const KEYS = {
  KeyW: [0, -1, 0, 0, 0], ArrowUp: [0, -1, 0, 0, 0], KeyS: [0, 1, 0, 0, 0], ArrowDown: [0, 1, 0, 0, 0],
  KeyA: [-1, 0, 0, 0, 0], ArrowLeft: [-1, 0, 0, 0, 0], KeyD: [1, 0, 0, 0, 0], ArrowRight: [1, 0, 0, 0, 0],
  KeyQ: [0, 0, -1, 0, 0], KeyE: [0, 0, 1, 0, 0], KeyR: [0, 0, 0, 0, 1], KeyF: [0, 0, 0, 0, -1],
  Equal: [0, 0, 0, 1, 0], NumpadAdd: [0, 0, 0, 1, 0], Minus: [0, 0, 0, -1, 0], NumpadSubtract: [0, 0, 0, -1, 0],
};
const SHIFT = new Set(["ShiftLeft", "ShiftRight"]);
const ARROWS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
const easeOut = t => 1 - (1 - t) * (1 - t);    // v에서 0까지 멎음: 거리 v*T/2
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export function init(ctx) {
  const { map, bus, ui } = ctx;
  map.keyboard.disable();   // 지도 라이브러리의 키 이동은 한 번에 툭툭 움직여서 이것으로 바꾼다
  // 끌고 놓았을 때 조금 더 길고 부드럽게 미끄러지게 (기본: linearity 0.3, deceleration 2500)
  map.dragPan.enable({ linearity: 0.25, easing: t => 1 - Math.pow(1 - t, 3), deceleration: 1900, maxSpeed: 1600 });

  const held = new Set();
  let fast = false;
  let cur = null;        // 지금 토막이 끝날 때의 빠르기 {x, y, r, z}. null이면 서 있다
  let stopping = false;  // 지금 토막이 멎는 토막인가
  let hinted = false;
  let gen = 0;           // 토막 번호. 새 토막이 앞 토막을 끊을 때 끊긴 토막의 moveend를 가려낸다

  const AX = ["x", "y", "r", "z", "p"];
  const zero = v => !v || AX.every(k => !v[k]);
  const same = (a, b) => a && b && AX.every(k => Math.abs(a[k] - b[k]) < 1e-6);
  const threeD = () => map.dragRotate.isEnabled();   // 입체 보기에서만 열린다 (themes.js)
  function want() {
    let x = 0, y = 0, r = 0, z = 0, p = 0;
    for (const c of held) { const k = KEYS[c]; x += k[0]; y += k[1]; r += k[2]; z += k[3]; p += k[4]; }
    const n = Math.hypot(x, y) || 1;
    if (!threeD()) p = 0;   // 평면 보기는 눕히지 않는다 (돌리기는 평면에서도 된다)
    const f = fast ? FAST : 1;
    return { x: x / n * PAN * f, y: y / n * PAN * f, r: Math.sign(r) * TURN * f, z: Math.sign(z) * ZOOM * f, p: Math.sign(p) * TILT * f };
  }

  // 빠르기 v로 간다. kind: "go"(서 있으면 RAMP 동안 빨라진 뒤 같은 빠르기), "keep"(이미 v로 가는 중), "stop"(v에서 멎음).
  // 옮기기는 화면 px가 아니라 메르카토르 좌표로 더한다. 기울인 화면에서 먼 점을 화면 좌표로 바꾸면 지평선 너머로 튄다
  let renew = 0;
  function step(v, kind) {
    clearTimeout(renew);
    const z0 = map.getZoom(), p0 = map.getPitch(), b0 = map.getBearing();
    let ms = kind === "stop" ? STOP : LONG;
    // 돌리기, 확대, 기울이기가 끝에 닿거나 너무 많이 돌지 않게 시간을 줄인다
    if (kind !== "stop") {
      if (v.r) ms = Math.min(ms, MAX_TURN / Math.abs(v.r) * 1000);
      if (v.z) ms = Math.min(ms, Math.max(300, ((v.z > 0 ? map.getMaxZoom() - z0 : z0 - map.getMinZoom()) / Math.abs(v.z)) * 1000));
      if (v.p) ms = Math.min(ms, Math.max(300, ((v.p > 0 ? map.getMaxPitch() - p0 : p0) / Math.abs(v.p)) * 1000));
    }
    const ramp = kind === "go" ? Math.min(RAMP, ms / 2) : 0;
    // 걸린 시간 T(초)에 간 몫: 빨라지는 동안 v*T*T/(2R), 그 뒤 v*(T - R/2), 멎을 때는 v*ms/2
    const s = kind === "stop" ? ms / 2000 : (ms - ramp / 2) / 1000;
    const easing = kind === "stop" ? easeOut : ramp ? (u => { const T = u * ms; return (T < ramp ? T * T / (2 * ramp) : T - ramp / 2) / (ms - ramp / 2); }) : (u => u);
    const W = 512 * Math.pow(2, z0), br = b0 * Math.PI / 180;
    const dx = v.x * s, dy = v.y * s;
    const m = maplibregl.MercatorCoordinate.fromLngLat(map.getCenter());
    const center = new maplibregl.MercatorCoordinate(m.x + (dx * Math.cos(br) - dy * Math.sin(br)) / W, m.y + (dx * Math.sin(br) + dy * Math.cos(br)) / W).toLngLat();
    const zoom = Math.min(map.getMaxZoom(), Math.max(map.getMinZoom(), z0 + v.z * s));
    const pitch = Math.min(map.getMaxPitch(), Math.max(0, p0 + v.p * s));
    map.easeTo({ center, zoom, pitch, bearing: b0 + v.r * s, duration: reduced() ? 0 : ms, easing, essential: true }, { drive: ++gen });
    // 끝나기 조금 전에 같은 빠르기로 이어 간다 (moveend를 기다리면 한 프레임 멈칫한다)
    if (kind !== "stop" && !reduced()) { const mine = gen; renew = setTimeout(() => { if (mine === gen && cur && !stopping) step(cur, "keep"); }, ms - 80); }
  }

  function update() {
    const v = want();
    if (zero(v)) {
      if (cur && !stopping) { stopping = true; step(cur, "stop"); }
      return;
    }
    if (same(v, cur) && !stopping) return;   // 같은 빠르기로 이미 가는 중
    const fromRest = !cur || stopping;
    stopping = false;
    if (fromRest) bus.emit("motion:start");
    cur = v;
    step(v, fromRest ? "go" : "keep");
  }

  // 키 움직임이 끝났거나 다른 움직임(찾은 곳으로 옮기기, 끌기)에 끊겼으면 정리한다.
  // 키를 바꿔 새로 시작하며 끊긴 앞 움직임의 moveend는 번호가 달라 그냥 넘긴다
  map.on("moveend", e => {
    if (!e.drive || e.drive !== gen) return;
    const mine = gen;
    requestAnimationFrame(() => {
      if (mine !== gen) return;   // 그 사이 새 움직임이 시작했다
      if (map.isMoving() || stopping || !cur) { clearTimeout(renew); cur = null; stopping = false; return; }
      // 이어 가기 전에 끝났다(느린 컴퓨터, 줄인 움직임 설정): 키가 눌려 있으면 다시 간다
      const v = want();
      if (zero(v)) { cur = null; return; }
      cur = v; step(v, "keep");
    });
  });

  const editable = el => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const onMap = el => !el || el === document.body || el === document.documentElement || map.getContainer().contains(el);
  document.addEventListener("keydown", e => {
    if (SHIFT.has(e.code)) { if (!fast) { fast = true; if (held.size) update(); } return; }
    const k = KEYS[e.code];
    if (!k || e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented || editable(e.target)) return;
    if (ARROWS.has(e.code) && !onMap(e.target)) return;
    if (k[4] && !threeD()) {
      if (!hinted) { hinted = true; ui.toast("R과 F로 기울이기는 입체 보기에서 됩니다. 3D 버튼을 눌러 주세요"); }
      return;
    }
    e.preventDefault();
    if (e.repeat || held.has(e.code)) return;
    held.add(e.code);
    update();
  });
  document.addEventListener("keyup", e => {
    if (SHIFT.has(e.code)) { fast = false; if (held.size) update(); return; }
    if (held.delete(e.code)) update();
  });
  window.addEventListener("blur", () => { fast = false; if (held.size) { held.clear(); update(); } });
  // 끌거나 휠을 쓰면 키 움직임은 그 자리에서 놓는다
  for (const t of ["pointerdown", "wheel"]) map.getContainer().addEventListener(t, () => { held.clear(); cur = null; stopping = false; clearTimeout(renew); }, { capture: true, passive: true });
}
