"use strict";
/* Animate view (/) — assemble the ALREADY-CROPPED sprites of a sheet into
 * hibitomo animation clips and bake the SpriteLayout asset the content-editor
 * consumes (the same shape PMDSpriteManager emits).
 *
 * Two screens, mirroring /crop:
 *   • GALLERY — a card grid of the sheets that have cropped sprites.
 *   • EDITOR  — left: walk/idle/sleep frame assembly + a live animated preview
 *               + Bake; right: a palette of the sheet's individual sprites
 *               (served transparent by /api/sheets/<id>/sprite/<box>).
 * Cropping/delimiting lives entirely in /crop; boxes here are read-only.
 *
 * Only WALK is directional, and it is directional in ISO: the four facings are
 * the compass diagonals SW / SE / NW / NE (see ISO_DIRS), because that is what
 * hibitomo's isometric apps ask the sheet for. Idle and sleep are single clips
 * (see FLAT_CLIPS).
 */

// The four facings the operator assembles, in slot order: the two front views
// first (SW, SE), then the two back ones (NW, NE). The spec /
// layout KEYS stay the schema's top-down names (`down_right`…) — firmware
// `DIR_KEYS`, @hibitomo/schema and PMDSpriteManager all key on them — but a step
// on the iso grid never reads as a screen cardinal, so the UI talks compass.
// The baker aliases the four cardinals the schema requires onto these same rows
// (right≡SE, up≡NE, left≡NW, down≡SW); nothing here authors them.
const ISO_DIRS = ["down_left", "down_right", "up_left", "up_right"];
const DIR_COLOR = {
  down_right: "#06b6d4", up_right: "#a855f7", up_left: "#f97316", down_left: "#14b8a6",
  sleep: "#4bd1c6",
};
const DIR_LABEL = { down_right: "SE", up_right: "NE", up_left: "NW", down_left: "SW" };
const DIR_NAME = {
  down_right: "south-east", up_right: "north-east",
  up_left: "north-west", down_left: "south-west",
};
const DIR_POSE = {
  down_right: "front-right", up_right: "back-right",
  up_left: "back-left", down_left: "front-left",
};
// Legacy specs (and anything authored before the view went iso-native) keep the
// top-down cardinals; fold each into the facing it is the top-down reading of.
const CARDINAL_TO_ISO = {
  right: "down_right", up: "up_right", left: "up_left", down: "down_left",
};
const dirLabel = (dir) => DIR_LABEL[dir] || dir;
const dirTitle = (dir) =>
  `${DIR_LABEL[dir] || dir} — ${DIR_NAME[dir] || ""} · ${DIR_POSE[dir] || ""} (spec key "${dir}")`;

// Clips with a single, NON-directional frame list instead of one per facing:
// the pet always faces the camera while standing still, and sleeping is one
// lying pose for every facing (firmware `sleep_col`/`sleep_row` is a flat list).
const FLAT_CLIPS = ["idle", "sleep"];
const isFlatClip = (clip) => FLAT_CLIPS.includes(clip);

// The gallery filter survives leaving the view (Animate ⇄ Crop are separate page
// loads), so you come back to the chip you were working through.
const FILTERS = ["all", "todo", "baked"];
const FILTER_KEY = "animate.filter";
function savedFilter() {
  try { const v = localStorage.getItem(FILTER_KEY); return FILTERS.includes(v) ? v : "all"; } catch { return "all"; }
}
function rememberFilter(f) { try { localStorage.setItem(FILTER_KEY, f); } catch { /* private mode */ } }

const state = {
  sheets: [], filter: savedFilter(), search: "",
  sheetId: null,
  boxes: [],              // read-only cropped sprites [{id,x,y,w,h}]
  bg: null,               // background payload from box cache
  spec: null,
  activeClip: "walk", activeDir: ISO_DIRS[0],
  sprites: {},            // boxId -> {img, w, h, loaded}
  maxW: 1, maxH: 1,
  pvDir: ISO_DIRS[0], pvPlaying: true, pvToken: 0,
};

const $ = (s) => document.querySelector(s);

