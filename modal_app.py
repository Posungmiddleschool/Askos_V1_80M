"""Askos-V1-80M on Modal: one app, one function per stage.

Notebooks never run code themselves: they call launch.sh / status.sh, which `modal deploy` this file
and `.spawn()` a function (guide rule 2: no `modal run`).
Every stage runs its real code as a subprocess through `_run`, so a crash in the code still leaves
the wrapper alive to upload the last log.

Stages in this file so far:
  setup       (notebook 00) -> setup() on CPU, which spawns gpu_probe_l4 / gpu_probe_l40s / gpu_probe_h100
  download    (notebook 01) -> download() on CPU, which spawns download_source(name) per source (12 in parallel)
  hub_status  (any)         -> lock table of the shared HF namespace (ops.py calls it for status/launch)

Several Modal accounts, one after another (NOTES.md D-23): account A runs everything; when A's credits run low a
human runs `bash stop.sh <stage>` on A (workers push to HF and release their locks as "paused"), then account B
simply runs `bash launch.sh <stage>` and resumes from the shared HF namespace (`--takeover` only if A died
without stopping). Each account's
Volume is only a cache; locks/<key>.json in the state repo say who holds what, and stage artifacts go to the
data repo (pushed every hub.push_every_min and at the end), so B loses at most that much of A's work.
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
REMOTE_ROOT = "/root/askos"
VOL_PATH = "/vol"
RUNS = "/root/runs"
LOG_UPLOAD_EVERY_MIN = 30

TORCH_INDEX = "https://download.pytorch.org/whl/cu124"
FLASH_ATTN_WHEEL = ("https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/"
                    "flash_attn-2.7.4.post1+cu12torch2.6cxx11abiFALSE-cp311-cp311-linux_x86_64.whl")

image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11")
    .apt_install("build-essential", "cmake", "git", "pkg-config", "zstd", "libzstd-dev", "curl",
                 "p7zip-full", "rsync", "groff-base")
    .pip_install("torch==2.6.0", index_url=TORCH_INDEX)           # bundles triton 3.2.0
    .pip_install("einops==0.8.0", FLASH_ATTN_WHEEL)
    .pip_install("numpy==1.26.4", "numba==0.60.0", "datasets==3.2.0", "huggingface_hub==0.33.4", "hf-xet==1.1.5",
                 "zstandard==0.23.0", "datasketch==1.6.5", "pyyaml==6.0.2", "pytest==8.3.4",
                 "psutil==6.1.1", "pybind11==2.13.6")
    .env({"HF_HOME": "/root/hf_cache", "HF_XET_HIGH_PERFORMANCE": "1", "PYTHONUNBUFFERED": "1",
          "TOKENIZERS_PARALLELISM": "false"})
    .add_local_dir(HERE, REMOTE_ROOT, ignore=["**/__pycache__/**", "**/*.pyc", ".git/**", "runs/**",
                                              "CALL_ID.*", "**/.ipynb_checkpoints/**", "*.ipynb"])  # always last
)
app = modal.App(APP_NAME, image=image)
data_vol = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
hf_secret = modal.Secret.from_name(HF_SECRET)   # the only secret the project uses
# Per-account control flags. `bash stop.sh <stage>` sets "stop/<stage>"; running workers check it every minute,
# stop their child process, push what they have to HF and release their locks as "paused".
CONTROL_DICT = "askos-control"
control = modal.Dict.from_name(CONTROL_DICT, create_if_missing=True)


def _stop_requested(stage):
    try:
        return bool(control.get(f"stop/{stage}", False))
    except Exception:  # noqa: BLE001 - a control read failure must not stop the work
        return False

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


def _hub():
    """common.hub from the project copy inside the container (never imported at deploy time)."""
    if REMOTE_ROOT not in sys.path:
        sys.path.insert(0, REMOTE_ROOT)
    from common import hub
    return hub


def _state_repo():
    """Private HF state repo in the shared namespace. None when no token (GPU probes run without the secret)."""
    if not os.environ.get("HF_TOKEN"):
        return None
    return _hub().ensure_repos()[0]


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


def _run(cmd, env, tag, hub_dir=None, vol=None, vol_commit_every_min=5, hooks=(), proc_holder=None):
    """Run `cmd` in REMOTE_ROOT. Each line: redact -> print -> log file. Log goes to the Hub every
    LOG_UPLOAD_EVERY_MIN minutes and at the end (only when hub_dir and a token are present).
    With `vol`, the Volume is committed every `vol_commit_every_min` minutes and at the end, so a
    killed container loses at most a few minutes of downloaded parts.
    `hooks`: [(every_min, fn)] called periodically in a thread (lock heartbeat, artifact push).
    `proc_holder`: dict that receives the child process as ["p"], so a hook can stop it (lost lock)."""
    run_dir = f"{RUNS}/{tag}"
    os.makedirs(run_dir, exist_ok=True)
    log_path = f"{run_dir}/log.txt"
    full_env = {**os.environ, **env, "PYTHONPATH": REMOTE_ROOT, "RUN_TAG": tag, "RUN_DIR": run_dir}
    stop = threading.Event()

    def periodic():
        while not stop.wait(LOG_UPLOAD_EVERY_MIN * 60):
            _upload(run_dir, hub_dir, f"{tag}: periodic log")

    def commit_loop():
        while not stop.wait(vol_commit_every_min * 60):
            try:
                vol.commit()
            except Exception as e:  # noqa: BLE001
                print(_redact(f"[wrapper] volume commit failed: {e}"), flush=True)

    def hook_loop(every, fn):
        while not stop.wait(every * 60):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                print(_redact(f"[wrapper] periodic hook failed: {type(e).__name__}: {e}"), flush=True)

    if hub_dir:
        threading.Thread(target=periodic, daemon=True).start()
    if vol is not None:
        threading.Thread(target=commit_loop, daemon=True).start()
    for every, fn in hooks:
        threading.Thread(target=hook_loop, args=(every, fn), daemon=True).start()
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as f:
        p = subprocess.Popen(cmd, cwd=REMOTE_ROOT, env=full_env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
        if proc_holder is not None:
            proc_holder["p"] = p
        for line in p.stdout:
            line = _redact(line.rstrip("\n"))
            print(line, flush=True)
            f.write(line + "\n")
            f.flush()
        rc = p.wait()
    stop.set()
    if vol is not None:
        try:
            vol.commit()
        except Exception as e:  # noqa: BLE001
            print(_redact(f"[wrapper] final volume commit failed: {e}"), flush=True)
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
    lines = ["# 00 설정 점검 결과", "", f"- 판정: **{summary['verdict']}**", f"- 계정: `{summary.get('account')}`",
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
            if c["status"] != "통과":   # never hide the reason behind the 300-char cut
                why = {k: d[k] for k in ("missing", "unreachable", "problems", "error", "note") if d.get(k)}
                lines.append(f"  - 원인: `{json.dumps(why, ensure_ascii=False, default=str)}`")
        lines.append("")
    return "\n".join(lines)


@app.function(cpu=4.0, memory=16384, timeout=3600, secrets=[hf_secret], volumes={VOL_PATH: data_vol})
def setup(git_commit: str = "unknown", gpus: str = "L4,L40S,H100", account: str = "unknown"):
    """Notebook 00, run once per Modal account (each checks its own environment). Results go to
    00_setup/<account>/ in the shared state repo, so accounts never overwrite each other."""
    acct = re.sub(r"[^A-Za-z0-9._-]+", "_", account)
    calls = {g: GPU_PROBES[g].spawn(git_commit) for g in gpus.split(",") if g}
    cpu = _run([sys.executable, "-m", "setup_check.cpu", "--report", f"{RUNS}/setup_cpu/report.json",
                "--vol", VOL_PATH, "--account", account], {"GIT_COMMIT": git_commit}, "setup_cpu",
               hub_dir=f"00_setup/{acct}/cpu")
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
        _upload(d, f"00_setup/{acct}/gpu_{g}", f"setup: gpu {g} ({acct})")
    ok = lambda r: bool((r.get("report") or {}).get("verdict", {}).get("pass"))  # noqa: E731
    failed = (["cpu"] if not ok(cpu) else []) + [g for g, r in results.items() if not ok(r)]
    summary = {"stage": "setup", "account": account, "git_commit": git_commit, "verdict": "통과" if not failed else f"실패: {failed}",
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
    hub = _upload(d, f"00_setup/{acct}", f"setup: summary ({acct})")
    print(f"판정: 00 설정 {summary['verdict']}", flush=True)
    print(f"보고서 → {hub or d}/SETUP.md", flush=True)
    summary["hub"] = hub
    summary["parts"] = slim["parts"]
    summary["summary_md"] = _summary_md(summary)
    return summary


# --------------------------------------------------------------------------------------------
# Stage "download" (notebook 01, spec §5.2)
# --------------------------------------------------------------------------------------------
RAW = f"{VOL_PATH}/raw"
SOURCE_NAMES = ["fineweb_edu", "dclm_top", "code", "korean_web", "papers_books", "math", "stackexchange",
                "geek_debate", "namuwiki", "wikipedia", "hacker_docs", "rebel_classics"]  # = data/sources.yaml


def _owner(account):
    return f"{account}:{_hub().hf_user()}"


def _lock_store():
    hub = _hub()
    return hub.HFStore(hub.ensure_repos()[0], "model")


def _local_state_bytes(name):
    try:
        with open(f"{RAW}/{name}/_state.json", encoding="utf-8") as f:
            st = json.load(f)
        return sum(x["bytes"] for x in st.get("subs", {}).values()), st.get("target", 0)
    except (OSError, ValueError):
        return 0, 0


@app.function(cpu=4.0, memory=32768, timeout=24 * 3600, secrets=[hf_secret],
              volumes={VOL_PATH: data_vol})
def download_source(name: str, git_commit: str = "unknown", account: str = "unknown", redo_done: bool = False,
                    takeover: bool = False):
    """One source -> /vol/raw/<name>/ -> data repo raw/<name>/. Holds lock download/<name> while running.
    If the data repo already has more of this source than the local Volume (another account worked on it),
    the local copy is replaced by the remote one first, so the download resumes instead of restarting."""
    hub, cfg = _hub(), None
    sys.path.insert(0, REMOTE_ROOT)
    from common.config import load_infra
    cfg = load_infra()["hub"]
    key, owner, store = f"download/{name}", _owner(account), _lock_store()
    if _stop_requested("download"):
        return {"tag": f"download_{name}", "returncode": 0, "skipped": "stop requested before start", "report": None, "log": ""}
    try:
        lk = hub.acquire(store, key, owner, cfg["lock_stale_hours"], force=redo_done, info={"git": git_commit},
                         takeover=takeover)
    except (hub.LockHeld, hub.AlreadyDone) as e:
        print(f"[download_source] {name}: 건너뜀 ({e})", flush=True)
        return {"tag": f"download_{name}", "returncode": 0, "skipped": str(e), "report": None, "log": ""}
    print(f"[download_source] {name}: 잠금 획득 {owner}" +
          (f" (이어받음: {lk['took_over_from']})" if lk.get("took_over_from") else ""), flush=True)
    data_vol.reload()
    remote = hub.remote_json(f"raw/{name}/_state.json")
    rb = sum(x["bytes"] for x in (remote or {}).get("subs", {}).values())
    lb, _ = _local_state_bytes(name)
    if rb > lb:
        print(f"[download_source] {name}: 원격 {rb / 1e9:.2f} GB > 로컬 {lb / 1e9:.2f} GB, 원격에서 이어 받기", flush=True)
        import shutil
        shutil.rmtree(f"{RAW}/{name}", ignore_errors=True)
        hub.pull_dir(VOL_PATH, f"raw/{name}")
        data_vol.commit()

    lost = threading.Event()
    stopped = threading.Event()
    holder = {}

    def check_stop():
        if not stopped.is_set() and _stop_requested("download"):
            stopped.set()
            print(f"[download_source] {name}: 멈춤 요청을 받았습니다. 지금까지 받은 것을 HF에 올리고 잠금을 paused로 풉니다.", flush=True)
            if holder.get("p"):
                holder["p"].terminate()

    def beat():
        b, t = _local_state_bytes(name)
        if not lost.is_set() and not hub.heartbeat(store, key, owner, {"bytes": b, "target": t}):
            lost.set()   # another account took over (--takeover or stale): stop, never overwrite its uploads
            print(f"[download_source] {name}: 잠금을 잃었습니다(다른 계정이 이어받음). 작업을 멈추고 업로드하지 않습니다.", flush=True)
            if holder.get("p"):
                holder["p"].terminate()

    def push():
        if lost.is_set():
            return
        data_vol.commit()
        hub.push_dir(VOL_PATH, f"raw/{name}")

    res = _run([sys.executable, "-m", "data.download", "source", name, "--out", RAW, "--tmp", "/tmp/askos_dl",
                "--report", f"{RUNS}/download_{name}/report.json"],
               {"GIT_COMMIT": git_commit}, f"download_{name}", hub_dir=f"01_download/{name}", vol=data_vol,
               hooks=[(cfg["heartbeat_every_min"], beat), (cfg["push_every_min"], push), (1, check_stop)],
               proc_holder=holder)
    beat()
    if lost.is_set():
        res["lost_lock"] = True
        return res
    try:
        push()
        res["pushed"] = True
    except Exception as e:  # noqa: BLE001
        res["pushed"] = False
        res["push_error"] = _redact(f"{type(e).__name__}: {e}")[:500]
    ok = bool(((res.get("report") or {}).get("verdict") or {}).get("pass")) and res["pushed"]
    b, t = _local_state_bytes(name)
    status = "paused" if stopped.is_set() else ("done" if ok else "failed")
    hub.release(store, key, owner, status, {"bytes": b, "target": t, "rc": res.get("returncode"), "pushed": res["pushed"]})
    res["lock_status"] = status
    print(f"[download_source] {name}: 잠금 해제 ({status}, {b / 1e9:.2f}/{t / 1e9:.2f} GB, HF 업로드 {'됨' if res['pushed'] else '실패'})", flush=True)
    return res


@app.function(cpu=1.0, memory=2048, timeout=600, secrets=[hf_secret])
def hub_status(prefix: str = ""):
    """Locks of the shared namespace (read only). ops.py calls this with .remote() for launch/status."""
    hub = _hub()
    sys.path.insert(0, REMOTE_ROOT)
    from common.config import load_infra
    store = _lock_store()
    lks = hub.locks(store, prefix)
    state, data = hub.repo_ids()
    return {"namespace": hub.namespace(), "shared": hub.shared_mode(), "state_repo": state, "data_repo": data,
            "hf_user": hub.hf_user(), "locks": lks, "table": hub.lock_table(lks, stale_hours=load_infra()["hub"]["lock_stale_hours"])}


@app.function(cpu=2.0, memory=8192, timeout=24 * 3600, secrets=[hf_secret], volumes={VOL_PATH: data_vol})
def download(git_commit: str = "unknown", sources: str = "all", account: str = "unknown", redo_done: bool = False,
             takeover: bool = False):
    """Notebook 01. Spawns one container per requested source (each takes its own lock), waits, then writes
    reports/D1.md over all 12 sources: sources this account did not run are read from the data repo."""
    names = SOURCE_NAMES if sources == "all" else [s for s in sources.split(",") if s]
    calls = {n: download_source.spawn(n, git_commit, account, redo_done, takeover) for n in names}
    print(f"[download] {len(calls)}개 소스 병렬 시작: {names}", flush=True)
    runs = {}
    for n, c in calls.items():
        try:
            runs[n] = c.get(timeout=24 * 3600)
        except Exception as e:  # noqa: BLE001 - reported, never retried automatically
            runs[n] = {"tag": f"download_{n}", "returncode": -1, "report": None, "log": "",
                       "error": _redact(f"{type(e).__name__}: {e}")[:1000]}
        print(f"[download] 끝: {n} rc={runs[n].get('returncode')} {runs[n].get('error', '')}", flush=True)
    data_vol.reload()
    hub = _hub()
    lks = {}
    try:                                    # reports of sources run by other accounts, and who ran what
        lks = hub.locks(_lock_store(), "download")
        missing = [n for n in SOURCE_NAMES if not os.path.exists(f"{RAW}/{n}/_report.json")]
        if missing:
            hub.pull_dir(VOL_PATH, "raw", patterns=[f"raw/{n}/_report.json" for n in missing] +
                         [f"raw/{n}/_manifest.json" for n in missing])
        os.makedirs(RAW, exist_ok=True)
        with open(f"{RAW}/_owners.json", "w", encoding="utf-8") as f:
            json.dump({k.split("/", 1)[1]: {"owner": v["owner"], "status": v["status"]} for k, v in lks.items()}, f)
        data_vol.commit()
    except Exception as e:  # noqa: BLE001
        print(_redact(f"[download] 원격 보고서를 가져오지 못함: {e}"), flush=True)
    rep = _run([sys.executable, "-m", "data.download", "report", "--raw", RAW, "--out", f"{RUNS}/download_report"],
               {"GIT_COMMIT": git_commit}, "download_report", hub_dir="01_download/report")
    d1 = f"{RUNS}/download_report/D1.md"
    os.makedirs(f"{VOL_PATH}/reports", exist_ok=True)
    md = open(d1, encoding="utf-8").read() if os.path.exists(d1) else "# D1\n\n보고서 생성 실패. download_report 로그를 보세요.\n"
    with open(f"{VOL_PATH}/reports/D1.md", "w", encoding="utf-8") as f:
        f.write(md)
    data_vol.commit()
    os.makedirs(f"{RUNS}/reports", exist_ok=True)
    with open(f"{RUNS}/reports/D1.md", "w", encoding="utf-8") as f:
        f.write(md)
    hub_url = _upload(f"{RUNS}/reports", "reports", "D1 report")
    r = rep.get("report") or {}
    verdict = "통과" if (r.get("verdict") or {}).get("pass") else f"미완: {(r.get('verdict') or {}).get('failed')}"
    paused = [n for n, x in runs.items() if x.get("lock_status") == "paused"]
    if paused:
        verdict = f"멈춤(사람 요청): {paused}는 HF에 올라간 지점부터 다른 계정이 `bash launch.sh download`로 이어 갑니다"
    print(f"판정: 01 다운로드 {verdict}", flush=True)
    print(f"보고서 → {hub_url or d1}/D1.md", flush=True)
    runs["report"] = rep
    return {"stage": "download", "account": account, "git_commit": git_commit, "verdict": verdict, "hub": hub_url,
            "locks_table": hub.lock_table(lks) if lks else "",
            "parts": {n: (x.get("report") or {"error": x.get("error")}) for n, x in runs.items()},
            "runs": runs, "summary_md": md, "summary_file": "D1.md"}
