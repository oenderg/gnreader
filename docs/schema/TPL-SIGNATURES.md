# `tpl` type signatures found in the app binary

Status 2026-10-26. Method: scan `__TEXT.__cstring` of the arm64 slice for signature-shaped ASCII runs
(`[vufidbsSAP(),0-9]` containing `A(`/`S(`), then find code references with `scripts/find_xrefs.py`
(one otool pass, adrp/add pairs) and identify the enclosing code with `scripts/xref_context.py`
(resolves `literal pool for: "…"` annotations around each xref). Raw evidence: `out/sig-xrefs.asm`,
`out/sig-xrefs-wide.asm`, `out/sig-context.asm`.

## Table (11 signatures)

| # | signature string | addr | class / owner | evidence | seen in 136k-blob corpus? |
|---|---|---|---|---|---|
| 1 | `vuA(v)A(S(uu))A(S(uuuu))vA(f)` | 0xabf4600 | **PConstantWidthStroke** (serialize) | xref 0x101f6778c, function also uses `PConstantWidthStroke+deserialize` and `PConstantWidthStroke+Serialize.swift` | yes — data form `uA(v)A(S(uu))A(S(uuuu))vA(f)` ×2,174 |
| 2 | `vuA(v)A(S(uu))A(S(uuuu))` | 0xabf4780 | **PConstantWidthStroke** (deserialize / legacy form) | xref 0x101f680d8, same function family | no |
| 3 | `vuA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)` | 0xabf4800 | **PDynamicWidthStroke** | xref 0x101f6a35c, function uses `PDynamicWidthStroke+Serialize.swift` + “unable to allocate memory to serialize PDynamicWidthStroke” | yes — `uA(v)A(u)…` ×6 |
| 4 | `vS(vvvvvvvv)S(uuuuuu)` | 0xabf4f00 | **PEraserStroke** | xrefs 0x101f6dee0/0x101f6e5a4, window = `PEraserStroke+Serialize.swift` | no (erasers are separate items) |
| 5 | `vuA(v)A(S(uuuuu))A(S(uuuuuuuuuuu))A(S(uu))A(v)A(S(uu))A(S(uuuu))A(u)` | 0xabf4f20 | **PVariableWidthStroke** | xrefs 0x101f6fbdc/0x101f70bc8, window = `PVariableWidthStroke+Serialize.swift` | yes — `uA(v)A(S(uuuuu))…` ×4,086 |
| 6 | `vA(v)A(u)A(u)A(v)A(v)A(u)A(u)A(u)A(u)A(v)` | 0xabf4fc0 | **PPencilStroke** | xrefs 0x101f733dc/0x101f73d48 near `PPencilStroke+Serialize.swift` | yes — data form `A(v)A(u)…` ×129,956 (dominant) |
| 7 | `A(A(S(iiiiii))S(iiu)S(iiu))` | 0xabf51d0 | **PLegacyVariableWidthLine** | xrefs 0x101f85074/0x101f86768, adjacent to `PLegacyVariableWidthLine.swift` | no |
| 8 | `A(S(uu))A(S(uu))A(v)` | 0xabf5220 | **PLegacyVariableWidthLine2** | xrefs 0x101f884b8/0x101f8a218, window = `PLegacyVariableWidthLine2.swift` | no |
| 9 | `A(S(iiiiiiii))` | 0xac9a779 | **GNLine** (GN4 ObjC) | xrefs 0x103d23cec/0x103d23e90, adjacent `GNLine.m`, `-[GNLine encodeWithCoder:]` | no |
| 10 | `A(S(iiiiii))` | 0xac9a88e | **GNQuadraticLine** (GN4 ObjC) | xrefs 0x103d253e4/0x103d25588, adjacent `GNQuadraticLine.m` | no |
| 11 | `A(S(uu))A(v)` | 0xac9a8f6 | **GNQuadraticLine2** (GN4 ObjC) | xrefs 0x103d26294/0x103d26680, window contains `GNQuadraticLine2.m`, `-[GNQuadraticLine2 encodeWithCoder:]` | no |

No other signature-shaped strings exist in the main binary or in `Contents/Frameworks/*` (only two false
positives in CryptoSwift/GNSVG2PDF unrelated to tpl).

## Cross-check against archive lane grammar (`gnreader/bv4.py`)

- Tokens used by the archive parser: `v` u16, `u`/`f` f32, `S(...)`, `A(T)`. **Confirmed** by signatures 1-6, 8.
- **Contradiction / extension**: signatures 7, 9, 10 use `i`, which `parse_signature` rejects
  (`unknown signature token 'i'`). `i` is almost certainly int32 (4 bytes LE) — GUESS, no payload with an
  `i` signature was found in the 136k-blob corpus. `gnreader` should either add `i` or report those items as
  unsupported instead of crashing.
- **Important offset**: the binary strings start with `v` (format version u16) but the signature stored in the
  `bv4` payload omits it — the archive's scan sees `A(v)…`/`uA(v)…` forms, i.e. *binary signature minus the
  leading `v`*. Consistent for all four observed variants; the version is consumed separately.
- The 24-char constant-width form (#2, no `vA(f)`) is declared in the binary but absent from the corpus;
  likely a legacy deserialize layout. `PEraserStroke`, both legacy lines and the three GN4 classes are also
  absent from the backup corpus today (old notebooks may still contain them).
- Numbers match: PPencilStroke 129,956 + PVariableWidth 4,086 + PConstantWidth 2,174 + PDynamicWidth 6 =
  136,222 = exactly the archive's blob count, so these four signatures cover every drawing blob in the corpus.

## Where the app builds/reads them

The signature constants are plain C strings referenced from the per-class `serialize`/`deserialize` code in
`CommonSwift/NotesItems/.../Stroke/Serialization/*.swift`; the loader is the `tpl` node parser
(`"Unable to load tpl node from memory"` @0xabf4d90, same code region). Each class passes its constant to the
same tpl writer/reader, so a reader can dispatch on the signature string alone.
