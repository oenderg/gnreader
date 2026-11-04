# Engineering notes

This is a condensed, sanitized record of the reverse-engineering work behind
`gnreader`. Private notebook names, UUIDs, paths and page content are omitted.

## Status

- Version **0.3.3**, `gnreader.capabilities()` + shipped `capabilities.json`.
- Reads `.goodnotes` backups and live page record streams. No write support.
- Pencil, constant-width and variable-width strokes decoded and rendered.
- Exact pencil outline polygons from item fields 5–11; centreline fallback.
- Item translation field 6, deletion tombstones and `NotesZIndex` z-order applied.
- Page templates (squared/ruled/dotted) and imported PDF/image backgrounds.
- Typed text through `gntext`: shape rich text, text boxes, text fragments,
  sticky notes and nexus/Yjs documents. Nexus layout is approximate.
- Images, common fill shapes, math attachments and sticky notes are partial or
  supported as listed in `capabilities.json`.
- Translucent highlighters use a per-channel multiply:
  `factor = 1 - alpha*(1-rgb)`, one flat mask per stroke, applied at the
  highlighter's z position. Ink below is multiplied; ink above is unchanged.
- 23 tests pass with the private corpus present; synthetic tests pass without it.
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
- Legacy `f3`, `f4`, `f6`, `f22` item kinds are unsupported.
- Classic text boxes (`f5`, `f8`, `f12`) have no corpus samples.
- Nexus/Yjs layout is approximate; document headers and font metrics are not
  byte-perfect.
- Shape fill/stroke is not rendered; math and sticky notes are partial.
- Whiteboard notebooks have no page canvas and render blank.
- Eraser masks are decoded but not applied (no observed samples).
- Missing built-in/sticker backgrounds render white.
- Highlighter bar ends are square.

## Tests and scale

`pytest` runs synthetic tests for the LZ4 container, varint streams, signature
grammar and highlighter math without private data. Tests that need notebooks
use `GNREADER_TEST_CORPUS` (default `tests/corpus`, not shipped) and skip when
the files are absent. The scale/quality reports are not shipped; the numbers
above are from the private development corpus.
