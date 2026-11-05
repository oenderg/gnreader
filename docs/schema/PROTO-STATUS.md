# PROTO-STATUS — what is recovered and how sure we are

Source: Goodnotes 7.1.30 (arm64 slice of `/Applications/Goodnotes.app/Contents/MacOS/Goodnotes`).
Deliverable: `proto/schema.proto` (package `goodnotes.schema`), 1,034 messages / 162 enums / 4,848 fields.
Method: see `HOWTO.md` (the helper scripts are not shipped).

## How the two halves are recovered
- **Names + numbers**: SwiftProtobuf compiles every message's `_NameMap` into a **bytecode blob in
  `__TEXT.__const`** (1,116 blobs, `out/namemaps.json`). Format decoded and verified against data:
  markers `0x01` (+1), `0x02 <6-bit-group varint delta>`, `0x03` (+1, snake_case name), `0x04 <delta>`, then a
  NUL-terminated name. `scripts/extract_namemaps.py` + `scripts/build_final.py`.
- **Types**: Swift field descriptors (`__swift5_fieldmd`, symbolic mangling resolution), then **refined from the
  wire types actually observed in 512k real records** (`scripts/infer_types.py`).

Result: **907 of 907 non-empty messages have all field numbers** (902 start at 1, max 315, no duplicates);
128 messages are empty (no fields). 86 field types were corrected by corpus evidence (e.g.
`LWWTimestamp.counter/random` uint64, `replicaId` uint64, `logicalTimestamp` int64, `PageRange` int32).

## Corpus validation — 0 problems
`scripts/validate_all.py` walks the raw wire bytes with the generated descriptors and reports any field number
the schema does not know, wire-type mismatch, or decode error, recursively.

| corpus | records | message types | unknown | mismatch | errors |
|---|---|---|---|---|---|
| all 124 `.goodnotes` backups (read straight from the ZIPs) | 290,843 | 90 | **0** | **0** | **0** |
| live store, 712 pages via `gnstore` | 221,532 | 42 | **0** | **0** | **0** |
| **total** | **512,375** | 90 | **0** | **0** | **0** |

Breakdown: 136,455 `AnyNotesItemDescriptor` + 136,453 `AnyNotesItem` in backups (110,766 + 110,766 in the
store), 16,637 event records, 533 page-list records, 440 search-index records, 325 attachment-descriptor
records, 124 `schema.pb`, 124 `document.info.pb`. Full per-message table: `CORPUS.md`.

Top message rows (full list in `CORPUS.md`):

| message | backup | store | unknown | mismatch | errors |
|---|---:|---:|---:|---:|---:|
| LWWTimestamp | 277,093 | 221,532 | 0 | 0 | 0 |
| Point | 137,945 | 111,691 | 0 | 0 | 0 |
| Color | 136,608 | 110,765 | 0 | 0 | 0 |
| AnyNotesItemDescriptor | 136,455 | 110,766 | 0 | 0 | 0 |
| AnyNotesItem (oneof container) | 136,453 | 110,766 | 0 | 0 | 0 |
| EventDescriptor | 16,637 | 0 | 0 | 0 | 0 |
| NotesItemGroupDescriptor | 533 | 0 | 0 | 0 | 0 |
| SearchIndexDescriptor | 440 | 0 | 0 | 0 | 0 |
| AttachmentDescriptor | 325 | 0 | 0 | 0 | 0 |

Messages that never occur in a file we decoded remain "from binary only" (numbers+names from `_NameMap`,
types from Swift metadata) — they are simply absent from `CORPUS.md`.

## Enums
14 of 162 enums occur in the corpus. 13 have all observed values inside `0 .. cases-1`, consistent with the
sequential values assigned in the .proto (e.g. `SchemaVersion` observed 24…35 with 36 cases; orientation 1 of 3;
image format 1/3/5 of 6). One anomaly:

- `Schema.BlockAttributesRaw.ListStyle.TypeEnum` (2 cases) is observed with value **-1**
  (0xFFFFFFFFFFFFFFFF) — the sequential mapping is wrong for it; the negative sentinel is not represented.
  Details per enum: `out/enum-observations.json` (`scripts/enum_report.py`).

Exact case→number mapping is still not proven for any enum (Swift metadata has no raw values); the .proto
values are the declaration order.

## tpl stroke signatures
11 signature strings found and xref'd to their classes — see `TPL-SIGNATURES.md`. Confirmed against the archive
lane's grammar for PPencilStroke (129,956 corpus blobs), PVariableWidthStroke (4,086), PConstantWidthStroke
(2,174) and PDynamicWidthStroke (6). **Contradiction:** three GN4/legacy signatures use token `i`, which
`gnreader` does not parse; and the signature stored in a payload is the binary string **minus its leading `v`**.

## Item payload corrections (round 3)
Two oneof payload types were wrong in the metadata-derived schema and are now overridden in
`scripts/gen_proto2.py`:
- `AnyNotesItem.stickyNote` (20): real payload is `SerializedStickyNotesItem` (field 1 UUID, field 31
  `RichTextRaw`), not `StickyNote`. Verified on one item (`Notebook H`); `ITEMS.md` §5.
- `TextNotesItemContent.text` and `Comment_Content.text`: byte chunks, not nested `TextNotesItem`s (the old type
  was a name-based guess; no corpus sample).
The corrections add `SerializedStickyNotesItem` to the exercised message set (90 types in the backup run) while
keeping 0 unknown fields / 0 mismatches. Item-kind census, rich-text layout and text extraction: `ITEMS.md`,
`gntext/`, `scripts/text_census.py`; image enum values cross-checked against attachment magic
(`scripts/image_formats.py`: 0=png, 1=jpeg, 3=pdf, 5=gif).

## Confidence levels
| level | meaning |
|---|---|
| **verified** | decoded real corpus records with 0 unknown fields / 0 wire mismatches (all rows in `CORPUS.md`) |
| **binary only** | numbers+names from `_NameMap`, types from Swift metadata, not exercised by any file decoded |
| **inferred** | type changed by `infer_types.py` from observed wire types (86 fields), marked in `out/final.json` |
| **guess** | enum numeric values; oneof member types guessed by name (183 `// GUESS member type`); 565 fields whose Swift type is an unresolved generic `Optional<T>` — 117 are still plain `bytes` in the .proto with `// GUESS type` |

## Known gaps
- Enum raw values (see above). The one negative value (`BlockAttributesRaw.ListStyle.TypeEnum` = -1) is an
  **unset sentinel** used across text block attributes, not a case; observed real values are 0/1, consistent with
  declaration order. Text/shape enum observations: `out/text-enums.json`.
- Oneof member **types** come from names (`image`→`ImageNotesItem`, `penStroke2`→`PenStrokeNotesItemV2`, …);
  member *numbers/names* are exact. 182 members are still marked `// GUESS member type`;
  `AnyNotesItem.legacyVariableWidthLine2` stays `bytes`.
- 565 fields with generic `Optional<T>` Swift types: those never seen in the corpus stay `bytes`.
- `Schema.GetEventsResponseVersions`: hand-matched name-map blob at 0xb7c6a81 → fields 2 (`tillVersion`) and
  4 (`has_more_data`); the unusual start (first number 2) is exact from the blob but not exercised by data.
- The `bv41` stroke geometry is **not** protobuf (LZ4 + `tpl` signature, solved by the archive lane); schema lane
  only carries `PenStrokeNotesItemV2.data` as bytes.
