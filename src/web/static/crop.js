"use strict";
// Crop view — "Pro Pixel Editor" build.
//   Gallery: visual thumbnail browser with To-do/Done/Deleted filters + quick
//            delete/restore, backed by /api/sheets (state) and /thumb.
//   Editor:  one unified pointer — drag empty = rubber-band SELECT (Del deletes
//            every selected box), click/drag a box = select/move, handle = resize,
//            Alt+drag = draw a new box. Background-picking is the hero action:
//            choosing a background colour re-separates the sheet into candidate sprites.

const MIN_BOX = 3;
const HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

// The gallery filter survives leaving the view (Animate ⇄ Crop are separate page
// loads), so you come back to the chip you were working through.
const FILTERS = ["all", "todo", "done", "deleted"];
const FILTER_KEY = "crop.filter";
function savedFilter() {
  try { const v = localStorage.getItem(FILTER_KEY); return FILTERS.includes(v) ? v : "all"; } catch { return "all"; }
}
function rememberFilter(f) { try { localStorage.setItem(FILTER_KEY, f); } catch { /* private mode */ } }

const S = {
  sheets: [], counts: {}, filter: savedFilter(),
  sheetId: null, img: null, octx: null,
  detect: null, boxes: [], boxSeq: 0, picks: [],
  eyedrop: false, selected: [], zoom: 3, drag: null, dirty: false,
};
// selection helpers (S.selected is an array of box ids)
const isSel = (id) => S.selected.includes(id);
const selBoxes = () => S.boxes.filter((b) => isSel(b.id));
const selOne = () => (S.selected.length === 1 ? S.boxes.find((b) => b.id === S.selected[0]) : null);
const rectsOverlap = (a, b) => !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y);

const $ = (s) => document.querySelector(s);
const canvas = $("#canvas");
const ctx = canvas.getContext("2d");
const setStatus = (m, err) => { const e = $("#status"); e.textContent = m || ""; e.style.color = err ? "var(--del)" : "var(--done)"; };
const hex = (rgb) => "#" + rgb.map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase();
function toRgb(c) {
  if (Array.isArray(c)) return c.slice(0, 3).map((v) => v | 0);
  if (typeof c === "string") { const s = c.replace(/0x|#/gi, ""); if (s.length === 6) return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16)); }
  return null;
}
function normalizeBg(bg) {
  if (!bg) return;
  if (!Array.isArray(bg.colors) || !bg.colors.length) {
    const c = bg.rgb || bg.color;
    bg.colors = c ? [toRgb(c)].filter(Boolean) : [];
  } else bg.colors = bg.colors.map(toRgb).filter(Boolean);
}

// ============================================================ GALLERY
async function loadSheets() {
  const r = await fetch("/api/sheets").then((x) => x.json());
  S.sheets = r.sheets; S.counts = r.counts || {};
  const pct = r.count ? Math.round((r.reviewed / r.count) * 100) : 0;
  $("#progress-bar").style.width = pct + "%";
  $("#progress-count").innerHTML = `<b>${r.reviewed}</b> / ${r.count} done`;
  $("#count-all").textContent = S.counts.all || 0;
  $("#count-todo").textContent = S.counts.todo || 0;
  $("#count-done").textContent = S.counts.done || 0;
  $("#count-deleted").textContent = S.counts.deleted || 0;
  syncChips();
  renderGallery();
}

function syncChips() {
  document.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c.dataset.filter === S.filter));
}

function visibleSheets() {
  return S.sheets.filter((s) => S.filter === "all" || s.state === S.filter);
}

function renderGallery() {
  const grid = $("#grid"); grid.innerHTML = "";
  const list = visibleSheets();
  const empty = $("#empty");
  if (!list.length) {
    empty.hidden = false;
    empty.textContent = S.sheets.length ? "No sheets match this filter." : "No sheets yet.";
    return;
  }
  empty.hidden = true;
  const frag = document.createDocumentFragment();
  for (const s of list) frag.appendChild(tileFor(s));
  grid.appendChild(frag);
}

function tileFor(s) {
  const tile = document.createElement("div");
  tile.className = "tile" + (s.state === "deleted" ? " is-deleted" : "");
  const badge = s.state === "deleted" ? "deleted" : s.state; // todo|done|deleted
  const badgeLabel = { todo: "To-do", done: "Done", deleted: "Deleted" }[badge];
  const badgeVar = { todo: "--todo", done: "--done", deleted: "--del" }[badge];
  const countTxt = (s.state === "done" && s.count) ? `<span class="mcount">${s.count} sprites</span>` : "";
  tile.innerHTML =
    `<span class="corner-badge badge ${badge}"><span class="d" style="background:var(${badgeVar})"></span>${badgeLabel}</span>` +
    `<div class="thumb"><img loading="lazy" src="/api/sheets/${s.id}/thumb?max=240" alt="${s.id}"></div>` +
    `<div class="meta"><span class="sid">${s.id}</span>${countTxt}</div>` +
    `<div class="quick">` +
      (s.state === "deleted"
        ? `<span class="qbtn go" data-act="open">Open</span><span class="qbtn" data-act="todo">Restore</span>`
        : s.state === "done"
        ? `<span class="qbtn go" data-act="open">Open</span><span class="qbtn" data-act="todo">To-do</span><span class="qbtn rm" data-act="delete">Delete</span>`
        : `<span class="qbtn go" data-act="open">Open</span><span class="qbtn rm" data-act="delete">Delete</span>`) +
    `</div>`;
  tile.onclick = (e) => {
    const act = e.target.closest("[data-act]") && e.target.closest("[data-act]").dataset.act;
    if (act === "delete") { e.stopPropagation(); setState(s.id, "deleted"); return; }
    if (act === "todo") { e.stopPropagation(); setState(s.id, "todo"); return; }
    openEditor(s.id);
  };
  return tile;
}

async function setState(sid, state) {
  await fetch(`/api/sheets/${sid}/state`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state }) });
  await loadSheets();
}

