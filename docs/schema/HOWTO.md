# HOWTO — re-extract the Goodnotes protobuf schema from the app binary

Everything here is read-only on the app and on user data. These notes describe
the development method; the scripts, venv and raw artifacts are **not shipped**
with the package. Re-run after a Goodnotes update with the private development
checkout:

```bash
scripts/regen_all.sh            # ~46 s, no Ghidra, no Hopper
scripts/regen_all.sh --with-store   # also validates the live-store pages
```
It performs steps 1–8 below and ends with a validation pass. Intermediates live
under `docs/schema/`.

## 0. Prerequisites
- REA: `REA doctor` → node 22 + Ghidra 12.1.4 OK; **Hopper is missing** (not needed).
- venv `development venv`: `protobuf`, `grpcio-tools` (provides `protoc`), `blackboxprotobuf`.
- Store phase only: `development venv` with `gnstore` and a snapshot at `$GNREADER_HOME/snapshot`.

## 1. Thin slice
```bash
lipo -thin arm64 -output raw/Goodnotes.arm64 /Applications/Goodnotes.app/Contents/MacOS/Goodnotes
```
The binary is stripped except exports (18k symbols, no protobuf), but all Swift metadata sections survive:
`__swift5_types` @0x10c60c328, `__swift5_proto` @0x10c5c3e30, `__swift5_fieldmd` @0x10c3aef4c,
`__swift5_reflstr` @0x10bfb6530, `__const` @0x10b6ec590.

REA commands used and their results:
- `REA doctor` → environment table.
- `REA inspect-macho /Applications/Goodnotes.app --format json` → segment/section table, stripped exports.
- `REA decompile <file> <addr> --target-format raw-binary --architecture arm64 --entry-point <addr>` →
  clean pseudocode from a byte window in ~10 s (`scripts/decomp.sh`). **Full-binary Ghidra import fails**
  in `ObjcTypeMetadataAnalyzer` (256 MB slice), so decompilation is not part of the pipeline.
- `REA inspect-sqlite-database` → fails under the wrapper's Node 22 (`DatabaseSync.limits` needs 24+);
  `sqlite3` CLI used instead.

