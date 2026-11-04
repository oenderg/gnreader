# Item kinds: census + field semantics

Scope: the 124 `.goodnotes` backups (533 pages) and the live store (162 notebooks listed, 712 pages) as of
2026-10-26. Counts come from `scripts/text_census.py` (`out/text-census.json`); value ranges from
`scripts/item_semantics.py` (`out/item-semantics.json`); image formats from `scripts/image_formats.py`
(`out/image-formats.json`). No user text is reproduced in this file.

Legend: **verified** = shown by real records (field present, value range, or cross-check); **GUESS** = inferred
from schema/metadata only, no direct sample.

## Ranked census (all `AnyNotesItem` oneof kinds)

| kind | oneof # | backup records | backup notebooks | store records | store notebooks | total |
|---|---:|---:|---:|---:|---:|---:|
| penStroke2 | 7 | 136222 | 79 | 110599 | 84 | 246821 |
| image | 1 | 118 | 29 | 86 | 31 | 204 |
| shape | 21 | 30 | 13 | 50 | 13 | 80 |
| fillShape | 9 | 62 | 18 | 16 | 6 | 78 |
| penStroke (legacy) | 2 | 70 | 31 | 0 | 0 | 70 |
| nexus | 13 | 15 | 14 | 15 | 14 | 30 |
| legacyVariableWidthLine2 | 6 | 15 | 9 | 0 | 0 | 15 |
| math | 11 | 6 | 3 | 0 | 0 | 6 |
| stickyNote | 20 | 1 | 1 | 0 | 0 | 1 |

Text-bearing kinds (extracted, not OCR): `shape` text is present on all 30 backup / 50 store shapes;
`nexus` documents are 15 backup / 15 store text documents. `stickyNote` has one empty item. There are **zero**
`text`, `text2` and `textFragment` items in either corpus.

| text kind | backup items | chars | notebooks | store items | chars | notebooks |
|---|---:|---:|---:|---:|---:|---:|
| shapeText | 30 | 4481 | 13 | 50 | 4256 | 13 |
| nexusText | 15 (13 non-empty) | 5123 | 14 | 15 (13 non-empty) | 5123 | 14 |
| stickyNote | 1 (empty) | 0 | 1 | 0 | 0 | 0 |

Reproduce: `python scripts/text_census.py` (backups + store), `--backups-only` for the fast pass.
`scripts/test_gntext.py --store` checks extraction on synthetic + real records.

## 1. shape — `SerializedShapeNotesItem` (oneof 21)

A vector shape (shape/textbox) that may carry rich text. The kind enum distinguishes `shape = 1` from
`textbox = 2`; every shape in this corpus is `kind = 1` but 30/30 backup and 50/50 store shapes carry a `text`
payload, so a "shape" is also the normal text-label container. Whether a GN6 text box is a `kind = 2` shape is a
GUESS (no sample); classic text boxes are separate oneof members (section 7, also no samples).

| # | field | type | observed | meaning | confidence |
|---:|---|---|---|---|---|
| 1 | id | string | 30 / 50 | stable item UUID | verified |
| 2 | schemaVersion | enum | 30 / 50 | item schema version | verified |
| 3 | version | LWWTimestamp | 30 / 50 | last-writer-wins version `{counter=1, random=2}` | verified |
| 4 | deleted | bool | 5 / 0 | tombstone | verified |
| 5 | tag | string | 0 | user tag (none in corpus) | verified absent |
| 6 | parent | NotesItemReference | 0 | parent item (`notesItemId`), grouping | verified absent |
| 7 | position | NotesZIndex | 30 / 50 | z/creation key, see below | verified |
| 8 | elementID | string | 0 | element identity for imports | verified absent |
| 9 | kind | enum `Kind` | 30 / 50 | `shape=1`, `textbox=2`; all corpus = 1 | values from enum metadata; 1 verified |
| 10 | lockStatus | enum | 0 | unlocked/locked | — |
| 20 | transform | SerializedShapeTransform | 30 / 50 | `translation{x,y}` = shape origin, `rotation` (rad), `scale`; all rotations 0, scale 1 | translation verified (equals bbox origin); rotation/scale units GUESS |
| 21 | sizingStrategy | oneof | 30 / 50 | `fixedWidthFixedHeight=1`, `fixedWidthAutoHeight=2` (16), `autoWidthAutoHeight=3` (14) | variants verified |
| 22 | geometry | oneof | 30 / 50 | `roundedRectangle` (30, empty bytes = default radius), `ellipse{center,radius,rotation}`, `hobbyPath` | variant verified; payload details GUESS |
| 30 | fill | Fill | 30 / 50 | `shading.color` RGBA floats 0..1; sample `[1,1,1,0]` = transparent | verified |
| 31 | stroke | SerializedShapeStroke | 30 / 50 | `width` (0 in all 30 = no border, GUESS), `pattern` (`solid`/`dashed`), `shading.color` | width observed |
| 32 | text | RichTextRaw | 30 / 50 | rich-text body, see below | verified |
| 33 | shadow | Shadow | 30 / 50 | color/radius/offset; radius 0 in all 30 | observed |

