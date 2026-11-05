# Ground-truth comparison (archive lane)

Status: 2026-10-26. Ground truth: the `.pdf` next to each `.goodnotes`
backup, rendered with `pypdfium2`. All comparisons use copies in `corpus/`;
the Drive folder is read-only. Metrics are binary ink masks (`<200` grey)
after subtracting the page background (PDF attachment or drawn template), so
they measure handwriting/highlighter only. Page numbers below are **1-based**
(CLI convention; `Page.index` stays 0-based in the API).

## Test notebooks

| notebook | pages | items | what it tests | status |
|---|---|---|---|---|
| `Notebook C` | 6 | 2,025 penStroke2 | dense handwriting with moved strokes, two templates/backgrounds | **rendered**, metrics below |
| `Notebook E` | 1 | 36 penStroke2 (7 deleted) | squared-paper template + few strokes | **rendered**, IoU 0.906 |
| `Notebook G` | 3 live | 11 penStroke2 + 2 f21 shape texts (1 deleted page) | shape rich-text decode + PDF background | rendered; font metrics approximate |
| `Notebook F` | 1 | 1 f13 nexus | typed-text-only page | rendered; nexus layout approximate (IoU 0.1966) |
| `Notebook J` | 1 | 1 f13 nexus | typed text + template | rendered with document-id heading/list (IoU 0.248) |
| `Notebook H` | 1 notes file | 5 f7 + 1 f1 | whiteboard edge case | one blank page; variants open |

## `Notebook C`, page 5 (1-based; API index 4)

- Page UUID `UUID`, canvas **1091.3 × 1543.5**,
  background template *Squared Paper* (`9FE8F365…_a4_1_1 - Yellow`) over a blank
  PDF attachment.
- 1,323 f7 records: **1,001 live items** (996 pencil, 5 constant-width) after
  skipping **322 deletion tombstones**. 2 variable-width items are deleted.
- Render: `out/render`; diff `out/render`;
  3-panel `out/render` (PDF | final | diff). The
  before/after translation panel is `out/render`.
- Ground truth: `reference.pdf` page 5 rendered at 1091×1543.

| stage | IoU | recall | precision | within 1 px |
|---|---:|---:|---:|---:|
| v0.1 centreline, no translation (SS=2) | 0.592 | 0.763 | 0.726 | 0.819 |
| + item field 6 translation + tombstones (SS=2) | **0.794** | **0.919** | 0.853 | 0.970 |
| + exact outline + true z-order + multiply highlighters (SS=3) | **0.825** | **0.948** | 0.864 | 0.971 |

Reference ink 50,887 px; final gnreader ink 55,842 px. The exact outline fills
the boundary (rather than a 2.5×-width centreline), and 3× supersampling +
LANCZOS downsample anti-aliases the edges.

## `Notebook C`, page 6 (1-based; API index 5)

- 702 f7 records → **564 live items** (561 pencil, 3 constant) after 138
  tombstones; 3× SS with outline + z-order.
- Render `out/render`. Reference ink 27,772 px.

| IoU | recall | precision | within 1 px |
|---:|---:|---:|---:|
| **0.857** | **0.981** | 0.872 | 0.998 |

## `Notebook E`, page 1

- One page, canvas **834.24 × 1078.82**, *Squared Paper* template
  (`9FE8F365…_standard_1_1 - Yellow`), 36 records → **29 live pencil strokes**
  after 7 tombstones. No translation on this page.
- Render `out/render`; PDF `private fixture` page 1.
- Grid: 16 pt lines, paper `(247,246,233)`, line `(208,210,211)`; our grid
  matches the PDF line positions (first line at 15 pt).

| reference ink | ours | IoU | recall | precision | within 1 px |
|---:|---:|---:|---:|---:|---:|
| 2,575 | 2,825 | **0.906** | **0.997** | 0.909 | 1.000 |

