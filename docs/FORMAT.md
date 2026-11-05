# Goodnotes `.goodnotes` backup format (archive lane)

Status: 2026-10-26. Claims marked **VERIFIED** have a command/output listed in
`NOTES.md` or reproducible with `gnreader`. **GUESS** marks inference. No page
text or handwriting content is reproduced here.

The user's data is read-only; all work was done on copies in `corpus/`.

## 1. Container

A `.goodnotes` backup is a ZIP:

```
document.info.pb            # 2 bytes in every sample: protobuf {1: 4}, not a descriptor set
schema.pb                   # 2 bytes: {1: 31}
index.notes.pb              # ordered page list
index.attachments.pb        # attachment id -> attachments/<uuid>
index.events.pb             # page/background events
index.search.pb             # search/OCR index
notes/<page-uuid>           # one page's item log
attachments/<uuid>          # background PDF (or imported PDF/image)
search/<attachment-uuid>    # extracted text / OCR sidecar
thumbnail.jpg               # optional
```

**VERIFIED** on the five small corpus files and `Notebook C` (`unzip -l`,
`out/01-inventory.txt`).

## 2. Record framing

`index.*.pb` and `notes/<uuid>` are **not** one protobuf message. They are a
stream of `varint length + protobuf message` records (SwiftProtobuf
`BinaryDelimited`).

```
index.notes.pb record:  1: uuid string, 2: "notes/<uuid>"
index.attachments.pb:   1: attachment id, 2: "attachments/<file-uuid>"
```

**VERIFIED** by `bin/gnprobe.py` / `gnreader.decode.read_records`; the first
record of a notes file begins `0x6b 0x0a 0x24 <uuid>` = length 107, field 1,
length 36.

A notes file alternates **`AnyNotesItemDescriptor`** records (several fields)
and **`AnyNotesItem`** records (one payload field). Descriptor field 3 is a
**deleted tombstone** boolean. The descriptor and item are paired by item UUID
(descriptor field 1 / item field 1).

**VERIFIED** on Notebook C: page 5 has 1,323 items, of which 322 descriptors
have `deleted=true`; page 6 has 702 items / 138 deleted. Deleted item payloads
in these samples have empty stroke command arrays. `out/eraser-scan.txt`.

## 3. Page model

- `index.notes.pb` order = **backup page order** (0-based; includes deleted pages).  After skipping `PageDeletedEvent` type 56, the remaining order matches the PDF export; `Page.index` is renumbered over live pages.
- `notes/<page-uuid>` contains all items for that page.
- Notes UUID = event page UUID + 1 as a 128-bit hex value (carry included, not
  just the last hex digit). Match by integer value (or `_uuid_prev`).
  Example `...A3A0` ↔ event `...A39F`.
- **Deleted pages** are `PageDeletedEvent` (event type **56**,
  `2: page id`, `3: LWWBool deleted`). `index.notes.pb` still lists them, but the
  PDF export omits them. `gnreader` removes them and sets
  `Notebook.deleted_pages`; the CLI page numbers then match the PDF.
- `index.events.pb`:
  - **type 54** = page event: `2: page uuid`, `3:{1: background uuid}`,
    `4:{1: label}`, `17:` page/background geometry.
  - **type 2** = background event: `2: background uuid`, `4: attachment id`
    (raw UUID string), `5: attachment page index` (1-based), `8:{1: canvas_w,
    2: canvas_h}` as `float32`, `9: dynamic template string` (present on
    template pages).
- `attachments/<uuid>` is a PDF in every sample. Its page `f5-1` is the page
  background. A 4-page attachment is shared by pages 0-3; a 1-page attachment
  by page 4, etc.
- Stroke coordinates are in **canvas units** (the `f8` size, e.g.
  `1091.3 x 1543.5`), not PDF points. The PDF export scales the canvas to the
  PDF page (`595.28 x 841.89`).