**Rendering geometry**: the text box occupies `bbox = {x: transform.translation.x, y: transform.translation.y,
w: text.size.width, h: text.size.height}` (verified: values match in all sampled items). `richText.padding` is
the inset; `sizingStrategy` tells whether the box auto-grows.

**`RichTextRaw` payload** (the same message is used by sticky notes):

```
RichTextRaw
  1 raw       RawData { 2 raw = bv4 frame, 4 version }
  2 size      Size { width, height }        # text box size
  5 defaultAttributes  InlineAttributesRaw
 10 padding     floats (top/right/bottom/left)
 11 lineHeight  TextLineHeight
```

`raw.raw` is a **bv4/LZ4 frame** whose plaintext is itself a protobuf paragraph tree (not a `Schema.*`
message; field numbers cross-checked against 30 real items):

```
repeated 1 paragraph:
  1 text        UTF-8 bytes
  2 inline:  1 strike, 2 underline, 3 color{RGBA}, 4 link, 5 highlight{RGBA},
             30 fontFamily, 40 fontSize, 50 italic, 60 fontWeight, 70 fontWidth
  3 block:   1 blockType (paragraph | heading{1 level}), 2 lineHeight{type,lineHeight,lineSpacing,verticalAlign},
             3 listStyle{type, startingItemNumber}, 4 textAlignment, 5/6 paragraph spacing, 7 indentationLevel
```

Paragraphs are joined with `\n`. Negative/-1 values are "unset" sentinels (observed `-404` for font size/weight,
`0xFFFF...` for line height/list type). Fonts observed: Courier (274 paragraph runs), Courier New (49), Times (5),
Palatino (2), Georgia (1). Largest shape text: 741 chars; most paragraphs in one shape: 91.

Enum evidence from the 30 shape texts (396 paragraphs, `out/text-enums.json`): `blockType` = paragraph on all 396
(no heading); `listStyle.type` = **1** on 24 paragraphs (unset 372) — with enum cases `bulleted=0, numbered=1`
this means 24 numbered items; `textAlignment` = 1 on 25 (unset 371, mapping of 1 = center is GUESS);
`lineHeight.type` unset everywhere; inline runs: 1 link, 8 italic, 0 underline/strike/highlight. The `-1`
list-style value seen on the corpus's empty sticky is therefore an **unset sentinel**, not a counter-example to
the enum numbering.

## 2. image — `ImageNotesItem` (oneof 1)

| # | field | type | observed | meaning | confidence |
|---:|---|---|---|---|---|
| 1 | id | string | 118 / 86 | item UUID | verified |
| 2 | rect | Rect | 118 / 86 | axis-aligned placement: origin top-left, size | verified |
| 3 | actualRect | RotatedRect | 118 / 86 | `center`, `size`, `angle`; center == rect center (≤6e-5), size == rect size for 107/118 | geometry verified; angle unit GUESS (radians: one item -1.183 ≈ -67.8°) |
| 4 | attachmentId | string | 118 / 86 | blob id → `attachments/<id>` inside the notebook zip; 114/118 present, 4 missing even though the item is not deleted (export gap) | verified |
| 5 | position | NotesZIndex | 118 / 86 | z/creation key | verified |
| 6 | format | enum `Format` | 49 / 34 | cross-checked against file magic: 0=png (65), 1=jpeg (29), 3=pdf (7), 5=gif (13) | enum 0/1/3/5 verified; 2=jpeg2000, 4=heic unused |
| 14 | deleted | bool | 40 / 2 | tombstone | verified |
| 18 | schemaVersion | enum | 118 / 86 | item schema version | verified |
| 21 | lockStatus | enum | 8 / 8 | unlocked/locked | values observed |
| 19/20 | mathGraphId / mathGraphData | string/bytes | 2 / 0 | link to a math graph | observed |

`rect` is the bounding box used for layout; `actualRect` carries the same box plus rotation. Rendering: draw the
attachment (or PDF page) into `rect`, rotated by `actualRect.angle` about `actualRect.center`.