## Live highlighter (`Notebook A`, page 3)

- Live document `UUID`, page
  `UUID`, canvas **1091.35 × 1543.46**.
- Frozen v1 `corpus/frozen-page-v1/`: raw page bytes 204,670 B, sha256
  `b5f88647…`, 91 descriptor/item pairs (90 pencil + one green highlighter).
- Frozen v2 `corpus/frozen-page-v2/` after a fresh snapshot: 219 pairs
  (214 pencil + **5 highlighters**), page bytes 460,565 B, sha256
  `425fce2d…`, live page modified 20:48:58. It contains the original formula
  bar plus black and purple handwriting under yellow and green test bars.
- The five v2 highlighters are green rgba `(92,255,42,128)` ×3 and yellow
  rgba `(255,255,54,128)` ×2, all thickness 24.0, z sessions
  **90, 183, 185, 201, 202** (bottom→top).
- Real iPad reference: `private fixture`
  (Display P3). Converted to sRGB, its bar/paper factors are green
  **(0.673, 1.000, 0.575)** and yellow **(1.000, 1.000, 0.601)**. The stored
  rgba formula `factor = 1 - alpha*(1-rgb/255)` gives **(0.679, 1.000,
  0.581)** and **(1.000, 1.000, 0.604)**, within **1.6/255**. No alpha
  recalibration is needed; comparing the raw P3 screenshot with an sRGB
  render made the bar look ~0.04–0.05 too strong.

Before/after on the frozen pages:

| | live highlighter path | ink under a green bar |
|---|---|---|
| v0.3.2 | flat mask + one **alpha** composite | faded green: mean (46,146,56) on v1 |
| v0.3.3 | flat mask + one **multiply** (`ImageChops.multiply`) | v1 darkest (0,36,41) vs (0,36,71) outside; v2 purple (111,68,163) → (75,68,94); black stays (0,0,0) |

`_apply_highlighter` now builds one `L` mask per stroke and one RGB factor
image, then multiplies the flattened page. `gnlive` flushes its transparent
ink layer when it reaches each highlighter, multiplies the flattened page at
that z position, and continues drawing above it. Ink drawn later is unchanged;
there is no per-segment alpha stacking. The actual `gnlive` PNG (1131×1600,
SS=2) gives formula-bar paper median **(168,247,135)**, navy-under-green median
**(0,38,42)**, and black test strokes under green with max channel **5**. The
final PNG is the frozen v2 snapshot (219 items); a later fresh MCP-venv render
saw 225 live items, also 0 warnings.

### Highlighter shape (v0.3.5)

The frozen shape page has **24 constant-width highlighters**, all thickness
24.0, alpha 128, and `ConstantWidthStroke` payload field 6 = 1; the
item-level `lineStyle` field 11 is absent. There is no separate straightened
item type: auto-straightened bars are stored as short collinear quads (max
line deviation 0.00), while freehand blobs are not collinear (0.68–26.14).
Round caps are therefore applied only to collinear paths, radius
`thickness/2`.

Pillow's even-odd polyline fill leaves thin white slivers in compact
scribbles. For marks shorter than 35 canvas units, the mask now also stamps a
disc at every flattened point. Weak/hollow pixels inside the reported short
marks dropped from **340→2**, **258→1**, **328→2**, **112→30**; long bars were
already solid. The table/red crops (`out/hl3_mine_table.png`,
`out/hl3_mine_red.png`, `out/hl3_shape_side.png`, `out/hl3_red_side.png`) match the iPad crops' round ends and solid fills. A
rough affine-aligned highlighter-mask IoU against the iPad table crop rose
**0.8134 → 0.8160** (`out/hl3_ipad_table_match.json`). Diagnostics:
`out/hl3_cap_diag.txt`, `out/hl3_shape_metrics.txt`.

