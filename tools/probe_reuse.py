"""Pilot measurement for ROADMAP §2.6: verbatim-reuse coverage vs library size, and word-substitution resumption.
Cursor-free and model-free: suffix array + matching statistics. It counts covered bytes; it is NOT a loss measurement.

Local tool (not part of the Modal image): needs numpy, numba, pyarrow, huggingface_hub, pydivsufsort, ~8 GB RAM.
  python tools/probe_reuse.py HuggingFaceFW/fineweb-edu sample/10BT/000_00000.parquet text 512 6 web
  python tools/probe_reuse.py codeparrot/github-code-clean data/train-00000-of-00880.parquet code 512 6 code
Library = first <lib_mb> MB of documents of the file, validation = last <val_mb> MB of documents of the same file.
Results of 2026-11-11 are in ROADMAP.md §2.6 and RESULTS_LOG.md."""
import sys, time, json
import numpy as np, numba as nb, pyarrow.parquet as pq, pydivsufsort
from huggingface_hub import hf_hub_download

repo, fname, col, lib_mb, val_mb, tag = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6]
MAXLEN = 256

def load(path, col, lib_bytes, val_bytes):
    pf = pq.ParquetFile(path)
    ng = pf.metadata.num_row_groups
    lib, n = [], 0
    g = 0
    while g < ng and n < lib_bytes:
        for t in pf.read_row_group(g, columns=[col]).column(col).to_pylist():
            if t:
                b = t.encode("utf-8", "ignore"); lib.append(b); n += len(b) + 1
                if n >= lib_bytes: break
        g += 1
    val, m, gv = [], 0, ng - 1
    while gv > g and m < val_bytes:                     # validation docs from the far end of the file
        for t in pf.read_row_group(gv, columns=[col]).column(col).to_pylist():
            if t:
                b = t.encode("utf-8", "ignore"); val.append(b); m += len(b) + 1
                if m >= val_bytes: break
        gv -= 1
    return lib, val

@nb.njit(cache=True)
def lcp_cmp(lib, s, val, i, maxlen):
    n, m, k = len(lib), len(val), 0
    while k < maxlen and s + k < n and i + k < m:
        a, b = lib[s + k], val[i + k]
        if a != b:
            return k, (1 if b > a else -1)
        k += 1
    if k == maxlen or i + k >= m:
        return k, 0
    return k, 1

@nb.njit(parallel=True, cache=True)
def match_stats(lib, sa, val, maxlen):
    n = len(val); L = np.zeros(n, np.int32); P = np.zeros(n, np.int64); ns = len(sa)
    for i in nb.prange(n):
        lo, hi, bl, bp = 0, ns, 0, -1
        while lo < hi:
            mid = (lo + hi) // 2
            k, c = lcp_cmp(lib, sa[mid], val, i, maxlen)
            if k > bl: bl = k; bp = sa[mid]
            if c == 0: lo = mid; break
            if c > 0: lo = mid + 1
            else: hi = mid
        for j in (lo - 1, lo):
            if 0 <= j < ns:
                k, c = lcp_cmp(lib, sa[j], val, i, maxlen)
                if k > bl: bl = k; bp = sa[j]
        L[i] = bl; P[i] = bp
    return L, P

@nb.njit(cache=True)
def coverage(L, ell):
    n, until, c = len(L), 0, 0
    for i in range(n):
        if L[i] >= ell and i + L[i] > until: until = i + L[i]
        if i < until: c += 1
    return c / n

@nb.njit(cache=True)
def readable(L, m, minlen):
    """byte t is 'readable' if it lies inside a match (>= minlen) that started at least m bytes before t."""
    n, c = len(L), 0
    until = 0
    for t in range(n):
        i = t - m
        if i >= 0 and L[i] >= minlen and i + L[i] > until: until = i + L[i]
        if t < until: c += 1
    return c / n

@nb.njit(cache=True)
def is_word(b):
    return (48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122) or b == 95 or (b >= 128 and b < 254)

@nb.njit(cache=True)
def lcp_plain(val, i, lib, q, maxlen):
    k, n, m = 0, len(lib), len(val)
    while k < maxlen and i + k < m and q + k < n and val[i + k] == lib[q + k] and val[i + k] < 254:
        k += 1
    return k