$("#chips").onclick = (e) => {
  const chip = e.target.closest(".chip"); if (!chip) return;
  S.filter = chip.dataset.filter;
  rememberFilter(S.filter);
  syncChips();
  renderGallery();
};

// ============================================================ EDITOR
async function openEditor(sid, push = true) {
  S.sheetId = sid; S.selected = []; S.dirty = false; S.eyedrop = false; S.picks = [];
  if (push) history.pushState({ view: "editor", sid }, "", "/crop/" + encodeURIComponent(sid));
  $("#btn-eyedrop").classList.remove("armed");
  $("#view-editor").hidden = false;
  growResult("");   // no stale grow report from the previous sheet
  S.img = await loadImage(`/api/sheets/${sid}/image`);
  buildImageData();
  const meta = S.sheets.find((x) => x.id === sid) || {};
  const idx = S.sheets.findIndex((x) => x.id === sid);
  if (meta.state === "done") {
    // Restore a previously-saved selection so it can be re-edited.
    S.detect = await fetch(`/api/sheets/${sid}/boxes`).then((x) => x.json());
    normalizeBg(S.detect.background);
    S.boxes = (S.detect.boxes || []).map((b) => ({ id: b.id, x: b.x, y: b.y, w: b.w, h: b.h }));
    S.picks = (S.detect.background.colors || []).map((c) => c.slice());
  } else {
    // Fresh sheet: start EMPTY. Sprites are generated only when the operator hits
    // Auto-detect or picks a background colour. No auto-detection on open.
    S.detect = { id: sid, image: { w: S.img.width, h: S.img.height }, background: { mode: "solid", colors: [] }, boxes: [] };
    S.boxes = []; S.picks = [];
  }
  bumpSeq();
  $("#ed-path").textContent = sid;
  $("#ed-crumb").textContent = `· sheet ${idx + 1} of ${S.sheets.length} · ${S.detect.image.w}×${S.detect.image.h} px`;
  renderBgChips(); renderCount();
  fitZoom(); draw();
}
function closeEditor(push = true) {
  if (push) history.pushState({ view: "gallery" }, "", "/crop");
  $("#view-editor").hidden = true;
  S.sheetId = null; S.img = null; S.octx = null; S.boxes = []; S.selected = []; S.drag = null;
  S.eyedrop = false;
  $("#btn-eyedrop").classList.remove("armed");
}

function loadImage(src) { return new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = src; }); }
function buildImageData() {
  const oc = document.createElement("canvas"); oc.width = S.img.width; oc.height = S.img.height;
  const octx = oc.getContext("2d", { willReadFrequently: true }); octx.imageSmoothingEnabled = false;
  octx.drawImage(S.img, 0, 0); S.octx = octx;
}
function bumpSeq() { let mx = -1; for (const b of S.boxes) { const n = parseInt(String(b.id).replace(/\D/g, ""), 10); if (n > mx) mx = n; } S.boxSeq = mx + 1; }
const newId = () => "b" + (S.boxSeq++);

// -------- canvas rendering
function fitZoom() {
  const stage = $("#stage"); if (!S.img) return;
  const z = Math.max(1, Math.min(12, Math.floor(Math.min((stage.clientWidth - 56) / S.img.width, (stage.clientHeight - 56) / S.img.height))));
  S.zoom = z || 1; $("#zoom").value = S.zoom; $("#zoom-val").textContent = S.zoom * 100 + "%";
}
function draw() {
  if (!S.img) return;
  renderSizePanel();   // the size box follows the selection
  const z = S.zoom;
  canvas.width = S.img.width * z; canvas.height = S.img.height * z;
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(S.img, 0, 0, canvas.width, canvas.height);
  const only = selOne();
  S.boxes.forEach((b, i) => {
    const sel = isSel(b.id);
    const col = sel ? "#6ea8fe" : "#4bd1c6";
    ctx.fillStyle = sel ? "rgba(110,168,254,.16)" : "rgba(75,209,198,.08)";
    ctx.fillRect(b.x * z, b.y * z, b.w * z, b.h * z);
    ctx.strokeStyle = col; ctx.lineWidth = sel ? 2 : 1.5;
    ctx.strokeRect(b.x * z + 0.5, b.y * z + 0.5, b.w * z - 1, b.h * z - 1);
    // index label
    const label = String(i + 1);
    ctx.font = "9px " + "monospace"; const tw = Math.ceil(ctx.measureText(label).width);
    ctx.fillStyle = col; ctx.fillRect(b.x * z, b.y * z, tw + 6, 12);
    ctx.fillStyle = "#06201e"; ctx.textBaseline = "top"; ctx.fillText(label, b.x * z + 3, b.y * z + 2);
    // resize handles only when exactly one box is selected
    if (only && b.id === only.id) {
      for (const h of HANDLES) { const [hx, hy] = handlePos(b, h); ctx.fillStyle = "#6ea8fe"; ctx.fillRect(hx * z - 4, hy * z - 4, 8, 8); ctx.strokeStyle = "#0c0e11"; ctx.lineWidth = 1; ctx.strokeRect(hx * z - 4, hy * z - 4, 8, 8); }
    }
  });
  if (S.drag && (S.drag.kind === "draw" || S.drag.kind === "select")) {
    // the rubber band may run past the sheet edges (so border sprites are easy to
    // sweep up); the canvas simply clips whatever falls outside.
    const r = normRect(S.drag.x0, S.drag.y0, S.drag.x1, S.drag.y1);
    const stroke = S.drag.kind === "select" ? "#6ea8fe" : "#4bd1c6";
    if (S.drag.kind === "select") { ctx.fillStyle = "rgba(110,168,254,.10)"; ctx.fillRect(r.x * z, r.y * z, r.w * z, r.h * z); }
    ctx.strokeStyle = stroke; ctx.setLineDash([5, 3]); ctx.lineWidth = 1.5;
    ctx.strokeRect(r.x * z + 0.5, r.y * z + 0.5, r.w * z, r.h * z); ctx.setLineDash([]);
  }
}
function handlePos(b, h) {
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
  return { nw: [b.x, b.y], n: [cx, b.y], ne: [b.x + b.w, b.y], e: [b.x + b.w, cy], se: [b.x + b.w, b.y + b.h], s: [cx, b.y + b.h], sw: [b.x, b.y + b.h], w: [b.x, cy] }[h];
}
const normRect = (x0, y0, x1, y1) => ({ x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) });
function evtImg(e) { const r = canvas.getBoundingClientRect(); return { x: Math.round((e.clientX - r.left) / S.zoom), y: Math.round((e.clientY - r.top) / S.zoom) }; }
function boxAt(x, y) { let best = null; for (const b of S.boxes) if (x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h) if (!best || b.w * b.h < best.w * best.h) best = b; return best; }
function handleAt(b, x, y) { const tol = Math.max(4, 8 / S.zoom); for (const h of HANDLES) { const [hx, hy] = handlePos(b, h); if (Math.abs(x - hx) <= tol && Math.abs(y - hy) <= tol) return h; } return null; }

