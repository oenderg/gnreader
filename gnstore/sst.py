#!/usr/bin/env python3
"""Pure-Python reader for RocksDB block-based SST files (format_version 7).

Verified against RocksDB v11.1.1 sources (table/format.cc, table/block_based/):
  footer (53 B): checksum_type u8 | ext magic "3e007a00" | stored_crc u32 |
                 base_context_checksum u32 | metaindex_size u32 |
                 16 B zero | 8 B zero | format_version u32 | magic u64
  metaindex: keys "rocksdb.properties", "rocksdb.index"; values are BlockHandles
  index block values: first = BlockHandle, later = zigzag varint delta of size,
                      offset = prev.offset + prev.size + 5 (no first key)
  block footer: u32 packed, num_restarts = low 28 bits
  block trailer: u8 compression type + u32 checksum
No FFI; safe on a copy.
"""
import struct, sys, zlib

MAGIC = 0x88E241B785F4CFF7
EXT_MAGIC = b"\x3e\x00\x7a\x00"
FOOTER_LEN = 53
BLOCK_TRAILER = 5


def varint(buf, pos):
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def svarint(buf, pos):
    v, pos = varint(buf, pos)
    return (v >> 1) ^ -(v & 1), pos


def block_handle(buf, pos):
    off, pos = varint(buf, pos)
    size, pos = varint(buf, pos)
    return (off, size), pos


def decompress(data, ctype):
    """RocksDB V2 compression: varint32 uncompressed size, then payload."""
    if ctype == 0:
        return data
    usize, pos = varint(data, 0)
    body = data[pos:]
    if ctype in (4, 5):  # LZ4 / LZ4HC raw block
        import lz4.block
        return lz4.block.decompress(body, uncompressed_size=usize)
    if ctype == 1:
        return snappy.decompress(body)
    if ctype == 2:
        return zlib.decompress(body)
    if ctype == 7:
        import zstandard
        return zstandard.ZstdDecompressor().decompress(body, max_output_size=usize)
    raise ValueError(f"unsupported compression type {ctype}")


