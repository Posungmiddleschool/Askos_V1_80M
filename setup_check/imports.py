"""Import smoke test of the image: every third-party module the project uses is imported AND exercised once.

python -m setup_check.imports            exit 0 = ok; prints one line per check
Used by notebook 00 (setup_cpu) and as the fail-fast preflight of every stage before any worker is spawned
(2026-11-11: an unpinned pyarrow that needed NumPy 2 passed the version check of notebook 00 but failed
every parquet reader at import time; checking versions is not enough, so this imports and runs them).
"""
import io
import json
import os
import sys
import tempfile
import traceback


def _numpy():
    import numpy as np
    assert np.arange(10).sum() == 45
    return np.__version__


def _pyarrow():
    import pyarrow as pa
    import pyarrow.ipc
    import pyarrow.parquet as pq
    t = pa.table({"text": ["가나", "b"], "score": [3.0, 1.0]})
    with tempfile.TemporaryDirectory() as d:
        pq.write_table(t, os.path.join(d, "x.parquet"))
        assert pq.ParquetFile(os.path.join(d, "x.parquet")).read().to_pylist()[0]["text"] == "가나"
        with pa.OSFile(os.path.join(d, "x.arrow"), "wb") as f, pa.ipc.new_stream(f, t.schema) as w:
            w.write_table(t)
        with pa.memory_map(os.path.join(d, "x.arrow")) as src:
            assert pa.ipc.open_stream(src).read_all().num_rows == 2
    return pa.__version__


def _pandas():
    import pandas as pd
    return pd.__version__


def _datasets():
    import datasets
    return datasets.__version__


def _hub():
    import huggingface_hub
    from huggingface_hub import CommitOperationAdd, HfApi, HfFileSystem, hf_hub_download, snapshot_download  # noqa: F401
    from huggingface_hub.utils import EntryNotFoundError, HfHubHTTPError  # noqa: F401
    from huggingface_hub.utils._runtime import is_xet_available
    import hf_xet  # noqa: F401
    assert is_xet_available(), "huggingface_hub cannot use hf_xet"
    return huggingface_hub.__version__


def _zstd():
    import zstandard
    b = io.BytesIO()
    with zstandard.ZstdCompressor(level=3, threads=2).stream_writer(b, closefd=False) as w:
        w.write(b'{"text":"x"}\n' * 100)
    assert zstandard.ZstdDecompressor().stream_reader(io.BytesIO(b.getvalue())).read().count(b"\n") == 100
    return zstandard.__version__


def _numba():
    import numba
    import numpy as np

    @numba.njit(cache=False)
    def s(a):
        t = 0
        for v in a:
            t += v
        return t
    assert s(np.arange(100, dtype=np.int64)) == 4950
    return numba.__version__


def _datasketch():
    import datasketch
    m1, m2 = datasketch.MinHash(num_perm=128), datasketch.MinHash(num_perm=128)
    for w in "a b c d e".split():
        m1.update(w.encode())
        m2.update(w.encode())
    assert m1.jaccard(m2) == 1.0
    return datasketch.__version__


def _scipy():
    import scipy
    return scipy.__version__


def _torch():
    import torch
    return torch.__version__


def _yaml_psutil_pybind():
    import psutil
    import pybind11
    import yaml
    return f"pyyaml {yaml.__version__}, psutil {psutil.__version__}, pybind11 {pybind11.__version__}"


def _project():
    # every project module imports cleanly (catches a missing dependency of our own code)
    import common.config, common.hub, common.log, common.redact, common.validate  # noqa: E401,F401
    import data.download, data.filters, data.io, data.polite, data.profiles, data.quota  # noqa: E401,F401
    import data.readers.hf, data.readers.hn, data.readers.web  # noqa: E401,F401
    return "ok"


CHECKS = [("numpy", _numpy), ("pyarrow (parquet + ipc)", _pyarrow), ("pandas", _pandas), ("datasets", _datasets),
          ("huggingface_hub + hf_xet", _hub), ("zstandard", _zstd), ("numba (njit)", _numba), ("scipy", _scipy),
          ("datasketch (minhash)", _datasketch), ("torch", _torch), ("pyyaml/psutil/pybind11", _yaml_psutil_pybind),
          ("project modules", _project)]


def run(skip=()):
    results = {}
    for name, fn in CHECKS:
        if name in skip:
            continue
        try:
            results[name] = {"ok": True, "detail": fn()}
        except BaseException as e:  # noqa: BLE001 - ImportError, AssertionError, anything
            results[name] = {"ok": False, "detail": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1200:]}
    return results


def main():
    res = run()
    for name, r in res.items():
        print(f"사전 점검 {'통과' if r['ok'] else '실패'}: import {name} · {r['detail']}", flush=True)
    bad = [n for n, r in res.items() if not r["ok"]]
    print(f"판정: import 점검 {'통과' if not bad else '실패 ' + json.dumps(bad, ensure_ascii=False)}", flush=True)
    if bad:
        for n in bad:
            print(res[n]["trace"], flush=True)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