@nb.njit(cache=True)
def chains(val, lib, L, P, min_start, min_resume):
    """Follow each long match as a chain that may step over substituted words. Returns counters."""
    n = len(val)
    starts = 0; copy_bytes = 0; chain_bytes = 0; word_mism = 0; resumes = 0; resumed_bytes = 0
    replay_opp = 0; replay_hit = 0; other_mism = 0
    in_chain_until = 0
    msrc = np.zeros(8, np.uint64); mtgt = np.zeros(8, np.uint64)
    i = 0
    while i < n:
        if L[i] < min_start or i < in_chain_until:
            i += 1; continue
        starts += 1
        d = i + L[i]; q = P[i] + L[i]; copy_bytes += L[i]; covered = L[i]; nmap = 0
        while d < n and q < len(lib):
            if not is_word(lib[q]):
                other_mism += 1; break
            word_mism += 1
            ws_l = q
            while ws_l > 0 and is_word(lib[ws_l - 1]) and q - ws_l < 32: ws_l -= 1
            we_l = q
            while we_l < len(lib) and is_word(lib[we_l]) and we_l - q < 32: we_l += 1
            ws_d = d
            while ws_d > 0 and is_word(val[ws_d - 1]) and d - ws_d < 48: ws_d -= 1
            we_d = d
            while we_d < n and is_word(val[we_d]) and we_d - d < 48: we_d += 1
            if we_l - ws_l >= 32 or we_d - ws_d >= 48: break
            hs = np.uint64(1469598103934665603)
            for k in range(ws_l, we_l): hs = (hs ^ np.uint64(lib[k])) * np.uint64(1099511628211)
            ht = np.uint64(1469598103934665603)
            for k in range(ws_d, we_d): ht = (ht ^ np.uint64(val[k])) * np.uint64(1099511628211)
            for k in range(nmap):
                if msrc[k] == hs:
                    replay_opp += 1
                    if mtgt[k] == ht: replay_hit += 1
            r = lcp_plain(val, we_d, lib, we_l, 256)
            if r < min_resume: break
            resumes += 1; resumed_bytes += r
            if nmap < 8: msrc[nmap] = hs; mtgt[nmap] = ht; nmap += 1
            covered = (we_d + r) - i
            d = we_d + r; q = we_l + r
        chain_bytes += covered
        in_chain_until = i + covered
        i += 1
    return starts, copy_bytes, chain_bytes, word_mism, resumes, resumed_bytes, replay_opp, replay_hit, other_mism

t0 = time.time()
path = hf_hub_download(repo, fname, repo_type="dataset", local_dir="dl_" + tag)
lib_docs, val_docs = load(path, col, lib_mb << 20, val_mb << 20)
val = np.frombuffer(b"\xfe".join(val_docs) + b"\xfe", dtype=np.uint8).copy()
print(f"[{tag}] lib docs {len(lib_docs):,}, val docs {len(val_docs):,} ({len(val)/1e6:.1f} MB), load {time.time()-t0:.0f}s", flush=True)
out = {"tag": tag, "val_mb": round(len(val) / 1e6, 2), "sizes": {}}
size = lib_mb
sizes = []
while size >= 32: sizes.append(size); size //= 2
for mb in reversed(sizes):
    t = time.time()
    buf = bytearray(); k = 0
    while k < len(lib_docs) and len(buf) < (mb << 20):
        buf += lib_docs[k] + b"\xff"; k += 1
    lib = np.frombuffer(bytes(buf[: mb << 20]), dtype=np.uint8).copy()
    sa = pydivsufsort.divsufsort(lib)
    tb = time.time() - t; t = time.time()
    L, P = match_stats(lib, sa, val, MAXLEN)
    r = {"mean_L": float(L.mean()), "median_L": float(np.median(L)),
         "cov16": coverage(L, 16), "cov32": coverage(L, 32), "cov64": coverage(L, 64),
         "readable8_16": readable(L, 8, 16), "readable8_24": readable(L, 8, 24), "readable16_32": readable(L, 16, 32),
         "sa_s": round(tb), "ms_s": round(time.time() - t)}
    if mb == sizes[0]:
        for name, ms, mr in (("start16_resume8", 16, 8), ("start16_resume16", 16, 16), ("start32_resume16", 32, 16)):
            st, cb, chb, wm, rs, rb, ro, rh, om = chains(val, lib, L, P, ms, mr)
            r[name] = {"chains": st, "copy_only_frac": cb / len(val), "with_subst_frac": chb / len(val), "word_mismatch": wm,
                       "other_mismatch": om, "resume_rate": rs / max(1, wm), "mean_resumed_bytes": rb / max(1, rs),
                       "replay_opportunities": ro, "replay_hit_rate": rh / max(1, ro)}
    if mb == sizes[0]:
        # near-duplicate proxy: drop validation docs that are >= 20% covered by >= 64-byte verbatim matches
        ends = np.flatnonzero(val == 254)
        keep = np.zeros(len(val), np.bool_); s0 = 0; nd = 0; kd = 0
        for e in ends:
            Ld = L[s0:e]; nd += 1
            if len(Ld) and coverage(Ld, 64) < 0.2:
                keep[s0:e + 1] = True; kd += 1
            s0 = e + 1
        vk, Lk, Pk = val[keep], L[keep], P[keep]
        rk = {"docs_kept": kd, "docs_total": nd, "bytes_kept_frac": float(keep.mean()),
              "cov32": coverage(Lk, 32), "cov64": coverage(Lk, 64), "readable8_24": readable(Lk, 8, 24), "readable16_32": readable(Lk, 16, 32)}
        st, cb, chb, wm, rs, rb, ro, rh, om = chains(vk, lib, Lk, Pk, 32, 16)
        rk["start32_resume16"] = {"copy_only_frac": cb / len(vk), "with_subst_frac": chb / len(vk), "word_mismatch": wm,
                                  "resume_rate": rs / max(1, wm), "mean_resumed_bytes": rb / max(1, rs),
                                  "replay_opportunities": ro, "replay_hit_rate": rh / max(1, ro)}
        r["low_overlap_docs"] = rk
    out["sizes"][mb] = r
    print(f"[{tag}] {mb:>4} MB: " + json.dumps(r), flush=True)
    del sa, lib
json.dump(out, open(f"result_{tag}.json", "w"), indent=1)
print("done", round(time.time() - t0), "s")
