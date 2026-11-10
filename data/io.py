"""Raw shard writing, resumable state, compressed line readers.

Raw format (spec §5.2): raw/{name}/{sub}/{unit:06d}_{row:010d}.jsonl.zst, one JSON object per line
with fields text, source, doc_id (+ meta: small dict used later by the decay stream, NOTES.md D-09).
"""
import gzip
import hashlib
import io
import json
import os
import re
import tempfile


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:120]


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class PartWriter:
    """Writes one part to a temp file; commit() renames it into place, abort() deletes it.
    `codec`: 'zst' (production) or 'raw' (tests without zstandard)."""

    def __init__(self, final_path, codec="zst", level=6):
        self.final_path = final_path + (".jsonl.zst" if codec == "zst" else ".jsonl")
        os.makedirs(os.path.dirname(self.final_path), exist_ok=True)
        self.tmp_path = self.final_path + ".tmp"
        self._raw = open(self.tmp_path, "wb")
        self._sha = hashlib.sha256()
        if codec == "zst":
            import zstandard
            self._z = zstandard.ZstdCompressor(level=level, threads=2).stream_writer(self._raw, closefd=False)
        else:
            self._z = self._raw
        self.text_bytes = 0
        self.docs = 0

    def write(self, doc):
        line = (json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self._z.write(line)
        n = len(doc["text"].encode("utf-8"))
        self.text_bytes += n
        self.docs += 1
        return n

    def commit(self):
        if self._z is not self._raw:
            self._z.close()
        self._raw.flush()
        os.fsync(self._raw.fileno())
        self._raw.close()
        with open(self.tmp_path, "rb") as f:
            for b in iter(lambda: f.read(1 << 22), b""):
                self._sha.update(b)
        size = os.path.getsize(self.tmp_path)
        os.replace(self.tmp_path, self.final_path)
        return {"path": self.final_path, "docs": self.docs, "text_bytes": self.text_bytes,
                "file_bytes": size, "sha256": self._sha.hexdigest()}

    def abort(self):
        try:
            if self._z is not self._raw:
                self._z.close()
            self._raw.close()
        finally:
            if os.path.exists(self.tmp_path):
                os.remove(self.tmp_path)


def open_lines(path_or_file, name=None):
    """Text line iterator over a plain / .gz / .zst / .bz2 file (path or binary file object)."""
    name = name or (path_or_file if isinstance(path_or_file, str) else getattr(path_or_file, "name", ""))
    f = open(path_or_file, "rb") if isinstance(path_or_file, str) else path_or_file
    if name.endswith(".zst"):
        import zstandard
        f = zstandard.ZstdDecompressor().stream_reader(f, read_size=1 << 20)
    elif name.endswith(".gz"):
        f = gzip.GzipFile(fileobj=f)
    elif name.endswith(".bz2"):
        import bz2
        f = bz2.BZ2File(f)
    return io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="\n")


def sha256_file(path, algo="sha256"):
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()