// The editor REPLACES the gallery, so the appbar's #status is off screen while
// you are assembling (the same trap /crop hit) — every message also goes to the
// editor's own foot line, or Save/Bake look like they did nothing.
function setStatus(msg, isErr) {
  for (const sel of ["#status", "#ed-status"]) {
    const el = $(sel);
    if (!el) continue;
    el.textContent = msg || "";
    el.classList.toggle("err", !!isErr);
  }
}
function activeDirs() { return ISO_DIRS; }
function toRgb(c) {
  if (Array.isArray(c)) return c.slice(0, 3).map((v) => v | 0);
  if (typeof c === "string") { const s = c.replace(/0x|#/gi, ""); if (s.length === 6) return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16)); }
  return null;
}
function normalizeBg(bg) {
  if (!bg) return bg;
  if (!Array.isArray(bg.colors) || !bg.colors.length) { const c = bg.rgb || bg.color; bg.colors = c ? [toRgb(c)].filter(Boolean) : []; }
  else bg.colors = bg.colors.map(toRgb).filter(Boolean);
  return bg;
}
function spriteURL(sid, boxId) { return `/api/sheets/${sid}/sprite/${encodeURIComponent(boxId)}`; }

// ======================================================================
// GALLERY
// ======================================================================
async function loadSheets() {
  const r = await fetch("/api/sheets").then((x) => x.json());
  // Only sheets with cropped sprites belong here (cropping happens in /crop).
  state.sheets = (r.sheets || []).filter((s) => s.state === "done");
  renderGallery();
}
function galleryCounts() {
  const all = state.sheets.length;
  const baked = state.sheets.filter((s) => s.baked).length;
  return { all, baked, todo: all - baked };
}
function syncChips() {
  $("#chips").querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c.dataset.filter === state.filter));
}
function renderGallery() {
  syncChips();
  const c = galleryCounts();
  $("#count-all").textContent = c.all;
  $("#count-todo").textContent = c.todo;
  $("#count-baked").textContent = c.baked;
  const pct = c.all ? Math.round((c.baked / c.all) * 100) : 0;
  $("#progress-bar").style.width = pct + "%";
  $("#progress-count").innerHTML = `<b>${c.baked}</b> / ${c.all} baked`;

  const q = state.search.trim();
  const shown = state.sheets.filter((s) => {
    if (q && !s.id.includes(q)) return false;
    if (state.filter === "baked") return s.baked;
    if (state.filter === "todo") return !s.baked;
    return true;
  });
  const grid = $("#grid");
  const empty = $("#empty");
  grid.innerHTML = "";
  if (!state.sheets.length) {
    empty.hidden = false;
    empty.innerHTML = `No cropped sheets yet.<br>Delimit sprites first in <a class="qbtn go" href="/crop" style="display:inline-flex">✂ Crop sheets</a>`;
    return;
  }
  if (!shown.length) { empty.hidden = false; empty.textContent = "No sheets match this filter."; return; }
  empty.hidden = true;
  for (const s of shown) grid.appendChild(cardFor(s));
}
function cardFor(s) {
  const badge = s.baked
    ? '<span class="badge done"><span class="d" style="background:var(--done)"></span>baked</span>'
    : (s.has_spec
      ? '<span class="badge accent"><span class="d" style="background:var(--accent)"></span>draft</span>'
      : '<span class="badge todo"><span class="d" style="background:var(--todo)"></span>ready</span>');
  const tile = document.createElement("div");
  tile.className = "tile";
  tile.innerHTML =
    `<span class="corner-badge">${badge}</span>` +
    `<div class="thumb"><img loading="lazy" src="/api/sheets/${s.id}/thumb?max=240" alt="${s.id}"/></div>` +
    `<div class="meta"><span class="sid">${s.id}</span><span class="mcount">${s.count} sprites</span></div>` +
    `<div class="quick">` +
      `<span class="qbtn go" data-act="open">▶ Animate</span>` +
      `<a class="qbtn" href="/crop/${s.id}" data-act="crop">✂ crop</a>` +
    `</div>`;
  tile.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]");
    if (act && act.dataset.act === "crop") return; // let the link navigate
    openEditor(s.id);
  });
  return tile;
}

