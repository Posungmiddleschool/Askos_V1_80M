"""Hugging Face dataset readers: pinned revision, file-level resume, parallel prefetch (hf-xet).

Spec §5.2 says `load_dataset(..., streaming=True)`. We read the same train files directly
(dataset card `configs:` -> data_files, at one pinned commit) because that gives exact resume
points (file, row) and parallel downloads; the documents are identical. NOTES.md D-12.
"""
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor

from data.io import atomic_write_json, open_lines, read_json, safe_name
from data.readers import Reader

DATA_EXT = (".parquet", ".arrow", ".jsonl.zst", ".json.zst", ".jsonl.gz", ".json.gz", ".jsonl", ".zst")


def _train_patterns(card, config):
    want = config or "default"
    for c in (card or {}).get("configs", []) or []:
        if c.get("config_name") != want:
            continue
        df = c.get("data_files")
        if isinstance(df, str):
            return [df]
        out = []
        for d in df or []:
            if isinstance(d, str):
                out.append(d)
            elif d.get("split") == "train":
                out += d["path"] if isinstance(d["path"], list) else [d["path"]]
        return out
    return []


def resolve_files(repo, config=None, glob_hint=None, shuffle_seed=None):
    """-> (sha, [repo-relative train files], how). With shuffle_seed the sorted list is permuted
    deterministically (for repos whose files are grouped, e.g. Stack-Edu by language)."""
    from huggingface_hub import HfApi, HfFileSystem
    info = HfApi().dataset_info(repo)
    sha = info.sha
    card = info.card_data.to_dict() if info.card_data else {}
    if glob_hint:
        patterns, how = [glob_hint], f"files hint {glob_hint}"
    else:
        patterns, how = _train_patterns(card, config), f"card config {config or 'default'}"
    if not patterns:
        raise ValueError(f"{repo}: no train data_files for config {config!r} and no files hint")
    fs = HfFileSystem()
    files = set()
    for p in patterns:
        for f in fs.glob(f"datasets/{repo}@{sha}/{p}"):
            rel = f.split(repo, 1)[1]
            rel = rel.split("/", 1)[1] if rel.startswith("@") else rel.lstrip("/")
            if rel.endswith(DATA_EXT):
                files.add(rel)
    if not files:
        raise ValueError(f"{repo}: patterns {patterns} matched no data files at {sha}")
    files = sorted(files)
    if shuffle_seed is not None:
        import random
        random.Random(shuffle_seed).shuffle(files)
        how += f", files shuffled with seed {shuffle_seed}"
    return sha, files, how


class _Prefetch:
    """Downloads units i+1..i+k while unit i is parsed; deletes a unit's file after use."""

    def __init__(self, fetch, n_units, k):
        self.fetch, self.n, self.k = fetch, n_units, k
        self.pool = ThreadPoolExecutor(max_workers=max(1, k))
        self.fut = {}
        self.lock = threading.Lock()

    def get(self, i):
        with self.lock:
            for j in range(i, min(self.n, i + self.k + 1)):
                if j not in self.fut:
                    self.fut[j] = self.pool.submit(self.fetch, j)
            f = self.fut[i]
        return f.result()

    def done(self, i):
        with self.lock:
            f = self.fut.pop(i, None)
        if f and f.done() and not f.exception():
            _rm(f.result())


