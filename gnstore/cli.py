"""gnstore CLI: notebooks | pages | page-bytes | snapshot."""
import argparse, json, os, sys

from .store import snapshot as make_snapshot, DEFAULT_SNAPSHOT, LIVE
from .catalog import Catalog, LIVE_PROJECTION
from .pageio import page_bytes, count_items, bump


def _snapshot(args):
    return args.snapshot or DEFAULT_SNAPSHOT


def _catalog(args):
    snap = _snapshot(args)
    path = os.path.join(snap, "projection.sqlite")
    if not os.path.exists(path):
        path = LIVE_PROJECTION
    return Catalog(path)


def cmd_snapshot(args):
    make_snapshot(args.dest or DEFAULT_SNAPSHOT, fresh=args.fresh,
                  dbs=("notes_main",) if not args.all else ("notes_main", "index_main", "events_main"),
                  sqlite=("projection",) if not args.all else ("projection", "fts"))


def _print_tree(node, indent=0):
    pad = "  " * indent
    for n in node["notebooks"]:
        print(f"{pad}{n['name']}  [{n['id']}]  {n['page_count']}p")
    for f in node["children"]:
        print(f"{pad}+ {f['name']}  [{f['id']}]")
        _print_tree(f, indent + 1)


def cmd_notebooks(args):
    cat = _catalog(args)
    tree = cat.tree(include_deleted=args.deleted)
    if args.json:
        print(json.dumps(tree, indent=2, default=str))
        return
    print("root:")
    _print_tree({"notebooks": tree["root_notebooks"], "children": tree["folders"]}, 1)


def cmd_pages(args):
    cat = _catalog(args)
    doc = cat.doc(args.notebook)
    pages = cat.pages_of(doc["id"], include_deleted=args.deleted)
    counts = count_items(os.path.join(_snapshot(args), "notes_main.db"),
                         [p["id"] for p in pages], include_deleted=args.deleted,
                         include_wal=not args.no_wal)
    if args.json:
        print(json.dumps([{**p, "items": counts.get(p["id"], 0)} for p in pages], indent=2))
        return
    print(f"{doc['name']}  [{doc['id']}]  {len(pages)} pages")
    for i, p in enumerate(pages):
        print(f"  {i+1:3d}  {p['id']}  pos={p['position']}  items={counts.get(p['id'], 0)}"
              f"{'  deleted' if p['deleted'] else ''}")
    print(f"  container ids are page id +1, e.g. {bump(pages[0]['id'])}" if pages else "")


def cmd_page_bytes(args):
    cat = _catalog(args)
    page_id = args.page
    if len(page_id) != 36 or "-" not in page_id:
        # allow "notebook:index"
        doc_part, _, idx = page_id.rpartition(":")
        doc = cat.doc(doc_part)
        pages = cat.pages_of(doc["id"], include_deleted=args.deleted)
        page_id = pages[int(idx) - 1]["id"]
    data, info = page_bytes(os.path.join(_snapshot(args), "notes_main.db"), page_id,
                            include_deleted=args.deleted, include_wal=not args.no_wal)
    with open(args.out, "wb") as f:
        f.write(data)
    print(f"wrote {len(data)} bytes, {info['items']} items, container {info['container']}"
          f" -> {args.out}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gnstore", description="Read the live Goodnotes library")
    ap.add_argument("--snapshot", default=None, help="snapshot dir (default $GNREADER_HOME/snapshot)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="copy live RocksDB + SQLite into the snapshot dir")
    s.add_argument("--dest")
    s.add_argument("--all", action="store_true", help="also index_main/events_main/fts")
    s.add_argument("--fresh", action="store_true", help="force a new copy of the live store")
    s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("notebooks", help="tree of folders and notebooks")
    s.add_argument("--json", action="store_true")
    s.add_argument("--deleted", action="store_true")
    s.set_defaults(func=cmd_notebooks)

    s = sub.add_parser("pages", help="pages of a notebook (id, name or unique substring)")
    s.add_argument("notebook")
    s.add_argument("--json", action="store_true")
    s.add_argument("--deleted", action="store_true")
    s.add_argument("--no-wal", action="store_true", help="read SSTs only")
    s.set_defaults(func=cmd_pages)

    s = sub.add_parser("page-bytes", help="write a page's records like backup notes/<uuid>")
    s.add_argument("page", help="page id, or notebook:index")
    s.add_argument("out")
    s.add_argument("--deleted", action="store_true")
    s.add_argument("--no-wal", action="store_true", help="read SSTs only")
    s.set_defaults(func=cmd_page_bytes)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except KeyError as e:
        print("error:", e.args[0], file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(f"error: {e}; run `python -m gnstore snapshot` first", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