// ======================================================================
// EDITOR
// ======================================================================
async function openEditor(sid, push = true) {
  state.sheetId = sid;
  if (push) history.pushState({ view: "editor", sid }, "", "/animate/" + encodeURIComponent(sid));
  $("#view-gallery").hidden = true;
  $("#view-editor").hidden = false;
  setStatus("loading…");
  $("#ed-path").textContent = sid;
  $("#ed-crumb").textContent = "loading…";
  $("#ed-crop-link").href = "/crop/" + encodeURIComponent(sid);
  $("#palette").innerHTML = "";
  $("#bake-result").innerHTML = "";

  const data = await fetch(`/api/sheets/${sid}/boxes`).then((x) => x.json());
  state.boxes = (data.boxes || []).map((b) => ({ id: b.id, x: b.x, y: b.y, w: b.w, h: b.h }));
  state.bg = normalizeBg(data.background) || { colors: [] };

  const info = state.sheets.find((s) => s.id === sid);
  state.spec = migrateSpec((info && info.has_spec ? await tryLoadSpecForSheet(sid) : null) || freshSpec(sid, data));
  pruneClips();
  state.activeClip = "walk";
  if (!activeDirs().includes(state.activeDir)) state.activeDir = ISO_DIRS[0];
  state.pvDir = state.activeDir;

  reflectSpecToUI();
  preloadSprites();
  renderPalette();
  renderSlots();
  refreshAssignHint();
  buildPvDir();
  restartPreview();

  $("#ed-count").textContent = state.boxes.length;
  $("#ed-crumb").textContent = `${state.boxes.length} sprites`;
  if (!state.boxes.length) setStatus("no cropped sprites — crop this sheet first (✂ crop)", true);
  else if (!data.edited) setStatus("not cropped yet — showing a rough auto-guess; crop it for clean sprites", true);
  else setStatus("ready");
}
function closeEditor(push = true) {
  stopPreview();
  if (push) history.pushState({ view: "gallery" }, "", "/");
  $("#view-editor").hidden = true;
  $("#view-gallery").hidden = false;
  loadSheets();
}

async function tryLoadSpecForSheet(sid) {
  const list = await fetch("/api/specs").then((x) => x.json());
  for (const nnn of list.specs) {
    const s = await fetch(`/api/specs/${nnn}`).then((x) => x.json());
    if (String(s.sheet_id) === String(sid)) return s;
  }
  return null;
}
function freshSpec(sid, data) {
  const bg = normalizeBg(data.background) || { colors: [] };
  return {
    spec_version: 1, species: "digimon", id: parseInt($("#c-id").value || "1", 10),
    source: `raw_sheets/${sid}.png`, sheet_id: sid,
    background: bg.color, background_tolerance: bg.tolerance, background_mode: bg.mode,
    seg_params: { ...(data.params || {}), extra_bg: (bg.colors || []).slice(1) },
    trim: true, export_scale: 1, pad: 1,
    boxes: [], clips: { walk: {}, idle: [], sleep: [] }, mirror: {},
    // `diagonals` = the sheet declares the 8-direction shape. Always true here:
    // the iso facings ARE the diagonal keys, and the baker aliases the cardinals.
    diagonals: true, anchor: "bottom_center", walk_style: "stride", tick_ms: 33,
    walk_durations: [6], idle_frame_ms: 300, sleep_frame_ms: 400, notes: "",
  };
}

/** Bring a spec saved by an older build to the current shape:
 *  • idle is one non-directional clip (it used to be per-direction), taken from
 *    the front facing — the pose the pet holds while standing still;
 *  • walk is authored in ISO facings, so a spec that filled the top-down
 *    cardinals moves each into the facing it is the top-down reading of.
 *  The baker does the same for a legacy spec baked from the CLI. */
function migrateSpec(spec) {
  const c = spec.clips || (spec.clips = {});
  if (c.idle && !Array.isArray(c.idle)) {
    const per = c.idle;
    c.idle = (per.down || ISO_DIRS.map((d) => per[d]).find((v) => v && v.length) || []).slice();
  }
  if (!Array.isArray(c.idle)) c.idle = [];
  if (!Array.isArray(c.sleep)) c.sleep = [];
  if (!c.walk || Array.isArray(c.walk)) c.walk = {};
  for (const [card, iso] of Object.entries(CARDINAL_TO_ISO)) {
    if ((c.walk[card] || []).length && !(c.walk[iso] || []).length) c.walk[iso] = c.walk[card];
    delete c.walk[card];
  }
  // Mirror pairs are between iso twins (SE⇄SW, NE⇄NW); a cardinal-era entry has
  // no meaning here and is cheaper to re-tick than to guess at.
  const mir = spec.mirror || (spec.mirror = {});
  for (const dir of Object.keys(mir)) if (!ISO_DIRS.includes(dir)) delete mir[dir];
  spec.diagonals = true;
  return spec;
}