## 3. fillShape — `FillShapeNotesItem` (oneof 9) — lasso fill / group

| # | field | type | observed | meaning | confidence |
|---:|---|---|---|---|---|
| 1 | id | string | 62 / 16 | item UUID | verified |
| 2 | rect | Rect | 62 / 16 | filled region bounds | verified |
| 3 | position | NotesZIndex | 62 / 16 | z/creation key | verified |
| 4 | shapes | repeated PenStrokeShape | 0 for 54, 1 for 8 | explicit outline geometry when present | observed |
| 5 | originalStrokeIds | repeated string | 1 id: 54, 2 ids: 7, 6 ids: 1 | **group membership**: source ink strokes this fill replaces | verified counts; grouping semantics GUESS |
| 6 | translation | Point | 62 / 16 | offset applied to the source stroke coordinates | observed |
| 7 | color | Color | 62 / 16 | fill RGBA; sample `[0,0.141,0.278,0.1]` | verified |
| 14 | deleted | bool | 54 / 8 | tombstone — most lasso fills were undone/deleted | verified |
| 15 | version | LWWTimestamp | 62 / 16 | LWW version | verified |
| 18 | schemaVersion | enum | 62 / 16 | item schema version | verified |

This is the only "lasso group" kind: there is no separate group item, the group is `originalStrokeIds`.
A renderer should draw the fill (color/rect) and hide/skip the source strokes while the fill item exists.

## 4. math — `MathNotesItem` (oneof 11)

