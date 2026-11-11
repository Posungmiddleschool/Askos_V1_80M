"""Local launcher used by launch.sh / status.sh. Runs on the machine of whoever drives Modal.

python3 ops.py launch <stage> [--sources a,b] [--redo-done] [--again]
python3 ops.py status <stage>
python3 ops.py whoami                       Modal account (profile) and the shared HF namespace

Several Modal accounts (NOTES.md D-23): each machine/agent drives its own Modal account. CALL_ID.<stage>
only prevents launching twice from this machine; who may run which unit of work across accounts is decided
by the locks in the shared HF state repo (common/hub.py), which `launch` shows before spawning and which
every Modal function takes again before working.

Exit codes: 0 ok/finished, 1 refused (already running, finished or held by another account),
2 precondition missing, 3 running, 4 ended with error.
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
SOURCE_NAMES = ["fineweb_edu", "dclm_top", "code", "korean_web", "papers_books", "math", "stackexchange",
                "geek_debate", "namuwiki", "wikipedia", "hacker_docs", "rebel_classics"]
STAGES = {
    # stage: (function name, runs dir, lock prefix or None)
    "setup": ("setup", "runs/00_setup", None),            # per account: every account checks its own environment
    "download": ("download", "runs/01_download", "download"),
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


def account():
    """Name of the active Modal profile (= which Modal account this machine drives)."""
    if os.environ.get("ASKOS_ACCOUNT"):
        return os.environ["ASKOS_ACCOUNT"]
    try:
        out = subprocess.run(["modal", "profile", "current"], capture_output=True, text=True, check=True).stdout.strip()
        return out.splitlines()[-1].strip() if out else "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


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


def hub_status(prefix=""):
    """Lock table of the shared namespace via the deployed hub_status function (needs a deployed app)."""
    import modal
    try:
        return modal.Function.from_name(APP_NAME, "hub_status").remote(prefix)
    except Exception as e:  # noqa: BLE001
        print(f"(공유 잠금 상태를 읽지 못함: {type(e).__name__}: {redact(str(e))[:200]})")
        return None


def print_hub(h):
    if not h:
        return
    mode = "공유 모드" if h["shared"] else "단일 계정 모드(hub.namespace 비어 있음)"
    print(f"공유 HF 네임스페이스: {h['namespace']} ({mode}) · 이 계정의 HF 사용자: {h['hf_user']}")
    print(h["table"])


def kwargs_for(stage, commit, acct, args):
    if stage == "setup":
        return {"git_commit": commit, "gpus": "L4,L40S,H100", "account": acct}
    if stage == "download":
        return {"git_commit": commit, "sources": ",".join(args["sources"]), "account": acct, "redo_done": args["redo_done"]}
    raise KeyError(stage)


def launch(stage, again, sources=None, redo_done=False):
    fn, _, lock_prefix = STAGES[stage]
    cp = call_id_path(stage)
    if os.path.exists(cp):
        cid = open(cp).read().strip()
        state, _ = poll(cid)
        if state == "RUNNING":
            print(f"이미 실행 중: {cid} (CALL_ID.{stage}). 새로 띄우지 않습니다.")
            return 1
        if not again:
            print(f"이 단계는 이 컴퓨터에서 이미 한 번 실행됐습니다({cid}, {state}). 결과는 `bash status.sh {stage}`로 보세요.\n"
                  f"다시 실행하려면 사람이 승인한 뒤 `bash launch.sh {stage} --again`.")
            return 1
    try:
        secrets = subprocess.run(["modal", "secret", "list"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"modal CLI를 쓸 수 없습니다({type(e).__name__}). 사람이 `pip install modal && modal setup`을 해야 합니다.")
        return 2
    if not re.search(rf"(^|\W){re.escape(HF_SECRET)}(\W|$)", secrets):
        print(f"Modal 시크릿 '{HF_SECRET}'이 없습니다. 사람이 터미널에서 직접:\n"
              f"  modal secret create {HF_SECRET} HF_TOKEN=<본인 HF 쓰기 토큰>\n(에이전트는 토큰을 입력하지 않습니다.)")
        return 2
    acct = account()
    subprocess.run(["modal", "deploy", "modal_app.py"], cwd=HERE, check=True)
    args = {"sources": list(sources or SOURCE_NAMES), "redo_done": redo_done}
    if lock_prefix:
        h = hub_status(lock_prefix)
        print_hub(h)
        if h is None:
            print("공유 잠금을 확인하지 못해 띄우지 않습니다(다른 계정과 겹칠 수 있음). 보고하세요.")
            return 2
        if stage == "download":
            free, skipped = [], []
            for s in args["sources"]:
                lk = h["locks"].get(f"download/{s}")
                if lk and lk["status"] == "done" and not redo_done:
                    skipped.append(f"{s}(완료: {lk['owner']})")
                elif lk and lk["status"] == "running" and not lk["owner"].startswith(acct + ":"):
                    skipped.append(f"{s}(실행 중: {lk['owner']})")
                else:
                    free.append(s)
            if skipped:
                print("다른 계정이 끝냈거나 실행 중이라 이번에 빼는 소스: " + ", ".join(skipped))
            if not free:
                print("이 계정이 맡을 소스가 남아 있지 않습니다.")
                return 1
            args["sources"] = free
            print(f"이 계정({acct})이 맡을 소스 {len(free)}개: {free}")
    import modal
    commit = git_commit()
    call = modal.Function.from_name(APP_NAME, fn).spawn(**kwargs_for(stage, commit, acct, args))
    with open(cp, "w") as f:
        f.write(call.object_id)
    print(f"spawned {call.object_id} -> CALL_ID.{stage} (계정 {acct}, git {commit}). 이 셸은 끝나도 됩니다. 작업은 Modal에서 계속됩니다.")
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
            f.write(redact(r.get("log") or r.get("error") or r.get("skipped") or ""))
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
    """Per-source progress of THIS account from /raw/<name>/_state.json on its Volume (committed every ~5 min)."""
    import modal
    import yaml
    try:
        vol = modal.Volume.from_name(VOLUME_NAME)
        sources = yaml.safe_load(open(os.path.join(HERE, "data", "sources.yaml"), encoding="utf-8"))
        over = yaml.safe_load(open(os.path.join(HERE, "configs", "download.yaml"), encoding="utf-8"))["overshoot"]
    except Exception as e:  # noqa: BLE001
        print(f"(진행률을 읽지 못함: {type(e).__name__})")
        return
    print(f"[이 계정의 Volume]\n{'소스':<16}{'받은 GB':>9}{'목표 GB':>9}{'비율':>7}  하위 소스(소진/실패)")
    for s in sources:
        tgt = (s["read_gb"] + s.get("l3_gb", 0.0)) * over
        try:
            st = json.loads(b"".join(vol.read_file(f"raw/{s['name']}/_state.json")))
        except Exception:  # noqa: BLE001 - not started here / not committed yet
            print(f"{s['name']:<16}{'-':>9}{tgt:>9.2f}{'-':>7}  (이 계정에는 기록 없음)")
            continue
        subs = st.get("subs", {})
        got = sum(x["bytes"] for x in subs.values()) / 1e9
        ex = sum(1 for x in subs.values() if x["exhausted"])
        bad = sum(1 for x in subs.values() if x["failed"])
        print(f"{s['name']:<16}{got:>9.2f}{tgt:>9.2f}{got / tgt:>7.0%}  {len(subs)}개 ({ex}/{bad})")


def status(stage):
    lock_prefix = STAGES[stage][2]
    cp = call_id_path(stage)
    if not os.path.exists(cp):
        print(f"이 컴퓨터에는 CALL_ID.{stage}가 없습니다(이 계정에서 아직 띄우지 않음).")
        if lock_prefix:
            print("[모든 계정의 공유 잠금]")
            print_hub(hub_status(lock_prefix))
        return 2
    cid = open(cp).read().strip()
    state, res = poll(cid)
    print(f"상태: {state} ({cid}, 계정 {account()})")
    if state == "RUNNING":
        if stage == "download":
            progress_download()
        if lock_prefix:
            print("[모든 계정의 공유 잠금]")
            print_hub(hub_status(lock_prefix))
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
    if res.get("locks_table"):
        print("[모든 계정의 공유 잠금]\n" + res["locks_table"])
    print(f"결과 파일 → {out}/ (HF: {res.get('hub')})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["launch", "status", "whoami"])
    ap.add_argument("stage", nargs="?", choices=sorted(STAGES))
    ap.add_argument("--again", action="store_true", help="relaunch from this machine (only after a human approved)")
    ap.add_argument("--sources", help="download only: comma-separated subset of sources for this account")
    ap.add_argument("--redo-done", action="store_true", help="download only: also redo sources marked done (human approval)")
    a = ap.parse_args()
    if a.cmd == "whoami":
        print(f"Modal 계정(프로필): {account()}")
        print_hub(hub_status(""))
        sys.exit(0)
    if not a.stage:
        ap.error("stage is required")
    if a.cmd == "launch":
        srcs = [s for s in (a.sources or "").split(",") if s] or None
        bad = [s for s in (srcs or []) if s not in SOURCE_NAMES]
        if bad:
            ap.error(f"unknown sources: {bad}")
        sys.exit(launch(a.stage, a.again, srcs, a.redo_done))
    sys.exit(status(a.stage))


if __name__ == "__main__":
    main()
