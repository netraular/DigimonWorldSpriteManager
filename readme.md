# digimonSpriteManager

A local tool to turn **Digimon World DS** sprite-sheet rips into clean,
**hibitomo**-ready pet assets. It downloads the sheets, auto-segments each one
into candidate sprites (handling the varied green / teal / magenta backgrounds),
gives you a web UI to assign the sprites you want to isometric-view animation
slots, and bakes a unified sprite sheet + the explicit `SpriteLayout` JSON that
the [hibitomo content-editor](../hibitomo-content-editor) consumes.

Sibling of [PMDSpriteManager](../PMDSpriteManager) (which does the same job for
Pokémon Mystery Dungeon sprites) and follows the same layout: a thin `run.py`,
GUI/HTTP-agnostic logic under `src/core/`, and argparse CLIs under `Scripts/`.

> **Asset copyright.** The Digimon World DS sprites are © Bandai/Namco, ripped
> by the community on [The Spriters Resource](https://www.spriters-resource.com/ds_dsi/dgmnworldds/).
> This tool downloads them **into your local `raw_sheets/` only** (which is
> git-ignored) for personal use. The rips are never committed to this repo; only
> your editable extraction specs are.

## Install

Needs Python 3 with Flask, Pillow, numpy, scipy:

```bash
pip install --user -r requirements.txt   # add --break-system-packages on PEP-668 distros
```

## Run

```bash
python3 run.py            # → http://127.0.0.1:5001
```

The work is split across **two views**, one per step, that link to each other
from the top bar:

- **`/crop`** — *delimit* the sprites of every sheet (the pixel-editor).
- **`/`** (default) — *animate* the already-cropped sprites and bake.

First, **Download sheets** (top bar of the animate view) fetches all ~320 sheets
into `raw_sheets/` (or run `python3 Scripts/download_sheets.py`).

### Step 1 · `/crop` — delimit the sprites

For delimiting sprites across the **whole** collection, open **`/crop`**. It's a
standalone "pixel-editor"-style view in two screens:

- **Gallery** — a visual grid of sheet **thumbnails** (not a list). Filter chips
  **All / To-do / Done / Deleted** (with live counts) narrow it down,
  and a progress bar tracks how many are reviewed. Hovering a tile reveals quick
  actions: **Open**, and **Delete** (or **Restore** for a deleted one). "Deleting" is
  a soft state for sheets with no usable sprites (maps, title cards, credits) — it
  never touches the file, so you can restore it any time.
- **Editor** — click a tile to open it. A fresh sheet opens **empty**: nothing is
  selected until you choose a background. **✦ Auto-detect sprites** (`A`) does the
  usual sheet in one click: it eyedrops the sheet's **corner colour** as the
  background (rips key the whole sheet with one flat colour), separates as below,
  then keeps only the **most repeated box size** that is at least **16×16 px**
  (±1 px of jitter) — since every sprite sits in an identically-sized cell, the modal
  size *is* the sprite — and drops everything else (artwork panels, credit text,
  stray specks). It reports what it kept and dropped, and re-running it starts over
  from the sheet if it trimmed too much. For the rest, the manual action is **Pick
  background** (`B`) — click the colour *between* the sprites and everything left separated
  becomes a candidate sprite. Only the **exact colour(s) you pick** are keyed out —
  no auto-inferred background, no hue expansion, no snapping — so each sprite keeps
  its full coloured "cell"/box instead of being cropped tight. Sprites almost always
  sit in cells a couple of pixels apart, so detection **never merges** them and
  **never drops small ones** — each cell falls apart into its own sprite. If a shade
  is still stuck to a sprite (or the sheet stays in one piece), pick that colour too.
  On a sheet that uses **transparency**, alpha is background whatever you pick: picks are
  keyed *on top* of it, never instead of it (dropping alpha to key a colour left every
  anti-aliased fringe pixel standing as foreground, and those faint pixels bridged sprites
  the eye sees clearly apart), and clicking a transparent pixel adds no colour at all — it
  has none to add, and keying its black would eat every outline on the sheet. There is **one unified
  tool** for touch-ups: **drag over empty space to select** every sprite the rectangle
  touches (Shift-drag adds to the selection) and `Del` deletes them all at once — handy
  for wiping out credit/text boxes or stray regions in bulk; click a box to select it,
  drag its body to **move**, drag a handle to **resize**, and **`Alt`+drag** draws a new
  box (no modes, no grid).

  **⤢ Grow to the sprite cell** (`G`) recovers the sheet's **cell**. With nothing selected it
  runs over *every box on the sheet*; **select the frames and it grows just those**, stepping
  around everything else — which is what a sheet straight out of Pick background needs, since a
  cell is one size for every frame and it can only be read off boxes that *are* frames, not off
  the credit text, the logo and the big portrait boxed in with them. A box hugs the sprite's pixels, so a frame caught
  mid-jump or mid-step loses the empty room it moves into — that room *is* the jump — and every
  frame comes out on a different origin, which makes the animation jitter. The cell is read
  from the boxes themselves: sprites whose intervals overlap form a column (or a row), which is a
  *hint*, never the answer — two tight crops that lean into each other and touch by a px would
  chain three real columns into one. So the cell's pitch is **estimated and then searched**. The
  estimate is the roomier of the median **step** from one line to the next and the widest box
  plus **half the median gap** between lines, both capped at twice the content they hold; taking
  the *median* is what keeps it honest, since the one big jump across a sheet (the space between
  two blocks of sprites, or a wide outer margin) can't set the cell size and blow every box up.
  A pitch is then only accepted when **every box lands entirely inside one cell** and **no two
  sprites share a cell** — which is exactly what a chained line can't do, so it fails and falls
  back instead of dumping three sprites into one giant box. Gaps typically wider than the
  sprites themselves mean the sheet isn't packed into cells at all (two lone sprites at opposite
  ends, say), and that axis just holds its content. A cell also has to hold its whole **column**,
  jitter and all: a row of frames bobbing by a pixel needs 33, not the 32 of its tallest box.
  The sheet is then re-laid as a **regular grid** of that one cell: every box in a column gets
  the same x and width, every box in a row the same y and height, so each sprite keeps its
  offset **inside** its cell — the bob of a walk cycle survives exactly as drawn. Boxes end up
  edge to edge: touching, never overlapping.

  Plenty of rips are laid out in **blocks** — three frames at a 37px pitch, a wider jump, three
  more — so no single arithmetic grid covers them even though every frame plainly came off the
  same cell. Those keep the one thing the animation actually needs, a single cell **size**, with
  each column and row sitting where its own sprites are rather than on a pitch. Columns are cut
  by where sprites *start* (half a cell apart), never by whether they touch, so neighbours can't
  be served one shared cell; two columns whose cells overlap are fine when their sprites are in
  different rows, and only cells that really overlap — or two sprites served one cell — send the
  size back a notch tighter, by exactly what the overlap costs. Since every sprite is inside its
  own cell and cells never overlap, no cell can ever hold a neighbour's pixels. When no common
  size fits at all — sprites of wildly different sizes, or two boxes already overlapping —
  **nothing moves** and it says so: growing each box into whatever gap it happens to have would
  leave every frame a different size, which is the one thing this is here to prevent. It never
  shrinks a box or moves one off its own pixels, and re-running it changes nothing. The
  recovered cell can sit a px off the sheet's real one — the true pitch isn't knowable from the
  pixels alone — but it is the *same* for every frame, which is what the animation needs.

  The cell can only be read as well as the sprites allow, so the **Size** box in the panel is
  the manual counterweight. It appears whenever something is selected, and it's a **picture of
  the box** rather than a form: the cell sits in the middle with its current size (or how many
  different ones are selected), and each edge has a pair of arrows — the one **pointing away**
  from the cell gives that edge a pixel, the one **pointing in** takes one back, with a
  `−`/`+` pair inside for **all sides** at once. Press and hold to keep going — trimming 6px
  isn't six clicks. Nothing has to share a size, since every box just moves its own edge.
  `Ctrl`/`⌘`+`A`
  selects every box, which is the quick way from a grow straight into a trim. It stops at 3px
  and at the sheet's edge, and — like the drag handles — it doesn't police overlaps, it just
  says when two boxes ended up on top of each other. The rubber band may run **past the sheet's edges** — and can
  even start on the empty stage outside it — so sprites sitting right on a border are
  easy to sweep up. The **mouse wheel zooms** (anchored on the cursor), and the top-right
  **`N` sprites detected** counter tracks the boxes currently on the sheet, so it's
  obvious when one is still missing. **Save** marks the sheet *Done* and returns to the
  gallery; **Delete — no sprites here** marks it *Deleted*. Keys: `A` auto-detect,
  `B` pick background, drag to select, `G` grow to the sprite cell, `Ctrl`+`A` select all,
  `Del` delete selected, `Alt`+drag new box, `S` save, `Esc` back. Both galleries remember the filter chip you left them on.

