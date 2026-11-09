"""Log lines as `[  12.3분] ...`. Fixed grep keywords: 사전 점검, 속도, 스텝, 저장, 판정, 보고서."""
import os
import time

from common.config import config_hash, load_config, manifest_hash
from common.redact import redact

T0 = time.time()


def log(msg):
    print(redact(f"[{(time.time() - T0) / 60:6.1f}분] {msg}"), flush=True)


def run_header(config_name="askos_80m"):
    """Rule 7: seed, config hash, git commit and manifest hash go first in every log."""
    cfg = load_config(config_name)
    info = {
        "seed": cfg["run"]["seed"],
        "config": config_name,
        "config_hash": config_hash(cfg),
        "git_commit": os.environ.get("GIT_COMMIT", "unknown"),
        "manifest_hash": manifest_hash(),
    }
    log("실행 정보: " + " ".join(f"{k}={v}" for k, v in info.items()))
    return info
