"""D1a: download every source of data/sources.yaml into raw/{name}/ (spec §5.2) and write reports/D1.md.

python -m data.download source fineweb_edu --out /vol/raw --tmp /tmp/askos_dl
python -m data.download report --raw /vol/raw --out runs/download_report
python -m data.download plan                      # print targets only (no network)

Resumable: re-running a source continues from raw/{name}/_state.json; finished sources return at once.
"""
import argparse
import json
import os
import sys
import time

from common.config import load_config, load_sources, load_yaml, path
from common.log import log, run_header
from data.io import atomic_write_json, read_json
from data.polite import Polite
from data.profiles import profile_for
from data.quota import Filler
from data.readers.hf import HFReader, URLListReader
from data.readers.web import (RFC, Convokit, DebianLists, Gutenberg, HackerNews, IrcLogs, Jargon, LKML,
                              ManPages, OpenStax, StackExchange, WikiDump)


def load_dl_cfg():
    return load_yaml(path("configs", "download.yaml"))


def target_bytes(spec, overshoot):
    return int(round((spec["read_gb"] + spec.get("l3_gb", 0.0)) * overshoot * 1e9))


def plan(sources, cfg, dl):
    rows = []
    for s in sources:
        skip = bool(cfg["data"]["commercial_safe"] and s.get("research_only"))
        rows.append({"name": s["name"], "target_gb": round(target_bytes(s, dl["overshoot"]) / 1e9, 3),
                     "read_gb": s["read_gb"], "l3_gb": s.get("l3_gb", 0.0), "skip": skip,
                     "kinds": [x["kind"] for x in s["sources"]]})
    return rows


def build_readers(spec, dl, polite, tmp, state_dir):
    name, out = spec["name"], []
    for s in spec["sources"]:
        k, sid = s["kind"], s.get("id", "")
        if k == "hf":
            configs = s.get("config")
            for c in (configs if isinstance(configs, list) else [configs]):
                prof, hint_key = profile_for(name, sid, c, dl["filters"])
                if sid == "togethercomputer/RedPajama-Data-1T":
                    out.append(URLListReader(name, sid, c, prof, tmp, state_dir, polite))
                else:
                    out.append(HFReader(name, sid, c, prof, tmp, state_dir, glob_hint=dl["hf"]["files"].get(hint_key),
                                        prefetch=dl["prefetch_files"]))
        elif k == "archive" and "stackexchange" in sid:
            out.append(StackExchange(name, s, dl, polite, tmp, state_dir))
        elif k == "dump" and sid.startswith("kowikisource"):
            out.append(WikiDump(name, "kowikisource", polite, tmp, state_dir,
                                authors=dl["rebel_classics"]["kowikisource_authors"]))
        elif k == "dump" and "wikibooks" in sid:
            out += [WikiDump(name, w, polite, tmp, state_dir) for w in dl["papers_books"]["wikibooks"]]
        elif k == "url" and "OpenStax" in sid:
            out.append(OpenStax(name, dl, polite, tmp, state_dir))
        elif k == "url" and "rfc-editor" in sid:
            out.append(RFC(name, dl, polite, tmp, state_dir))
        elif k == "url" and "man-pages" in sid:
            out.append(ManPages(name, dl, polite, tmp, state_dir))
        elif k == "url" and "Jargon" in sid:
            out.append(Jargon(name, dl, polite, tmp, state_dir))
        elif k == "url" and "lore.kernel.org" in sid:
            out += [LKML(name, dl, polite, tmp, state_dir), DebianLists(name, dl, polite, tmp, state_dir)]
        elif k == "url" and "irclogs" in sid:
            out.append(IrcLogs(name, dl, polite, tmp, state_dir))
        elif k == "bigquery":
            out.append(HackerNews(name, dl, polite, tmp, state_dir))
        elif k == "convokit":
            out.append(Convokit(name, sid, dl, polite, tmp, state_dir))
        elif k == "gutenberg":
            out.append(Gutenberg(name, dl, polite, tmp, state_dir))
        else:
            raise ValueError(f"{name}: no reader for kind={k} id={sid}")
    return out