### Step 2 · `/` — animate the cropped sprites

The **default view** is animation only — no cropping controls. Like `/crop` it has
two screens, and shares its look (dark pixel-editor theme, teal accent, monospace
metadata):

- **Gallery** — a card grid of the sheets that already hold cropped sprites (chips
  **All / To animate / Baked** with counts + a search box + a *baked* progress bar).
  Each card shows the sheet thumb, a **ready / draft / baked** badge and its sprite
  count; hover for **▶ Animate** (open) and **✂ crop** (re-delimit). The **✂ Crop
  sheets** link in the top bar jumps to `/crop`.
- **Editor** — click a card to open it. The **left two thirds are the authoring
  panel**: preview + timing + creature fields in a narrow column, and the frame
  slots for the 4 iso facings in a wide one. The **right third is a palette of the
  sheet's individual sprites**, each keyed to transparent by the very same extraction
  the baker runs (`/api/sheets/<id>/sprite/<box>`), so *what you see is what bakes*.

Assembling a creature:

- **Walk is authored in the four ISO facings** — `SW south-west`, `SE south-east`,
  `NW north-west`, `NE north-east` (in that slot order: the two front views, then
  the two back ones), with the pose each shows in the tooltip (`front-left`,
  `front-right`, `back-left`, `back-right`). That is what hibitomo's isometric apps
  ask a sheet for: `PetWalker::walker_dir_from_delta_iso` maps every grid step to a
  screen diagonal (4=SE, 5=NE, 6=NW, 7=SW), never to a screen cardinal.
  The spec/layout **keys are unchanged** (`down_right`/`up_right`/`up_left`/
  `down_left`) because firmware `DIR_KEYS` and `@hibitomo/schema` key on them —
  hover a slot to see the key it writes. **The four top-down cardinals the schema
  requires are not authored here**: the baker aliases them onto the same rows
  (`right≡SE, up≡NE, left≡NW, down≡SW` — the two readings of one grid step), so the
  sheet declares 8 directions, an iso app gets four distinct facings instead of the
  nearest-cardinal fallback, and a top-down app still gets a pose per direction.
  A spec authored before this (top-down cardinals) is folded into the iso facings
  by the same mapping when you open it.