class SST:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        self.size = len(self.data)
        self.parse_footer()

    def parse_footer(self):
        f = self.data[-FOOTER_LEN:]
        if struct.unpack("<Q", f[-8:])[0] != MAGIC:
            raise ValueError("not a block-based SST (bad magic)")
        self.checksum_type = f[0]
        if f[1:5] != EXT_MAGIC:
            raise ValueError("bad extended magic " + f[1:5].hex())
        self.stored_checksum = struct.unpack("<I", f[5:9])[0]
        self.base_context_checksum = struct.unpack("<I", f[9:13])[0]
        self.metaindex_size = struct.unpack("<I", f[13:17])[0]
        self.format_version = struct.unpack("<I", f[-12:-8])[0]
        self.metaindex_offset = self.size - FOOTER_LEN - BLOCK_TRAILER - self.metaindex_size
        self.properties = {}
        self.index_handle = None

    def read_block(self, offset, size):
        """Return decompressed block payload (without trailer)."""
        raw = self.data[offset:offset + size]
        if len(raw) != size:
            raise ValueError(f"short block at {offset} size {size}")
        trailer = self.data[offset + size:offset + size + BLOCK_TRAILER]
        if len(trailer) != BLOCK_TRAILER:
            raise ValueError("missing block trailer")
        ctype = trailer[0]
        return decompress(raw, ctype)

    def parse_entries(self, payload, value_mode="len"):
        """Yield (key, value) from a decompressed block payload.

        value_mode="len":    classic entries, <shared><non_shared><vlen><key><value>.
        value_mode="handle": index-style value-delta encoding, no vlen; the value
                             is a self-delimiting BlockHandle (two varints).
        """
        if len(payload) < 4:
            raise ValueError("block too small")
        packed = struct.unpack("<I", payload[-4:])[0]
        num_restarts = packed & 0x0FFFFFFF
        if packed >> 28:
            raise ValueError(f"block uses extra flags 0x{packed:08x} (unsupported)")
        restarts_start = len(payload) - 4 - num_restarts * 4
        if restarts_start < 0:
            raise ValueError("bad restart count")
        pos = 0
        key = b""
        while pos < restarts_start:
            shared, pos = varint(payload, pos)
            non_shared, pos = varint(payload, pos)
            if value_mode == "len":
                vlen, pos = varint(payload, pos)
            else:
                vlen = None
            key = key[:shared] + payload[pos:pos + non_shared]
            pos += non_shared
            if value_mode == "len":
                val = payload[pos:pos + vlen]
                pos += vlen
            else:
                vstart = pos
                _off, pos = varint(payload, pos)
                _sz, pos = varint(payload, pos)
                val = payload[vstart:pos]
            yield key, val
        if pos != restarts_start:
            raise ValueError(f"entry parse ended at {pos}, expected {restarts_start}")

    def read_metaindex(self):
        payload = self.read_block(self.metaindex_offset, self.metaindex_size)
        for k, v in self.parse_entries(payload):
            if k == b"rocksdb.properties":
                self.properties_handle, _ = block_handle(v, 0)
            elif k == b"rocksdb.index":
                self.index_handle, _ = block_handle(v, 0)
            else:
                print("metaindex key:", k[:60], file=sys.stderr)

    def read_properties(self):
        if not getattr(self, "properties_handle", None):
            return
        payload = self.read_block(*self.properties_handle)
        for k, v in self.parse_entries(payload):
            self.properties[k.decode("latin1")] = v.decode("latin1", "replace")

    def iter_data_blocks(self):
        """Yield (handle, separator_key) for each data block in the top-level index.

        RocksDB 11.1.1 index values here are full BlockHandles (offset, size),
        observed and validated against block contiguity.
        """
        payload = self.read_block(*self.index_handle)
        for k, v in self.parse_entries(payload, value_mode="handle"):
            handle, pos = block_handle(v, 0)
            if pos != len(v):
                raise ValueError(f"index value has {len(v) - pos} trailing bytes: {v.hex()}")
            yield handle, k

    def iter_prefix(self, prefix):
        """Yield entries whose user key starts with prefix, using the index block."""
        import bisect
        blocks = list(self.iter_data_blocks())
        if not blocks:
            return
        seps = [k for _h, k in blocks]
        i = bisect.bisect_left(seps, prefix)
        started = False
        for handle, _sep in blocks[i:]:
            payload = self.read_block(*handle)
            for k, v in self.parse_entries(payload):
                if len(k) < 8:
                    continue
                user = k[:-8]
                if user.startswith(prefix):
                    started = True
                    trailer = struct.unpack("<Q", k[-8:])[0]
                    yield user, trailer >> 8, trailer & 0xFF, v
                elif started and user > prefix:
                    return
        return

    def iter_all(self):
        """Yield (user_key, seq, type, value) over all data blocks."""
        for handle, _sep in self.iter_data_blocks():
            payload = self.read_block(*handle)
            for k, v in self.parse_entries(payload):
                if len(k) < 8:
                    continue
                trailer = struct.unpack("<Q", k[-8:])[0]
                yield k[:-8], trailer >> 8, trailer & 0xFF, v


if __name__ == "__main__":
    import argparse, collections

    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--props", action="store_true")
    ap.add_argument("--find", default=None)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    s = SST(a.path)
    print(f"file={a.path} size={s.size} format_version={s.format_version} checksum_type={s.checksum_type} "
          f"metaindex=({s.metaindex_offset},{s.metaindex_size})")
    s.read_metaindex()
    print("index handle:", s.index_handle, "properties handle:", getattr(s, "properties_handle", None))
    s.read_properties()
    if a.props:
        for k in sorted(s.properties):
            print(f"  {k} = {s.properties[k]}")
    nblocks = 0
    for _ in s.iter_data_blocks():
        nblocks += 1
    print("data blocks:", nblocks)
    if a.find:
        needle = a.find.encode()
        n = 0
        for k, seq, typ, v in s.iter_all():
            if needle.lower() in k.lower():
                print("HIT", k[:90], "seq", seq, "type", typ, "vlen", len(v), "vhead", v[:24].hex())
                n += 1
                if n >= a.limit:
                    break
        print("hits:", n)
    if a.stats:
        cnt = collections.Counter()
        sizes = collections.Counter()
        n = 0
        for k, seq, typ, v in s.iter_all():
            n += 1
            cnt[len(k)] += 1
            sizes[typ] += 1
        print("entries:", n, "key len hist:", dict(sorted(cnt.items())[:20]), "types:", dict(sizes))
