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
    v["xet_usable_by_hub"] = xet_usable_by_hub()
    missing = [k for k, x in v.items() if x is None]
    return {"ok": not missing, "python": platform.python_version(), "versions": v, "missing": missing}


def xet_usable_by_hub():
    """True if hf_xet imports and huggingface_hub would use it; None (= missing) otherwise.
    is_xet_available lives in huggingface_hub.utils._runtime (not re-exported by utils in 0.33)."""
    import hf_xet  # noqa: F401
    from huggingface_hub.utils._runtime import is_xet_available
    return True if is_xet_available() else None


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


def hf_shared_repos(account):
    """Shared namespace (NOTES.md D-23): state + data repos exist, are private, and this token can write both."""
    import re
    from huggingface_hub import HfApi, hf_hub_download
    from common import hub
    api = HfApi()
    state, data = hub.ensure_repos(api)
    acct = re.sub(r"[^A-Za-z0-9._-]+", "_", account)
    payload = json.dumps({"probe": "setup", "account": account, "time": time.time()}).encode()
    ok = True
    for repo, rtype in ((state, "model"), (data, "dataset")):
        path = f"00_setup/{acct}/write_test.json"
        api.upload_file(path_or_fileobj=payload, path_in_repo=path, repo_id=repo, repo_type=rtype,
                        commit_message=f"setup: write test ({acct})")
        back = open(hf_hub_download(repo, path, repo_type=rtype, force_download=True), "rb").read()
        ok = ok and back == payload and api.repo_info(repo, repo_type=rtype).private
    log(f"저장: HF 비공개 저장소 {state}, {data} 쓰기·읽기 확인")
    shared = hub.shared_mode()
    note = "" if shared else ("단일 계정 모드: configs/infra.yaml hub.namespace가 비어 있어 토큰 주인 계정을 씁니다. "
                              "여러 Modal 계정이 함께 쓰려면 공유할 HF 계정 이름을 넣으세요(NOTES.md D-23)")
    user = hub.hf_user(api)
    ns = hub.namespace(api)
    if ns != user:   # token of another HF account: it must still be able to write ns (checked above)
        note = (note + " " if note else "") + f"토큰 주인({user})이 공유 네임스페이스({ns})와 다릅니다"
    return {"ok": ok, "namespace": ns, "token_user": user, "shared_mode": shared, "state_repo": state, "data_repo": data,
            "account": account, "note": note}


def shared_mode_check():
    from common import hub
    if hub.shared_mode():
        return {"ok": True, "namespace": hub.namespace()}
    return {"ok": False, "note": "hub.namespace가 비어 있음: 계정이 하나뿐이면 정상, 여러 계정이면 공유 HF 계정을 설정해야 함"}


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
    ap.add_argument("--account", default="unknown")
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
        c.run("hf_shared_repos", lambda: hf_shared_repos(a.account))
    else:
        c.items.append({"name": "hf_shared_repos", "required": True, "status": "실패", "detail": {"error": "skipped: hf_auth failed"}})
    c.run("shared_namespace", shared_mode_check, required=False)
    c.run("hf_public_read", lambda: hf_public_read(pr["hf_public_glob"], pr["hf_read_mb"]))
    c.run("source_hosts", lambda: hosts(pr["hosts"], pr["http_timeout_s"]), required=False)
    sys.exit(0 if c.write(a.report, {"run_info": info}) else 1)


if __name__ == "__main__":
    main()
