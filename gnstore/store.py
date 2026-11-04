"""Snapshot the live store (read-only source) and iterate RocksDB SST + WAL records."""
import os, glob, shutil, sqlite3, subprocess, json

LIVE = os.path.expanduser("~/Library/Containers/com.goodnotesapp.x/Data/Library/Databases")


def _home() -> str:
    """Writable state root; never inside the installed package or live library."""
    value = (os.environ.get("GNREADER_HOME")
             or os.environ.get("GOODNOTES_INKEYE_HOME")
             or os.path.expanduser("~/.cache/gnreader"))
    return os.path.abspath(os.path.expanduser(value))


GNREADER_HOME = _home()
DEFAULT_SNAPSHOT = os.path.join(GNREADER_HOME, "snapshot")

ROCK_DBS = ["notes_main", "index_main", "events_main"]

# Copied in this order so SSTs (immutable) land before the mutable MANIFEST/CURRENT/log set.
_COPY_ORDER = [".sst", "MANIFEST-", "OPTIONS-", "CURRENT", "IDENTITY", ".log", "LOG"]


def _clone_file(src, dst):
    """APFS clone (`cp -c`) if possible, else a normal copy."""
    try:
        subprocess.run(["cp", "-c", src, dst], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        shutil.copy2(src, dst)


def _stat(path):
    try:
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    except FileNotFoundError:
        return None


def _source_state(dbs, sqlite):
    rocks = {}
    for name in dbs:
        src = os.path.join(LIVE, name + ".db")
        rocks[name] = {f: _stat(os.path.join(src, f)) for f in sorted(os.listdir(src))
                       if os.path.isfile(os.path.join(src, f))}
    sql = {}
    for name in sqlite:
        src = os.path.join(LIVE, name + ".sqlite")
        sql[name] = {"main": _stat(src), "wal": _stat(src + "-wal"),
                     "shm": _stat(src + "-shm")}
    return {"rocks": rocks, "sqlite": sql}


def snapshot(dest=DEFAULT_SNAPSHOT, dbs=("notes_main",), sqlite=("projection",), quiet=False, fresh=False):
    """Incremental APFS-clone snapshot of the live RocksDB dirs + SQLite DBs.

    - SSTs/MANIFEST/OPTIONS/CURRENT/logs are cloned (`cp -c`); only new/changed
      files are copied after the first run.
    - SQLite is copied with `.backup` only when its source stat changed.
    Returns the destination path. `fresh` forces the stat check (it always runs).
    """
    os.makedirs(dest, exist_ok=True)
    state_path = os.path.join(dest, ".gnstore-state.json")
    cur = _source_state(dbs, sqlite)
    old = None
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                old = json.load(f)
        except Exception:
            old = None
    if old == cur:
        return dest

    for name in dbs:
        src = os.path.join(LIVE, name + ".db")
        dst = os.path.join(dest, name + ".db")
        os.makedirs(dst, exist_ok=True)
        src_files = cur["rocks"][name]
        for fname, st in src_files.items():
            target = os.path.join(dst, fname)
            if old and old.get("rocks", {}).get(name, {}).get(fname) == st and os.path.exists(target):
                continue
            tmp = target + ".tmp"
            _clone_file(os.path.join(src, fname), tmp)
            os.replace(tmp, target)
        for fname in os.listdir(dst):
            if fname not in src_files:
                os.remove(os.path.join(dst, fname))
        if not quiet:
            print(f"synced {name}.db -> {dst}")

    for name in sqlite:
        src = os.path.join(LIVE, name + ".sqlite")
        target = os.path.join(dest, name + ".sqlite")
        if old and old.get("sqlite", {}).get(name) == cur["sqlite"][name] and os.path.exists(target):
            continue
        con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        tmp = target + ".tmp"
        out = sqlite3.connect(tmp)
        con.backup(out)
        out.close()
        con.close()
        os.replace(tmp, target)
        if not quiet:
            print(f"backed up {name}.sqlite -> {target}")

    tmp_state = state_path + ".tmp"
    with open(tmp_state, "w") as f:
        json.dump(cur, f)
    os.replace(tmp_state, state_path)
    return dest


def iter_sst_files(db_dir):
    return sorted(glob.glob(os.path.join(db_dir, "*.sst")))


def _wal_overlay(db_dir):
    from gnstore.wal import iter_wal
    overlay = {}
    for _path, seq, key, typ, val in iter_wal(db_dir):
        cur = overlay.get(key)
        if cur is None or seq > cur[0]:
            overlay[key] = (seq, typ, val)
    return overlay


def iter_items(db_dir, include_wal=True, prefix=None):
    """Yield (key, seq, type, value), with newer WAL records applied over SSTs.

    `prefix` (bytes) uses SST index-block seeks and is much faster for one page.
    """
    from gnstore.sst import SST

    overlay = _wal_overlay(db_dir) if include_wal else {}
    if prefix is not None:
        overlay = {k: v for k, v in overlay.items() if k.startswith(prefix)}
    seen = set()
    for path in iter_sst_files(db_dir):
        s = SST(path)
        s.read_metaindex()
        it = s.iter_prefix(prefix) if prefix is not None else s.iter_all()
        for k, seq, typ, v in it:
            o = overlay.get(k)
            if o is not None and o[0] > seq:
                continue                     # newer WAL version wins
            seen.add(k)
            yield k, seq, typ, v
    for k, (seq, typ, v) in overlay.items():
        if k in seen or typ == 0:
            continue
        yield k, seq, typ, v


def count_wal_only(db_dir, prefix):
    """(sst_keys, wal_keys) counts for a key prefix; useful for freshness checks."""
    from gnstore.sst import SST
    pre = prefix if isinstance(prefix, bytes) else prefix.encode()
    sst = 0
    for path in iter_sst_files(db_dir):
        s = SST(path); s.read_metaindex()
        for k, _seq, _typ, _v in s.iter_all():
            if k.startswith(pre):
                sst += 1
    wal = sum(1 for _p, _s, k, _t, _v in __import__("gnstore.wal", fromlist=["iter_wal"]).iter_wal(db_dir)
              if k.startswith(pre))
    return sst, wal
