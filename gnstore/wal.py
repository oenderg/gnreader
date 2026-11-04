"""RocksDB WAL (.log) reader for the live store.

Log format (stable across RocksDB versions):
  32 KiB blocks; record header = crc32 u32 | length u16 | type u8
  types: 1 FULL, 2 FIRST, 3 MIDDLE, 4 LAST (0 = zero padding to block end)
  FIRST/MIDDLE/LAST fragments concatenate into one logical record.

Logical record plays a WriteBatch:
  seq fixed64 | count fixed32 | count * (tag u8 + entry)
  tag 1 = put   : varint klen | key | varint vlen | value
  tag 0 = delete: varint klen | key
  tag 2 = merge : same as put
  tag 3 = log data: varint len | blob (not a key/value)
  tags 4..8 = column-family variants (single-CF DB should not use them)

No per-entry protection bytes: OPTIONS has memtable_protection_bytes_per_key=0.
"""
import glob, os, struct

BLOCK = 32768
HEADER = 7


def _varint(buf, pos):
    r = s = 0
    while True:
        b = buf[pos]
        pos += 1
        r |= (b & 0x7F) << s
        if not b & 0x80:
            return r, pos
        s += 7


def logical_records(path):
    """Yield raw logical records (bytes) from one WAL file."""
    with open(path, "rb") as f:
        data = f.read()
    pos = 0
    pending = bytearray()
    while pos + HEADER <= len(data):
        block_end = min(len(data), (pos // BLOCK + 1) * BLOCK)
        crc, length, rtype = struct.unpack("<IHB", data[pos:pos + HEADER])
        if length == 0 and rtype == 0:
            pos = block_end          # zero padding to next block
            continue
        start = pos + HEADER
        end = start + length
        if end > block_end:
            break                    # truncated tail
        frag = data[start:end]
        if rtype == 1:               # FULL
            if pending:
                pending.clear()
            yield frag
        elif rtype == 2:             # FIRST
            pending = bytearray(frag)
        elif rtype == 3:             # MIDDLE
            pending += frag
        elif rtype == 4:             # LAST
            pending += frag
            yield bytes(pending)
            pending.clear()
        pos = end
        # records always start at the next byte; no alignment padding in RocksDB


def batch_ops(record):
    """Parse one WriteBatch record -> (seq, [(key, type, value)])."""
    if len(record) < 12:
        return None
    seq = struct.unpack("<Q", record[:8])[0]
    count = struct.unpack("<I", record[8:12])[0]
    pos = 12
    ops = []
    for _ in range(count):
        if pos >= len(record):
            break
        tag = record[pos]
        pos += 1
        if tag in (0, 1, 2):         # delete / put / merge
            klen, pos = _varint(record, pos)
            key = record[pos:pos + klen]
            pos += klen
            if tag == 0:
                ops.append((key, 0, b""))
            else:
                vlen, pos = _varint(record, pos)
                val = record[pos:pos + vlen]
                pos += vlen
                ops.append((key, 1, val))
        elif tag == 3:               # log data blob
            blen, pos = _varint(record, pos)
            pos += blen
        else:
            # column family / transactional tags: not expected in this DB
            raise ValueError(f"unsupported WriteBatch tag {tag} at {pos}")
    return seq, ops


def iter_wal(db_dir):
    """Yield (wal_path, seq, key, type, value) in file-number order."""
    paths = sorted(glob.glob(os.path.join(db_dir, "*.log")),
                   key=lambda p: int(os.path.basename(p).split(".")[0]))
    for path in paths:
        for rec in logical_records(path):
            parsed = batch_ops(rec)
            if not parsed:
                continue
            seq, ops = parsed
            for i, (key, typ, val) in enumerate(ops):
                yield path, seq + i, key, typ, val
