"""Command line interface: python -m gnreader ..."""
from __future__ import annotations

import argparse
import json
import sys

from .decode import open_notebook, open_records
from .model import Notebook
from .render import render_page


def _json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _page(nb, selector, zero_based=False):
    """CLI page selector is 1-based (matches the PDF export); UUID prefixes work."""
    if selector.isdigit():
        idx = int(selector) - (0 if zero_based else 1)
        if idx < 0 or idx >= len(nb.pages):
            raise SystemExit("page %s out of range (1..%d)" % (selector, len(nb.pages)))
        return nb.pages[idx]
    return nb.page(selector)


def cmd_info(args):
    nb = open_notebook(args.file)
    _json({
        "file": nb.path,
        "title": nb.title,
        "pages": len(nb.pages),
        "deleted_pages": getattr(nb, "deleted_pages", 0),
        "attachments": len(nb.attachments),
        "strokes": sum(len(p.strokes) for p in nb.pages),
        "unknown": nb.unknown,
        "page_list": [p.as_dict() for p in nb.pages],
    })


def cmd_pages(args):
    nb = open_notebook(args.file)
    rows = [p.as_dict() for p in nb.pages]
    if args.json:
        _json(rows)
    else:
        for r in rows:
            print("%3d  %-36s  %8.1fx%-8.1f  %-28s  %4d strokes  %3d text" % (
                r["page"], r["uuid"], r["canvas"][0], r["canvas"][1],
                (r["background"]["attachment"] or r["background"]["kind"])[-28:],
                r["strokes"], r["text_items"]))


def cmd_render(args):
    nb = open_notebook(args.file)
    page = _page(nb, args.page, args.zero_based)
    out = render_page(nb, page, args.out, scale=args.scale,
                      supersample=args.supersample,
                      highlighter=not args.no_highlighter)
    print("wrote %s (%dx%d), page %d, %d strokes, %d texts" % (
        out, round(page.canvas_width * args.scale), round(page.canvas_height * args.scale),
        page.index + 1, len(page.strokes), len(page.texts)))


def cmd_render_records(args):
    with open(args.records, "rb") as f:
        data = f.read()
    page = open_records(data, width=args.width, height=args.height,
                        template=args.template,
                        paper_width=args.paper_width, paper_height=args.paper_height)
    nb = Notebook(path="", title="live")
    out = render_page(nb, page, args.out, scale=args.scale,
                      supersample=args.supersample,
                      highlighter=not args.no_highlighter)
    print("wrote %s (%dx%d), records page, %d strokes, %d texts, %d unknown" % (
        out, round(page.canvas_width * args.scale), round(page.canvas_height * args.scale),
        len(page.strokes), len(page.texts), len(page.unknown_items)))


def cmd_capabilities(args):
    from .capabilities import capabilities
    _json(capabilities())


def cmd_dump(args):
    nb = open_notebook(args.file)
    page = _page(nb, args.page, args.zero_based)
    _json({
        "page": page.index + 1,
        "uuid": page.uuid,
        "canvas": [page.canvas_width, page.canvas_height],
        "strokes": [s.as_dict() for s in page.strokes if s.kind != "eraser"],
        "unknown": page.unknown_items,
    })


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gnreader")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="notebook metadata and page list")
    p.add_argument("file")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("pages", help="page list")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pages)

    p = sub.add_parser("render", help="render one page to PNG")
    p.add_argument("file")
    p.add_argument("page", help="1-based page number or page UUID prefix")
    p.add_argument("out")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--supersample", type=int, default=3)
    p.add_argument("--no-highlighter", action="store_true")
    p.add_argument("--zero-based", action="store_true", help="use 0-based page numbers")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("render-records", help="render a live page from record bytes")
    p.add_argument("records", help="file containing descriptor+item record bytes")
    p.add_argument("out")
    p.add_argument("--width", type=float, default=1024.0)
    p.add_argument("--height", type=float, default=1366.0)
    p.add_argument("--template", default="", help="template id/string (e.g. squared)")
    p.add_argument("--paper-width", type=float, default=595.28)
    p.add_argument("--paper-height", type=float, default=841.89)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--supersample", type=int, default=3)
    p.add_argument("--no-highlighter", action="store_true")
    p.set_defaults(func=cmd_render_records)

    p = sub.add_parser("capabilities", help="print the machine-readable capability map")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("dump-strokes", help="JSON strokes for one page")
    p.add_argument("file")
    p.add_argument("page", help="1-based page number or page UUID prefix")
    p.add_argument("--zero-based", action="store_true")
    p.set_defaults(func=cmd_dump)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
