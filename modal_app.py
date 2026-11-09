"""Askos-V1-80M on Modal: one app, one function per stage.

Notebooks never run code themselves: they call launch.sh / status.sh, which `modal deploy` this file
and `.spawn()` a function (guide rule 2: no `modal run`).
Every stage runs its real code as a subprocess through `_run`, so a crash in the code still leaves
the wrapper alive to upload the last log.

Stages in this file so far:
  setup  (notebook 00) -> setup() on CPU, which spawns gpu_probe_l4 / gpu_probe_l40s / gpu_probe_h100
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

import modal

HERE = os.path.dirname(os.path.abspath(__file__))
# Must match configs/infra.yaml (tests/test_infra.py checks).
APP_NAME = "askos-v1-80m"
VOLUME_NAME = "askos-data"
HF_SECRET = "huggingface"
STATE_REPO_NAME = "askos-v1-80m-state"
REMOTE_ROOT = "/root/askos"
VOL_PATH = "/vol"
RUNS = "/root/runs"
LOG_UPLOAD_EVERY_MIN = 30

TORCH_INDEX = "https://download.pytorch.org/whl/cu124"
FLASH_ATTN_WHEEL = ("https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/"
                    "flash_attn-2.7.4.post1+cu12torch2.6cxx11abiFALSE-cp311-cp311-linux_x86_64.whl")

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11")
    .apt_install("build-essential", "cmake", "git", "pkg-config", "zstd", "libzstd-dev", "curl")
    .pip_install("torch==2.6.0", index_url=TORCH_INDEX)           # bundles triton 3.2.0
    .pip_install("einops==0.8.0", FLASH_ATTN_WHEEL)
    .pip_install("numpy==1.26.4", "numba==0.60.0", "datasets==3.2.0", "huggingface_hub[hf_xet]==0.30.2",
                 "zstandard==0.23.0", "datasketch==1.6.5", "pyyaml==6.0.2", "pytest==8.3.4",
                 "psutil==6.1.1", "pybind11==2.13.6")
    .env({"HF_HOME": "/root/hf_cache", "HF_XET_HIGH_PERFORMANCE": "1", "PYTHONUNBUFFERED": "1",
          "TOKENIZERS_PARALLELISM": "false"})
    .add_local_dir(HERE, REMOTE_ROOT, ignore=["**/__pycache__/**", "**/*.pyc", ".git/**", "runs/**",
                                              "CALL_ID.*", "**/.ipynb_checkpoints/**", "*.ipynb"])  # always last
)
app = modal.App(APP_NAME, image=image)
data_vol = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
hf_secret = modal.Secret.from_name(HF_SECRET)

# Same patterns as common/redact.py (tests/test_infra.py checks they are identical).
TOK_PATTERNS = [
    (r"hf_[A-Za-z0-9]{20,}", "hf_***"),
    (r"\b(?:ak|as)-[A-Za-z0-9]{16,}", "modal_***"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "<private key>"),
]
_TOK = [(re.compile(p), r) for p, r in TOK_PATTERNS]


def _redact(s):
    for pat, rep in _TOK:
        s = pat.sub(rep, s)
    return s


def _state_repo():
    """Private HF repo for all state. None when no token (GPU probes run without the secret)."""
    if not os.environ.get("HF_TOKEN"):
        return None
    from huggingface_hub import HfApi
    api = HfApi()
    repo = os.environ.get("STATE_REPO") or f"{api.whoami()['name']}/{STATE_REPO_NAME}"
    api.create_repo(repo, private=True, exist_ok=True)
    return repo


def _upload(local_dir, hub_dir, msg):
    """Best effort: an upload failure must never kill the run."""
    try:
        repo = _state_repo()
        if repo and os.path.isdir(local_dir):
            from huggingface_hub import HfApi
            HfApi().upload_folder(folder_path=local_dir, path_in_repo=hub_dir, repo_id=repo, commit_message=msg)
            return f"{repo}/{hub_dir}"
    except Exception as e:  # noqa: BLE001
        print(_redact(f"[wrapper] upload failed: {type(e).__name__}: {e}"), flush=True)
    return None


def _run(cmd, env, tag, hub_dir=None):
    """Run `cmd` in REMOTE_ROOT. Each line: redact -> print -> log file. Log goes to the Hub every
    LOG_UPLOAD_EVERY_MIN minutes and at the end (only when hub_dir and a token are present)."""
    run_dir = f"{RUNS}/{tag}"
    os.makedirs(run_dir, exist_ok=True)
    log_path = f"{run_dir}/log.txt"
    full_env = {**os.environ, **env, "PYTHONPATH": REMOTE_ROOT, "RUN_TAG": tag, "RUN_DIR": run_dir}
    stop = threading.Event()

    def periodic():
        while not stop.wait(LOG_UPLOAD_EVERY_MIN * 60):
            _upload(run_dir, hub_dir, f"{tag}: periodic log")

    if hub_dir:
        threading.Thread(target=periodic, daemon=True).start()
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as f:
        p = subprocess.Popen(cmd, cwd=REMOTE_ROOT, env=full_env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
        for line in p.stdout:
            line = _redact(line.rstrip("\n"))
            print(line, flush=True)
            f.write(line + "\n")
            f.flush()
        rc = p.wait()
    stop.set()
    report = None
    rp = f"{run_dir}/report.json"
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            report = json.load(f)
    hub = _upload(run_dir, hub_dir, f"{tag}: final (rc={rc})") if hub_dir else None
    with open(log_path, encoding="utf-8") as f:
        log_text = f.read()
    return {"tag": tag, "returncode": rc, "minutes": round((time.time() - t0) / 60, 1),
            "log": log_text[-400_000:], "report": report, "hub": hub}


# --------------------------------------------------------------------------------------------
# Stage "setup" (notebook 00)
# --------------------------------------------------------------------------------------------
def _gpu_probe(label, git_commit):
    return _run([sys.executable, "-m", "setup_check.gpu", "--label", label,
                 "--report", f"{RUNS}/gpu_{label}/report.json"],
                {"GIT_COMMIT": git_commit}, f"gpu_{label}")


@app.function(gpu="L4", cpu=4.0, memory=32768, timeout=1800)
def gpu_probe_l4(git_commit: str = "unknown"):
    return _gpu_probe("L4", git_commit)


@app.function(gpu="L40S", cpu=4.0, memory=32768, timeout=1800)
def gpu_probe_l40s(git_commit: str = "unknown"):
    return _gpu_probe("L40S", git_commit)


@app.function(gpu="H100", cpu=4.0, memory=32768, timeout=1800)
def gpu_probe_h100(git_commit: str = "unknown"):
    return _gpu_probe("H100", git_commit)


GPU_PROBES = {"L4": gpu_probe_l4, "L40S": gpu_probe_l40s, "H100": gpu_probe_h100}


def _summary_md(summary):
    lines = ["# 00 설정 점검 결과", "", f"- 판정: **{summary['verdict']}**",
             f"- git: `{summary['git_commit']}`", f"- 상태 저장소: `{summary.get('state_repo')}`", ""]
    for name, part in [("CPU", summary["cpu"])] + [(f"GPU {g}", r) for g, r in summary["gpus"].items()]:
        rep = part.get("report") or {}
        v = rep.get("verdict", {})
        lines.append(f"## {name}: {'통과' if v.get('pass') else '실패'} (rc={part.get('returncode')}, {part.get('minutes')}분)")
        if part.get("error"):
            lines.append(f"- 오류: `{part['error']}`")
        for c in rep.get("checks", []):
            d = {k: x for k, x in c["detail"].items() if k != "trace"}
            lines.append(f"- {c['status']} · {c['name']} · `{json.dumps(d, ensure_ascii=False, default=str)[:300]}`")
        lines.append("")
    return "\n".join(lines)


@app.function(cpu=4.0, memory=16384, timeout=3600, secrets=[hf_secret], volumes={VOL_PATH: data_vol})
def setup(git_commit: str = "unknown", gpus: str = "L4,L40S,H100"):
    """Notebook 00. CPU checks here, GPU checks in parallel containers, one summary to the Hub."""
    calls = {g: GPU_PROBES[g].spawn(git_commit) for g in gpus.split(",") if g}
    cpu = _run([sys.executable, "-m", "setup_check.cpu", "--report", f"{RUNS}/setup_cpu/report.json",
                "--vol", VOL_PATH], {"GIT_COMMIT": git_commit}, "setup_cpu", hub_dir="00_setup/cpu")
    data_vol.commit()
    results = {}
    for g, call in calls.items():
        try:
            results[g] = call.get(timeout=1800)
        except Exception as e:  # noqa: BLE001 - e.g. no GPU capacity / quota; reported, not retried
            results[g] = {"tag": f"gpu_{g}", "returncode": -1, "report": None, "log": "",
                          "error": _redact(f"{type(e).__name__}: {e}")[:1000]}
        d = f"{RUNS}/gpu_{g}"
        os.makedirs(d, exist_ok=True)
        with open(f"{d}/log.txt", "w", encoding="utf-8") as f:
            f.write(results[g].get("log") or results[g].get("error", ""))
        if results[g].get("report"):
            with open(f"{d}/report.json", "w", encoding="utf-8") as f:
                json.dump(results[g]["report"], f, ensure_ascii=False, indent=2)
        _upload(d, f"00_setup/gpu_{g}", f"setup: gpu {g}")
    ok = lambda r: bool((r.get("report") or {}).get("verdict", {}).get("pass"))  # noqa: E731
    failed = (["cpu"] if not ok(cpu) else []) + [g for g, r in results.items() if not ok(r)]
    summary = {"stage": "setup", "git_commit": git_commit, "verdict": "통과" if not failed else f"실패: {failed}",
               "failed": failed, "state_repo": None, "cpu": cpu, "gpus": results}
    try:
        summary["state_repo"] = _state_repo()
    except Exception as e:  # noqa: BLE001
        summary["state_repo_error"] = _redact(str(e))[:300]
    d = f"{RUNS}/setup_summary"
    os.makedirs(d, exist_ok=True)
    slim = {k: v for k, v in summary.items() if k not in ("cpu", "gpus")}
    slim["parts"] = {"cpu": cpu.get("report")} | {f"gpu_{g}": r.get("report") or {"error": r.get("error")}
                                                  for g, r in results.items()}
    with open(f"{d}/report.json", "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2, default=str)
    with open(f"{d}/SETUP.md", "w", encoding="utf-8") as f:
        f.write(_summary_md(summary))
    hub = _upload(d, "00_setup", "setup: summary")
    print(f"판정: 00 설정 {summary['verdict']}", flush=True)
    print(f"보고서 → {hub or d}/SETUP.md", flush=True)
    summary["hub"] = hub
    summary["parts"] = slim["parts"]
    summary["summary_md"] = _summary_md(summary)
    return summary