Highlighter pages in the backup corpus are only `Notebook C` p5/p6 (3 each);
Notebook E p1 is a no-highlighter control. Scores remain unchanged at 3
decimals; the p6 4-decimal value moves 0.8572→0.8566 because the new round
caps cover a few text pixels at a bar end (`out/score-check-v035.txt`):

| page | highlighters | v0.3.1 IoU | v0.3.3 IoU | v0.3.5 IoU | recall | precision |
|---|---:|---:|---:|---:|---:|---:|
| Notebook C p5 | 3 | 0.825 | 0.8250 | **0.8250** | 0.9481 | 0.8640 |
| Notebook C p6 | 3 | 0.857 | 0.8572 | **0.8566** | 0.9808 | 0.8712 |
| Notebook E p1 (control) | 0 | 0.906 | 0.9061 | **0.9061** | 0.9969 | 0.9087 |

`tests/test_highlighter.py` has six tests using the frozen pages: v1
highlighter above and below ink, a synthetic purple pen under a green bar
(channel error ≤ 2/255), iPad P3→sRGB factor calibration (including the actual
ICC-converted screenshot), and all five v2 bars (paper factor, ink channel
error, black stays black). `tests/test_highlighter_shape.py` adds five shape
tests. The full private development suite is 38 passed.

## Live f1 images

- Frozen private-corpus page (`GNREADER_TEST_CORPUS/live_images_p1/`): 413
  descriptor/item pairs, raw page sha256 `ad57abdc…`. One image at bbox
  `(402.5,40.8,679.1,655.5)`, z session 219, attachment `45872143…`. Item
  fields: f2 rect, f3 actualRect
  centre/size/angle, f4 attachment, f5 NotesZIndex, f6 format 1 (JPEG), f18
  schemaVersion, f21 lockStatus. No crop/mask/corner-radius field exists; round
  photo edges are baked into the JPEG.
- Before v0.3.4 the live loop ignored `ImageItem`: the mockup was absent and
  `warnings` was `[]`. After the fix the ink layer is flushed, `_draw_image`
  pastes the image with `live_attachments.load`, and later ink stays above.
  `images_drawn=1`, `warnings=[]`.
- A 638-page live scan found **43 image pages / 82 images**; all but one have
  `rotation=0`. The exception is `-1.183 rad`; it is rendered from actualRect
  centre/size and `math.degrees(angle)`.
- Rendered and inspected: activity mockup, full-page scan with ink on top,
  3-image page, video-frame page, rotated sticker page. Missing/unreadable
  attachments produce `{"kind":"image missing","count":n}` without failing the
  page.
- COMPARE scores at 3 decimals are unchanged: page 5 **0.825**, page 6
  **0.857**, third reference page **0.906**; the v0.3.6 4-decimal page-6 value
  is 0.8566.

## Page-4 missed item groups (v0.3.7 Round 3)

Frozen private-corpus page 4 (68 record pairs). Before 0.3.6 the renderer
emitted 3× `line`, 1× `sticky note`, 7× short-constant/unknown-pencil warnings
and drew none of the five test groups. After 0.3.7: **68/68 items drawn,
warnings `[]`** in `gnlive` at 1600 px (`out/page4_render.png`, `.json`).

Round 3 re-measured the v0.3.6 image with the refined P3/page mapping; the old
reported value is kept for reference. Same crop-mask metric:

| group | v0.3.6 reported | remeasured before | Round 3 after | main change |
|---|---:|---:|---:|---|
| sticker body | 0.9052 | 0.9180 | **0.9179** | calibrated tints, pale ball glow, black details last |
| sticky note | 0.9777 | 0.9823 | **0.9895** | rounded corners, body/author position |
| ellipse + arrows | 0.3510 | 0.3663 | **0.4566** | stroke centred on bbox, Hobby curl, 14-unit/34° heads |
| tape | 0.6476 | 0.6736 | **0.7693** | exact 40-polygon outline union, flat ends, thin shadow |
| grainy pencil | 0.2211 | 0.2335 | **0.4316** | smooth grey core, soft edge profile, mottled dust |