// ---- sprite image cache (for preview canvas) ----
function preloadSprites() {
  state.sprites = {};
  state.maxW = state.maxH = 1;
  for (const b of state.boxes) {
    const rec = { img: new Image(), w: 0, h: 0, loaded: false };
    rec.img.onload = () => {
      rec.loaded = true; rec.w = rec.img.naturalWidth; rec.h = rec.img.naturalHeight;
      if (rec.w > state.maxW) state.maxW = rec.w;
      if (rec.h > state.maxH) state.maxH = rec.h;
      drawPreviewFrameSoon();
    };
    rec.img.src = spriteURL(state.sheetId, b.id);
    state.sprites[b.id] = rec;
  }
}

// ---- palette (individual sprites) ----
function renderPalette() {
  const host = $("#palette");
  host.innerHTML = "";
  for (const b of state.boxes) {
    const a = boxAssignment(b.id, state.activeClip);
    const tile = document.createElement("div");
    tile.className = "pal-tile" + (a ? " assigned" : "");
    if (a) {
      const flat = isFlatClip(state.activeClip);
      const col = flat ? DIR_COLOR.sleep : (DIR_COLOR[a.dir] || "#fff");
      tile.style.borderColor = col;
      // Compass tags (S0, SE2…) so the 8 iso facings never collide — the old
      // first-letter tag read "D" for both down and down_right.
      const tag = flat ? `${state.activeClip[0].toUpperCase()}${a.idx}` : `${dirLabel(a.dir)}${a.idx}`;
      tile.innerHTML = `<span class="pid">${b.id}</span>` +
        `<span class="pal-tag" style="background:${col}">${tag}</span>` +
        `<img src="${spriteURL(state.sheetId, b.id)}" alt="${b.id}"/>`;
    } else {
      tile.innerHTML = `<span class="pid">${b.id}</span><img src="${spriteURL(state.sheetId, b.id)}" alt="${b.id}"/>`;
    }
    tile.addEventListener("click", (e) => {
      if (e.shiftKey) removeFrame(b.id); else appendFrame(b.id);
      afterAssignChange();
    });
    host.appendChild(tile);
  }
}
function refreshAssignHint() {
  const flat = isFlatClip(state.activeClip);
  const to = flat ? state.activeClip : `${state.activeClip} · ${dirLabel(state.activeDir)}`;
  $("#assign-to").textContent = to;
  $("#btn-clear-active").textContent = `clear ${to}`;
}
function afterAssignChange() {
  renderPalette(); renderSlots(); restartPreview();
}

