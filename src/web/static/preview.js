"use strict";
/* Preview view (/preview) — the wall of baked creatures, all animating.
 *
 * Every card plays the SAME clip (a facing, idle, sleep, or "all facings", which
 * cycles SW→SE→NW→NE), so a whole batch can be judged at a glance: a mirrored
 * row that came out backwards, a walk cycle assembled out of order, a sheet
 * whose keying ate the outline. Clicking a card opens that sheet in /animate.
 *
 * It draws straight from the baked assets — output/<id>.png plus the layout's
 * {col,row} cells, exactly what hibitomo consumes — so what animates here is the
 * asset itself, not a re-render of the spec.
 *
 * One rAF loop drives every visible card (an IntersectionObserver keeps the
 * off-screen ones idle and their sheets unloaded), so 250 creatures cost one
 * timer and only the sprite sheets you can actually see.
 */

const ISO = ["down_left", "down_right", "up_left", "up_right"];
const LABEL = { down_left: "SW", down_right: "SE", up_left: "NW", up_right: "NE" };
const CYCLE_MS = 1400;        // how long "all facings" holds each facing

const $ = (s) => document.querySelector(s);
const state = {
  creatures: [], cards: [], clip: "down_left", speed: 1, cell: 128,
  playing: true, search: "", t0: performance.now(),
};

// ---------------------------------------------------------------- data
async function load() {
  const r = await fetch("/api/baked").then((x) => x.json()).catch(() => null);
  state.creatures = (r && r.creatures) || [];
  $("#count").textContent = `${state.creatures.length} creature${state.creatures.length === 1 ? "" : "s"}`;
  render();
}

/** Frames + per-frame durations (ms) of the clip a card is showing right now. */
function clipOf(c, clip) {
  if (clip === "idle") {
    const f = c.idle || [];
    return { frames: f, ms: f.map(() => c.idle_frame_ms || 300) };
  }
  if (clip === "sleep") {
    const f = c.sleep || [];
    return { frames: f, ms: f.map(() => c.sleep_frame_ms || 400) };
  }
  const f = (c.walk || {})[clip] || [];
  const tick = c.tick_ms || 33;
  const durs = c.walk_durations || [];
  // walk_durations are in ticks and indexed by frame (padded with the last one).
  return { frames: f, ms: f.map((_, i) => (durs[i] || durs[durs.length - 1] || 6) * tick) };
}

// ---------------------------------------------------------------- rendering
function visible() {
  const q = state.search.trim().toLowerCase();
  if (!q) return state.creatures;
  return state.creatures.filter((c) =>
    String(c.id).padStart(3, "0").includes(q) || String(c.sheet_id).includes(q) ||
    (c.name || "").toLowerCase().includes(q));
}

function render() {
  const wall = $("#wall");
  wall.innerHTML = "";
  state.cards.forEach((card) => card.io && card.io.disconnect());
  state.cards = [];
  const list = visible();
  const empty = $("#empty");
  if (!list.length) {
    empty.hidden = false;
    empty.textContent = state.creatures.length
      ? "No creature matches this search."
      : "Nothing baked yet — assemble a sheet in ▶ Animate first.";
    return;
  }
  empty.hidden = true;
  const frag = document.createDocumentFragment();
  for (const c of list) frag.appendChild(cardFor(c));
  wall.appendChild(frag);
}

function cardFor(c) {
  const el = document.createElement("div");
  el.className = "card";
  el.title = `${String(c.id).padStart(3, "0")} — sheet ${c.sheet_id} · ${c.sprites} sprites\nclick to open in Animate`;
  el.innerHTML =
    `<canvas width="${c.cell_w}" height="${c.cell_h}"></canvas>` +
    `<div class="cap"><b>${String(c.id).padStart(3, "0")}</b>` +
    `<span class="sheet">${c.name || c.sheet_id}</span></div>`;
  el.onclick = () => { location.href = "/animate/" + encodeURIComponent(c.sheet_id); };

  const card = { c, el, canvas: el.querySelector("canvas"), img: null, on: false };
  card.ctx = card.canvas.getContext("2d");
  card.ctx.imageSmoothingEnabled = false;
  // Only cards you can see load their sheet and get drawn.
  card.io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      card.on = e.isIntersecting;
      if (card.on && !card.img) {
        card.img = new Image();
        card.img.onload = () => drawCard(card, now());
        card.img.src = c.png;
      }
    }
  }, { rootMargin: "200px" });
  card.io.observe(el);
  state.cards.push(card);
  return el;
}

