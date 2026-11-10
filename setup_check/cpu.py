"""CPU-side checks: packages, toolchain, configs, unit tests, Modal Volume, Hugging Face access.

python -m setup_check.cpu --report /root/runs/setup_cpu/report.json --vol /vol
Never prints a token: only the HF user name and token role are logged.
"""
import argparse
import hashlib
import importlib.metadata as md
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request

from common.config import load_config, load_infra, load_sources, load_yaml, path
from common.log import log, run_header
from common.validate import check_config, check_smoke, check_sources
from setup_check import Checks

PACKAGES = ["torch", "triton", "flash-attn", "numpy", "numba", "datasets", "huggingface_hub", "hf-xet",
            "zstandard", "datasketch", "PyYAML", "pytest", "psutil", "pybind11", "einops"]


def packages():
    v = {}
    for p in PACKAGES:
        try:
            v[p] = md.version(p)
        except md.PackageNotFoundError:
            v[p] = None
    try:  # hf-xet must also be usable by huggingface_hub, not just installed
        from huggingface_hub.utils import is_xet_available
        v["xet_usable_by_hub"] = bool(is_xet_available()) or None
    except ImportError:
        v["xet_usable_by_hub"] = None
    missing = [k for k, x in v.items() if x is None]
    return {"ok": not missing, "python": platform.python_version(), "versions": v, "missing": missing}


def toolchain():
    out = {}
    for tool in ["g++", "cmake", "git", "zstd"]:
        exe = shutil.which(tool)
        out[tool] = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.splitlines()[0] if exe else None
    return {"ok": all(out.values()), **out}


def resources(vol):
    import psutil
    d = {"cpus_visible": os.cpu_count(), "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
         "disk_root_free_gb": round(shutil.disk_usage("/").free / 1e9, 1)}
    if os.path.isdir(vol):
        d["disk_vol_free_gb"] = round(shutil.disk_usage(vol).free / 1e9, 1)
    return d


def configs():
    base = load_config("askos_80m")
    problems = check_config(base)
    problems += check_smoke(base, load_yaml(path("configs", "smoke.yaml")))
    problems += check_sources(load_sources(), base)
    return {"ok": not problems, "problems": problems}


def unit_tests():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=path(), capture_output=True, text=True)
    tail = (r.stdout + r.stderr).strip().splitlines()[-15:]
    return {"ok": r.returncode == 0, "returncode": r.returncode, "tail": tail}


def volume(vol, mb):
    if not os.path.isdir(vol):
        return {"ok": False, "error": f"{vol} is not mounted"}
    p = os.path.join(vol, "_setup_probe.bin")
    chunk = 8 * 1024 * 1024
    h_w = hashlib.sha256()
    t = time.time()
    with open(p, "wb") as f:
        for _ in range(mb // 8):
            b = os.urandom(chunk)
            h_w.update(b)
            f.write(b)
        f.flush()
        os.fsync(f.fileno())
    w_s = time.time() - t
    h_r = hashlib.sha256()
    t = time.time()
    with open(p, "rb") as f:
        while b := f.read(chunk):
            h_r.update(b)
    r_s = time.time() - t
    os.remove(p)
    same = h_w.hexdigest() == h_r.hexdigest()
    log(f"속도: Volume 쓰기 {mb / w_s:,.0f} MB/s, 읽기 {mb / r_s:,.0f} MB/s (실측, {mb} MB)")
    return {"ok": same, "mb": mb, "write_mb_s": round(mb / w_s), "read_mb_s": round(mb / r_s), "readback_equal": same}


def hf_auth():
    from huggingface_hub import HfApi
    if not os.environ.get("HF_TOKEN"):
        return {"ok": False, "error": "HF_TOKEN is not set: the 'huggingface' Modal secret is missing or has no HF_TOKEN key"}
    info = HfApi().whoami()
    role = (info.get("auth") or {}).get("accessToken", {}).get("role")
    return {"ok": role != "read", "user": info.get("name"), "token_role": role,
            "note": "role 'read' cannot write checkpoints" if role == "read" else ""}


def hf_state_repo(user, repo_name):
    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi()
    repo = os.environ.get("STATE_REPO") or f"{user}/{repo_name}"
    api.create_repo(repo, private=True, exist_ok=True, repo_type="model")
    private = api.repo_info(repo).private
    payload = json.dumps({"probe": "setup", "time": time.time()}).encode()
    api.upload_file(path_or_fileobj=payload, path_in_repo="00_setup/write_test.json", repo_id=repo,
                    commit_message="setup: write test")
    back = open(hf_hub_download(repo, "00_setup/write_test.json", force_download=True), "rb").read()
    log(f"저장: HF 비공개 저장소 {repo} 쓰기·읽기 확인")
    return {"ok": bool(private) and back == payload, "repo": repo, "private": private, "readback_equal": back == payload}


def hf_public_read(glob, mb):
    from huggingface_hub import HfFileSystem
    fs = HfFileSystem(token=False)
    files = sorted(fs.glob(glob))
    if not files:
        return {"ok": False, "error": f"no files for {glob}"}
    want, got = mb * 1024 * 1024, 0
    t = time.time()
    with fs.open(files[0], "rb", block_size=16 * 1024 * 1024) as f:
        while got < want and (b := f.read(16 * 1024 * 1024)):
            got += len(b)
    s = time.time() - t
    log(f"속도: HF 공개 데이터 읽기 {got / 1e6 / s:,.0f} MB/s (실측, {files[0]})")
    return {"ok": got > 0, "file": files[0], "files_matched": len(files), "mb": round(got / 1e6), "mb_s": round(got / 1e6 / s)}


def hosts(urls, timeout):
    res = {}
    for u in urls:
        req = urllib.request.Request(u, headers={"User-Agent": "askos-setup-check/1.0", "Range": "bytes=0-0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res[u] = r.status
        except Exception as e:  # noqa: BLE001
            res[u] = f"{type(e).__name__}: {getattr(e, 'code', '') or e}"[:120]
    bad = [u for u, s in res.items() if not (isinstance(s, int) and s < 400)]
    return {"ok": not bad, "status": res, "unreachable": bad}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--vol", default="/vol")
    a = ap.parse_args()
    info = run_header()
    infra = load_infra()
    pr = infra["probe"]
    c = Checks("setup_cpu")
    c.run("packages", packages)
    c.run("toolchain", toolchain)
    c.run("resources", lambda: resources(a.vol))
    c.run("configs", configs)
    c.run("unit_tests", unit_tests)
    c.run("modal_volume", lambda: volume(a.vol, pr["volume_write_mb"]))
    ok, auth = c.run("hf_auth", hf_auth)
    if ok:
        c.run("hf_state_repo", lambda: hf_state_repo(auth["user"], infra["hub"]["state_repo"]))
    else:
        c.items.append({"name": "hf_state_repo", "required": True, "status": "실패", "detail": {"error": "skipped: hf_auth failed"}})
    c.run("hf_public_read", lambda: hf_public_read(pr["hf_public_glob"], pr["hf_read_mb"]))
    c.run("source_hosts", lambda: hosts(pr["hosts"], pr["http_timeout_s"]), required=False)
    sys.exit(0 if c.write(a.report, {"run_info": info}) else 1)


if __name__ == "__main__":
    main()