// ---- clip frame assembly ----
function boxAssignment(boxId, clip) {
  if (!state.spec) return null;
  if (isFlatClip(clip)) { const i = (state.spec.clips[clip] || []).indexOf(boxId); return i >= 0 ? { dir: clip, idx: i } : null; }
  const c = state.spec.clips[clip] || {};
  for (const dir of activeDirs()) { const i = (c[dir] || []).indexOf(boxId); if (i >= 0) return { dir, idx: i }; }
  return null;
}
function appendFrame(boxId) {
  const clip = state.activeClip;
  if (isFlatClip(clip)) { state.spec.clips[clip].push(boxId); return; }
  const c = state.spec.clips[clip]; if (!c[state.activeDir]) c[state.activeDir] = [];
  c[state.activeDir].push(boxId);
}
function removeFrame(boxId) {
  const clip = state.activeClip;
  const lst = isFlatClip(clip) ? state.spec.clips[clip] : (state.spec.clips[clip][state.activeDir] || []);
  const i = lst.indexOf(boxId); if (i >= 0) lst.splice(i, 1);
}
function renderSlots() {
  const host = $("#slot-rows"); host.innerHTML = "";
  if (!state.spec) return;
  const clip = state.activeClip;
  if (isFlatClip(clip)) { host.appendChild(slotRow(clip, state.spec.clips[clip], false)); return; }
  for (const dir of activeDirs()) host.appendChild(slotRow(dir, state.spec.clips[clip][dir] || [], true));
}
// `dir` is a facing key for walk, or the clip name itself for the flat clips.
const FLAT_ROW_LABEL = { idle: ["idle", "front (SW)"], sleep: ["sleep", "any facing"] };
function slotRow(dir, frames, showMirror) {
  const flat = isFlatClip(state.activeClip);
  const row = document.createElement("div"); row.className = "slot-row";
  const dirBox = document.createElement("div");
  dirBox.className = "dir" + (!flat && dir === state.activeDir ? " active" : "");
  const b = document.createElement("button");
  if (flat) {
    const [main, sub] = FLAT_ROW_LABEL[dir] || [dir, ""];
    b.innerHTML = `<b>${main}</b><i>${sub}</i>`;
  } else {
    b.innerHTML = `<b>${dirLabel(dir)}</b><i>${DIR_NAME[dir] || ""}</i>`;
    b.title = dirTitle(dir);
  }
  b.style.borderLeftColor = flat ? DIR_COLOR.sleep : (DIR_COLOR[dir] || "#888");
  b.onclick = () => { if (!flat) { state.activeDir = dir; state.pvDir = dir; } renderSlots(); renderPalette(); refreshAssignHint(); syncPvDir(); restartPreview(); };
  dirBox.appendChild(b); row.appendChild(dirBox);

  const fr = document.createElement("div"); fr.className = "frames";
  const mir = state.spec.mirror || {};
  const mirrored = showMirror && state.activeClip === "walk" && mir[dir];
  if (mirrored) {
    fr.innerHTML = `<span class="empty-hint">↤ mirrored from ${dirLabel(mir[dir])}</span>`;
  } else if (!frames.length) {
    fr.innerHTML = `<span class="empty-hint">${state.activeClip === "idle"
      ? "click sprites to add frames — empty bakes the SW walk[0] pose"
      : "click sprites to add frames"}</span>`;
  } else {
    frames.forEach((id, i) => {
      const chip = document.createElement("span"); chip.className = "frame-chip"; chip.draggable = true;
      chip.title = `frame ${i} (${id}) — click to remove, drag to reorder`;
      chip.innerHTML = `<img src="${spriteURL(state.sheetId, id)}" alt="${id}"/><span class="n">${i}</span>`;
      // The whole chip is the remove target (drag still reorders) — no hunting
      // for a tiny × hitbox.
      chip.onclick = () => { frames.splice(i, 1); afterAssignChange(); };
      chip.ondragstart = (e) => e.dataTransfer.setData("idx", i);
      chip.ondragover = (e) => e.preventDefault();
      chip.ondrop = (e) => { e.preventDefault(); const from = +e.dataTransfer.getData("idx"); const [m] = frames.splice(from, 1); frames.splice(i, 0, m); afterAssignChange(); };
      fr.appendChild(chip);
    });
  }
  row.appendChild(fr);

  const src = showMirror && state.activeClip === "walk" ? mirrorSourceFor(dir) : null;
  if (src) {
    const lab = document.createElement("label"); lab.className = "mirror";
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = !!mir[dir]; cb.title = `mirror from ${dirTitle(src)}`;
    cb.onchange = () => { if (cb.checked) state.spec.mirror[dir] = src; else delete state.spec.mirror[dir]; afterAssignChange(); };
    lab.appendChild(cb); lab.appendChild(document.createTextNode(`⇄${dirLabel(src)}`)); row.appendChild(lab);
  }
  return row;
}
/** The facing `dir` can be baked from by flipping it horizontally. Every iso
 *  facing has a twin (SE⇄SW front pair, NE⇄NW back pair), which is why a DS
 *  sheet shipping only one side still fills all four. */
function mirrorSourceFor(dir) {
  return { down_right: "down_left", down_left: "down_right",
    up_right: "up_left", up_left: "up_right" }[dir] || null;
}
function pruneClips() {
  if (!state.spec) return;
  const ids = new Set(state.boxes.map((b) => b.id));
  const walk = state.spec.clips.walk || {};
  for (const dir of Object.keys(walk)) walk[dir] = (walk[dir] || []).filter((id) => ids.has(id));
  for (const clip of FLAT_CLIPS)
    state.spec.clips[clip] = (state.spec.clips[clip] || []).filter((id) => ids.has(id));
}

// ---- live preview ----
/** Frames actually baked for a (clip, dir). `dir` is ignored for the flat clips. */
function resolveFrames(clip, dir) {
  const mir = state.spec.mirror || {};
  if (clip === "sleep") return { ids: (state.spec.clips.sleep || []).slice(), flip: false };
  if (clip === "idle") {
    const ids = (state.spec.clips.idle || []).slice();
    if (ids.length) return { ids, flip: false };
    return frontWalkFrame();          // bake defaults idle -> the front walk pose
  }
  if (mir[dir]) return { ids: (state.spec.clips.walk[mir[dir]] || []).slice(), flip: true };
  return { ids: (state.spec.clips.walk[dir] || []).slice(), flip: false };
}
/** The single front-facing walk frame the baker falls back to for an empty idle:
 *  SW, else SE, honoring a mirror on whichever it lands on. */