- Choose a clip (walk / idle / sleep) and, for walk, a facing, then **click a sprite
  in the palette** to append it as that slot's next frame (shift-click removes). Each
  palette sprite is tagged with its assignment (e.g. `SE0`), and each frame shows as a
  draggable thumbnail chip you can reorder. Keys `1`–`4` jump between facings.
- **Only walk is directional.** **idle** is a single clip — the pet faces the camera
  (front / `SW`) while standing still — and **sleep** is one lying pose for every
  facing. Both bake to a single sheet row; the emitted layout still lists every
  direction (pointing at that one row) so the schema and firmware shape is unchanged.
- **Preview** — the panel plays the active clip live at your timing (for walk, pick
  any facing to inspect; for idle/sleep the picker is greyed out); mirrored and
  idle-default frames preview exactly as they'll bake.
- **Mirror** — tick the `⇄` box on a facing to bake it from its horizontal twin,
  flipped (the usual case for DS sprites that only ship one side): `SE` mirrors `SW`,
  `NE` mirrors `NW` and back. Every iso facing has a twin, so a sheet with one front
  and one back view fills all four slots with two ticks.
- **idle = SW walk[0]** — one click fills the idle clip with the front walk's first
  frame. Leaving idle empty bakes that same pose.
- Fill in the creature (id, name, type, color, stage) + timing, then **Save & bake**
  (or press <kbd>S</kbd>, as in `/crop`). One action, because the spec IS the recipe
  for the bake: it writes `specs/digimon/<id>.extract.json` (boxes, clip assignments,
  timing, creature fields — the editable part) and then renders
  `output/digimon/<id>.png` + `<id>.json` (+ `pet_<id>.json`), the same
  `SpriteLayout` shape [PMDSpriteManager](../PMDSpriteManager) emits and the
  content-editor consumes. Tick *stage to content-editor* to also copy them into the
  content-editor's dev tree.
