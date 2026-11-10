"""Hacker News threads from the public HF archive open-index/hacker-news (no API key, no GCP).

Spec §5.1 geek_debate: "threads with story score >= 100". One unit = one story month m (newest first).
For unit m we read the monthly files m, m+1, ..., m+window-1: stories of month m with score >= min,
then every comment whose parent is already in the thread (ids grow with time, so one pass in id order
finds them). The newest months are dropped when they have no full comment window yet. NOTES.md D-15.
"""
import html
import os
import re
import shutil

from data.io import atomic_write_json, read_json, safe_name
from data.readers import Reader, doc

STORY, COMMENT = 1, 2
COLUMNS = ["id", "type", "deleted", "dead", "parent", "text", "url", "score", "title"]


def hn_text(s):
    s = html.unescape(s or "")
    s = re.sub(r"<p>", "\n\n", s)
    return re.sub(r"<[^>]+>", "", s).strip()


def build_threads(months_rows, min_score):
    """months_rows: [rows of month m, rows of m+1, ...]; each row a dict with COLUMNS.
    -> [(story_id, text, score, n_comments)] for stories of month m (first list), sorted by id."""
    stories = {}
    for r in months_rows[0]:
        if r["type"] == STORY and r["score"] >= min_score and not r["dead"] and not r["deleted"]:
            stories[r["id"]] = r
    root_of = {sid: sid for sid in stories}
    kids = {}
    for rows in months_rows:
        for r in sorted((x for x in rows if x["type"] == COMMENT), key=lambda x: x["id"]):
            p = r["parent"]
            if p in root_of and not r["dead"] and not r["deleted"]:
                root_of[r["id"]] = root_of[p]
                kids.setdefault(p, []).append(r)
    out = []
    for sid in sorted(stories):
        s = stories[sid]
        parts = [s["title"] or "", s["url"] or "", hn_text(s["text"])]
        n = 0
        stack = [(c, 0) for c in reversed(kids.get(sid, []))]
        while stack:                                    # depth-first, children in time order
            c, depth = stack.pop()
            t = hn_text(c["text"])
            if t:
                n += 1
                pad = "  " * depth
                parts.append("\n".join(pad + l if l else l for l in t.split("\n")))   # indent = reply depth
            stack += [(k, depth + 1) for k in reversed(kids.get(c["id"], []))]
        out.append((sid, "\n\n".join(p for p in parts if p), s["score"], n))
    return out


class HNThreads(Reader):
    def __init__(self, source, repo, min_score, window_months, tmp_dir, state_dir, cache_months=4):
        self.source, self.repo, self.min_score, self.window = source, repo, min_score, window_months
        self.sub = safe_name(f"hf_{repo}_threads")
        self.tmp, self.state_dir = os.path.join(tmp_dir, self.sub), state_dir
        self.cache_months = cache_months
        self._c = None
        self._rows = {}

    def _ensure(self):
        if self._c is not None:
            return
        cache = os.path.join(self.state_dir, f"_files_{self.sub}.json")
        c = read_json(cache)
        if c is None:
            from data.readers.hf import resolve_files
            sha, files, how = resolve_files(self.repo, None, "data/*/*.parquet")
            months = sorted(re.search(r"(\d{4}-\d{2})\.parquet$", f).group(1) for f in files)
            usable = months[: len(months) - self.window] if len(months) > self.window else []
            c = {"sha": sha, "files": {re.search(r"(\d{4}-\d{2})\.parquet$", f).group(1): f for f in files},
                 "months": months, "units": list(reversed(usable)), "how": how}
            atomic_write_json(cache, c)
        self._c = c

    def units(self):
        self._ensure()
        return self._c["units"]

    def _month_rows(self, m):
        if m not in self._rows:
            from huggingface_hub import hf_hub_download
            import pyarrow.parquet as pq
            d = os.path.join(self.tmp, m)
            p = hf_hub_download(self.repo, self._c["files"][m], repo_type="dataset", revision=self._c["sha"], local_dir=d)
            t = pq.read_table(p, columns=COLUMNS)
            self._rows[m] = t.to_pylist()
            shutil.rmtree(d, ignore_errors=True)
            while len(self._rows) > self.cache_months:      # keep only the months the next units reuse
                self._rows.pop(max(self._rows))
        return self._rows[m]

    def docs(self, unit_idx, start_row):
        self._ensure()
        m = self._c["units"][unit_idx]
        i = self._c["months"].index(m)
        window = self._c["months"][i: i + self.window]
        threads = build_threads([self._month_rows(x) for x in window], self.min_score)
        for row, (sid, text, score, n) in enumerate(threads):
            if row >= start_row:
                yield row, doc(text, self.source, f"hn:{sid}", hn_score=score, n_comments=n, month=m)

    def info(self):
        try:
            self._ensure()
        except Exception as e:  # noqa: BLE001
            return {"repo": self.repo, "error": str(e)[:300]}
        return {"repo": self.repo, "revision": self._c["sha"], "months": len(self._c["months"]),
                "story_months_used": len(self._c["units"]), "comment_window_months": self.window,
                "min_story_score": self.min_score, "resolved_by": self._c["how"]}