// -------- unified pointer interaction (no tool modes)
//   drag            → rubber-band SELECT the sprites the rectangle touches
//                     (works even starting on a box; Shift adds to the selection)
//   click a box     → select it · drag an ALREADY-selected box → move it
//   handle          → resize (only when exactly one box is selected)
//   Alt + drag      → draw a new box (rare manual add)
//   Del             → delete every selected box
const CLICK_EPS = 3;  // image px of movement below which a drag counts as a click
canvas.addEventListener("mousedown", (e) => {
  const { x, y } = evtImg(e);
  if (S.eyedrop) { doEyedrop(x, y); return; }
  e.preventDefault();   // no text/image drag-ghost while rubber-banding
  // Alt+drag draws a new box, wherever it starts
  if (e.altKey) { S.selected = []; S.drag = { kind: "draw", x0: x, y0: y, x1: x, y1: y }; draw(); return; }
  // resize handle (only when exactly one box is selected)
  const one = selOne();
  if (one) { const h = handleAt(one, x, y); if (h) { S.drag = { kind: "resize", h, box: one, orig: { ...one } }; return; } }
  const b = boxAt(x, y);
  // dragging an ALREADY-selected box moves the whole selection; anything else
  // begins a rubber-band select (so you can sweep across cells on dense sheets).
  if (b && isSel(b.id) && !e.shiftKey) {
    const boxes = selBoxes().map((bx) => ({ box: bx, ox: bx.x, oy: bx.y }));
    S.drag = { kind: "move", boxes, sx: x, sy: y, moved: false };
    return;
  }
  S.drag = { kind: "select", x0: x, y0: y, x1: x, y1: y, add: e.shiftKey, downBox: b };
  draw();
});
// …and it can also START outside the sheet, on the empty stage around it, so a
// border sprite can be swept from the outside in.
$("#stage").addEventListener("mousedown", (e) => {
  if (e.target === canvas || !S.img || S.eyedrop) return;
  e.preventDefault();
  const { x, y } = evtImg(e);
  S.drag = { kind: e.altKey ? "draw" : "select", x0: x, y0: y, x1: x, y1: y, add: e.shiftKey, downBox: null };
  if (e.altKey) S.selected = [];
  draw();
});
// A live drag is tracked on the WINDOW, not the canvas: the rubber band keeps
// following the pointer once it leaves the sheet (coordinates go negative / past
// w,h freely), so sprites sitting right on the border are easy to sweep up.
window.addEventListener("mousemove", (e) => {
  const d = S.drag;
  if (!d || !S.img) return;
  const { x, y } = evtImg(e);
  if (d.kind === "draw" || d.kind === "select") { d.x1 = x; d.y1 = y; draw(); return; }
  if (d.kind === "move") { for (const m of d.boxes) { m.box.x = clampX(m.ox + (x - d.sx), m.box.w); m.box.y = clampY(m.oy + (y - d.sy), m.box.h); } d.moved = true; S.dirty = true; draw(); return; }
  if (d.kind === "resize") { resizeBox(d, x, y); S.dirty = true; draw(); }
});
canvas.addEventListener("mousemove", (e) => {   // hover feedback only
  if (S.drag || !S.img) return;
  if (S.eyedrop || e.altKey) { canvas.style.cursor = "crosshair"; return; }
  const { x, y } = evtImg(e);
  const b = boxAt(x, y);
  const one = selOne();
  if (one && handleAt(one, x, y)) canvas.style.cursor = "nwse-resize";
  else canvas.style.cursor = (b && isSel(b.id)) ? "move" : "crosshair";
});
window.addEventListener("mouseup", () => {
  const d = S.drag; S.drag = null;
  if (!d) return;
  if (d.kind === "draw") {
    const r = clipToSheet(intRect(normRect(d.x0, d.y0, d.x1, d.y1)));
    if (r.w >= MIN_BOX && r.h >= MIN_BOX) { const nb = { id: newId(), ...r }; S.boxes.push(nb); S.selected = [nb.id]; S.dirty = true; renderCount(); }
    draw();
  } else if (d.kind === "select") {
    const r = normRect(d.x0, d.y0, d.x1, d.y1);
    if (r.w < CLICK_EPS && r.h < CLICK_EPS) {   // a click, not a drag
      if (d.downBox) S.selected = d.add ? (isSel(d.downBox.id) ? S.selected.filter((id) => id !== d.downBox.id) : [...S.selected, d.downBox.id]) : [d.downBox.id];
      else if (!d.add) S.selected = [];         // click empty → clear
      setStatus("");
    } else {                                    // a real drag → rubber-band select
      const hit = S.boxes.filter((b) => rectsOverlap(b, r)).map((b) => b.id);
      S.selected = d.add ? Array.from(new Set([...S.selected, ...hit])) : hit;
      setStatus(hit.length ? `${hit.length} selected — Del to delete` : "");
    }
    draw();
  } else draw();
});
const clampX = (x, w) => Math.max(0, Math.min(S.img.width - w, x));
const clampY = (y, h) => Math.max(0, Math.min(S.img.height - h, y));
const intRect = (r) => ({ x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h) });
// clip a rect (which may spill outside while dragging) back onto the sheet
function clipToSheet(r) {
  const x0 = Math.max(0, r.x), y0 = Math.max(0, r.y);
  const x1 = Math.min(S.img.width, r.x + r.w), y1 = Math.min(S.img.height, r.y + r.h);
  return { x: x0, y: y0, w: Math.max(0, x1 - x0), h: Math.max(0, y1 - y0) };
}
function resizeBox(d, x, y) {
  const o = d.orig; let { x: nx, y: ny, w: nw, h: nh } = o; const h = d.h;
  if (h.includes("w")) { nx = Math.min(x, o.x + o.w - MIN_BOX); nw = o.x + o.w - nx; }
  if (h.includes("e")) { nw = Math.max(MIN_BOX, x - o.x); }
  if (h.includes("n")) { ny = Math.min(y, o.y + o.h - MIN_BOX); nh = o.y + o.h - ny; }
  if (h.includes("s")) { nh = Math.max(MIN_BOX, y - o.y); }
  d.box.x = Math.max(0, Math.round(nx)); d.box.y = Math.max(0, Math.round(ny)); d.box.w = Math.round(nw); d.box.h = Math.round(nh);
}
function deleteSelected() { if (!S.selected.length) return; const n = S.selected.length; S.boxes = S.boxes.filter((b) => !isSel(b.id)); S.selected = []; S.dirty = true; renderCount(); draw(); setStatus(`deleted ${n} ${n === 1 ? "box" : "boxes"}`); }