Remaining differences: the basketball body is flatter/more saturated than the
app's soft gradient; elbow-1's shaft sits ~4 units above the iPad (the
connection tip is on the ellipse, exact no-middle-point corner rule still
unknown); tape x-marks are ~10% larger; grain dust is less clumpy.
Side-by-sides: `out/p4_full_side.png`,
`out/p4_r3_after_{sticker,sticky,shapes,tape,grain}_side.png`; scores
`out/p4_round3_scores.json`. COMPARE scores are unchanged: page 5 **0.8250**,
page 6 **0.8566**, third page **0.9061**.

## What changed (before/after evidence)

- **Translation/deleted items** (`out/render` →
  `out/render`): applying `penStroke2` field 6 moved 193 of 996
  live strokes into place and took p5 from IoU 0.592 to **0.794**. This fixed
  the short/garbled equation lines. Deleted tombstones are skipped but are
  empty in this backup, so they do not change pixels.
- **Z-order**: `NotesZIndex` (session, batchID, batchIndex) is applied
  bottom→top (`Page.objects()`); higher session on top. With normal alpha,
  translucent highlighters above pen dropped IoU to 0.771; multiplying them at
  their z position restored 0.795 (SS=2) and 0.825 (SS=3).
- **Exact outline**: `PencilStroke` fields 5–11 are a closed polygon per group
  (`0` move = 2 floats, `2` cubic = 6 floats/3 points, `3` arc = 5 floats).
  996/996 live pencil strokes on p5 build valid polygons. At SS=2, recall rose
  0.919 → 0.961; at SS=3, final recall is 0.948 with IoU 0.825.
- **Templates**: the missing vertical/horizontal divider lines on Notebook C
  p5/p6 are the *Squared Paper* template, not item shapes. 102 template pages
  across 124 backups (`out/template-scan.txt`).

## Live pages (`render-records`)

`gnreader.render_records(records, out_path, *, width, height, template,
paper_width, paper_height, supersample=3)` accepts raw live page bytes or the
`[(descriptor, item)]` list from `gnstore.get_page_records()` (already
bottom→top). CLI: `python -m gnreader render-records`.

Proof on Notebook D (not present in any backup; bytes from
`gnstore/handoff/Notebook D/`):

| page | output | canvas | strokes | text boxes | unknown |
|---|---|---|---:|---:|---:|
| 1 | `out/render` / `out/render` | 1091×1543 | 1 variable-width | 11 | 0 |
| 2 | `out/render` | 1091×1543 | 0 | 3 | 0 |

The original `reference.pdf` was located from the live store
(`Library/Attachments/2819B4E5-…`) and copied to `private fixture` only
for local comparison; text positions/fonts are still approximate, so no IoU is
claimed for these pages.

## Item types and current status

Counts: `out/item-types.txt`; machine-readable status: `capabilities.json`.

- **Notebook C**: only `penStroke2`; all live strokes decoded (pencil,
  constant, variable). Deleted tombstones skipped.
- **Notebook F**: one `nexus` (`f13`); exact text via `gntext`, rendered
  with the document-id/section/list layout.
- **Notebook G**: `penStroke2` + `shape` (`f21`, kind 1 shape text) —
  both decoded/rendered via `gntext`.
- **Live image page**: f1 image + 3 shape texts; 1 image and 3 texts rendered,
  no warnings.

Across 124 backups: `image` 118 (backup and live paths), `fillShape` 62
(common geometries), `shape` 30 (rich text), `nexus` 15 (text rendered,
layout partial), `math` 6 (all deleted; attachment/placeholder path),
`stickyNote` 1 in backups (empty/deleted); the page-4 private-corpus item
renders paper/body/author. `penEraser` 0. Classic `text`/`text2`/
`textFragment` are decoded through `gntext` but have zero corpus samples.
`f3/f4` and `f6` remain unsupported; `f22` elbow/hobby connectors are
rendered.

