"""Rich-text payload decoding for Goodnotes shapes/sticky notes.

`Schema.RichTextRaw` carries `raw.raw`, a bv4/LZ4-framed protobuf tree:
  repeated 1: paragraph
     1: text (UTF-8 bytes)
     2: inline attributes  (same numbers as Schema.InlineAttributesRaw)
     3: block attributes   (same numbers as Schema.BlockAttributesRaw)
Verified against 30 shape items in the backup corpus (see ITEMS.md).
"""
from ._lz4 import maybe_bv4
from . import _wire as W


def _color(b):
    if not b:
        return None
    c = {"r": 0.0, "g": 0.0, "b": 0.0, "a": 1.0}
    for n, w, v in W.fields(b):
        if n == 1 and w == 5:
            c["r"] = v
        elif n == 2 and w == 5:
            c["g"] = v
        elif n == 3 and w == 5:
            c["b"] = v
        elif n == 4 and w == 5:
            c["a"] = v
        elif n == 5:
            c["dynamic"] = W.as_text(v)
    return c


def _inline_attrs(b):
    if not b:
        return None
    a = {}
    for n, w, v in W.fields(b):
        if n == 1 and w == 0:
            a["strike"] = bool(v)
        elif n == 2 and w == 0:
            a["underline"] = bool(v)
        elif n == 3 and w == 2:
            a["color"] = _color(v)
        elif n == 4 and w == 2:
            a["link"] = W.as_text(v)
        elif n == 5 and w == 2:
            a["highlight"] = _color(v)
        elif n == 30 and w == 2:
            a["fontFamily"] = W.as_text(v)
        elif n == 40 and w == 5:
            if v > 0:                      # negative = "unset" sentinel
                a["fontSize"] = v
        elif n == 50 and w == 0:
            a["italic"] = bool(v)
        elif n == 60 and w == 0:
            # encoded as signed varint; negative = "unset" sentinel
            wv = v - (1 << 64) if v >= 1 << 63 else v
            if wv > 0:
                a["fontWeight"] = wv
        elif n == 70 and w == 5:
            if v > 0:
                a["fontWidth"] = v
    return a or None


def _block_attrs(b):
    if not b:
        return None
    a = {}
    for n, w, v in W.fields(b):
        if n == 1 and w == 2:  # blockType oneof: 1 paragraph, 2 heading{1: level}
            for bn, bw, bv in W.fields(v):
                if bn == 2 and bw == 2:
                    lvl = W.first(bv, 1, 0)
                    a["blockType"] = f"heading{lvl}" if lvl else "heading"
                else:
                    a["blockType"] = "paragraph"
        elif n == 2 and w == 2:
            lh = {}
            for ln, lw, lv in W.fields(v):
                if isinstance(lv, int) and lv >= (1 << 63):
                    continue                  # -1 sentinel: unset
                if ln == 1:
                    lh["type"] = lv
                elif ln == 2:
                    lh["lineHeight"] = lv
                elif ln == 3:
                    lh["lineSpacing"] = lv
                elif ln == 4:
                    lh["verticalAlign"] = lv
            a["lineHeight"] = lh
        elif n == 3 and w == 2:
            ls = {}
            for ln, lw, lv in W.fields(v):
                sv = lv - (1 << 64) if isinstance(lv, int) and lv >= 1 << 63 else lv
                if sv == -1:
                    continue                  # sentinel: unset
                ls["type" if ln == 1 else "startingItemNumber" if ln == 2 else f"f{ln}"] = sv
            if ls:
                a["listStyle"] = ls
        elif n == 4 and w == 0:
            a["textAlignment"] = v
        elif n == 5 and w == 5:
            a["paragraphSpacingAfter"] = v
        elif n == 6 and w == 5:
            a["paragraphSpacingBefore"] = v
        elif n == 7 and w == 0:
            a["indentationLevel"] = v
    return a or None


def parse_rich_text(raw_message: bytes) -> dict:
    """Parse a Schema.RichTextRaw message into {text, paragraphs, size, padding}."""
    inner = None
    size = None
    padding = None
    line_height = None
    for n, w, v in W.fields(raw_message):
        if n == 1 and w == 2:            # raw: RawData{2: raw, 4: version}
            inner = W.first(v, 2)
        elif n == 2 and w == 2:          # Size
            size = {"width": W.first(v, 1, 0.0), "height": W.first(v, 2, 0.0)}
        elif n == 10 and w == 2:
            padding = {k: vv for k, vv in (("top", W.first(v, 1)), ("right", W.first(v, 2)),
                                           ("bottom", W.first(v, 3)), ("left", W.first(v, 4)))}
        elif n == 11 and w == 2:
            line_height = {"type": W.first(v, 1, 0), "value": W.first(v, 2, 0.0)}
    if not inner:
        return {"text": "", "paragraphs": [], "size": size, "padding": padding}
    doc = maybe_bv4(inner)
    paragraphs = []
    for n, w, v in W.fields(doc):
        if n != 1 or w != 2:
            continue
        p = {"text": "", "inline": None, "block": None}
        for pn, pw, pv in W.fields(v):
            if pn == 1 and pw == 2:
                p["text"] = W.as_text(pv)
            elif pn == 2 and pw == 2:
                p["inline"] = _inline_attrs(pv)
            elif pn == 3 and pw == 2:
                p["block"] = _block_attrs(pv)
        paragraphs.append(p)
    text = "\n".join(p["text"] for p in paragraphs)
    return {"text": text, "paragraphs": paragraphs, "size": size,
            "padding": padding, "lineHeight": line_height}


def rtf_to_text(data: bytes) -> str:
    """Very small RTF reader for TextNotesItemV2/TextFragment rtfData."""
    if not data:
        return ""
    s = data.decode("latin-1", "replace")
    out = []
    stack = []
    skip = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "{":
            stack.append(skip)
            i += 1
        elif c == "}":
            skip = stack.pop() if stack else False
            i += 1
        elif c == "\\":
            j = i + 1
            if j < n and s[j] in "\\{}":
                if not skip:
                    out.append(s[j])
                i = j + 1
                continue
            if j < n and s[j] == "'":
                if not skip and j + 2 < n:
                    try:
                        out.append(bytes([int(s[j + 1:j + 3], 16)]).decode("cp1252", "replace"))
                    except ValueError:
                        pass
                i = j + 3
                continue
            k = j
            while k < n and s[k].isalpha():
                k += 1
            word = s[j:k]
            num = ""
            if k < n and s[k] == "-":
                m = k + 1
                while m < n and s[m].isdigit():
                    m += 1
                num = s[k:m]
                k = m
            elif k < n and s[k].isdigit():
                m = k
                while m < n and s[m].isdigit():
                    m += 1
                num = s[k:m]
                k = m
            if k < n and s[k] == " ":
                k += 1
            if word in ("fonttbl", "colortbl", "stylesheet", "info", "pict", "header",
                        "footer", "footnote", "object", "themedata", "colorschememapping",
                        "listtable", "listoverridetable"):
                skip = True
            elif word in ("par", "line"):
                if not skip:
                    out.append("\n")
            elif word == "tab":
                if not skip:
                    out.append("\t")
            elif word == "u":
                if not skip and num:
                    try:
                        v = int(num)
                        out.append(chr(v + 65536 if v < 0 else v))
                    except ValueError:
                        pass
                # skip the single ANSI fallback character after \uN
                if k < n and s[k] not in "\\{}":
                    k += 1
            i = k
        elif c in "\r\n":
            i += 1
        else:
            if not skip:
                out.append(c)
            i += 1
    return "".join(out)