// -------- nudging the selection edge by edge
// Grow-to-cell reads the cell off the sheet and can only ever read it as well as
// the sprites let it, so this is the manual counterweight: push ONE EDGE of
// everything selected in or out, a pixel at a time. No sizes to type and no need
// for the selection to share a size — each box just moves its own edge, so `+` on
// the left always means "one more px of room on the left". Shrinking is the point
// (a grown cell that came out a shade too generous), but it grows just as well.
// Like the drag handles it doesn't police overlaps; it just says when two boxes
// ended up on top of each other. Nothing goes under MIN_BOX or off the sheet.
const SIDE_NAME = { t: "top", r: "right", b: "bottom", l: "left", a: "all sides" };

function renderSizePanel() {
  const sel = selBoxes();
  $("#size-sec").hidden = !sel.length;
  if (!sel.length) return;
  const sizes = new Set(sel.map((b) => `${b.w} × ${b.h} px`));
  $("#size-now").textContent = sizes.size === 1 ? [...sizes][0] : `${sizes.size} different sizes`;
  $("#size-count").textContent = sel.length > 1 ? `${sel.length} selected` : "";
}

function nudge(side, d) {
  const sel = selBoxes(); if (!sel.length || !S.img) return;
  let moved = 0, blocked = 0;
  for (const b of sel) {
    const r = { x: b.x, y: b.y, w: b.w, h: b.h };
    if (side === "l" || side === "a") { r.x -= d; r.w += d; }
    if (side === "r" || side === "a") { r.w += d; }
    if (side === "t" || side === "a") { r.y -= d; r.h += d; }
    if (side === "b" || side === "a") { r.h += d; }
    if (r.w < MIN_BOX || r.h < MIN_BOX ||
        r.x < 0 || r.y < 0 || r.x + r.w > S.img.width || r.y + r.h > S.img.height) { blocked += 1; continue; }
    Object.assign(b, r); moved += 1;
  }
  if (moved) { S.dirty = true; draw(); }
  const sizes = new Set(sel.map((b) => `${b.w}×${b.h}px`));
  const hits = S.boxes.filter((a) => S.boxes.some((c) => a !== c && overlaps(a, c))).length;
  growResult(`${SIDE_NAME[side]} ${d > 0 ? "+" : "−"}1 · ${sel.length} ${sel.length === 1 ? "sprite" : "sprites"} at ` +
    (sizes.size === 1 ? [...sizes][0] : `${sizes.size} sizes`) +
    (blocked ? ` · ${blocked} at the limit` : "") + (hits ? ` · ${hits} overlap` : ""),
    !!(blocked || hits));
}

// Press and hold to keep nudging — trimming 6px shouldn't be six clicks.
function holdToRepeat(el, fire) {
  let delay = null, tick = null;
  const stop = () => { clearTimeout(delay); clearInterval(tick); delay = tick = null; };
  el.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    fire();
    delay = setTimeout(() => { tick = setInterval(fire, 60); }, 400);
  });
  for (const ev of ["pointerup", "pointerleave", "pointercancel"]) el.addEventListener(ev, stop);
  window.addEventListener("pointerup", stop);
}

// -------- growing the boxes out to the sheet's cell
// Boxing a sprite on its pixels throws away the empty room it moves into — a frame
// caught mid-jump or mid-step is drawn high, or off to one side, inside its cell,
// and that room IS the jump — so every frame comes out on a different origin and
// the animation jitters. This grows the boxes back out until they tile, which
// hands the room back: each sprite keeps its offset inside the cell it came from.
// It runs over EVERY box on the sheet, so there is nothing to select first.
const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

// The editor is a fixed overlay over the gallery, so the appbar's status line is
// out of sight while cropping — the outcome is reported in the panel as well.
function growResult(msg, warn) {
  const e = $("#grow-result");
  e.textContent = msg; e.hidden = !msg; e.classList.toggle("warn", !!warn);
  setStatus(msg, warn);
}

// Boxes whose intervals overlap on one axis belong to the same line — a column
// for x, a row for y. That's the sheet's grid, read off the boxes themselves.
function axisLines(boxes, s, e) {
  const out = [];
  for (const b of boxes.slice().sort((p, q) => p[s] - q[s])) {
    const last = out[out.length - 1];
    if (last && b[s] < last.end) { last.end = Math.max(last.end, b[s] + b[e]); last.items.push(b); }
    else out.push({ start: b[s], end: b[s] + b[e], items: [b] });
  }
  return out;
}