function frontWalkFrame() {
  const mir = state.spec.mirror || {};
  for (const dir of ["down_left", "down_right"]) {
    const w = state.spec.clips.walk[mir[dir] || dir] || [];
    if (w.length) return { ids: [w[0]], flip: !!mir[dir] };
  }
  return { ids: [], flip: false };
}
function frameMs(clip, i) {
  const s = state.spec;
  if (clip === "walk") { const d = (s.walk_durations || [])[i] ?? (s.walk_durations || []).slice(-1)[0] ?? 6; return Math.max(30, (s.tick_ms || 33) * d); }
  if (clip === "idle") return Math.max(60, s.idle_frame_ms || 300);
  return Math.max(60, s.sleep_frame_ms || 400);
}
function fitPreview() {
  // Zoom to whatever the stage box actually is (it changes with the panel size),
  // leaving a small margin so the sprite never touches the border.
  const stage = $(".pv-stage");
  const budgetW = Math.max(60, (stage ? stage.clientWidth : 150) - 20);
  const budgetH = Math.max(60, (stage ? stage.clientHeight : 150) - 20);
  const z = Math.max(1, Math.min(8, Math.floor(Math.min(budgetW / state.maxW, budgetH / state.maxH))));
  const cv = $("#pv-canvas");
  cv.width = state.maxW * z; cv.height = state.maxH * z;
  cv._z = z;
}
let _pvRedrawPending = false;
function drawPreviewFrameSoon() { if (_pvRedrawPending) return; _pvRedrawPending = true; requestAnimationFrame(() => { _pvRedrawPending = false; if (state._pvFrame) state._pvFrame(); }); }
function stopPreview() { state.pvToken++; }
function restartPreview() {
  stopPreview();
  const clip = state.activeClip;
  const flat = isFlatClip(clip);
  const dir = flat ? clip : state.pvDir;
  const { ids, flip } = resolveFrames(clip, dir);
  const cv = $("#pv-canvas");
  const ctx = cv.getContext("2d");
  const emptyEl = $("#pv-empty");
  $("#pv-info").textContent = `${clip}${flat ? "" : " · " + dirLabel(dir)} · ${ids.length}f`;
  fitPreview();
  const z = cv._z || 1;

  const drawOne = (idx) => {
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (!ids.length) { emptyEl.hidden = false; return; }
    emptyEl.hidden = true;
    const rec = state.sprites[ids[idx % ids.length]];
    if (!rec || !rec.loaded) return;
    const dw = rec.w * z, dh = rec.h * z;
    const ox = Math.floor((cv.width - dw) / 2), oy = cv.height - dh; // bottom-center
    ctx.imageSmoothingEnabled = false;
    if (flip) { ctx.save(); ctx.translate(cv.width, 0); ctx.scale(-1, 1); ctx.drawImage(rec.img, cv.width - ox - dw, oy, dw, dh); ctx.restore(); }
    else ctx.drawImage(rec.img, ox, oy, dw, dh);
  };
  // expose a redraw for late-loading images (paused or single frame)
  let i = 0;
  state._pvFrame = () => drawOne(i);
  drawOne(0);
  if (!ids.length || !state.pvPlaying || ids.length === 1) return;

  const token = state.pvToken;
  const step = () => {
    if (token !== state.pvToken) return;
    i = (i + 1) % ids.length;
    drawOne(i);
    setTimeout(step, frameMs(clip, i));
  };
  setTimeout(step, frameMs(clip, 0));
}
function buildPvDir() {
  const sel = $("#pv-dir");
  sel.innerHTML = "";
  for (const dir of activeDirs()) {
    const o = document.createElement("option");
    o.value = dir; o.textContent = `${dirLabel(dir)} · ${DIR_NAME[dir] || ""}`;
    sel.appendChild(o);
  }
  sel.value = activeDirs().includes(state.pvDir) ? state.pvDir : ISO_DIRS[0];
  syncPvDir();
}
function syncPvDir() {
  const sel = $("#pv-dir");
  // idle + sleep are non-directional: grey the picker out and blank it rather
  // than leaving a facing showing that the clip does not have.
  const flat = isFlatClip(state.activeClip);
  sel.disabled = flat;
  if (flat) sel.selectedIndex = -1;
  else if (activeDirs().includes(state.pvDir)) sel.value = state.pvDir;
}