function drawCard(card, t) {
  const c = card.c;
  const clip = state.clip === "cycle"
    ? ISO[Math.floor(t / CYCLE_MS) % ISO.length]
    : state.clip;
  const { frames, ms } = clipOf(c, clip);
  const missing = !frames.length;
  card.el.classList.toggle("no-clip", missing);
  if (missing) {
    if (!card.el.querySelector(".miss")) {
      const tag = document.createElement("span");
      tag.className = "miss";
      tag.textContent = "no " + (LABEL[clip] || clip);
      card.el.appendChild(tag);
    }
    return;
  }
  const tag = card.el.querySelector(".miss");
  if (tag) tag.remove();
  if (!card.img || !card.img.complete) return;

  const total = ms.reduce((a, b) => a + b, 0) || 1;
  let rest = (t % total + total) % total;
  let i = 0;
  while (i < frames.length - 1 && rest >= ms[i]) { rest -= ms[i]; i++; }
  const cell = frames[i];
  const cw = c.cell_w, ch = c.cell_h;
  card.ctx.clearRect(0, 0, cw, ch);
  card.ctx.drawImage(card.img, cell.col * cw, cell.row * ch, cw, ch, 0, 0, cw, ch);
  // In cycle mode the facing changes under you, so the card says which one it is
  // showing; with a facing picked by hand the chip already says it.
  let badge = card.el.querySelector(".facing");
  if (state.clip !== "cycle") { if (badge) badge.remove(); return; }
  if (!badge) {
    badge = document.createElement("span");
    badge.className = "facing";
    card.el.appendChild(badge);
  }
  if (badge.textContent !== LABEL[clip]) badge.textContent = LABEL[clip];
}

// One clock for the whole wall, scaled by the speed slider: a paused wall keeps
// its last frame (and its position), it does not reset to frame 0.
let clock = 0, last = performance.now();
function now() { return clock; }
function frame(ts) {
  const dt = ts - last;
  last = ts;
  if (state.playing) clock += dt * state.speed;
  for (const card of state.cards) if (card.on) drawCard(card, clock);
  requestAnimationFrame(frame);
}

// ---------------------------------------------------------------- events
$("#clips").onclick = (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  state.clip = chip.dataset.clip;
  $("#clips").querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c === chip));
  for (const card of state.cards) if (card.on) drawCard(card, clock);
};
$("#speed").oninput = (e) => {
  state.speed = (+e.target.value || 100) / 100;
  $("#speed-val").textContent = state.speed.toFixed(1) + "×";
};
$("#size").oninput = (e) => {
  state.cell = +e.target.value || 128;
  document.documentElement.style.setProperty("--cell", state.cell + "px");
};
$("#btn-play").onclick = () => {
  state.playing = !state.playing;
  $("#btn-play").textContent = state.playing ? "❚❚ pause" : "► play";
};
$("#search").oninput = (e) => { state.search = e.target.value; render(); };
document.addEventListener("keydown", (e) => {
  if (document.activeElement && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if (e.key === " ") { e.preventDefault(); $("#btn-play").click(); return; }
  // 1–4 pick a facing, the same numbers the animate editor uses.
  if (e.key >= "1" && e.key <= "4") {
    const chip = $("#clips").querySelector(`[data-clip="${ISO[+e.key - 1]}"]`);
    if (chip) chip.click();
  }
});

document.documentElement.style.setProperty("--cell", state.cell + "px");
requestAnimationFrame((ts) => { last = ts; frame(ts); });
load();