const median = (xs) => xs.slice().sort((a, b) => a - b)[Math.floor(xs.length / 2)];

// How big a cell on this axis may be: never smaller than the biggest box it has to
// hold, and no bigger than the sheet's own packing says.
//   Two estimates set the ceiling and the roomier one wins, because each misses in
// its own direction. The median STEP from one line to the next is the pitch itself,
// and the median keeps a chained line (which shows up as one double step) from
// inflating it — but sprites leaning within their cells shorten the steps, so it
// reads low. The widest box plus the typical margin reads low the other way, when a
// line happens to hold only narrow frames. Both are capped at twice a typical line:
// past that it isn't a cell margin any more.
//   Neither means anything unless the sprites are actually PACKED, so a typical gap
// wider than a typical sprite says this isn't a grid at all — two lone sprites at
// opposite ends of a sheet, say — and the axis just holds its content. `cap` lets a
// caller come back asking for something tighter.
function pitchRange(boxes, s, e, cap) {
  const ls = axisLines(boxes, s, e);
  const extents = ls.map((l) => l.end - l.start);
  const steps = [], gaps = [];
  for (let i = 1; i < ls.length; i++) {
    steps.push(ls[i].start - ls[i - 1].start);
    gaps.push(Math.max(0, ls[i].start - ls[i - 1].end));
  }
  const ext = median(extents), gap = gaps.length ? median(gaps) : Infinity;
  // Boxes that start within half a box of each other are the same column, and a
  // column shares one cell — so the cell has to hold the whole cluster, jitter and
  // all. A row of frames bobbing by a px needs 33, not the 32 of its tallest box.
  let pMin = Math.max(...boxes.map((b) => b[e]));
  for (const c of startClusters(boxes, s, e, pMin / 2)) pMin = Math.max(pMin, c.hi - c.lo);
  let pMax = gap <= ext ? Math.max(median(steps), pMin + 2 * Math.floor(gap / 2)) : pMin;
  pMax = Math.min(Math.max(pMax, pMin), Math.max(2 * ext, pMin), cap);
  return { pMin, pMax };
}

// Group boxes into columns (or rows) by where they START, splitting whenever the
// next one begins `apart` px or more further along. Unlike axisLines this cannot
// chain: two neighbours that merely lean into each other stay apart, because what
// separates columns is where their sprites begin, not whether they touch.
function startClusters(boxes, s, e, apart) {
  const out = [];
  for (const b of boxes.slice().sort((p, q) => p[s] - q[s])) {
    const g = out[out.length - 1], end = b[s] + b[e];
    if (g && b[s] - g.lo < apart) { g.hi = Math.max(g.hi, end); g.items.push(b); }
    else out.push({ lo: b[s], hi: end, items: [b] });
  }
  return out;
}

// Read one axis of the grid: a pitch p and an origin c0 laying cells at c0 + i*p.
//   Lines are a HINT, never the answer. Clustering chains boxes through each other,
// so one wide sprite — or two tight crops that lean into each other and touch by a
// px — drags several real columns into a single line. Handing every box in a line
// the same cell then dumps three sprites into one giant box, which is exactly the
// bug this guards against. So the pitch is estimated from the lines, then SEARCHED
// downwards, and a placement only counts once every box lands ENTIRELY inside one
// cell. A chained line can't survive that: boxes that overlap each other's track
// can't be split across cells, so the fit fails and the caller falls back.
function fitAxis(boxes, s, e, limit, cap) {
  const { pMin, pMax } = pitchRange(boxes, s, e, cap);
  if (pMax < pMin) return null;                             // the caller wants tighter than a box
  const lo = Math.min(...boxes.map((b) => b[s]));
  for (let p = pMax; p >= pMin; p--) {
    // c0 can only sit in the p px before the first box — any further back and the
    // first cell would be empty. Collect what works and take the middle one, which
    // spreads the leftover slack instead of piling it against one edge.
    const ok = [];
    for (let c0 = Math.max(0, lo - p + 1); c0 <= lo; c0++) {
      let last = 0, fits = true;
      for (const b of boxes) {
        const i = Math.floor((b[s] - c0) / p);
        if (b[s] + b[e] > c0 + (i + 1) * p) { fits = false; break; }   // straddles two cells
        if (i > last) last = i;
      }
      if (fits && c0 + (last + 1) * p <= limit) ok.push(c0);
    }
    if (ok.length) return { c0: ok[Math.floor((ok.length - 1) / 2)], p };
  }
  return null;
}

// The regular grid the sheet's sprites came off. Every box in a column gets the
// SAME x and width and every box in a row the same y and height, so a sprite's
// offset inside its cell survives exactly as drawn — which is the whole point:
// that offset IS the jump or the step. Returns the new rects, or null when the
// boxes don't all lie on one regular grid (two blocks at different pitches, say).
//   ONE SPRITE PER CELL is the invariant that keeps a bad fit from shipping: if two
// boxes land in the same cell they'd come out as one identical rect drawn twice, so
// the pitch is asked again a notch tighter, and if nothing separates them the sheet
// simply isn't one grid and the caller grows each box where it stands instead.
//   `others` are the boxes left out of this run — credit text, a logo, a portrait.
// Cells step around them exactly as they step around each other.
function gridFit(boxes, others) {
  let capX = Infinity, capY = Infinity;
  for (let tries = 0; tries < 24; tries += 1) {
    const fx = fitAxis(boxes, "x", "w", S.img.width, capX);
    const fy = fitAxis(boxes, "y", "h", S.img.height, capY);
    if (!fx || !fy) return null;
    const taken = new Set(), cells = [];
    for (const b of boxes) {
      const i = Math.floor((b.x - fx.c0) / fx.p), j = Math.floor((b.y - fy.c0) / fy.p);
      if (taken.has(i + ":" + j)) break;
      taken.add(i + ":" + j);
      cells.push({ ref: b, x: fx.c0 + i * fx.p, w: fx.p, y: fy.c0 + j * fy.p, h: fy.p });
    }
    if (cells.length === boxes.length && !cells.some((c) => others.some((o) => overlaps(c, o)))) return cells;
    if (fx.p >= fy.p) capX = fx.p - 1; else capY = fy.p - 1;   // tighten the roomier axis
  }
  return null;
}

