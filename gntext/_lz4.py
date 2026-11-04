"""Pure-Python LZ4 block decoder + Goodnotes `bv4` frame parser.

Frame variants (see docs/FORMAT.md §4):
  "bv41" | u32le raw_size | u32le compressed_size | raw LZ4 block
  "bv4-" | u32le size | raw stored bytes
  "bv4$" end marker
A stream can contain several frames; they are decompressed in order.
"""
import struct


def lz4_decompress(src: bytes, dest_size: int) -> bytes:
    """Decompress one raw LZ4 block (the format used by lz4.block.compress)."""
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]; i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]; i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= n:
            break
        if i + 2 > n:
            raise ValueError("truncated lz4 offset")
        off = src[i] | (src[i + 1] << 8); i += 2
        if off == 0 or off > len(dst):
            raise ValueError("invalid lz4 offset")
        mlen = token & 0x0F
        if mlen == 15:
            while True:
                b = src[i]; i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(dst) - off
        for k in range(mlen):
            dst.append(dst[start + k])
    return bytes(dst[:dest_size]) if dest_size else bytes(dst)


def bv4_decompress(data: bytes) -> bytes:
    """Decompress a bv4 stream (possibly several frames)."""
    out = bytearray()
    i = 0
    n = len(data)
    seen = False
    while i + 4 <= n:
        magic = data[i:i + 4]
        if magic == b"bv41":
            if i + 12 > n:
                break
            raw_sz, comp_sz = struct.unpack_from("<II", data, i + 4)
            body = data[i + 12:i + 12 + comp_sz]
            out += lz4_decompress(body, raw_sz)
            i += 12 + comp_sz
            seen = True
        elif magic == b"bv4-":
            if i + 8 > n:
                break
            sz = struct.unpack_from("<I", data, i + 4)[0]
            out += data[i + 8:i + 8 + sz]
            i += 8 + sz
            seen = True
        elif magic == b"bv4$":
            i += 4
        else:
            break
    if not seen:
        raise ValueError("no bv4 frame")
    return bytes(out)


def maybe_bv4(data: bytes) -> bytes:
    """Return decompressed bytes when data starts with/contains a bv4 frame, else data."""
    if data[:4] in (b"bv41", b"bv4-", b"bv4$") or b"bv41" in data[:64]:
        try:
            return bv4_decompress(data)
        except Exception:
            return data
    return data