def run_source(name, out_root, tmp_root, codec="zst"):
    cfg, dl = load_config("askos_80m"), load_dl_cfg()
    spec = next(s for s in load_sources() if s["name"] == name)
    out_dir = os.path.join(out_root, name)
    os.makedirs(out_dir, exist_ok=True)
    tgt = target_bytes(spec, dl["overshoot"])
    if cfg["data"]["commercial_safe"] and spec.get("research_only"):
        rep = {"name": name, "skipped": "commercial_safe=true drops research_only sources", "target_bytes": tgt, "bytes": 0}
        atomic_write_json(os.path.join(out_dir, "_report.json"), rep)
        return rep
    h = dl["http"]
    polite = Polite(h["user_agent"], h["min_interval_s"], h["retries"], h["backoff_s"], h["timeout_s"])
    readers = build_readers(spec, dl, polite, os.path.join(tmp_root, name), out_dir)
    log(f"다운로드 시작: {name} 목표 {tgt / 1e9:.2f} GB (= ({spec['read_gb']} + {spec.get('l3_gb', 0)}) x {dl['overshoot']}), 하위 소스 {len(readers)}개")
    t0 = time.time()
    f = Filler(out_dir, readers, tgt, dl["part_flush_mb"] * 1e6, codec=codec, max_doc_errors=dl["max_doc_errors"], log=log)
    before = f.total()
    state = f.run()
    secs = max(1.0, time.time() - t0)
    got = f.total()
    log(f"속도: {name} 텍스트 {(got - before) / 1e6 / secs:,.1f} MB/s (실측, 이번 실행 {(got - before) / 1e9:.2f} GB / {secs / 60:.1f}분)")
    subs = {}
    for r in readers:
        s = state["subs"][r.sub]
        subs[r.sub] = {"bytes": s["bytes"], "docs": s["docs"], "parts": len(s["parts"]), "units_done": s["unit"],
                       "exhausted": s["exhausted"], "failed": s["failed"], "doc_errors": s["doc_errors"],
                       "errors": [{k: v for k, v in e.items() if k != "trace"} for e in s["errors"]], "info": r.info()}
    rep = {"name": name, "target_bytes": tgt, "bytes": got, "docs": sum(s["docs"] for s in subs.values()),
           "reached": got >= 0.95 * tgt, "all_exhausted": all(s["exhausted"] for s in subs.values()),
           "failed_subs": [k for k, s in subs.items() if s["failed"]], "license": spec.get("license"),
           "research_only": bool(spec.get("research_only")), "verify": any(x.get("verify") for x in spec["sources"]),
           "read_gb": spec["read_gb"], "l3_gb": spec.get("l3_gb", 0.0), "passes": state["passes"], "subs": subs,
           "seconds_this_run": round(secs), "finished_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    atomic_write_json(os.path.join(out_dir, "_report.json"), rep)
    atomic_write_json(os.path.join(out_dir, "_manifest.json"),
                      {sub: state["subs"][sub]["parts"] for sub in state["subs"]})
    status = "목표 도달" if rep["reached"] else ("소스 소진" if rep["all_exhausted"] else "미달")
    log(f"판정: {name} {status} · {got / 1e9:.2f}/{tgt / 1e9:.2f} GB · 문서 {rep['docs']:,} · 실패 하위 소스 {rep['failed_subs']}")
    return rep


def source_status(rep):
    if rep.get("skipped"):
        return "건너뜀"
    if rep.get("failed_subs"):
        return "일부 실패"
    if rep.get("reached"):
        return "통과"
    return "소진(작은 소스)" if rep.get("all_exhausted") else "미달"


def d1_markdown(reps, dl, cfg):
    lines = ["# D1 — 데이터 다운로드 보고서", "",
             f"- 목표 = (read_gb + l3_gb) x {dl['overshoot']} (구현서 §5.2). 바이트 = UTF-8 텍스트 바이트(압축 전).",
             f"- commercial_safe = {cfg['data']['commercial_safe']}", "- 숫자는 모두 `raw/*/_report.json`에서 복사했습니다.", "",
             "| 소스 | 목표 GB | 받은 GB | 비율 | 문서 | 판정 | 라이선스 | 연구용 |", "|---|---|---|---|---|---|---|---|"]
    tot_t = tot_b = 0
    for r in reps:
        t, b = r.get("target_bytes", 0), r.get("bytes", 0)
        tot_t, tot_b = tot_t + t, tot_b + b
        lines.append(f"| {r['name']} | {t / 1e9:.2f} | {b / 1e9:.2f} | {b / max(t, 1):.0%} | {r.get('docs', 0):,} | "
                     f"{source_status(r)} | {r.get('license', '')} | {'예' if r.get('research_only') else ''} |")
    lines += [f"| **합계** | {tot_t / 1e9:.2f} | {tot_b / 1e9:.2f} | {tot_b / max(tot_t, 1):.0%} | | | | |", ""]
    for r in reps:
        lines += [f"## {r['name']}", ""]
        if r.get("skipped"):
            lines += [f"- 건너뜀: {r['skipped']}", ""]
            continue
        lines.append(f"- 판정: {source_status(r)}, 할당 {r.get('passes')}차, verify 대상: {'예' if r.get('verify') else '아니오'}")
        for sub, s in r.get("subs", {}).items():
            info = {k: v for k, v in (s.get("info") or {}).items() if k not in ("checksums",)}
            lines.append(f"- `{sub}`: {s['bytes'] / 1e9:.3f} GB, 문서 {s['docs']:,}, 파트 {s['parts']}, 유닛 {s['units_done']}, "
                         f"{'소진' if s['exhausted'] else '남음'}{', **실패**' if s['failed'] else ''}, 불량 문서 {s['doc_errors']}")
            lines.append(f"  - 정보: `{json.dumps(info, ensure_ascii=False, default=str)[:900]}`")
            ck = (s.get("info") or {}).get("checksums") or {}
            for u, c in list(ck.items())[:20]:
                lines.append(f"  - 체크섬: `{u}` sha256 `{c.get('sha256', '')[:16]}…` ({c.get('bytes', 0) / 1e6:,.0f} MB)")
            for e in s.get("errors", []):
                lines.append(f"  - 오류(유닛 {e['unit']}, 행 {e['row']}): `{e['error']}`")
        lines.append("")
    return "\n".join(lines)


def report(raw, out):
    cfg, dl = load_config("askos_80m"), load_dl_cfg()
    reps = []
    for s in load_sources():
        rep = read_json(os.path.join(raw, s["name"], "_report.json"))
        reps.append(rep or {"name": s["name"], "missing": True, "target_bytes": target_bytes(s, dl["overshoot"]), "bytes": 0,
                            "failed_subs": ["(no report)"]})
    os.makedirs(out, exist_ok=True)
    md = d1_markdown(reps, dl, cfg)
    open(os.path.join(out, "D1.md"), "w", encoding="utf-8").write(md)
    summary = {r["name"]: source_status(r) for r in reps}
    bad = [n for n, st in summary.items() if st in ("일부 실패", "미달")]
    rep = {"title": "download", "verdict": {"pass": not bad, "failed": bad, "warnings": [n for n, st in summary.items() if st == "건너뜀"]},
           "sources": summary, "total_gb": round(sum(r.get("bytes", 0) for r in reps) / 1e9, 3),
           "target_gb": round(sum(r.get("target_bytes", 0) for r in reps) / 1e9, 3)}
    atomic_write_json(os.path.join(out, "report.json"), rep)
    log(f"판정: 01 다운로드 {'통과' if not bad else '미완'} · {rep['total_gb']:.1f}/{rep['target_gb']:.1f} GB · 문제 {bad}")
    log(f"보고서 → {os.path.join(out, 'D1.md')}")
    return rep


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("source")
    a.add_argument("name")
    a.add_argument("--out", default="raw")
    a.add_argument("--tmp", default="/tmp/askos_dl")
    a.add_argument("--report")
    b = sp.add_parser("report")
    b.add_argument("--raw", default="raw")
    b.add_argument("--out", default="reports")
    sp.add_parser("plan")
    args = ap.parse_args()
    run_header()
    if args.cmd == "plan":
        for r in plan(load_sources(), load_config("askos_80m"), load_dl_cfg()):
            print(json.dumps(r, ensure_ascii=False))
        return
    if args.cmd == "report":
        rep = report(args.raw, args.out)
        sys.exit(0 if rep["verdict"]["pass"] else 1)
    rep = run_source(args.name, args.out, args.tmp)
    if args.report:
        atomic_write_json(args.report, {"title": f"download_{args.name}", "verdict": {"pass": source_status(rep) in ("통과", "소진(작은 소스)", "건너뜀"),
                                                                                    "failed": rep.get("failed_subs", []), "warnings": []},
                                        "source": rep})
    sys.exit(0)


if __name__ == "__main__":
    main()