// Same axis, read loosely: one cell SIZE for everyone, but each column free to sit
// where its own sprites are. Plenty of rips are laid out in blocks — three frames at
// a 37px pitch, a wider jump, three more — so no single arithmetic grid covers the
// sheet even though every frame plainly came off the same 36px cell. Insisting on the
// grid there sent the whole sheet to per-box growth, which hands each box whatever
// its own gap happens to be and leaves twelve sprites at nine different sizes.
//   Columns are cut by START, not by overlap, and the cut is half a cell: sprites of
// one column start within a few px of each other, the next column starts a whole
// pitch away. That is what keeps two neighbours from being served one shared cell —
// the chained-line bug — and any group too wide to fit a cell rejects the size
// outright. Each cell then sits as close to centred on its own content as the sheet
// allows. Two columns may well end up overlapping here; whether that matters is a
// question for the other axis, and looseFit answers it.
function looseAxis(boxes, s, e, limit, cap) {
  const { pMin, pMax } = pitchRange(boxes, s, e, cap);
  for (let p = pMax; p >= pMin; p--) {
    const groups = startClusters(boxes, s, e, p / 2);
    if (groups.some((g) => g.hi - g.lo > p)) continue;      // a column too wide for the cell
    if (groups.some((g) => g.hi - p > Math.min(g.lo, limit - p))) continue;  // no room on the sheet
    for (const g of groups) {
      g.c = Math.min(Math.min(g.lo, limit - p),
                     Math.max(Math.max(0, g.hi - p), g.lo - Math.floor((p - (g.hi - g.lo)) / 2)));
    }
    return { p, groups };
  }
  return null;
}