## Known gaps

1. Nexus/Yjs text uses `gntext` exact text plus Yjs child boundaries; the
   document-id header/section/list layout is a best-effort approximation, not
   byte-perfect Goodnotes typography.
2. `f21` shape fill/stroke is rendered for ellipse and rounded rectangle;
   other shape geometries warn.
3. `f11` math is attachment/placeholder only (all six corpus items deleted).
   Sticky-note paper/body/author is rendered on the page-4 private-corpus
   item; the one backup item is empty/deleted.
4. Whiteboard documents have no page event/canvas and still render blank.
5. Variable-width stroke per-point angles are decoded structurally but not
   used (global thickness is used); the 11-float “pad” is a GUESS.
6. A non-empty `PenEraserNotesItem` has never been observed, so its geometry
   mapping remains a best-effort GUESS; 0 records in all backups.
7. `Notebook I` backup and its adjacent PDF contain different page
   content; the quality row is a PDF/backup mismatch, not a renderer failure.
8. The Notebook C p5 dark column separators and the old Notebook I planner
   background are absent from both backup and live sources (see
   `out/live-source-check.txt`), so they are not faked.

## Scale run (all backups)

`out/scale-run.md` / `out/scale-run.json`: 124 notebooks, **503 live pages**
(30 deleted pages omitted), **0 page errors / 0 notebook errors**, **18
warnings**, slowest page **2.42 s**, max worker RSS **479 MB**, wall time
**37.8 s** with 4 notebook workers. One process per notebook with a 240 s
timeout.

Warnings are explicit: 13 nexus layout-approximation notices, 4 missing image
attachments in the backup export gap, and 1 uncommon fill geometry. They are
returned in `RenderResult.warnings`; items are skipped or rendered partially
rather than raising.

## Quality sample (30 pages, 24 notebooks)

Full table: `out/quality.md`, raw data `out/quality.json`. Comparable
handwriting pages range from IoU 0.52 to 0.91. High-confidence examples:

| page | IoU | recall | precision |
|---|---:|---:|---:|
| Notebook C p5 | 0.825 | 0.948 | 0.864 |
| Notebook C p6 | 0.857 | 0.981 | 0.872 |
| Notebook H/Notebook H p5 | 0.871 | 0.979 | 0.887 |
| Notebook J/Notebook B p3 | 0.883 | 0.984 | 0.897 |
| Notebook E/Notebook E p1 | 0.906 | 0.997 | 0.909 |

Typed pages are rendered now: the two scored nexus pages reach **0.1966**
(Notebook F) and **0.248** (Notebook J) IoU, with exact text and the
document-id/section/list layout. Remaining differences are font metrics and
PDF-only document headers, labelled "typed text (nexus layout approximate)" in
`out/quality.md`. `Notebook I p1` is **not a renderer failure**: its
backup and adjacent PDF contain different page content (0.010 IoU, labelled
"PDF/backup page mismatch"). Page deletion was the biggest mapping bug found:
Notebook H had a deleted page still in `index.notes`; skipping it moved its p5 IoU
from 0.02 to **0.871** and made CLI/PDF page numbers agree.

## Fill shapes and column dividers

`f9` fillShape is now rendered (ellipse/rect/polygon/quad, alpha and z-order),
verified on `Notebook H/Notebook H` p5 (4 translucent ellipses).  The **two dark
vertical column dividers** visible in the Notebook C p5 PDF are not in
`notes/<uuid>` (item census: only penStroke2), not in the 29AC template PDF
(which contains only the 16 pt grid), and not in the Squared Paper
`template.svg`.  They are PDF-export-only vector content; no stored item or
background can reproduce them.  Evidence: `out/divider-lines.txt`; recorded as a
known source-data gap, not silently guessed.
