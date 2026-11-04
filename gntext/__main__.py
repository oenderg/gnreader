"""CLI: python -m gntext <file.goodnotes|page-bytes.bin> [--no-nexus] [--indent N]"""
import argparse
import json
import os
import sys

from . import extract_notebook, extract_page


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gntext",
                                 description="Extract typed text from Goodnotes data")
    ap.add_argument("path", nargs="?", help=".goodnotes backup or a page-bytes binary "
                                            "(varint-length-delimited protobuf records)")
    ap.add_argument("--store", metavar="PAGE_ID", default=None,
                    help="read one live-store page via gnstore")
    ap.add_argument("--no-nexus", action="store_true", help="skip Yjs text documents")
    ap.add_argument("--indent", type=int, default=None, help="JSON indent")
    ap.add_argument("--summary", action="store_true",
                    help="only counts per page (no text)")
    a = ap.parse_args(argv)

    if a.store:
        from . import extract_store_page
        data = extract_store_page(a.store, include_nexus=not a.no_nexus)
        if a.summary:
            data = {"page_id": data["page_id"], "counts": data["counts"],
                    "text_items": len(data["items"])}
        json.dump(data, sys.stdout, ensure_ascii=False, indent=a.indent)
        sys.stdout.write("\n")
        return 0
    path = a.path
    if not path or not os.path.exists(path):
        print(f"no such file: {path}", file=sys.stderr)
        return 2
    if path.endswith(".goodnotes") or path.endswith(".zip"):
        data = extract_notebook(path, include_nexus=not a.no_nexus)
        if a.summary:
            data = {"notebook": data["notebook"], "title": data["title"],
                    "metadata": {"title": data["metadata"]["title"],
                                 "labels": len(data["metadata"]["page_labels"]),
                                 "outlines": len(data["metadata"]["outlines"])},
                    "pages": [{"page_id": p["page_id"], "counts": p["counts"],
                               "text_items": len(p["items"])} for p in data["pages"]]}
    else:
        with open(path, "rb") as f:
            raw = f.read()
        page = extract_page(raw, include_nexus=not a.no_nexus)
        if a.summary:
            page = {"counts": page["counts"], "text_items": len(page["items"])}
        data = {"pages": [page]}
    json.dump(data, sys.stdout, ensure_ascii=False, indent=a.indent)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