// Uniform cells for a sheet that never was one regular grid. Same size everywhere —
// which is what an animation needs — with each column and row anchored on its own
// sprites instead of on a pitch.
//   Overlap is settled here, on the finished cells, not one axis at a time: two
// columns whose cells overlap are perfectly fine when the sprites in them sit in
// different rows, and rips are full of that — one frame reaching further left than
// the frame above it. What is not fine is two cells that overlap for real, or two
// sprites served one cell, and either sends the size back a notch tighter. Cells
// never overlapping means no cell can hold a neighbour's pixels either, since every
// sprite is inside its own.
function looseFit(boxes, others) {
  let capX = Infinity, capY = Infinity;
  for (let tries = 0; tries < 24; tries += 1) {
    const fx = looseAxis(boxes, "x", "w", S.img.width, capX);
    const fy = looseAxis(boxes, "y", "h", S.img.height, capY);
    if (!fx || !fy) return null;
    const at = new Map();
    fx.groups.forEach((g, i) => g.items.forEach((b) => at.set(b, { i, x: g.c })));
    const taken = new Set(), cells = [];
    fy.groups.forEach((g, j) => g.items.forEach((b) => {
      const col = at.get(b), k = col.i + ":" + j;
      if (taken.has(k)) return;
      taken.add(k);
      cells.push({ ref: b, x: col.x, w: fx.p, y: g.c, h: fy.p });
    }));
    const bad = [];
    cells.forEach((a, i) => cells.forEach((b, j) => { if (j > i && overlaps(a, b)) bad.push([a, b]); }));
    cells.forEach((a) => others.forEach((o) => { if (overlaps(a, o)) bad.push([a, o]); }));
    if (cells.length === boxes.length && !bad.length) return cells;
    if (bad.length) {
      // Give back exactly what the worst overlap costs, on the axis it costs least:
      // two columns crossing by 2px want 2px off the cell width, not a slow squeeze
      // of the rows, which is the axis that had nothing to do with it.
      let cx = 0, cy = 0;
      for (const [a, b] of bad) {
        cx = Math.max(cx, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
        cy = Math.max(cy, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
      }
      if (cx <= cy) capX = fx.p - cx; else capY = fy.p - cy;
    } else if (fx.p >= fy.p) capX = fx.p - 1; else capY = fy.p - 1;   // shared cell: tighten
  }
  return null;
}

// Write the new rects back onto the boxes; returns how many actually moved.
function applyRects(rects) {
  let changed = 0;
  for (const k of rects) {
    if (k.x !== k.ref.x || k.y !== k.ref.y || k.w !== k.ref.w || k.h !== k.ref.h) changed += 1;
    k.ref.x = k.x; k.ref.y = k.y; k.ref.w = k.w; k.ref.h = k.h;
  }
  if (changed) S.dirty = true;
  draw();
  return changed;
}

// A cell is one size for every frame, so it can only be read off boxes that ARE
// frames. Pick background leaves the whole sheet boxed — credit text, a logo, the big
// portrait — and against that crowd no size fits everyone and nothing can be grown.
// So a SELECTION, if there is one, is the set of frames: grow those and step around
// the rest. With nothing selected it still runs over every box, as before.
function growToCells() {
  if (!S.img) return;
  const sel = selBoxes();
  const boxes = sel.length > 1 ? sel : S.boxes;
  const others = sel.length > 1 ? S.boxes.filter((b) => !isSel(b.id)) : [];
  const what = sel.length > 1 ? "selected sprites" : "sprites";
  if (boxes.length < 2) { growResult("nothing to grow — detect the sprites first", true); return; }
  // Best case the sprites sit on one regular grid and the cell can be laid down as
  // one: the same size everywhere AND lined up, so no frame drifts against another.
  const cells = gridFit(boxes, others);
  if (cells) {
    applyRects(cells);
    const nx = new Set(cells.map((c) => c.x)).size, ny = new Set(cells.map((c) => c.y)).size;
    growResult(`${cells.length} ${what} on a ${cells[0].w}×${cells[0].h}px grid · ${nx} × ${ny} cells`);
    return;
  }
  // Not one grid — plenty of rips are laid out in blocks. Keep the one thing the
  // animation actually needs, a single cell SIZE, and let each column and row sit
  // where its own sprites are.
  const loose = looseFit(boxes, others);
  if (loose) {
    const n = applyRects(loose);
    growResult(n
      ? `${loose.length} ${what} on ${loose[0].w}×${loose[0].h}px cells · not one regular grid, ` +
        "so each column keeps its own place"
      : `${loose.length} ${what} already on ${loose[0].w}×${loose[0].h}px cells — nothing to grow`);
    return;
  }
  // No size fits them all: the sprites are wildly different, or already touching, or
  // two of them overlap. Growing each box into whatever gap it happens to have would
  // leave every frame a different size — the one thing this is here to prevent — so
  // nothing moves. Odds are the sheet still has its credit text and artwork boxed in
  // with the frames, and selecting just the frames is the way out.
  growResult(sel.length > 1
    ? "no common cell fits the selected sprites — they differ too much, or already touch · " +
      "use Size to nudge them by hand"
    : "no common cell fits every box on the sheet — select just the animation frames " +
      "(drag over them) and press G again", true);
}

// -------- background picking (drives detection)
function doEyedrop(x, y) {
  if (!S.octx) return;
  const px = S.octx.getImageData(x, y, 1, 1).data;
  const rgb = [px[0], px[1], px[2]];
  // A transparent pixel has no colour to key — its RGB reads as black, and keying
  // black would eat every outline on the sheet. Transparency is already background,
  // so just separate on it.
  if (px[3] < 8) {
    S.eyedrop = false; $("#btn-eyedrop").classList.remove("armed");
    setStatus("that pixel is transparent — already background, separating…");
    redetect();
    return;
  }
  if (!S.picks.some((c) => c[0] === rgb[0] && c[1] === rgb[1] && c[2] === rgb[2])) S.picks.push(rgb);
  S.eyedrop = false; $("#btn-eyedrop").classList.remove("armed");
  renderBgChips(); setStatus(`background ${hex(rgb)} — separating…`); redetect();
}
function renderBgChips() {
  const host = $("#bg-chips"); host.innerHTML = "";
  S.picks.forEach((c, i) => {
    const row = document.createElement("div"); row.className = "swatch-row";
    row.innerHTML = `<span class="sw" style="background:${hex(c)}"></span><span class="hex">${hex(c)}</span><span class="lbl">bg</span><span class="x">✕</span>`;
    row.querySelector(".x").onclick = () => {
      S.picks.splice(i, 1); renderBgChips();
      if (S.picks.length) { redetect(); }
      else { S.boxes = []; S.selected = []; renderCount(); draw(); setStatus("pick a background colour to find sprites"); }
    };
    host.appendChild(row);
  });
}

// -------- one-click auto-detect
//   1. the sheet's corner colour IS the background (rips key the whole sheet with
//      one flat colour), so it is eyedropped for you and separated as usual;
//   2. of what comes out, the most repeated box size that is at least
//      AUTO_MIN_SIDE px on both sides is taken to be "the sprite size" — every
//      sprite sits in an identically-sized cell, so the modal size is the sprite —
//      and everything that isn't that size (credits, labels, stray specks) is
//      dropped. Re-running it (or picking a background by hand with B) starts the
//      separation over from the sheet, so nothing here is a dead end.
const AUTO_MIN_SIDE = 16;   // a sprite is at least this many px on each side
const AUTO_TOL = 1;         // px of jitter tolerated around the modal size

function cornerBackground() {
  if (!S.octx || !S.img) return null;
  const w = S.img.width, h = S.img.height;
  const px = [[0, 0], [w - 1, 0], [0, h - 1], [w - 1, h - 1]]
    .map(([x, y]) => S.octx.getImageData(x, y, 1, 1).data)
    .filter((p) => p[3] > 16);            // transparent corners → alpha keying, no pick
  if (!px.length) return null;
  const key = (p) => `${p[0]},${p[1]},${p[2]}`;
  const tally = new Map();
  for (const p of px) tally.set(key(p), (tally.get(key(p)) || 0) + 1);
  let best = key(px[0]), bestN = 0;        // most repeated corner, ties → top-left
  for (const [k, n] of tally) if (n > bestN) { best = k; bestN = n; }
  return best.split(",").map(Number);
}

// The size shared by the most boxes (within AUTO_TOL px), ignoring anything
// smaller than AUTO_MIN_SIDE. Ties go to the bigger box.
function modalSize(boxes) {
  const cands = boxes.filter((b) => b.w >= AUTO_MIN_SIDE && b.h >= AUTO_MIN_SIDE);
  if (!cands.length) return null;
  let best = null;
  for (const c of cands) {
    const n = cands.filter((b) => Math.abs(b.w - c.w) <= AUTO_TOL && Math.abs(b.h - c.h) <= AUTO_TOL).length;
    if (!best || n > best.n || (n === best.n && c.w * c.h > best.w * best.h)) best = { w: c.w, h: c.h, n };
  }
  return best;
}

async function autoDetect() {
  if (!S.img) return;
  const rgb = cornerBackground();
  S.picks = rgb ? [rgb] : [];
  renderBgChips();
  setStatus(rgb ? `corner background ${hex(rgb)} — separating…` : "transparent sheet — separating…");
  await redetect();
  if (!S.boxes.length) return;              // redetect already said why
  const size = modalSize(S.boxes);
  if (!size) {
    setStatus(`nothing is ${AUTO_MIN_SIDE}×${AUTO_MIN_SIDE}px or bigger — kept all ${S.boxes.length}`, true);
    return;
  }
  const before = S.boxes.length;
  S.boxes = S.boxes.filter((b) => Math.abs(b.w - size.w) <= AUTO_TOL && Math.abs(b.h - size.h) <= AUTO_TOL);
  S.selected = []; S.dirty = true;
  renderCount(); draw();
  const dropped = before - S.boxes.length;
  setStatus(`${S.boxes.length} sprites of ${size.w}×${size.h}px` + (dropped ? ` · dropped ${dropped} odd-sized` : ""));
}

async function redetect() {
  // Literal keying: remove ONLY the colours the operator picked (explicit_bg_only,
  // no HSV hue expansion, no canonical snapping, no auto-inferred border bg), then
  // box whatever remains. Every gap-bridging step is off (no merge/dilate/close)
  // and there's no min-size filter, so each sprite keeps its full coloured cell box
  // and the sheet is split purely by the general background colour(s).
  //   tol is the RGB radius around a picked colour that also counts as background.
  // The default (40) is meant for chroma-keyed rips and is wide enough to swallow a
  // NEIGHBOURING flat colour: on 48692 the outer teal #008080 and the per-sprite
  // cell blue #006888 are only 25 apart, so picking the teal also keyed the cells
  // away and every sprite came out as a ragged tight box instead of its 32×32 cell.
  // These rips are flat-palette, so a small radius is all the jitter allowance we
  // need, and it keeps "only the exact colour you pick is removed" honest.
  const params = {
    explicit_bg_only: true, use_hsv: false, tol: 8,
    merge_gap: 0, area_min: 0, auto_secondary_bg: false,
    morph_px: 0, dilate_px: 0, max_bg_colors: 8,
    extra_bg: S.picks,
  };
  const r = await fetch(`/api/sheets/${S.sheetId}/boxes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ params }) }).then((x) => x.json());
  S.detect = r; normalizeBg(S.detect.background);
  S.boxes = (r.boxes || []).map((b) => ({ id: b.id, x: b.x, y: b.y, w: b.w, h: b.h }));
  S.selected = []; S.dirty = true; bumpSeq();
  renderCount(); draw();
  if (S.boxes.length) setStatus(`separated ${S.boxes.length} sprites`);
  else setStatus("nothing separated — pick the colour between the sprites too", true);
}

// -------- panel readouts
// live count of the sprites currently boxed on the sheet, so it's obvious when one
// is still missing (or one too many is selected).
function renderCount() { $("#ed-count").textContent = S.boxes.length; }

// -------- save / delete
async function save(skip) {
  if (!S.sheetId) return false;
  const bg = S.detect.background;
  const r = await fetch(`/api/sheets/${S.sheetId}/selection`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ boxes: skip ? [] : S.boxes, background: bg, image: S.detect.image, skip: !!skip }),
  }).then((x) => x.json());
  if (r.error) { setStatus(r.error, true); return false; }
  S.dirty = false;
  await loadSheets();
  return true;
}
async function saveAndBack(skip) {
  if (!(await save(skip))) return;
  closeEditor();
  setStatus(skip ? "marked: no sprites" : "saved");
}

// ============================================================ wire up
$("#btn-back").onclick = () => closeEditor();
function toggleEyedrop() {
  S.eyedrop = !S.eyedrop; $("#btn-eyedrop").classList.toggle("armed", S.eyedrop);
  setStatus(S.eyedrop ? "click the background on the image" : "");
}
$("#btn-eyedrop").onclick = toggleEyedrop;
$("#btn-auto").onclick = autoDetect;
$("#btn-grow").onclick = growToCells;
document.querySelectorAll("#nudge button").forEach((btn) =>
  holdToRepeat(btn, () => nudge(btn.dataset.side, parseInt(btn.dataset.d, 10))));
$("#btn-save").onclick = () => saveAndBack(false);
$("#btn-delete").onclick = () => saveAndBack(true);
$("#zoom").oninput = (e) => { S.zoom = parseInt(e.target.value, 10); $("#zoom-val").textContent = S.zoom * 100 + "%"; draw(); };
$("#btn-fit").onclick = () => { fitZoom(); draw(); };
// mouse wheel zooms (anchored on the cursor); no vertical scroll-to-pan
$("#stage").addEventListener("wheel", (e) => {
  if (!S.img || $("#view-editor").hidden) return;
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const imgX = (e.clientX - rect.left) / S.zoom, imgY = (e.clientY - rect.top) / S.zoom;
  const nz = Math.max(1, Math.min(12, S.zoom + (e.deltaY < 0 ? 1 : -1)));
  if (nz === S.zoom) return;
  S.zoom = nz; $("#zoom").value = nz; $("#zoom-val").textContent = nz * 100 + "%";
  draw();
  const stage = $("#stage"), nrect = canvas.getBoundingClientRect();
  stage.scrollLeft += (nrect.left + imgX * nz) - e.clientX;
  stage.scrollTop += (nrect.top + imgY * nz) - e.clientY;
}, { passive: false });

document.addEventListener("keydown", (e) => {
  if (document.activeElement && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if ($("#view-editor").hidden) return;
  // Ctrl/⌘+A selects every box — the quick way into the size box after a grow.
  if ((e.ctrlKey || e.metaKey) && (e.key === "a" || e.key === "A")) {
    S.selected = S.boxes.map((b) => b.id); draw(); e.preventDefault(); return;
  }
  if (e.key === "Escape") { closeEditor(); }
  else if (e.key === "Delete" || e.key === "Backspace") { if (S.selected) { deleteSelected(); e.preventDefault(); } }
  else if (e.key === "a" || e.key === "A") { autoDetect(); }
  else if (e.key === "b" || e.key === "B") { toggleEyedrop(); }
  else if (e.key === "g" || e.key === "G") { growToCells(); }
  else if (e.key === "s" || e.key === "S") { saveAndBack(false); }
});

// deep-link support: /crop/<sid> opens that sheet's editor; back/forward move
// between gallery and editor via the History API.
function pathSid() { const m = location.pathname.match(/^\/crop\/([^/]+)\/?$/); return m ? decodeURIComponent(m[1]) : null; }
window.addEventListener("popstate", () => {
  const sid = pathSid();
  if (sid) { if (S.sheetId !== sid || $("#view-editor").hidden) openEditor(sid, false); }
  else if (!$("#view-editor").hidden) closeEditor(false);
});
loadSheets().then(() => { const sid = pathSid(); if (sid) openEditor(sid, false); });