def _rm(p):
    try:
        if p and os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def iter_rows(path, columns=None, start=0):
    """(row_idx, dict) for parquet or json-lines files, starting at source row `start`.
    A malformed line yields (row_idx, None)."""
    if path.endswith(".arrow"):   # `datasets` save_to_disk shards: Arrow IPC stream
        import pyarrow as pa
        row = 0
        with pa.memory_map(path) as src:
            for batch in pa.ipc.open_stream(src):
                if columns:
                    batch = batch.select([c for c in columns if c in batch.schema.names])
                for rec in batch.to_pylist():
                    if row >= start:
                        yield row, rec
                    row += 1
        return
    if path.endswith(".parquet"):
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(path)
        offs, g0 = 0, 0
        for g in range(pf.metadata.num_row_groups):
            n = pf.metadata.row_group(g).num_rows
            if offs + n > start:
                g0 = g
                break
            offs += n
        else:
            return
        row = offs
        for batch in pf.iter_batches(batch_size=1024, columns=columns, row_groups=range(g0, pf.metadata.num_row_groups)):
            for rec in batch.to_pylist():
                if row >= start:
                    yield row, rec
                row += 1
        return
    with open_lines(path) as f:
        for row, line in enumerate(f):
            if row < start:
                continue
            try:
                yield row, json.loads(line)
            except json.JSONDecodeError:
                yield row, None


class HFReader(Reader):
    """profile: object with
         columns (list|None), keep(rec, reader)->bool, make(rec, unit_name, row, reader)->doc|None,
         calibrate(reader)->dict (optional; frozen in <state_dir>/_calib_<sub>.json)."""

    def __init__(self, source, repo, config, profile, tmp_dir, state_dir, glob_hint=None, prefetch=4, shuffle_seed=None):
        self.source, self.repo, self.config = source, repo, config
        self.sub = safe_name(f"hf_{repo}_{config}" if config else f"hf_{repo}")
        self.profile = profile
        self.tmp = os.path.join(tmp_dir, self.sub)
        self.state_dir = state_dir
        self.glob_hint = glob_hint
        self.shuffle_seed = shuffle_seed
        self.k = prefetch
        self._files = None
        self.sha = None
        self.how = None
        self.calib = {}
        self._pf = None

    def _ensure(self):
        if self._files is not None:
            return
        cache = os.path.join(self.state_dir, f"_files_{self.sub}.json")
        c = read_json(cache)
        if c:   # the file list is frozen at the first run: resume points stay valid
            self.sha, self._files, self.how = c["sha"], c["files"], c["how"]
        else:
            self.sha, self._files, self.how = resolve_files(self.repo, self.config, self.glob_hint, self.shuffle_seed)
            atomic_write_json(cache, {"sha": self.sha, "files": self._files, "how": self.how})
        self._pf = _Prefetch(self._fetch, len(self._files), self.k)
        cal = os.path.join(self.state_dir, f"_calib_{self.sub}.json")
        if hasattr(self.profile, "calibrate"):
            self.calib = read_json(cal) or self.profile.calibrate(self)
            atomic_write_json(cal, self.calib)

    def _fetch(self, i):
        from huggingface_hub import hf_hub_download
        os.makedirs(self.tmp, exist_ok=True)
        return hf_hub_download(self.repo, self._files[i], repo_type="dataset", revision=self.sha,
                               local_dir=os.path.join(self.tmp, f"u{i}"))

    def units(self):
        self._ensure()
        return self._files

    def local(self, i):
        self._ensure()
        return self._pf.get(i)

    def docs(self, unit_idx, start_row):
        self._ensure()
        name = self._files[unit_idx]
        try:
            path = self._pf.get(unit_idx)
            for row, rec in iter_rows(path, self.profile.columns, start_row):
                if rec is None:
                    yield row, None
                    continue
                if not self.profile.keep(rec, self):
                    continue
                d = self.profile.make(rec, name, row, self)
                if d is not None:
                    yield row, d
        finally:
            self._pf.done(unit_idx)
            shutil.rmtree(os.path.join(self.tmp, f"u{unit_idx}"), ignore_errors=True)

    def info(self):
        try:
            self._ensure()
        except Exception as e:  # noqa: BLE001
            return {"repo": self.repo, "config": self.config, "error": str(e)[:300]}
        return {"repo": self.repo, "config": self.config, "revision": self.sha, "files": len(self._files),
                "resolved_by": self.how, "calibration": self.calib}