- It reports on the foot line next to the button (the editor covers the gallery's
  status bar) — including *why* a bake was refused, e.g. `walk has no frames for
  NW (up_left)`. A refused bake still leaves the spec saved, so a half-assembled
  creature is never lost.

> **Backdrops are keyed automatically.** A crop box is normally the sprite's flat
> "cell" rectangle, and that cell colour is *not* the sheet background — so extraction
> also keys whatever flat colour owns the box's own border ring, per box. Sprites come
> out transparent without eyedropping every cell shade by hand, and because the palette
> and the baker share this code, *what you see is what bakes*. The guard: the keying is
> kept only if the sprite survives in one piece, so a white Digimon on a white cell is
> left alone instead of being eaten (`auto_cell_bg`, `cell_bg_frac`, `cell_min_solid`).
>
> **Enclosed backdrop goes too** (`bg_enclosed`, on): the gap an arm or a tail walls in
> is backdrop, and a plain border flood leaves it opaque — a magenta wedge riding along
> under the sprite's own arm. Two limits keep that from eating the art:
> it applies to the **sheet colours you eyedropped**, never to the per-box cell colour
> (a guess that lands on the outline or the armour's grey), and it matches the **exact
> colour** (`bg_enclosed_tol = 0`) instead of the silhouette's wide `tol` — a teal
> Digimon's body sits well within 40 of a teal key. The survival guard above runs on
> this pass as well.
>
> The one case it cannot judge is a sheet keyed on a colour the artist also drew with —
> a **black** key on black outlines. Set `"enclosed": false` in that sheet's
> `boxcache/<id>.boxes.json` background payload to fall back to the border flood; the
> animate view copies the flag into the spec's `seg_params`, so the bake keys exactly
> what the palette showed.

### Auto-assemble: the whole backlog in one click

The rips animate in **blocks of three frames**, in reading order, so a sheet's
sprite count already says which facings it carries:

| sprites | blocks |
|---|---|
| **15** | SW · SE · NW · NE · idle |
| **12** | SW · SE · NW · NE (no idle block — the baker holds the first SW pose) |
| **9** | SW · NW · idle, with **SE mirrored from SW and NE from NW** |

**Auto-assemble** in the gallery appbar (badge = how many sheets are waiting)
writes each of those sheets the spec the editor would have written and bakes it,
so they leave the *To animate* chip on their own. Only sheets that are cropped,
not marked "no sprites" and **not already baked** are touched — a second click is
harmless, and hand-assembled work is never overwritten. `core/autoassemble.py`
holds the layout table; the same code backs the CLI below.

## CLIs

```bash
python3 Scripts/download_sheets.py [--limit N] [--urls-only]
python3 Scripts/autodetect_all.py  [--force]     # warm the box cache
python3 Scripts/animate_blocks.py  [--dry-run] [--counts 9 12 15] [--limit N] [--stage]
python3 Scripts/bake_all.py        [--stage]     # bake every saved spec
```

## How it works

```
raw_sheets/<id>.png                 downloaded rip (gitignored)
        │  core/segmenter.py  (bg infer → mask → connected components → boxes)
        ▼
boxcache/<id>.boxes.json            cached auto-detected boxes
        │  web UI: you assign boxes → directions/frames
        ▼
specs/digimon/<NNN>.extract.json    your editable extraction spec
        │  core/extractor.py + baker.py  (clean keying + single placement pass)
        ▼
output/digimon/<NNN>.png            unified sheet (regular grid, bottom-anchored)
output/digimon/<NNN>.json           explicit hibitomo SpriteLayout
output/digimon/pet_<NNN>.json       creature node
```

### Segmentation

`core/segmenter.py` infers the background (alpha, or the modal border color
snapped to a canonical chroma key), builds a foreground mask (RGB distance +
optional HSV hue keying), labels connected components (scipy, with optional
dilation to join disconnected limbs), filters specks / thin text, merges nearby
boxes, and sorts them in reading order. `core/extractor.py` then keys each
chosen box to transparent alpha with a **border flood** (so an interior
background-colored pixel — a green eye on a green screen — survives) and
de-fringes the anti-aliased rim. The returned frame keeps the **crop box** as its
canvas rather than being shrink-wrapped to its own content: the sprite's position
inside its cell *is* the animation (an airborne frame sits high, a landing frame
sits low), and the baker anchors whatever it is handed, so trimming per frame
would flatten every jump onto one baseline (`trim_content`, off by default).

### Baking

`core/baker.py` runs a **single placement pass** that drives both the composited
PNG and the layout `{col,row}` cells, so they can never drift. The grid is
walk rows (one per authored facing — `SW, SE, NW, NE` for an iso spec) → **one**
idle row → an optional sleep row; every cell is
the same size and frames are bottom-center anchored. Idle and sleep are
non-directional, so every direction's `idle` in the layout points at that single
row. The four cardinals are then **aliased onto the iso rows** (`right≡SE, up≡NE,
left≡NW, down≡SW`) — no extra pixels, just extra keys, so the schema's
"cardinals required" rule is met by a sheet that only draws iso facings. A legacy
top-down spec (four cardinals, no diagonals) still bakes exactly as before.
The emitted `SpriteLayout` matches `packages/schema/src/pets.ts` (`.strict()`)
and the hand-authored `pokemon/001.json`.

## Output format

`output/digimon/<NNN>.json` is an explicit `SpriteLayout`:

```json
{ "style": "explicit", "cols": 2, "rows": 6, "walk_style": "stride", "tick_ms": 33,
  "walk": { "down": [{ "col": 0, "row": 0 }, …], "left": […], "right": […], "up": […] },
  "idle": { "down": [{ "col": 0, "row": 4 }], "left": [{ "col": 0, "row": 4 }], … },
  "sleep": [{ "col": 0, "row": 5 }],
  "walk_durations": [6, 6] }
```

Every `idle` direction points at the **same** row — the pet holds one camera-facing
pose while standing still — but the per-direction shape is kept so the schema and the
firmware resolver read it unchanged.

A sheet animated here is an **iso** sheet, so all eight keys are emitted: the four
diagonals (`down_right`, `up_right`, `up_left`, `down_left`) are the rows actually
drawn, and the four cardinals point at the same rows (`right≡SE, up≡NE, left≡NW,
down≡SW`). Keys stay in the schema's top-down naming; the UI shows them as the
compass facings `SW / SE / NW / NE`. Because the `walk` map carries diagonal keys,
`PetSpriteLayout` reads `dir_count = 8` and the iso apps use the four real facings
instead of falling back to the nearest cardinal.
