"""Decode Goodnotes' `bv41` LZ4 container and its signed payloads.

Verified against several private notebooks and ~136k pen-stroke blobs:
  "bv41" + u32 raw_size + u32 compressed_size + LZ4 block  ... "bv4$"
Stored blocks use "bv4-" + u32 size + raw bytes.  The decompressed payload is
  "tpl\0" + u32 size + ASCII signature + 0x00 + values
The signature grammar is a tiny type language:
  v = uint16, u = float32, f = float32, S(...) = struct, A(T) = array of T
Arrays are a u32 count followed by the elements.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

import lz4.block

MAGIC_COMPRESSED = b"bv41"
MAGIC_STORED = b"bv4-"
MAGIC_END = b"bv4$"
TPL_MAGIC = b"tpl\0"


class Bv4Error(ValueError):
    pass


def decompress(data: bytes) -> bytes:
    """Decode a complete bv4 frame (one or more blocks)."""
    out = bytearray()
    pos = 0
    while pos < len(data):
        if pos + 4 > len(data):
            raise Bv4Error("truncated block magic at %d" % pos)
        magic = data[pos:pos + 4]
        pos += 4
        if magic == MAGIC_COMPRESSED:
            if pos + 8 > len(data):
                raise Bv4Error("truncated bv41 header")
            raw_size, comp_size = struct.unpack_from("<II", data, pos)
            pos += 8
            block = data[pos:pos + comp_size]
            if len(block) != comp_size:
                raise Bv4Error("truncated bv41 block")
            pos += comp_size
            try:
                out += lz4.block.decompress(block, uncompressed_size=raw_size)
            except Exception as exc:  # lz4 raises ValueError
                raise Bv4Error("LZ4 block failed: %s" % exc) from exc
        elif magic == MAGIC_STORED:
            if pos + 4 > len(data):
                raise Bv4Error("truncated bv4- header")
            size, = struct.unpack_from("<I", data, pos)
            pos += 4
            block = data[pos:pos + size]
            if len(block) != size:
                raise Bv4Error("truncated bv4- block")
            pos += size
            out += block
        elif magic == MAGIC_END:
            break
        else:
            raise Bv4Error("unknown block magic %r at %d" % (magic, pos - 4))
    return bytes(out)


@dataclass(frozen=True)
class Field:
    kind: str                  # 'v', 'u', 'f', 'S', 'A'
    children: tuple["Field", ...] = ()

    def describe(self) -> str:
        if self.kind == "A":
            return "A(" + self.children[0].describe() + ")"
        if self.kind == "S":
            return "S(" + "".join(c.describe() for c in self.children) + ")"
        return self.kind


def parse_signature(text: str) -> tuple[Field, ...]:
    """Parse a whole signature string as the field list of the root struct."""
    i = 0

    def one() -> Field:
        nonlocal i
        if i >= len(text):
            raise Bv4Error("unexpected end of signature")
        c = text[i]
        i += 1
        if c == "A":
            if i >= len(text) or text[i] != "(":
                raise Bv4Error("expected '(' after A")
            i += 1
            child = one()
            if i >= len(text) or text[i] != ")":
                raise Bv4Error("expected ')' after array element")
            i += 1
            return Field("A", (child,))
        if c == "S":
            if i >= len(text) or text[i] != "(":
                raise Bv4Error("expected '(' after S")
            i += 1
            children = []
            while i < len(text) and text[i] != ")":
                children.append(one())
            if i >= len(text):
                raise Bv4Error("unterminated S(...)")
            i += 1
            return Field("S", tuple(children))
        if c in "vuf":
            return Field(c)
        raise Bv4Error("unknown signature token %r" % c)

    fields = []
    while i < len(text):
        fields.append(one())
    return tuple(fields)


def read_values(fields: tuple[Field, ...], payload: bytes) -> tuple[list[Any], int]:
    """Read values in prefix order. Returns (values, bytes_consumed)."""
    pos = 0

    def read_one(f: Field) -> Any:
        nonlocal pos
        if f.kind == "A":
            n, pos = _u32(payload, pos)
            return [read_one(f.children[0]) for _ in range(n)]
        if f.kind == "S":
            return [read_one(c) for c in f.children]
        if f.kind == "v":
            val, pos = _u16(payload, pos)
            return val
        if f.kind in ("u", "f"):
            val = struct.unpack_from("<f", payload, pos)[0]
            pos += 4
            return val
        raise Bv4Error("bad field")
    vals = [read_one(f) for f in fields]
    return vals, pos


def _u16(b: bytes, pos: int) -> tuple[int, int]:
    if pos + 2 > len(b):
        raise Bv4Error("truncated u16")
    return struct.unpack_from("<H", b, pos)[0], pos + 2


def _u32(b: bytes, pos: int) -> tuple[int, int]:
    if pos + 4 > len(b):
        raise Bv4Error("truncated u32")
    return struct.unpack_from("<I", b, pos)[0], pos + 4


def _f32(b: bytes, pos: int) -> float:
    if pos + 4 > len(b):
        raise Bv4Error("truncated f32")
    return struct.unpack_from("<f", b, pos)[0]


@dataclass
class TplPayload:
    signature: str
    fields: tuple[Field, ...]
    values: list[Any]
    raw: bytes
    size: int


def decode_tpl(data: bytes) -> TplPayload:
    """Parse the decompressed `tpl` payload of an f7 item."""
    if not data.startswith(TPL_MAGIC):
        raise Bv4Error("missing tpl magic")
    if len(data) < 8:
        raise Bv4Error("truncated tpl header")
    size, = struct.unpack_from("<I", data, 4)
    end = data.find(b"\x00", 8)
    if end < 0:
        raise Bv4Error("unterminated signature")
    signature = data[8:end].decode("ascii")
    fields = parse_signature(signature)
    payload = data[end + 1:]
    values, consumed = read_values(fields, payload)
    if consumed != len(payload):
        raise Bv4Error("%d trailing bytes after signature payload" % (len(payload) - consumed))
    return TplPayload(signature, fields, values, data, size)