// ---- reflect / pull spec <-> UI ----
function reflectSpecToUI() {
  const s = state.spec;
  $("#t-tick").value = s.tick_ms; $("#t-style").value = s.walk_style;
  $("#t-walkdur").value = (s.walk_durations || []).join(",");
  $("#t-idlems").value = s.idle_frame_ms || ""; $("#t-sleepms").value = s.sleep_frame_ms || "";
  $("#c-id").value = s.id; $("#c-name").value = s.creature_name || "";
  if (s.creature_type) $("#c-type").value = s.creature_type;
  if (s.creature_color) $("#c-color").value = s.creature_color;
  if (s.creature_stage) $("#c-stage").value = s.creature_stage;
  // reset clip tabs to walk
  $("#clip-tabs").querySelectorAll("button").forEach((btn) => btn.classList.toggle("active", btn.dataset.clip === "walk"));
}
function pullUIToSpec() {
  const s = state.spec;
  s.diagonals = true;   // iso facings ARE the diagonal keys (see ISO_DIRS)
  s.tick_ms = parseInt($("#t-tick").value || "33", 10);
  s.walk_style = $("#t-style").value;
  s.walk_durations = ($("#t-walkdur").value || "").split(",").map((x) => parseInt(x.trim(), 10)).filter((x) => x > 0);
  s.idle_frame_ms = parseInt($("#t-idlems").value || "0", 10) || undefined;
  s.sleep_frame_ms = parseInt($("#t-sleepms").value || "0", 10) || undefined;
  s.id = parseInt($("#c-id").value || "1", 10);
  s.creature_name = $("#c-name").value;
  s.creature_type = $("#c-type").value;
  s.creature_color = $("#c-color").value;
  s.creature_stage = parseInt($("#c-stage").value || "1", 10);
  // boxes + bg come from the saved crop selection
  s.boxes = state.boxes.map((b) => ({ id: b.id, x: b.x, y: b.y, w: b.w, h: b.h }));
  const bg = state.bg || { colors: [] };
  s.background = bg.color; s.background_mode = bg.mode;
  // `enclosed` rides along so the bake keys the backdrop the palette keyed —
  // including the pixels a sprite walls in (see extractor `bg_enclosed`).
  s.seg_params = { ...(s.seg_params || {}), tol: bg.tolerance, extra_bg: (bg.colors || []).slice(1),
    ...(bg.enclosed === undefined ? {} : { bg_enclosed: bg.enclosed }) };
}
function creaturePayload() {
  return { id: parseInt($("#c-id").value || "1", 10), name: $("#c-name").value || `Digimon ${$("#c-id").value}`,
    type: $("#c-type").value || "Data", color: $("#c-color").value || "0x8899AA",
    stage: parseInt($("#c-stage").value || "1", 10), evolutions: [] };
}

// ---- save / bake ----
/** POST/PUT JSON and always come back with an object — a crashed request or a
 *  non-JSON error page must still surface as a message, not as a silent no-op. */
async function sendJSON(url, method, body) {
  try {
    const r = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok && !j.error) j.error = `HTTP ${r.status}`;
    return j;
  } catch (e) {
    return { error: String(e && e.message || e) };
  }
}
async function saveSpec() {
  if (!state.spec) { setStatus("pick a sheet first", true); return false; }
  pullUIToSpec(); pruneClips();
  const nnn = String(state.spec.id).padStart(3, "0");
  setStatus("saving…");
  const j = await sendJSON(`/api/specs/${nnn}`, "PUT", state.spec);
  if (j.error) { setStatus("save: " + j.error, true); return false; }
  setStatus("saved " + j.path);
  return true;
}
async function bake() {
  if (!(await saveSpec())) return;
  const nnn = String(state.spec.id).padStart(3, "0");
  setStatus("baking…");
  const j = await sendJSON(`/api/specs/${nnn}/bake`, "POST",
    { write_creature: true, creature: creaturePayload(), stage_to_content: $("#stage-toggle").checked });
  if (j.error) { setStatus("bake: " + j.error, true); return; }
  setStatus(`baked ${j.cols}×${j.rows}` + (j.staged ? " (staged)" : ""));
  $("#bake-result").innerHTML =
    `<div class="cap">${j.png} — ${j.cols}×${j.rows} cells${j.staged ? " · staged to content-editor" : ""}</div>` +
    `<div class="sheet"><img src="/${j.png}?t=${Date.now()}" style="width:${j.cols * (j.cell_w || 32) * 2}px"/></div>`;
}

