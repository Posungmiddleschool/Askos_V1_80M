"""Local launcher used by launch.sh / status.sh. Runs on the machine of whoever drives Modal.

python3 ops.py launch <stage> [--again]   deploy + spawn once, save CALL_ID.<stage>
python3 ops.py status <stage>             RUNNING / FINISHED / ENDED WITH ERROR, results -> ./runs/<dir>/

Exit codes: 0 ok/finished, 1 refused (already running or finished), 2 precondition missing,
3 running, 4 ended with error.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "askos-v1-80m"
VOLUME_NAME = "askos-data"
HF_SECRET = "huggingface"
STAGES = {
    # stage: (function name, runs dir, kwargs builder)
    "setup": ("setup", "runs/00_setup", lambda commit: {"git_commit": commit, "gpus": "L4,L40S,H100"}),
    "download": ("download", "runs/01_download", lambda commit: {"git_commit": commit, "sources": "all"}),
}
TOK = [re.compile(r"hf_[A-Za-z0-9]{20,}"), re.compile(r"\b(?:ak|as)-[A-Za-z0-9]{16,}")]


def redact(s):
    for p in TOK:
        s = p.sub("***", s)
    return s


def call_id_path(stage):
    return os.path.join(HERE, f"CALL_ID.{stage}")


def git_commit():
    try:
        c = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=HERE, capture_output=True, text=True).stdout.strip()
        return c + ("-dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "nogit"


def poll(call_id):
    """Returns (state, result_or_error)."""
    import modal
    from modal.exception import FunctionTimeoutError
    try:
        return "FINISHED", modal.FunctionCall.from_id(call_id).get(timeout=0)
    except FunctionTimeoutError as e:            # the function itself hit its timeout
        return "ENDED WITH ERROR", e
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ == "TimeoutError":   # result not ready yet
            return "RUNNING", None
        return "ENDED WITH ERROR", e


def launch(stage, again):
    fn, _, kwargs = STAGES[stage]
    cp = call_id_path(stage)
    if os.path.exists(cp):
        cid = open(cp).read().strip()
        state, _ = poll(cid)
        if state == "RUNNING":
            print(f"이미 실행 중: {cid} (CALL_ID.{stage}). 새로 띄우지 않습니다.")
            return 1
        if not again:
            print(f"이 단계는 이미 한 번 실행됐습니다({cid}, {state}). 결과는 `bash status.sh {stage}`로 보세요.\n"
                  f"다시 실행하려면 사람이 승인한 뒤 `bash launch.sh {stage} --again`.")
            return 1
    try:
        secrets = subprocess.run(["modal", "secret", "list"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"modal CLI를 쓸 수 없습니다({type(e).__name__}). 사람이 `pip install modal && modal setup`을 해야 합니다.")
        return 2
    if not re.search(rf"(^|\W){re.escape(HF_SECRET)}(\W|$)", secrets):
        print(f"Modal 시크릿 '{HF_SECRET}'이 없습니다. 사람이 터미널에서 직접:\n"
              f"  modal secret create {HF_SECRET} HF_TOKEN=<HF 쓰기 토큰>\n(에이전트는 토큰을 입력하지 않습니다.)")
        return 2
    subprocess.run(["modal", "deploy", "modal_app.py"], cwd=HERE, check=True)
    import modal
    commit = git_commit()
    call = modal.Function.from_name(APP_NAME, fn).spawn(**kwargs(commit))
    with open(cp, "w") as f:
        f.write(call.object_id)
    print(f"spawned {call.object_id} -> CALL_ID.{stage} (git {commit}). 이 셸은 끝나도 됩니다. 작업은 Modal에서 계속됩니다.")
    print(f"실시간 로그: modal app logs {APP_NAME}")
    return 0


def write_results(stage, res):
    out = os.path.join(HERE, STAGES[stage][1])
    os.makedirs(out, exist_ok=True)
    parts = res.get("runs")
    if parts is None:
        parts = {"cpu": res.get("cpu", {})} | {f"gpu_{g}": r for g, r in (res.get("gpus") or {}).items()}
    for name, r in parts.items():
        with open(os.path.join(out, f"{name}.log.txt"), "w", encoding="utf-8") as f:
            f.write(redact(r.get("log") or r.get("error") or ""))
        if r.get("report"):
            with open(os.path.join(out, f"{name}.report.json"), "w", encoding="utf-8") as f:
                json.dump(r["report"], f, ensure_ascii=False, indent=2)
    slim = {k: v for k, v in res.items() if k not in ("cpu", "gpus", "runs", "log", "summary_md")}
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2, default=str)
    if res.get("summary_md"):
        with open(os.path.join(out, res.get("summary_file", "SETUP.md")), "w", encoding="utf-8") as f:
            f.write(redact(res["summary_md"]))
    return out


def progress_download():
    """Per-source progress from /raw/<name>/_state.json on the Volume (committed every ~5 minutes)."""
    import modal
    import yaml
    try:
        vol = modal.Volume.from_name(VOLUME_NAME)
        sources = yaml.safe_load(open(os.path.join(HERE, "data", "sources.yaml"), encoding="utf-8"))
        over = yaml.safe_load(open(os.path.join(HERE, "configs", "download.yaml"), encoding="utf-8"))["overshoot"]
    except Exception as e:  # noqa: BLE001
        print(f"(진행률을 읽지 못함: {type(e).__name__})")
        return
    print(f"{'소스':<16}{'받은 GB':>9}{'목표 GB':>9}{'비율':>7}  하위 소스(소진/실패)")
    for s in sources:
        tgt = (s["read_gb"] + s.get("l3_gb", 0.0)) * over
        try:
            st = json.loads(b"".join(vol.read_file(f"raw/{s['name']}/_state.json")))
        except Exception:  # noqa: BLE001 - not started yet / not committed yet
            print(f"{s['name']:<16}{'-':>9}{tgt:>9.2f}{'-':>7}  (아직 기록 없음)")
            continue
        subs = st.get("subs", {})
        got = sum(x["bytes"] for x in subs.values()) / 1e9
        ex = sum(1 for x in subs.values() if x["exhausted"])
        bad = sum(1 for x in subs.values() if x["failed"])
        print(f"{s['name']:<16}{got:>9.2f}{tgt:>9.2f}{got / tgt:>7.0%}  {len(subs)}개 ({ex}/{bad})")


def status(stage):
    cp = call_id_path(stage)
    if not os.path.exists(cp):
        print(f"CALL_ID.{stage}가 없습니다. 아직 `bash launch.sh {stage}`를 하지 않았습니다.")
        return 2
    cid = open(cp).read().strip()
    state, res = poll(cid)
    print(f"상태: {state} ({cid})")
    if state == "RUNNING":
        if stage == "download":
            progress_download()
        print(f"15~20분 뒤 다시 `bash status.sh {stage}`. 실시간 로그: modal app logs {APP_NAME}")
        return 3
    if state == "ENDED WITH ERROR":
        out = os.path.join(HERE, STAGES[stage][1])
        os.makedirs(out, exist_ok=True)
        msg = redact(f"{type(res).__name__}: {res}")
        with open(os.path.join(out, "error.txt"), "w", encoding="utf-8") as f:
            f.write(msg)
        print(msg[:2000])
        print("재시작하지 말고 이 내용을 그대로 보고하세요.")
        return 4
    out = write_results(stage, res)
    print(f"판정: {res.get('verdict')}")
    print(f"결과 파일 → {out}/ (HF: {res.get('hub')})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["launch", "status"])
    ap.add_argument("stage", choices=sorted(STAGES))
    ap.add_argument("--again", action="store_true", help="only after a human approved a rerun")
    a = ap.parse_args()
    sys.exit(launch(a.stage, a.again) if a.cmd == "launch" else status(a.stage))


if __name__ == "__main__":
    main()
