"""Notebook 00: environment checks run inside Modal containers (cpu.py, gpu.py)."""
import json
import os
import traceback

from common.log import log
from common.redact import redact


class Checks:
    """Runs named checks, never stops on the first failure, writes a JSON report."""

    def __init__(self, title):
        self.title = title
        self.items = []

    def run(self, name, fn, required=True):
        log(f"사전 점검 시작: {name}")
        try:
            detail = fn() or {}
            ok = bool(detail.pop("ok", True))
        except Exception as e:  # noqa: BLE001 - every failure is recorded, not raised
            ok, detail = False, {"error": redact(f"{type(e).__name__}: {e}"),
                                 "trace": redact(traceback.format_exc()[-2000:])}
        status = "통과" if ok else ("실패" if required else "경고")
        self.items.append({"name": name, "required": required, "status": status, "detail": detail})
        short = {k: v for k, v in detail.items() if k != "trace"}
        log(f"사전 점검 {status}: {name} {json.dumps(short, ensure_ascii=False, default=str)[:600]}")
        return ok, detail

    def verdict(self):
        failed = [i["name"] for i in self.items if i["status"] == "실패"]
        warned = [i["name"] for i in self.items if i["status"] == "경고"]
        return {"pass": not failed, "failed": failed, "warnings": warned}

    def write(self, report_path, extra=None):
        v = self.verdict()
        rep = {"title": self.title, "verdict": v, "checks": self.items, **(extra or {})}
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
        log(f"판정: {self.title} {'통과' if v['pass'] else '실패'}"
            f" (실패 {len(v['failed'])}: {v['failed']}, 경고 {len(v['warnings'])}: {v['warnings']})")
        log(f"보고서 → {report_path}")
        return v["pass"]
