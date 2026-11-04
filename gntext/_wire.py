"""Minimal protobuf wire reader used for the *undocumented* rich-text payload.

The shape/sticky rich text is a protobuf-shaped tree that is not a `Schema.*`
message (no name map): repeated field 1 = paragraph, inside it field 1 = text,
field 2 = inline attributes, field 3 = block attributes.  The attribute
sub-messages match `Schema.InlineAttributesRaw` / `BlockAttributesRaw`
(field numbers verified against real data).
"""
import struct


def varint(b, i):
    v = 0
    s = 0
    while True:
        x = b[i]; i += 1
        v |= (x & 0x7F) << s
        if not x & 0x80:
            return v, i
        s += 7
        if s > 70:
            raise ValueError("varint too long")


def fields(b):
    """Yield (number, wire, value) where value is int / bytes / float."""
    i = 0
    n = len(b)
    while i < n:
        tag, i = varint(b, i)
        num, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = varint(b, i)
            yield num, wt, v
        elif wt == 1:
            yield num, wt, struct.unpack_from("<d", b, i)[0]
            i += 8
        elif wt == 5:
            yield num, wt, struct.unpack_from("<f", b, i)[0]
            i += 4
        elif wt == 2:
            ln, i = varint(b, i)
            yield num, wt, b[i:i + ln]
            i += ln
        else:
            raise ValueError(f"unsupported wire {wt}")


def group(b, num):
    return [v for n, w, v in fields(b) if n == num]


def first(b, num, default=None):
    for n, w, v in fields(b):
        if n == num:
            return v
    return default


def as_text(v):
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except UnicodeDecodeError:
            return v.decode("utf-8", "replace")
    return "" if v is None else str(v)
