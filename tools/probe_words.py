"""Premise of ROADMAP 3.2 (word-prefix births in the document): how many bytes of repeated words would a
3-byte-prefix birth read earlier than the spec's 8-byte suffix birth? Model-free count on validation docs.

Local tool (not part of the Modal image): needs pyarrow, huggingface_hub.
  python tools/probe_words.py HuggingFaceFW/fineweb-edu sample/10BT/000_00000.parquet text 6 web
Results of 2026-11-11 are in ROADMAP.md §2.6."""
import sys, re, json
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download
repo, fname, col, val_mb, tag = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
path = hf_hub_download(repo, fname, repo_type="dataset", local_dir="dl_" + tag)
pf = pq.ParquetFile(path)
docs, m, g = [], 0, pf.metadata.num_row_groups - 1
while g >= 0 and m < val_mb << 20:
    for t in pf.read_row_group(g, columns=[col]).column(col).to_pylist():
        if t:
            b = t.encode("utf-8", "ignore"); docs.append(b); m += len(b)
            if m >= val_mb << 20: break
    g -= 1
WORD = re.compile(rb"[0-9A-Za-z_\x80-\xfd]+")
tot = 0; rep_bytes = 0; rep_n = 0; spec_bytes = 0; w3_bytes = 0; w6_bytes = 0; gain3 = 0; first_n = 0; first_bytes = 0
for d in docs:
    tot += len(d)
    seen = {}                      # word -> list of start positions
    for mo in WORD.finditer(d):
        w, ws = mo.group(), mo.start()
        n = len(w)
        if n < 4: continue
        prev = seen.get(w)
        if prev:
            rep_n += 1; rep_bytes += n
            # spec: an order-8 doc-hash birth needs the last 8 bytes to match an earlier position
            best_k = 0
            for p in prev[-4:]:
                k = 0
                while k < 8 and ws - 1 - k >= 0 and p - 1 - k >= 0 and d[ws - 1 - k] == d[p - 1 - k]: k += 1
                best_k = max(best_k, k)
            j_spec = max(0, 8 - best_k)            # bytes of the word typed before the spec's cursor can be born
            j_w3 = min(j_spec, 3)                  # with a 3-byte word-prefix birth
            spec_bytes += max(0, n - j_spec); w3_bytes += max(0, n - j_w3); gain3 += max(0, n - j_w3) - max(0, n - j_spec)
        else:
            first_n += 1; first_bytes += n
        seen.setdefault(w, []).append(ws)
print(json.dumps({"tag": tag, "docs": len(docs), "bytes": tot, "repeat_word_bytes_frac": rep_bytes / tot,
                  "first_mention_word_bytes_frac": first_bytes / tot,
                  "readable_by_spec_order8_frac": spec_bytes / tot, "readable_with_word3_frac": w3_bytes / tot,
                  "gain_frac": gain3 / tot, "relative_gain": (w3_bytes - spec_bytes) / max(1, spec_bytes)}))
