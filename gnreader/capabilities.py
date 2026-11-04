"""Machine-readable renderer capability map (kept in sync with out/item-types.txt)."""
from __future__ import annotations

import json
import os

VERSION = "0.3.3"

ITEM_KINDS = {
    "image": {"field": 1, "status": "decoded", "render": True,
              "note": "backup zip attachments and live attachment lookup"},
    "penStroke": {"field": 2, "status": "unsupported", "render": False,
                   "note": "legacy container; 70 corpus records are empty tombstones"},
    "fillEllipse": {"field": 3, "status": "unsupported", "render": False},
    "fillPath": {"field": 4, "status": "unsupported", "render": False},
    "text": {"field": 5, "status": "decoded", "render": True,
             "note": "via gntext; zero corpus samples"},
    "legacyVariableWidthLine2": {"field": 6, "status": "unsupported", "render": False},
    "penStroke2": {"field": 7, "status": "decoded", "render": True,
                   "note": "exact pencil outlines, constant/variable width, translation, z-order"},
    "text2": {"field": 8, "status": "decoded", "render": True,
              "note": "via gntext/RTF; zero corpus samples"},
    "fillShape": {"field": 9, "status": "decoded", "render": True,
                  "note": "ellipse/rect/polygon/quad; uncommon geometry reports a warning"},
    "penEraser": {"field": 10, "status": "partial", "render": False,
                  "note": "decoder exists; 0 observed items, mask not applied"},
    "math": {"field": 11, "status": "partial", "render": True,
             "note": "attachment image/placeholder; all 6 corpus items are deleted"},
    "textFragment": {"field": 12, "status": "decoded", "render": True,
                     "note": "via gntext/RTF; zero corpus samples"},
    "nexus": {"field": 13, "status": "partial", "render": True,
              "note": "exact text via gntext; full-page layout is approximate"},
    "loadedAttachments": {"field": 15, "status": "partial", "render": False,
                          "note": "container field"},
    "stickyNote": {"field": 20, "status": "partial", "render": True,
                   "note": "gntext text/paper box; corpus item is empty/deleted"},
    "shape": {"field": 21, "status": "decoded", "render": True,
              "note": "gntext rich text; shape fill/stroke not rendered"},
    "line": {"field": 22, "status": "unsupported", "render": False,
             "note": "zero observed items"},
}


def capabilities() -> dict:
    supported, partial, unsupported = [], [], []
    for name, row in ITEM_KINDS.items():
        label = "%s (f%d): %s" % (name, row["field"], row["note"] if row.get("note") else row["status"])
        if row["status"] == "decoded":
            supported.append(label)
        elif row["status"] == "partial":
            partial.append(label)
        else:
            unsupported.append(label)
    return {
        "version": VERSION,
        "lane": "archive",
        "renderer": "gnreader",
        "supported": supported,
        "partial": partial,
        "unsupported": unsupported,
        "item_kinds": ITEM_KINDS,
        "features": {
            "warnings_api": "render_page_result/render_records_result",
            "live_images": True,
            "typed_text": "gntext (shape/textboxes/sticky/nexus)",
            "deleted_pages": True,
            "deleted_items": True,
            "notes_zindex": True,
            "highlighter": "per-channel multiply (factor = 1 - alpha*(1-rgb), iPad-confirmed); one flat mask; live gnlive and render_records use it at the stroke z position",
        },
    }


def packaged() -> dict:
    """Read the capability map shipped inside the installed package."""
    from importlib.resources import files
    return json.loads((files("gnreader") / "capabilities.json").read_text(encoding="utf-8"))


def write_json(path: str | None = None) -> str:
    """Write the capability map to a writable path (never into site-packages)."""
    if path is None:
        home = (os.environ.get("GNREADER_HOME")
                or os.environ.get("GOODNOTES_INKEYE_HOME")
                or os.path.expanduser("~/.cache/gnreader"))
        path = os.path.join(home, "capabilities.json")
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(capabilities(), f, indent=1)
    return path
