"""Quota filling across the sub-sources of one source, resumable at part granularity.

A Reader has:
  sub                   unique id, used as directory name
  units()               ordered list of unit names (files, sites, epochs, days, ...)
  docs(unit_idx, row)   yields (row_idx, doc) for unit `unit_idx` starting at source row `row`;
                        row_idx counts source rows (before filtering), so resume points are stable
  info()                dict for the report (revision, verify notes, ...)

Allocation (NOTES.md D-11): pass 1 gives each active sub-source an equal share of what is left;
later passes hand the shortfall of exhausted sub-sources to the others, until the target is met
or every sub-source is exhausted.
"""
import os
import traceback

from common.redact import redact
from data.io import PartWriter, atomic_write_json, read_json, safe_name


def _blank():
    return {"unit": 0, "row": 0, "bytes": 0, "docs": 0, "parts": [], "exhausted": False,
            "failed": False, "errors": [], "doc_errors": 0}


class Filler:
    def __init__(self, out_dir, readers, target_bytes, flush_bytes, codec="zst", max_doc_errors=30, log=print):
        self.out_dir = out_dir
        self.readers = readers
        self.target = int(target_bytes)
        self.flush = int(flush_bytes)
        self.codec = codec
        self.max_doc_errors = max_doc_errors
        self.log = log
        self.state_path = os.path.join(out_dir, "_state.json")
        st = read_json(self.state_path, {}) or {}
        self.state = {"subs": st.get("subs", {}), "passes": st.get("passes", 0), "target": self.target}
        for r in readers:
            s = self.state["subs"].setdefault(r.sub, _blank())
            if s["failed"] and len(s["errors"]) < 3:   # a new run retries a failed sub-source (max 3 tries)
                s["failed"] = s["exhausted"] = False

    def save(self):
        atomic_write_json(self.state_path, self.state)

    def total(self):
        return sum(s["bytes"] for s in self.state["subs"].values())

    def run(self):
        while True:
            active = [r for r in self.readers if not self.state["subs"][r.sub]["exhausted"]]
            left = self.target - self.total()
            if left <= 0 or not active:
                break
            self.state["passes"] += 1
            share = left / len(active)
            self.log(f"할당 {self.state['passes']}차: 남은 {left / 1e9:.2f} GB, 활성 {len(active)}개, 각 {share / 1e9:.2f} GB")
            for r in active:
                self._consume(r, share)
            self.save()
        self.save()
        return self.state

    def _new_writer(self, r, s):
        name = f"{s['unit']:06d}_{s['row']:010d}"
        return PartWriter(os.path.join(self.out_dir, safe_name(r.sub), name), codec=self.codec)

    def _commit(self, w, s):
        rec = w.commit()
        rec["path"] = os.path.relpath(rec["path"], self.out_dir)
        s["parts"].append(rec)
        s["bytes"] += rec["text_bytes"]
        s["docs"] += rec["docs"]

    def _consume(self, r, budget):
        s = self.state["subs"][r.sub]
        got = 0
        try:
            units = r.units()
        except Exception as e:  # noqa: BLE001
            self._fail(r, s, e)
            return 0
        while s["unit"] < len(units) and got < budget:
            w = None
            next_row = s["row"]
            try:
                for row, doc in r.docs(s["unit"], s["row"]):
                    next_row = row + 1
                    if doc is None:           # a row the reader counted but could not use
                        s["doc_errors"] += 1
                        if s["doc_errors"] > self.max_doc_errors:
                            raise RuntimeError(f"more than {self.max_doc_errors} bad documents")
                        continue
                    if not doc["text"].strip():   # empty after the reader's own filtering: skip, not an error
                        continue
                    if w is None:
                        w = self._new_writer(r, s)
                    got += w.write(doc)
                    if got >= budget or w.text_bytes >= self.flush:
                        self._commit(w, s)
                        w = None
                        s["row"] = next_row
                        self.save()
                        self.log(f"저장: {r.sub} 유닛 {s['unit']}/{len(units)} · 누적 {s['bytes'] / 1e9:.2f} GB · 문서 {s['docs']:,}")
                        if got >= budget:
                            return got
            except Exception as e:  # noqa: BLE001
                if w is not None:
                    w.abort()
                self._fail(r, s, e)
                return got
            if w is not None:
                self._commit(w, s)
            s["unit"] += 1
            s["row"] = 0
            self.save()
        if s["unit"] >= len(units):
            s["exhausted"] = True
            self.log(f"소진: {r.sub} ({s['bytes'] / 1e9:.2f} GB, 문서 {s['docs']:,})")
        return got

    def _fail(self, r, s, e):
        msg = redact(f"{type(e).__name__}: {e}")[:500]
        s["errors"].append({"unit": s["unit"], "row": s["row"], "error": msg,
                            "trace": redact(traceback.format_exc())[-1500:]})
        s["failed"] = s["exhausted"] = True
        self.save()
        self.log(f"실패: {r.sub} 유닛 {s['unit']} 행 {s['row']}: {msg}")