| # | field | type | observed | meaning | confidence |
|---:|---|---|---|---|---|
| 1 | id | string | 6 | item UUID | verified |
| 2 | rect | Rect | 6 | placement | verified |
| 3 | actualRect | RotatedRect | 6 | rotated placement | verified |
| 4 | attachmentId | string | 6 | rendered image → `attachments/<id>` (all 6 present) | verified |
| 5 | position | NotesZIndex | 1 | z/creation key | observed |
| 6 | deleted | bool | 6 | tombstone — all 6 corpus math items are deleted | verified |
| 7 | version | LWWTimestamp | 6 | LWW version | verified |
| 9 | dataString | string | 6 | formula source, 51–166 chars; first char `{` (4), letter (1), `\` (1) → LaTeX/MathJSON-style source (GUESS) | format GUESS |
| 11 | originalColoredPenStroke | repeated ColoredPenStrokeData | 6 | 10–71 ink strokes the formula was recognized from | observed: `data` bytes + color + type + translation |
| 26 | schemaVersion | enum | 6 | item schema version | verified |

All corpus math items are deleted, so rendered output must be taken from `attachmentId` (image) rather than
re-running recognition.

## 5. stickyNote — `SerializedStickyNotesItem` (oneof 20) — **schema correction**

The recovered Swift metadata typed `AnyNotesItem.stickyNote` as `StickyNote` (position/color/size/expanded),
but a real item's bytes parse as **`SerializedStickyNotesItem`**: field 1 is a UUID string, field 31 is
`RichTextRaw`. `gen_proto2.py` now overrides the member type (`ONEOF_TYPE_OVERRIDES`), and validation of the
record is clean.

| # | field | type | observed | meaning |
|---:|---|---|---|---|
| 1 | id | string | 1 | item UUID |
| 2 | schemaVersion | enum | 1 | item schema version |
| 3 | version | LWWTimestamp | 1 | LWW version |
| 4 | deleted | bool | 1 | tombstone (the one corpus sticky is deleted) |
| 6 | parent | NotesItemReference | 0 | parent group |
| 7 | position | NotesZIndex | 1 | z/creation key |
| 20 | transform | SerializedShapeTransform | 1 | translation = sticky origin |
| 21 | sizingStrategy | oneof | 1 | fixed/auto sizing |
| 30 | color | Color | 1 | sticky paper color |
| 31 | richText | RichTextRaw | 1 | body (empty in the corpus item) |
| 32/33 | authorId / authorName | string | 1 | author of the note (present) |
| 40/41 | expanded / showAuthor | bool | 1 | UI state: expanded=true, showAuthor=false |

## 6. nexus — `NexusNotesItem` (oneof 13) — Goodnotes 6 text documents

| # | field | type | observed | meaning |
|---:|---|---|---|---|
| 1 | id | string | 15 / 15 | item UUID |
| 2 | schemaVersion | enum | 15 / 15 | item schema version |
| 3 | version | LWWTimestamp | 15 / 15 | LWW version |
| 4 | deleted | bool | 0 | tombstone |
| 10 | yjsData | bytes | 15 / 15 | Yjs v1 update for the document (133–13490 bytes) |
| 11 | yjsSnapshot | bytes | 15 / 15 | Yjs state vector (16–388 bytes; GUESS: snapshot, not applied) |
| 12 | clientId | bytes | 0 | collaboration client id (empty) |
| 13 | thumbnailData | string | 0 | preview (empty) |

Decoding: apply `yjsData` to a Yjs doc; root type **`content`** is an `XmlFragment` whose children are
`XmlText` runs (no paragraph elements in this corpus). Concatenate the runs in order — exact text, no OCR.
`gntext` uses the optional `pycrdt` binding for this; without it the item is returned with `text=None` and an
`error` string. 13/15 documents have non-empty text (5123 chars total).

## 7. Text boxes (schema-complete, zero corpus samples)

| member | message | fields that matter | extraction |
|---|---|---|---|
| 5 text | `TextNotesItem` | `rect`, `textRect`, `position`, `transform`, `content[]` = `{1 text bytes, 2 tagOpen, 3 tagClose, 4 fontOpen, 5 fontClose, 6 colorOpen, 7 colorClose}`, border/background, padding, alignment, glyphRects | concatenate `content[].text` (newline-on-tagClose is GUESS) |
| 8 text2 | `TextNotesItemV2` | `rect`, `textRect`, `textRectTransform`, `rtfData`, border/background, padding, preset, lineHeight, glyphRects | RTF → text (`rtf_to_text`); the field name says RTF, no sample (GUESS) |
| 12 textFragment | `TextFragmentNotesItem` | `textSize`, `rtfData`, `position`, `textPresetId`, `indentationLevel` | RTF → text (same GUESS) |

`TextNotesItemContent.text` was previously guessed as a nested `TextNotesItem` because of its name; it is a byte
chunk (siblings `tagOpen`/`fontOpen`/... are bytes). Fixed via `ONEOF_FIELD_OVERRIDES`; no sample exists, so the
chunk layout remains GUESS.

Synthetic coverage: `scripts/test_gntext.py` builds one of each and checks text, bbox and size.

## 8. Document-level metadata (`gntext.extract_metadata`)

Read from a backup's `index.events.pb` (event-sourced; last rename wins):

| metadata | source | corpus |
|---|---|---|
| document title | `DocumentCreatedEvent.name` / `DocumentRenamedEvent.name` (`LWWString.value`), last event wins | 124/124 notebooks; 108/124 equal the ZIP filename (renames can postdate an export; event `timestamp` is epoch-ms) |
| page labels | `PageLabelsDidSetEvent.labels` (`LWWStrings.value`) | 1 event |
| outlines/bookmarks | `PageOutlineCreatedEvent{pageID, outlineID, parentOutlineID, title, position, rect, outlineType}` | 10 events in 2 notebooks (3 + 7) |
| hyperlinks | inline attribute `link` (field 4) on rich-text paragraphs | **1** value in 396 shape-text paragraphs (`gntext` returns it in `style.link`) |
| tags | `tag` string on shape/image/fillShape/nexus/sticky items | 0 in corpus |
| audio transcripts | `TranscriptionCreatedEvent.text` (`LWWString`) | 4 events; `gntext` exposes lengths only (not text) |

PDF annotations: the schema has only annotation **geometry** (`AnnotationRect`, `AnnotationRectList`), no
annotation text. PDF text/forms live inside the imported `attachments/<id>` PDF and are not part of this format.

## Geometry and ordering conventions (for renderers)

- All coordinates are page points with origin at the page's top-left (GUESS on origin; consistent with values).
- `bbox` in `gntext` output is `{x, y, w, h}` at the item's origin; shape/sticky derive it from
  `transform.translation` + `richText.size` (verified), text boxes from `rect`/`textRect`.
- `z` is `NotesZIndex.elements = [[session, batchId, batchIndex], ...]`; the store lane sorts items by this key
  and documents it as draw order bottom → top (verified by `gnstore.get_page_records`). `gntext` also returns
  `draw_index` = input order, which is draw order when records come from `gnstore.get_page_records`.
- `deleted=true` items are tombstones: skip them unless reconstructing history.

## Reproduce

```bash
cd docs/schema
python scripts/text_census.py                 # out/text-census.json (backups + store)
python scripts/item_semantics.py              # out/item-semantics.json
python scripts/image_formats.py               # out/image-formats.json
python scripts/test_gntext.py --store         # extraction self-test
```
