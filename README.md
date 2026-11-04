# gnreader

`gnreader` is a personal-use reader for the Goodnotes notebook format. It opens
a `.goodnotes` backup (a ZIP), decodes pages, strokes, typed text, images,
shapes, templates and z-order, and renders a page close to Goodnotes' PDF
export. It can also decode live page records copied out of the Goodnotes store.

This is an independent reverse-engineering project. Nothing from the Goodnotes
application or from any notebook is included. The format was recovered from
shipped artifacts and the user's own files.

## Install

From the private repository:

```sh
pip install "git+https://github.com/oenderg/gnreader.git"
```

For development from a checkout:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

Optional Yjs/nexus text extraction uses `pycrdt`:

```sh
pip install -e ".[nexus]"
```

## Command line

```sh
python -m gnreader info notebook.goodnotes
python -m gnreader pages notebook.goodnotes
python -m gnreader render notebook.goodnotes 1 page.png
python -m gnreader dump-strokes notebook.goodnotes 1 > strokes.json
python -m gnreader capabilities
```

The same commands are installed as console scripts: `gnreader`, `gnstore`,
`gnlive` and `gntext`.

Typed text can be extracted from a backup or from live page records:

```sh
python -m gntext notebook.goodnotes --summary
python -m gntext page-records.bin --indent 2
python -m gntext --store PAGE_ID
```

Live-store helpers (read-only source):

```sh
python -m gnstore snapshot --fresh
python -m gnstore notebooks
python -m gnstore pages "Notebook name"
python -m gnstore page-bytes PAGE_ID page-records.bin
python -m gnlive list
python -m gnlive render "Notebook name" 1 page.png --max-side 1600
```

## Python API

```python
from gnreader import open_notebook, render_page, render_records

nb = open_notebook("notebook.goodnotes")
print(nb.title, len(nb.pages))
render_page(nb, nb.pages[0], "page.png", supersample=3)

# Live page: raw length-delimited records or gnstore's [(descriptor, item)] list
render_records(records, "live-page.png", width=1091, height=1543)
```

`render_page_result()` and `render_records_result()` return a `RenderResult`
with `path`, `warnings` and `stats`. `gnreader.capabilities()` returns the
machine-readable supported/partial/unsupported map; the same JSON is shipped as
package data and can be printed with `python -m gnreader capabilities`.

## Live data is read-only

The Goodnotes container is only ever read. `gnstore` copies the RocksDB and
SQLite files into a writable snapshot before opening them; on macOS it uses
APFS clones (`cp -c`) so unchanged files are cheap. Writable state defaults to:

```
$GNREADER_HOME/snapshot      # or $GOODNOTES_INKEYE_HOME
$GNREADER_HOME/cache
```

`GNREADER_HOME` defaults to `~/.cache/gnreader`. Nothing is written inside the
installed package, the Goodnotes container, or a notebook.

## What is supported

- Pencil strokes with exact outline polygons, centreline fallback, translation,
  variable width, constant width and NotesZIndex z-order.
- Transparent highlighters as a per-channel multiply (`factor =
  1 - alpha*(1-rgb)`), preserving ink below and leaving ink above unchanged.
  Auto-straightened bars have round caps (`ConstantWidthStroke` field 6);
  compact freehand marks use a solid disc union so no white slivers show.
- Page templates: squared, ruled and dotted paper, plus imported PDF/image
  backgrounds when the bytes are available.
- Typed text through `gntext`: shape rich text, text boxes, text fragments,
  sticky notes and nexus/Yjs documents. Nexus layout is approximate.
- Images, common fill shapes, math attachments and deletion tombstones. Live
  `f1` images are drawn in z-order with the same attachment loader; a missing
  or unreadable attachment becomes an `image missing` warning.
- Backup `.goodnotes` files and live page record streams.

## Known gaps

- No write support: the library only reads and renders.
- Legacy item kinds `f3`, `f4`, `f6` and `f22` are unsupported.
- Classic text boxes (`f5`, `f8`, `f12`) are decoded through `gntext` but have
  no samples in the development corpus.
- Nexus/Yjs layout is not byte-perfect; document headers and font metrics are
  approximate.
- Shape fill/stroke is not rendered. Math and sticky notes are partial.
- The live renderer does not draw `FillShapeItem` yet; it reports a
  `fill shape` warning.
- Whiteboard notebooks have no page canvas and render blank.
- Eraser masks are decoded but not applied (no observed samples).
- Missing built-in/sticker backgrounds render white.
- The exact highlighter blend field is not decoded; multiply is confirmed by
  measured iPad output.

## The format in ten lines

1. A `.goodnotes` file is a ZIP with `index.notes.pb`, `index.events.pb`,
   `notes/<page-id>` and optional `attachments/<uuid>`.
2. Page events carry the canvas size, background/template and page order.
3. Each `notes/<page-id>` is a varint-length-delimited protobuf stream of
   descriptor + item records.
4. Item types: image 1, legacy pen 2, text 5, `penStroke2` 7, text2 8,
   fillShape 9, eraser 10, math 11, textFragment 12, nexus 13, sticky 20,
   shape 21, line 22.
5. Strokes are `bv41` LZ4 frames around a `tpl` signature + typed values;
   stored (`bv4-`) blocks are also accepted.
6. Pencil outlines use move/cubic/arc commands; constant and variable strokes
   use quadratic Bézier paths.
7. Item colour is RGBA; thickness is stored in canvas units.
8. Item field 6 is an `(x, y)` translation; item field 7 is `NotesZIndex`
   `(session, batchID, batchIndex)`, higher session on top.
9. Deleted items are descriptor tombstones; deleted pages are event records.
10. Templates are SVG-like grids over paper `(247,246,233)` with lines
    `(208,210,211)`; imported PDFs are page backgrounds.

## Tests

```sh
pytest
```

Tests that need private notebooks look for them under
`$GNREADER_TEST_CORPUS` (default `tests/corpus`, not shipped) and skip when the
files are absent. Synthetic tests cover the LZ4 container, varint streams,
signature parsing and highlighter math without any private data.

## License

Private repository. All rights reserved; no license is granted.