## 2. Swift types + messages (`scripts/swift_meta.py`, `extract_schema.py`, `dump_enums.py`)
- 34,935 nominal types from `__swift5_types`; SwiftProtobuf messages are recognised by the stored
  `unknownFields` property → **1,034 messages in module `Schema`** (plus 54 P2P, 60 SwiftProtobuf's own, …).
- Many-field messages keep fields in a nested `_StorageClass` (properties prefixed `_`).
- Symbolic manglings in field descriptors: `0x01` + rel32 → context descriptor at `pos+1+rel`;
  `0x02` + rel32 → pointer slot; take `& 0xFFFFFFFFF` for local rebases (external/generic instantiations
  resolve to `context`, which step 6 fixes from data).

## 3. Field numbers: `_NameMap` bytecode (`scripts/extract_namemaps.py`)
SwiftProtobuf emits each message's name map as a static bytecode string in `__TEXT.__const`:
```
entry := 0x01 <name> 0x00                  number = previous + 1
       | 0x02 <delta> <name> 0x00          number = previous + delta (6-bit groups, bit6 = continue)
       | 0x03 <name> 0x00                  previous + 1, proto name is snake_case
       | 0x04 <delta> <name> 0x00          as 0x02 but snake_case
blob ends at 0x00 padding; first number starts at 1
```
Example `Schema.PenStrokeNotesItemV2` (0xb7e5821): `id=1 data=2 type=3 color=4 blendMode=5 translation=6
position=7 shape=9 elementId=10 lineStyle=11 deleted=14 version=15 erasers=16 tag=17 ignoredInAIChecks=18
logical_group=19 tapeStyle=20 schemaVersion=21 mathSuggestionId=22 parent=100`.
Cross-checks: `EventDescriptor` (documentCreated=30, pageCreated=54 — exactly the tags in `index.events.pb`),
and every message decoded in the corpus.

## 4. Match blobs to messages (`scripts/build_final.py`)
A blob belongs to the message whose normalised field-name set (fields + oneof cases) equals the blob's names.
907/907 non-empty messages matched; `GetEventsResponseVersions` is hand-matched (blob 0xb7c6a81).
Output `out/final.json`.

## 5. Bootstrap proto
`scripts/gen_proto2.py out/final.json proto/schema.proto goodnotes.schema Schema out/enums.json`, then compile
with `python -m grpc_tools.protoc -I proto --python_out=out/py proto/schema.proto`.
This bootstrap schema has metadata types only; it is what the validation walks.

## 6. Corpus validation + type inference
```bash
python scripts/validate_all.py                        # 124 backups, ~19 s
python scripts/validate_all.py --store --skip-backups # 712 live pages, ~7 min (SST reader)
python scripts/infer_types.py                         # 86 field types corrected from wire evidence
```
`validate_all.py` walks raw wire bytes with the descriptors (never the object API), reports unknown field
numbers, wire mismatches and errors per message, and writes `out/validate-*.json` (+`.md`). It reads backups
straight from the ZIPs and writes nothing. Store pages come from `scripts/dump_store_pages.py` (venv313 +
gnstore); `--store-file` replays a previous dump.
`infer_types.py` maps observed wire types to proto types (`varint`→bool/uint64/int64/int32 by name+values,
`fixed32`→float, `fixed64`→double, `len`→string/bytes/message) and never rewrites enums or repeated/scalar
kinds. Fields never observed keep `bytes` + `// GUESS type`.

## 7. Final proto + validation
Regenerate with `gen_proto2.py` again and recompile; `regen_all.sh` then runs `validate_all.py` once more and
prints the totals. Current: 512,375 records, 0 unknown, 0 mismatch, 0 errors.

## 8. Extras
- `scripts/enum_report.py` → observed enum values vs assigned numbers (`out/enum-observations.json`).
- `scripts/find_xrefs.py` + `scripts/xref_context.py` → adrp/add code xrefs and the `literal pool for:` strings
  around them (used for `TPL-SIGNATURES.md`; one otool pass over the 178 MB `__text`).
- `scripts/check.py` / `check_page.py` → single-file checks (message name / page stream classification).

## 9. Text extraction (`gntext/`)
`gntext` is a self-contained pure-Python package (`schema_pb2.py` is copied in by `regen_all.sh`) that reads
typed text from a backup page stream, a page-bytes file, or `gnstore.get_page_records()` output:

```python
from gntext import extract_text
items = extract_text(records)   # bytes | list[bytes] | list[(descriptor, item)]
# [{kind, text, paragraphs, bbox, z, draw_index, style, ...}, ...]
```

```bash
python -m gntext "Notebook.goodnotes" --summary   # counts per page, no text
python -m gntext page-records.dat --indent 1             # JSON for one page stream
python -m gntext --store <page_id>                # live store page via gnstore
```

Kinds: `shapeText`, `stickyNote`, `textBox` (v1), `textBoxV2`, `textFragment`, `nexusText`.
Rich text (shapes/sticky) is a bv4/LZ4-framed protobuf paragraph tree; text boxes v2/fragments use Apple RTF;
nexus text documents are Yjs (`pycrdt` optional dependency). LZ4 is implemented in pure Python
(`gntext/_lz4.py`) and checked against `lz4.block`.

Semantics/coverage: `ITEMS.md`. Corpus census: `scripts/text_census.py` → `out/text-census.json`.
Self-test: `scripts/test_gntext.py --store` (synthetic + real records, structure only).

### Item-payload corrections found by data
`gen_proto2.py` has two override tables: `ONEOF_TYPE_OVERRIDES` (an `AnyNotesItem.stickyNote` payload is a
`SerializedStickyNotesItem`, verified from a real item) and `ONEOF_FIELD_OVERRIDES`
(`TextNotesItemContent.text` / `Comment_Content.text` are byte chunks, not nested `TextNotesItem`s).
Recompile after changing them (`regen_all.sh`).

## Notes
- Regeneration is deterministic: the validation pass always runs against the **bootstrap** schema, so type
  inference cannot feed back into what the walker recurses into.
- Enum raw values are not in the metadata; the .proto uses declaration order (see `PROTO-STATUS.md`).
  Two image enum values are now verified against file magic: 0=png, 1=jpeg, 3=pdf, 5=gif (`out/image-formats.json`).
- Keep `raw/Goodnotes.arm64` (256 MB) only while working; it is a copy, never the app itself.
