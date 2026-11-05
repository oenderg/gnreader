# Engineering notes

This is a condensed, sanitized record of the reverse-engineering work behind
`gnreader`. Private notebook names, UUIDs, paths and page content are omitted.

## Status

- Version **0.3.7**, `gnreader.capabilities()` + shipped `capabilities.json`.
- Reads `.goodnotes` backups and live page record streams. No write support.
- Pencil, constant-width and variable-width strokes decoded and rendered.
- Exact pencil outline polygons from item fields 5–11; centreline fallback.
- Item translation field 6, deletion tombstones and `NotesZIndex` z-order applied.
- Page templates (squared/ruled/dotted) and imported PDF/image backgrounds.
- Typed text through `gntext`: shape rich text, text boxes, text fragments,
  sticky notes and nexus/Yjs documents. Nexus layout is approximate.
- Images, common fill shapes, page-4 sticker/sticky/shape/line/tape/grain
  items are supported as listed in `capabilities.json`; math remains
  attachment/placeholder only. The live renderer draws f1 `ImageItem`s in
  `NotesZIndex` order with an ink-layer flush; a missing or unreadable
  attachment becomes an `image missing` warning.
- Translucent highlighters use a per-channel multiply:
  `factor = 1 - alpha*(1-rgb)`, one flat mask per stroke, applied at the
  highlighter's z position. Ink below is multiplied; ink above is unchanged.
  Auto-straightened bars get round caps from `ConstantWidthStroke` field 6;
  compact scribbles are unioned with per-point discs so no white slivers show.
- Page-4 item groups are rendered and Round-3 calibrated: short-constant
  sticker colour body (orange/glow/green tints), sticky-note paper/body/author
  (4-unit corners), ellipse + elbow/hobby arrows (Hobby curl -0.45/0.35,
  14-unit/34° heads), tape whose fill is the exact 40-polygon outline union
  (including type-6 elliptical arcs) with a thin 1-unit shadow, and a
  smooth-core type-5 grainy pencil.
- 44 tests pass in the private development suite; the package suite is 29
  passed / 18 skipped with the private corpus and 10 passed / 37 skipped in a
  clean checkout without `tests/corpus` (0 failed in both). Private-fixture
  tests skip cleanly when their corpus file is absent.
- Handwriting IoU on three PDF-matched pages: **0.825**, **0.857**, **0.906**.
- Typed nexus pages score **0.1966–0.248** IoU because layout is approximate.
- A 124-backup / 503-live-page scale run completed with 0 errors and 18
  classified warnings; slowest page 2.42 s, max RSS 479 MB.

## Format findings

1. A `.goodnotes` backup is a ZIP: `index.notes.pb`, `index.events.pb`,
   `notes/<page-id>` and optional `attachments/<uuid>`.
2. Page events carry canvas size, page order, background/template and labels.
3. `notes/<page-id>` is a varint-length-delimited protobuf stream. Each record
   is a descriptor or an `AnyNotesItem` payload.
4. Item map: image 1, legacy pen 2, text 5, `penStroke2` 7, text2 8,
   fillShape 9, penEraser 10, math 11, textFragment 12, nexus 13, sticky 20,
   shape 21, line 22.
5. Stroke payloads are `bv41` LZ4 frames around a `tpl` signature + typed
   values; stored `bv4-` blocks are accepted too.
6. Pencil outlines: per-group counts + flat command types; move consumes 2
   floats, cubic 6 floats, arc 5 floats plus clockwise flags.
7. Constant-width strokes: version, thickness, command types, move points and
   quadratic control/end points. Variable-width points carry 5 floats; the
   observed 11-float quad is one pad float plus two 5-float points (GUESS).
8. Colour is an RGBA message; pen and highlighter use the same field.
9. Item field 6 is an `(x, y)` translation. Item field 7 is `NotesZIndex`
   `(session, batchID, batchIndex)`; a higher session is drawn on top.
10. Deleted items are descriptor tombstones; deleted pages are event records.
11. Templates are SVG-like grids over paper `(247,246,233)` with lines
    `(208,210,211)`; squared spacing is 16 pt, ruled 16/25 pt.
12. Typed shape text bbox is `transform.translation + richText.size`; rich text
    is a bv4/LZ4 frame around a protobuf paragraph tree.
