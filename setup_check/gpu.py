"""GPU-side checks for the stack the later notebooks need: torch bf16, flash-attn varlen, triton,
torch.compile, numba, HBM headroom. All speeds are measured here, never assumed.

python -m setup_check.gpu --label H100 --report /root/runs/gpu_H100/report.json
"""
import argparse
import subprocess
import sys
import time

import torch
import triton
import triton.language as tl

from common.config import load_infra
from common.log import log, run_header
from setup_check import Checks


@triton.jit
def _add_kernel(x_ptr, y_ptr, o_ptr, n, BLOCK: tl.constexpr):
    offs = tl.program_id(0) * BLOCK + tl.arange(0, BLOCK)
    m = offs < n
    tl.store(o_ptr + offs, tl.load(x_ptr + offs, mask=m) + tl.load(y_ptr + offs, mask=m), mask=m)


def device_info():
    p = torch.cuda.get_device_properties(0)
    smi = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True).stdout.strip()
    return {"ok": torch.cuda.is_available(), "torch": torch.__version__, "cuda": torch.version.cuda,
            "name": p.name, "capability": f"{p.major}.{p.minor}", "hbm_gb": round(p.total_memory / 1e9, 1),
            "nvidia_smi": smi}


def matmul(n, iters):
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    a = torch.randn(n, n, device="cuda", dtype=torch.bfloat16)
    b = torch.randn(n, n, device="cuda", dtype=torch.bfloat16)
    for _ in range(3):
        a @ b
    torch.cuda.synchronize()
    s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
    s.record()
    for _ in range(iters):
        a @ b
    e.record()
    torch.cuda.synchronize()
    tflops = 2 * n ** 3 * iters / (s.elapsed_time(e) / 1e3) / 1e12
    log(f"속도: bf16 행렬곱 {tflops:,.0f} TFLOPS (실측, {n}x{n}, {iters}회)")
    return {"tflops_bf16": round(tflops, 1), "n": n}


def flash_varlen(lens, heads, hd, tol):
    import torch.nn.functional as F
    from flash_attn import flash_attn_varlen_func
    torch.manual_seed(0)
    total = sum(lens)
    q, k, v = (torch.randn(total, heads, hd, device="cuda", dtype=torch.bfloat16) for _ in range(3))
    cu = torch.tensor([0] + list(torch.tensor(lens).cumsum(0)), device="cuda", dtype=torch.int32)
    out = flash_attn_varlen_func(q, k, v, cu, cu, max(lens), max(lens), causal=True)
    err = 0.0
    for i, L in enumerate(lens):   # document mask: each document is attended to on its own
        sl = slice(int(cu[i]), int(cu[i + 1]))
        ref = F.scaled_dot_product_attention(*(t[sl].transpose(0, 1).float() for t in (q, k, v)), is_causal=True)
        err = max(err, (out[sl].float() - ref.transpose(0, 1)).abs().max().item())
    return {"ok": err < tol, "max_abs_err": err, "tol": tol, "doc_lens": lens}


def triton_kernel():
    n = 1 << 20
    x, y = torch.randn(n, device="cuda"), torch.randn(n, device="cuda")
    o = torch.empty_like(x)
    _add_kernel[(triton.cdiv(n, 1024),)](x, y, o, n, BLOCK=1024)
    return {"ok": torch.equal(o, x + y), "triton": triton.__version__}


def compile_check():
    m = torch.nn.Sequential(torch.nn.Linear(256, 1024), torch.nn.ReLU(), torch.nn.Linear(1024, 256)).cuda()
    x = torch.randn(64, 256, device="cuda")
    t = time.time()
    cm = torch.compile(m)
    y = cm(x)
    torch.cuda.synchronize()
    err = (y - m(x)).abs().max().item()
    return {"ok": err < 1e-3, "max_abs_err": err, "compile_s": round(time.time() - t, 1)}


def numba_check():
    import numba
    import numpy as np

    @numba.njit(cache=False)
    def csum(a):
        s = 0
        for v in a:
            s += v
        return s
    a = np.arange(1000, dtype=np.int64)
    return {"ok": csum(a) == a.sum(), "numba": numba.__version__}


def numba_cuda_check():
    """ROADMAP §5.4: the cursor scan kernel is planned as a numba.cuda kernel (one thread per document,
    sequential loop, zero-copy torch tensors). This only checks that such a kernel compiles and runs here."""
    import numba
    from numba import cuda
    if not cuda.is_available():
        return {"ok": False, "error": "numba.cuda.is_available() is False (NVVM / CUDA_HOME?)"}

    @cuda.jit
    def kern(x, out):
        i = cuda.grid(1)
        if i < x.size:
            s = 0
            j = 0
            while j < 8:            # per-thread sequential loop, like the scan
                s += x[i] * j
                j += 1
            out[i] = s
    x = torch.arange(1024, device="cuda", dtype=torch.int64)
    out = torch.zeros_like(x)
    kern[4, 256](cuda.as_cuda_array(x), cuda.as_cuda_array(out))
    cuda.synchronize()
    return {"ok": bool((out == x * 28).all()), "numba": numba.__version__, "zero_copy_torch": True}


def hbm(target_gb):
    """Allocate 1 GB blocks until target (or OOM). Required only where a target is set."""
    blocks, got = [], 0.0
    limit = target_gb if target_gb else torch.cuda.get_device_properties(0).total_memory / 1e9 * 0.9
    try:
        while got + 1 <= limit:
            blocks.append(torch.empty(int(1e9), dtype=torch.uint8, device="cuda"))
            got += 1
    except torch.OutOfMemoryError:
        pass
    del blocks
    torch.cuda.empty_cache()
    log(f"사전 점검: HBM {got:.0f} GB 할당 성공 (목표 {target_gb or '기록만'})")
    return {"ok": (got >= target_gb) if target_gb else True, "allocated_gb": got, "target_gb": target_gb}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--report", required=True)
    a = ap.parse_args()
    info = run_header()
    pr = load_infra()["probe"]
    c = Checks(f"gpu_{a.label}")
    ok, dev = c.run("device", device_info)
    if not ok:
        sys.exit(0 if c.write(a.report, {"run_info": info, "label": a.label}) else 1)
    c.run("matmul_bf16", lambda: matmul(pr["matmul_n"], pr["matmul_iters"]))
    c.run("flash_attn_varlen", lambda: flash_varlen(pr["flash_doc_lens"], pr["flash_heads"], pr["flash_head_dim"], pr["flash_tol"]))
    c.run("triton", triton_kernel)
    c.run("torch_compile", compile_check)
    c.run("numba", numba_check)
    c.run("numba_cuda", numba_cuda_check, required=False)
    c.run("hbm", lambda: hbm(pr["hbm_target_gb"].get(a.label)))
    sys.exit(0 if c.write(a.report, {"run_info": info, "label": a.label, "device": dev}) else 1)


if __name__ == "__main__":
    main()
