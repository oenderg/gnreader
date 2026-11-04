"""gnlive CLI: list | find | render | text."""
import argparse, json, sys

from .render import render_page, page_text_hint
from . import list_notebooks, find


def cmd_list(args):
    nbs = list_notebooks()
    if args.json:
        print(json.dumps(nbs, indent=2))
        return
    for nb in nbs:
        folder = (nb["folder_path"] + "/") if nb["folder_path"] else ""
        print(f"{folder}{nb['name']}  [{nb['id']}]  {nb['page_count']}p  {nb['modified'] or ''}")


def cmd_find(args):
    hits = find(args.query)
    for nb in hits:
        folder = (nb["folder_path"] + "/") if nb["folder_path"] else ""
        print(f"{folder}{nb['name']}  [{nb['id']}]  {nb['page_count']}p")
    print(f"{len(hits)} match(es)", file=sys.stderr)


def cmd_render(args):
    data, info = render_page(args.notebook, args.page, max_side=args.max_side,
                             fresh=not args.no_fresh)
    with open(args.out, "wb") as f:
        f.write(data)
    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print(f"{info['notebook']} p{info['page_number']} -> {args.out}")
        print(f"  {info['size'][0]}x{info['size'][1]}  canvas {info['canvas'][0]}x{info['canvas'][1]}"
              f"  items {info['items']}  strokes {info['strokes_total']}"
              f" (pencil {info['pencil']}, constant {info['constant']}, variable {info['variable']})")
        print(f"  drawable {info['drawable']}  text/other {info['text_or_other']}"
              f"  background {info['background']}")


def cmd_text(args):
    print(json.dumps(page_text_hint(args.page_id), indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gnlive")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("find")
    s.add_argument("query")
    s.set_defaults(func=cmd_find)

    s = sub.add_parser("render")
    s.add_argument("notebook")
    s.add_argument("page", type=int, help="1-based page number")
    s.add_argument("out")
    s.add_argument("--max-side", type=int, default=1600)
    s.add_argument("--no-fresh", action="store_true", help="reuse the existing snapshot")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("text")
    s.add_argument("page_id")
    s.set_defaults(func=cmd_text)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except (KeyError, IndexError, FileNotFoundError) as e:
        print("error:", e, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