// ======================================================================
// EVENTS
// ======================================================================
// gallery
$("#search").oninput = (e) => { state.search = e.target.value; renderGallery(); };
$("#chips").querySelectorAll(".chip").forEach((chip) => {
  chip.onclick = () => {
    state.filter = chip.dataset.filter;
    rememberFilter(state.filter);
    renderGallery();
  };
});
$("#btn-download").onclick = async () => { await fetch("/api/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); pollDownload(); };

// editor
$("#btn-back").onclick = closeEditor;
$("#clip-tabs").querySelectorAll("button").forEach((btn) => {
  btn.onclick = () => {
    $("#clip-tabs").querySelectorAll("button").forEach((b) => b.classList.remove("active")); btn.classList.add("active");
    state.activeClip = btn.dataset.clip;
    if (!isFlatClip(state.activeClip) && !activeDirs().includes(state.activeDir)) state.activeDir = ISO_DIRS[0];
    syncPvDir(); renderSlots(); renderPalette(); refreshAssignHint(); restartPreview();
  };
});
$("#btn-clear-active").onclick = () => {
  if (!state.spec) return;
  const clip = state.activeClip;
  if (isFlatClip(clip)) state.spec.clips[clip] = []; else state.spec.clips[clip][state.activeDir] = [];
  afterAssignChange();
};
$("#pal-zoom").oninput = (e) => { document.documentElement.style.setProperty("--pal-cell", e.target.value + "px"); };
$("#pv-dir").onchange = (e) => { state.pvDir = e.target.value; restartPreview(); };
$("#pv-toggle").onclick = () => {
  state.pvPlaying = !state.pvPlaying;
  $("#pv-toggle").textContent = state.pvPlaying ? "❚❚ pause" : "► play";
  restartPreview();
};
$("#btn-bake").onclick = bake;
// live timing → preview
["t-tick", "t-walkdur", "t-idlems", "t-sleepms"].forEach((id) => { $("#" + id).oninput = () => { pullUIToSpec(); restartPreview(); }; });

document.addEventListener("keydown", (e) => {
  if ($("#view-editor").hidden) return;
  // Ctrl/⌘+S fires from inside a field too, only to stop the browser offering to
  // save the page; plain S is the shortcut (same as /crop) and waits until you
  // are out of the creature/timing inputs, where "s" is just a letter.
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); bake(); return; }
  if (document.activeElement && /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if (e.key === "Escape") { closeEditor(); return; }
  if (e.key === "s" || e.key === "S") { bake(); return; }
  if (state.spec && e.key >= "1" && e.key <= "4" && !isFlatClip(state.activeClip)) {
    const idx = +e.key - 1, dirs = activeDirs();
    if (idx < dirs.length) { state.activeDir = dirs[idx]; state.pvDir = dirs[idx]; syncPvDir(); renderSlots(); renderPalette(); refreshAssignHint(); restartPreview(); }
  }
});

async function pollDownload() {
  const tick = async () => {
    const s = await fetch("/api/download/status").then((x) => x.json());
    $("#dl-status").textContent = s.running ? `downloading ${s.done}/${s.total}…` : (s.total ? `done ${s.done} (${s.fail} failed)` : "");
    if (s.running) setTimeout(tick, 1000); else loadSheets();
  };
  tick();
}

// deep-link support: /animate/<sid> opens that sheet's editor; back/forward move
// between gallery and editor via the History API.
function pathSid() { const m = location.pathname.match(/^\/animate\/([^/]+)\/?$/); return m ? decodeURIComponent(m[1]) : null; }
window.addEventListener("popstate", () => {
  const sid = pathSid();
  if (sid) { if (state.sheetId !== sid || $("#view-editor").hidden) openEditor(sid, false); }
  else if (!$("#view-editor").hidden) closeEditor(false);
});
loadSheets().then(() => { const sid = pathSid(); if (sid) openEditor(sid, false); });