**VERIFIED** on `Notebook C` (6 pages, 4-page + 1-page attachments),
`Notebook E` (1 page, template, 834.2×1078.8) and `Notebook G`.

### 3.1 Dynamic page templates

Some pages have no printed PDF; background event field 9 carries a template
string such as:

```
TEMPLATE-UUID_a4_1_1 - Yellow
```

The UUID maps to a built-in Goodnotes template. On this Mac the app bundle's
`.../GoodNotesCore_GoodNotesCore.bundle/Contents/Resources/ios_predefined_ids.json`
maps them to names; the dynamic SVG definitions are in
`.../Resources/GNDynamicTemplates_GNDynamicTemplates.bundle/Contents/Resources/`:

| UUID prefix | name | layout |
|---|---|---|
| `9FE8F365…`, `AD55B2D7…` | Squared Paper | 16 pt square grid |
| `238EE861…` | Ruled Narrow | horizontal lines every 16 pt |
| `AA273C17…` | Ruled Wide | horizontal lines every 25 pt |
| `CC365888…` | Dotted Paper | dots on a 16 pt grid |

SVG constants: paper `rgb(247,246,233)`; lines/dots `rgb(208,210,211)`,
0.5 pt thick; squared lines at offsets 8 + 16k (the exported PDF places the
first line at 15 pt after page margins); ruled patterns start at y=50 pt and
cover 80 % of the page; dotted pattern starts at x=10, y=15.

`gnreader` renders these programmatically at canvas scale. **VERIFIED** on
Notebook C page 5 (squared grid, 16 pt spacing at PDF scale) and Notebook E/Notebook E:
`out/template-scan.txt` counts **102 template pages** in 124 backups.

**GUESS**: paper colour names other than Yellow/White map to the same
`247,246,233` fallback.

### 3.2 Item types