13. Nexus text is a Yjs document; `gntext` extracts exact text, while paragraph
    boundaries and document headings remain approximate.
14. Live `ImageItem` (`f1`): f2 is the axis-aligned `Rect`, f3 the
    `RotatedRect` centre/size/angle, f4 the attachment id, f5 `NotesZIndex`,
    f6 format (1 = JPEG in samples), f18 schemaVersion, f21 lockStatus. There
    is no crop/mask/corner-radius field; round photo edges are baked into the
    JPEG. Rotated items use f3's true centre/size and an angle converted from
    radians to degrees; f2 is only the bounding box. The live renderer flushes
    ink before pasting each image so ink above/below keeps z-order.
15. Highlighter shape: `ConstantWidthStroke` payload field 6 is the line cap
    (0=butt, 1=round, 2=square); all 24 highlighters on the frozen shape page
    are 1, and item-level `PenStrokeLineStyle` (f11) is absent. There is no
    separate straightened flag: auto-straightened bars are collinear
    (max deviation 0.00), freehand marks are not (0.68–26.14). Round caps of
    radius `thickness/2` are applied only to collinear paths. Compact marks
    (<35 canvas units) also get a disc at every flattened point, removing the
    white slivers left by Pillow's even-odd polyline fill.

16. Page-4 items: the short constant signature
    `vuA(v)A(S(uu))A(S(uuuu))` is the sticker colour body (no cap/style arrays);
    `f20` sticky note carries translation/sizing/colour/rich text/author;
    `f21` shape carries ellipse fill/stroke; `f22` line carries hobby/elbow
    geometry and arrow style; type-1 `penStroke2` can carry a pattern-tape
    attachment (thickness 18, tile side 36); type-5 pencil carries per-vertex
    grain scalars `a,b` and renders as a procedural grain. Details and the
    per-group calibration are in `FORMAT.md` §5.6.

## Highlighter calibration

Goodnotes multiplies the backdrop by `1 - alpha*(1-rgb)` per channel. This was
confirmed against a real iPad screenshot and PDF exports, not by finding an
explicit blend-mode field. The screenshot is Display P3; after converting it to
sRGB, measured bar/paper factors match the rgba formula within 1.6/255. Raw P3
values are not directly comparable to an sRGB render. Black ink stays black,
purple ink shifts as expected, and overlapping segments of one highlighter do
not stack alpha.

## Known gaps

- No write support.
- Legacy `f3`, `f4`, `f6` item kinds are unsupported.
- Classic text boxes (`f5`, `f8`, `f12`) have no corpus samples.
- Nexus/Yjs layout is approximate; document headers and font metrics are not
  byte-perfect.
- Page-4 arrowhead size/angle and line weight are calibrated approximations;
  tape edge/tile scale and grain noise are calibrated GUESSes. The Hobby
  spline itself reproduces the app's teardrop loop.
- The live renderer does not draw `FillShapeItem` yet; it reports a
  `fill shape` warning instead.
- Whiteboard notebooks have no page canvas and render blank.
- Eraser masks are decoded but not applied (no observed samples).
- Missing built-in/sticker backgrounds render white.
- Highlighter straight bars now have round caps; freehand ends follow their
  stored path. Compact scribbles use a solid disc union.

## Tests and scale

`pytest` runs synthetic tests for the LZ4 container, varint streams, signature
grammar and highlighter math without private data. Tests that need notebooks
use `GNREADER_TEST_CORPUS` (default `tests/corpus`, not shipped) and skip when
the files are absent. The scale/quality reports are not shipped; the numbers
above are from the private development corpus.

## 0.3.8
- Live renderer: an undrawable shape adds a warning instead of aborting the page.
- Shapes: `roundedRectangle` corner radius and `hobbyPath` polygons (triangle, diamond) with per-vertex corner radius.
- Text boxes: shape + text pairing (10 pt padding, fill, text above fill), absent fill alpha = transparent.
- Rich text: gntext entries are runs; per-run size/font/colour/bold/italic/underline/strike, bullet and numbered lists, left/centre/right alignment; default size 24, line height 1.2.
- Pencil grain: pressure (per-vertex c) sets darkness; width stays constant; large irregular grains.