`AnyNotesItem` payload field (schema lane's recovered `schema.proto`):

```
1 image, 2 penStroke (v1), 3 fillEllipse, 4 fillPath, 5 text,
6 legacyVariableWidthLine2, 7 penStroke2, 8 text2, 9 fillShape,
10 penEraser, 11 math, 12 textFragment, 13 nexus, 15 loadedAttachments,
20 stickyNote, 21 shape, 22 line
```

**VERIFIED** against `docs/schema/schema.proto` and all 136,222 payload
records. The corpus totals are in `out/item-types.txt`.

## 4. `bv41`: LZ4 in the legacy Apple `bv4` frame

The pen-stroke payload `f7.2` is **LZ4 compressed** inside a custom frame, not a
protobuf message:

```
"bv41" | u32le raw_size | u32le compressed_size | raw LZ4 block
...
"bv4$"                                  # end marker
"bv4-" | u32le size | raw bytes         # stored block
```

`lz4.block.decompress(block, uncompressed_size=raw_size)` works.

**VERIFIED** on 136,222 blobs (`out/scan-drawing.txt`) and by the matching
`bv41`/`bv4$`/`bv4-` constants in the app binary.

The decompressed bytes are:

```
"tpl\0" | u32le size | ASCII type signature | 0x00 | values
```

`tpl` is the same magic the app's renderer looks for ("Unable to load tpl node
from memory"). The size equals the decompressed length.

## 5. Signature grammar

A signature is a flat list of fields (the root struct):

| token | meaning |
|---|---|
| `v` | uint16 |
| `u` | float32 |
| `f` | float32 |
| `S(...)` | struct, fields concatenated |
| `A(T)` | array of `T`: **u32 count** then elements |

All little-endian. `gnreader.bv4.parse_signature` / `read_values` implement it.
The parser must consume the payload exactly; `gnreader` reports trailing bytes
as an unknown field.

**VERIFIED** on the Notebook C strokes; a 218-byte stroke parses byte-exactly.

### 5.1 `PencilStroke`

Signature:

```
vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)
```

Fields in order:

1. `v` format version (2).
2. `A(v)` **commandTypes**: 0 = `moveTo`, 1 = `quadCurveTo`.
3. `A(u)` **moveTo** points, 3 floats each: `(x, y, width)`.
4. `A(u)` **quadCurveTo** points, 6 floats each: control `(x,y,width)` then
   end `(x,y,width)`.
5. `A(v)` **outline group counts** (one integer per outline group).
6. `A(v)` **outline point types**, one per outline point. Sum of counts equals
   `len(types)`.
7. `A(u)` outline `moveTo` points, 2 floats each (type 0).
8. `A(u)` outline quad points; empty in every sample seen.
9. `A(u)` outline **cubic** points, **6 floats = 3 points** per type-2 command.
10. `A(u)` outline **arc** parameters, **5 floats**
    `(center_x, center_y, radius, start_angle, end_angle)` per type-3 command.
11. `A(v)` outline arc clockwise flags, one per arc.

Outline mapping **VERIFIED** on Notebook C page 5: 996/996 live PencilStrokes
produce a closed polygon; type 0 consumes 2 floats, type 2 consumes 6, type 3
consumes 5, and the arrays are consumed exactly. Example group counts
`[5,5,5,5,5,7,10]`, types `[0,2,3,2,3,…]`, 7 move points, 18 cubic points
(3 floats pairs), 17 arc records. The 218-byte sample has the same grammar.
`gnreader.render._outline_polygons` builds the polygon; rendering it raised
Notebook C p5 recall from 0.919 to 0.948 at SS=2 and to 0.948/0.825 IoU at SS=3
(see `COMPARE.md`).

**VERIFIED** exact consumption for 1,316/1,316 PencilStrokes on Notebook C page 4
before deletion filtering; after filtering 996 live strokes.

### 5.2 `ConstantWidthStroke` / highlighter

Signature:

```
vuA(v)A(S(uu))A(S(uuuu))vA(f)
```

1. `v` version (2).
2. `u` **thickness** (float32; 1.819 for a pen, 24.0 for a highlighter).
3. `A(v)` commandTypes (0=moveTo, 1=quadCurveTo).
4. `A(S(uu))` moveTo points: one struct `[x, y]` per command.
5. `A(S(uuuu))` quadCurveTo: one struct `[control_x, control_y, end_x, end_y]`.
6. `v` **line cap**: 0=butt, 1=round, 2=square. All 24 highlighters on the
   frozen shape page are 1; the item-level `PenStrokeLineStyle` (f11) is
   absent. `gnreader.model.Stroke.line_cap` carries it.
7. `A(f)` frame floats (empty in samples).

**VERIFIED** on 5 strokes in Notebook C (3 highlighters thickness 24, 2 pens
thickness 1.819). Corpus-wide, 218 of 748 live constant-width items have a
non-zero item field 6 translation; `gnreader` applies it the same way as for
pencil strokes (`out/translation-scan.txt`).

A highlighter is the same message with a semi-transparent colour (alpha 128)
and a broad thickness. The frozen live shape page has 24 highlighters, all
thickness 24.0, field 6=1. There is **no separate straightened flag or item
type**: auto-straightened bars are stored as many short, collinear
`quadCurveTo` commands (max deviation from the start/end line 0.00), while
freehand blobs are not collinear (max deviation 0.7–26.1). The cap field is
therefore applied only when the flattened path is collinear; a straight bar
gets a full semicircular end of radius `thickness/2`. Applying round caps to
every constant stroke would round freehand ends that Goodnotes exports square.
Goodnotes composites the bars as a per-channel multiply with
`factor = 1 - alpha*(1-rgb/255)`; see §7.

### 5.3 Variable-width stroke

Signature:

```
vuA(v)A(S(uuuuu))A(S(uuuuuuuuuuu))A(S(uu))A(v)A(S(uu))A(S(uuuu))A(u)
```

Decoded from a live Notebook D page 1 item (1,946-byte payload; 1 move + 41
quads, 42 commands):

1. `v` version = 1.
2. `u` **thickness** = 1.559.
3. `A(v)` commandTypes (0=move, 1=quad).
4. `A(S(uuuuu))` move points: 5 floats `(x, y, angle1, angle2, unused)`.
5. `A(S(uuuuuuuuuuu))` quad segments: 11 floats. Observed layout is
   **1 padding/uninitialised float + two 5-float points** (control then end):
   `[pad, cx, cy, a1, a2, 0, ex, ey, a1, a2, 0]`. The pad is garbage in the
   sample; drop it.
6. Trailing arrays (`A(S(uu))`, `v`, …) are absent/empty for this version;
   only the final 2-byte `v=0` remains after 41 quads.

**VERIFIED** structurally and by render: `gnreader` now draws the Notebook D
p1 variable stroke (42 commands) with no unknown report; the 5-float point
angles are **GUESS**/unused. Field 6 translation is applied when present
(corpus-wide 781 of 1,073 live variable items have a non-zero translation).

### 5.4 Erasers and deletion tombstones

`PenEraserNotesItem` exists (`AnyNotesItem` field 10, `data` bv41, signature
`vS(vvvvvvvv)S(uuuuuu)`), and `penStroke2` has a repeated `erasers` field 16.
However, a scan of **124 backups / 136,222 penStroke2 items** found:

- `penEraser` items: **0**
- penStroke2 items with non-empty field 16: **0**

So backups contain no eraser strokes. Instead, erasing/editing leaves empty
**deletion tombstones**: descriptor field 3 = true. `gnreader` skips them
(322 on Notebook C p5, 138 on p6, 7 on Notebook E/Notebook E). `out/eraser-scan.txt`.

The visible "erased" artefacts were two other things:
1. deleted tombstones (skipped now; they are empty and do not change pixels);
2. **item field 6 translation** was ignored. Applying it is the real fix
   (section 5.5).

**VERIFIED** by scan and by before/after render (see `COMPARE.md`).

### 5.5 Item translation (`penStroke2` field 6)

`PenStrokeNotesItemV2` field 6 is a `Point {x, y}`. Stored path/outline points
are **local**; the rendered position is `point + translation`. Most items have
translation `(0,0)`; on Notebook C p5 **193 of 996** live pencil strokes have a
non-zero translation, with x ∈ [-58.1, 521.4], y ∈ [-61.6, 58.8].
Corpus-wide (124 backups): pencil **22,513 / 93,985**, constant-width
**218 / 748**, variable-width **781 / 1,073** live items have a non-zero
translation; ranges are in `out/translation-scan.txt`. The translation is
applied to the path and all outline polygon coordinates.

**VERIFIED**: applying field 6 took Notebook C p5 from IoU 0.592 / recall 0.763
to IoU 0.794 / recall 0.919 at SS=2. This fixed the short/garbled equation lines.

### 5.6 Page-4 connector, tape and short-constant items

A frozen 68-pair private-corpus page exercises the item kinds the old renderer
skipped. Proto/field tables are in the development corpus notes; the renderer
consumes:

- **Short constant signature** `vuA(v)A(S(uu))A(S(uuuu))` — the same fields as
  `ConstantWidthStroke` but **without** the trailing cap/style arrays. It is
  the sticker colour body (7 items, thickness 14.064/11.433/2.858, alpha 128).
  Round 3 uses calibrated sRGB tints: orange `(247,74,0)`, pale glow filled
  `(248,224,134)`, green ball `(211,212,64)` at alpha 0.65; black
  short-constant details are drawn last so the ball cannot cover the squiggle.
- **`f20` sticky note** — `SerializedStickyNotesItem`: f20 translation, f21
  `fixedWidthAutoHeight` size, f30 colour `#FAE778`, f31 rich text, f32/33
  author, f40/41 show state. Render a 4-unit rounded paper rect, 24.6 pt body
  text at an 8.5/12.8 pad and a 13.2 pt author line at the bottom left.
- **`f21` shape** — `SerializedShapeNotesItem`: f20 translation, f21 sizing,
  f22 empty ellipse (fills the sizing box), f30 fill, f31 stroke style.
  Ellipse and rounded-rectangle fill+stroke are drawn. The stroke is centred
  on the sizing box, so the draw box is expanded by half the stroke width.
- **`f22` line** — `SerializedLineNotesItem`: f20 hobby geometry, f21 elbow
  geometry, f30/31 arrow styles, f32 stroke style. Endpoints are points or
  connections (`anchor` + `angle`); a connection resolves to the ellipse
  boundary as `(cx + rx·cos θ, cy − ry·sin θ)`. Elbow routing uses
  `startDirection` (1 = vertical first) and stored middle points, with 7-unit
  rounded corners. Hobby uses Hobby's algorithm (1986) through the stored
  knots with `curl_start=-0.45`, `curl_end=0.35`, which matches the app's
  teardrop loop and end tangent. Arrow style 1 is an open V 14 units long with
  a 34° half-angle and a round-joined tip (GUESS, calibrated from the iPad
  reference).
- **Tape** — `f7 penStroke2` type 1 with `TapeStyle_PatternTape`: a pattern
  attachment, thickness 18, tile side 36 page units (2×thickness), rotation
  anchor at the start. The fill mask is the exact union of the 40
  cross-section outline polygons, including type-6 elliptical arcs
  (`cx,cy,rx,ry,a0,a1,rotation`); the ends are flat/torn rather than
  round-capped. A thin 1-unit grey shadow is added below/right (GUESS; no
  shadow field).
- **Grain pencil** — `f7 penStroke2` type 5 with per-vertex `a,b` scalars
  (thick test: thickness 7.795, a 0.08533, b 0.92483). There is no texture
  blob; render a smooth `(45,42,40)` core at 0.82× thickness plus deterministic
  dark/light mottles and edge dust, then a 0.6-unit Gaussian soften. Thin
  type-5 strokes keep the normal hairline.

Calibration: `out/p4_full_side.png` and `out/p4_r3_after_*_side.png` compare
the iPad reference (left) with the 1600 px render (right);
`out/p4_round3_scores.json` has the v0.3.6, remeasured-before and after values.
Sticker 0.905→0.918→0.918, sticky 0.978→0.982→0.990, shapes
0.351→0.366→0.457, tape 0.648→0.674→0.769, grain 0.221→0.234→0.432.
Remaining differences: the basketball gradient is flatter, elbow-1's shaft is
~4 units above the iPad, tape x-marks are ~10% larger and grain is less
clumpy. Warnings are empty for all 68 items. Tests:
`tests/test_page4_items.py` (decode) and `tests/test_page4_render.py` (render).

## 6. Text, images, shapes

Typed text extraction is delegated to the schema lane's **`gntext`** package
(`gntext`), which owns rich-text and Yjs parsing; `gnreader` only
turns its records into renderable text boxes. This covers the real corpus's
`shape` rich text (30 backup / 50 live), `nexus` Yjs documents (15/15) and the
one empty sticky note. There are zero classic `text`/`text2`/`textFragment`
items in the corpus, but the same adapter handles them when present.

### 6.1 `f21` shape rich text

`AnyNotesItem.shape = 21` is a `SerializedShapeNotesItem`. `gntext` returns:

- `bbox = transform.translation{x,y} + richText.size{width,height}`; the origin
  is **top-left** in canvas units (the old bottom-left guess was wrong).
- paragraphs with inline/block attributes (color, font family/size,
  alignment, list style, line height).
- `NotesZIndex` from item field 7.

`gnreader` draws the text at the bbox, preserves paragraph/newline runs, maps
Courier/Times/Georgia to system fonts, uses the inline color/size, and supports
center alignment and numbered/bulleted blocks. Ellipse and rounded-rectangle
shape fill/stroke are rendered on the page-4 private-corpus item; other shape
geometries warn. Text inside a shape that has no text is reported as a warning
rather than silently dropped.

**VERIFIED** on `Notebook G` p1 (2 shape texts, font 14/17), `Notebook B`
p6 (2 live code shapes after deleted tombstones) and the live image test page
(3 shape texts).

### 6.2 `f9` fill shapes

`AnyNotesItem.fillShape = 9` is a `FillShapeNotesItem` (`id=1`, `rect=2`,
`position=3`, `shapes=4`, `translation=6`, `color=7`).  Live samples store field
4 as geometry messages: an **ellipse** (`center`, `radius`, `rotation`) or a
rect/polyline/quad; the color alpha is small (0.1 in the sample). `gnreader`
renders ellipse/rect/polygon/quad fills in z-order and applies the item
translation. 62 fill items exist across the corpus; one uncommon shape is
reported as a warning rather than crashing.

**VERIFIED** on `Notebook H/Notebook H` page 5 (4 translucent ellipses) and by the
scale run (fill warning reduced from 5 to 1).

### 6.3 `f13` nexus / Yjs text documents

`f13` is `NexusNotesItem` (`yjsData` / `yjsSnapshot` / `clientId`), a Yjs
document rather than a positioned text box. `gntext` applies the Yjs update and
returns exact text; `gnreader` supplements that with the Yjs XML-child
boundaries for paragraph/list layout (the first child is the document name and
is omitted from the export). The PDF export of a nexus page uses a document-id
heading plus a standard text area; `gnreader` reproduces that layout:

- document-id heading from the `DocumentCreatedEvent` (verified to equal the
  UUID heading in the PDFs);
- section heading for the first short body run (e.g. `T6a.1`);
- numbered list for short non-code runs, ordinary wrapped paragraphs otherwise;
- text area `x=44,y=58.67,w=746.24,h=961.49` in the 834×1078 standard template,
  scaled to the page canvas.

This is a **layout approximation**, not a reimplementation of text extraction;
`capabilities.json` marks nexus as `partial`. On the two scored nexus pages
the new render improves intersection from 0 to 0.196–0.248 IoU while retaining
all text; the remaining difference is font/format fidelity and PDF-only
document headers.

### 6.4 image, math and sticky notes

- **Image (`f1`)**: rect placement is decoded from backup and live records.
  Backups read `attachments/<id>` from the zip; live records use a read-only
  resolver (`~/Library/Containers/com.goodnotesapp.x/Data/Library/Attachments/
  <id>`, then `gnstore.get_attachment`). `gnlive` flushes its ink layer before
  pasting an image, so ink below/above keeps z-order; missing/unreadable
  attachments produce `{"kind":"image missing","count":n}`. Item fields: f2
  axis-aligned `Rect`, f3 `RotatedRect` centre/size/angle, f4 attachment id,
  f5 `NotesZIndex`, f6 format (1 = JPEG in the sample), f18 schema version,
  f21 lock state. There is **no crop/mask/corner-radius field**; round photo
  edges are baked into the JPEG. For rotated items, f3's true size/centre and
  angle are used (converted from radians to degrees); f2 is only the bounding
  box. Verified on live image pages: an activity mockup, a full-page scan with
  ink on top, a 3-image page, a video-frame page and one image rotated
  -1.183 rad.
- **Math (`f11`)**: rect + `attachmentId`; all 6 corpus items are deleted
  tombstones. The attachment is rendered when present; otherwise a clearly
  marked placeholder box (with the formula string when present) is drawn and
  listed as partial.
- **Sticky (`f20`)**: paper rect, body text and author line are rendered on the
  page-4 test; rich text still comes from `gntext` when present.
- **Unimplemented**: `f3 fillEllipse`, `f4 fillPath`,
  `f6 legacyVariableWidthLine2`, and legacy `f2 penStroke` (empty tombstones).

**VERIFIED** on `gntext` self-tests plus live page image/text evidence.

## 7. Rendering facts

- Canvas size comes from event type 2 `f8`; template page paper size comes from
  the blank attachment PDF (e.g. 595.28×841.89 or 455×588).
- Background: template if event field 9 is present, else attachment page
  `f5-1`, else white.
- Coordinate scale: canvas units -> PDF points = `canvas_w / pdf_page_width`.
- Stored `PencilStroke` width is a **half-width**: the median ratio of
  ink-distance / stored width in the PDF export is 1.26, so centreline fallback
  uses 2.5×. Exact outline polygons use the stored boundary directly.
- Colour message (item field 4): `1=red` (default 0), `2=green`, `3=blue`,
  `4=alpha`; pen `(0,36,71)` in the PDF matches `0, .141, .278, 1`.
- Draw order: item payload field 7 (`NotesZIndex`, or field 5 for images) is a
  `NotesZIndex {1:[{1:session,2:batchID,3:batchIndex}]}`. **Higher session is
  drawn on top** (verified on 20+ PDF crossings).
  `gnreader` sorts `Page.objects()` by `(session,batchID,batchIndex)` and draws
  bottom→top. The backup file order is arbitrary and is not used for z-order.
- Translucent highlighters use a **per-channel multiply** over everything
  below their z position: `factor = 1 - alpha*(1 - rgb/255)`, using the rgba
  colour message. Black stays black; coloured ink shifts as observed in
  Goodnotes (purple `(111,68,163)` under green becomes `(75,68,94)`). Build
  one flat `L` mask per stroke, turn it into one RGB factor image and multiply
  the flattened page (`ImageChops.multiply`); do not alpha-composite segment
  by segment, which stacks overlaps and changes ink strength. `render_page`
  does this directly; `gnlive` flushes its transparent RGBA ink layer when it
  reaches a highlighter, multiplies the flattened page, then continues drawing
  above it. Ink drawn after the highlighter is unchanged. The iPad reference
  screenshot is Display P3; after conversion to sRGB, measured bar/paper
  factors match the rgba formula within 1.6/255 (green `0.679/1/0.581`,
  yellow `1/1/0.604`). Proof: `out/highlighter_multiply_check.txt`,
  `out/render` and the two crops.
- Highlighter **shape** (v0.3.5): the coverage mask is a thick polyline plus a
  disc of radius `thickness/2` at each end when the path is collinear and the
  cap field is 1. Compact scribbled marks (start-to-end distance <35 canvas
  units) also get a disc at every flattened point; without that union, Pillow's
  even-odd polyline rasteriser leaves thin white slivers where short segments
  overlap. Do not alpha-composite per segment. Evidence:
  `out/hl3_cap_diag.txt`, `out/hl3_shape_metrics.txt`, the `out/hl3_mine_*`
  and `out/hl3_*_side.png` crops, plus `tests/test_highlighter_shape.py`.
- `render_page_result` / `render_records_result` return a `RenderResult`
  (`path`, `warnings`, `stats`); `render_page` and `render_records` keep their
  old string return by default. Warnings include unknown item types and
  per-item render exceptions; a failed item is skipped, not raised.
- Page deletion is applied before rendering: 30 of 533 indexed backup pages
  were deleted across the corpus; the scale run renders **503 live pages**.
- `render_records(..., attachment_loader=None, document_id="")`; live images
  default to the read-only resolver, and `document_id` supplies the nexus
  document heading. `render_records_result` returns warnings/stats.
- Exact pencil outlines are filled as polygons; super-sampling defaults to 3×
  and is downsampled with LANCZOS (anti-aliasing). SS=3 vs SS=2 on p5:
  0.795→0.825 IoU, ink 59,592→55,842 px (closer to reference 50,887).
- An earlier mean-width polyline is kept as fallback when outline arrays are
  absent; centreline curves are sampled as true quadratic Béziers and subpaths
  are split at `moveTo`.

## 8. Reproduce

Page numbers in the CLI are **1-based** (matching the PDF and what users say).
The Python API `Page.index` remains 0-based. Use `--zero-based` for raw indexes.

```bash
python -m gnreader info notebook.goodnotes
python -m gnreader pages notebook.goodnotes
python -m gnreader render notebook.goodnotes 5 out/page.png
python -m gnreader dump-strokes notebook.goodnotes 5 > out/strokes.json
python -m pytest tests -q
```

Live page bytes (store handoff / `gnlive`):

```bash
python -m gnreader render-records page-records.dat out/page.png \
  --width 1091 --height 1543
```

**API** (`gnreader.render_records` / `render_records_result`): `render_records(records, out_path, *,
width=1091, height=1543, template="", paper_width=595.28,
paper_height=841.89, supersample=3, attachment_loader=None,
document_id="")` accepts either the raw length-delimited
page bytes or the `[(descriptor_bytes, item_bytes)]` list from
`gnstore.get_page_records()` (already draw-ordered); it decodes and renders in
that order. The CLI entry point is `python -m gnreader render-records`.
`render_records_result()` / `render_page_result()` return `RenderResult` with
`path`, `warnings` and `stats`; the plain functions return the path for
backward compatibility.

## 9. Scale and quality runs

- `out/scale-run.md` / `.json`: 124 notebook subprocesses (one process per
  backup, 240 s timeout, 4 concurrent), **503 live pages**, **0 page errors**,
  **0 notebook errors**, **18 warnings**, slowest page **2.42 s**, max worker
  RSS **479 MB**, wall **37.8 s**. Deleted pages are omitted (30). Warnings are
  13 nexus layout-approximation notices, 4 missing image attachments in the
  backup (export gap) and 1 uncommon fill geometry.
- `out/quality.md` / `.json`: 30 sampled pages across 24 notebooks, including
  whiteboards, imported-PDF pages, text-heavy pages and image/fill pages.
  Comparable handwriting pages range from IoU 0.52 to 0.91. Typed nexus pages
  now score **0.1966** (Notebook F) and **0.248** (Notebook J) IoU with
  exact text; remaining differences are font/format fidelity. Pages whose
  PDF background differs are marked **N/A** with a reason. `Notebook I
  p1` is a confirmed PDF/backup page-content mismatch, not a render failure.
- Divider lines: on Notebook C p5 the two dark vertical column separators are
  **not present in `notes/<uuid>`, the 29AC template PDF, or the page template
  SVG** (which contains only the 16 pt grid). They are PDF-export-only vector
  content; no item kind or stored background can reproduce them.

## 10. Unknowns / edge cases

- Whiteboard notebooks (`Notebook H`): one notes file holds
  several canvas/page variants (payload fields 1 and 7); current decoder only
  handles `f7` and shows blank canvas (0x0) because there is no page event.
- `f13`/`f9`/`f1` are rendered; `f11` math remains attachment/placeholder
  only (all corpus math items deleted). `f20` sticky paper/body/author is
  rendered on the page-4 private-corpus item. `f3/f4` and `f6` remain
  unsupported; `f22` elbow/hobby connectors are rendered; see
  `capabilities.json`.
- `document.info.pb` is empty (`08 04`); notebook title/folder only come from
  the path/name.
- Page delete/duplicate and folder hierarchy are in `projection.sqlite`
  (store lane), not decoded here.
- The app's `NotesItems.Deserializer` Swift type has fields
  `rootNode, formatVersionPtr, commandTypePtr, moveToPtr, quadCurveToPtr,
  outline...Ptr, commandTypes, moveTos, quadCurveTos, outline...` confirming
  the columnar layout above (`docs/schema/swift-types.txt`).
