"""Static checks of configs and the source manifest. Used by tests/test_infra.py and setup_check/cpu.py.

Each function returns a list of problems (empty = ok).
"""
from common.config import deep_merge

REQUIRED_SECTIONS = ["run", "data", "model", "chunker", "cursor", "saegim", "l3", "doc_hash",
                     "selection", "train", "features"]
RESEARCH_ONLY = {"geek_debate", "namuwiki"}
SLOTS = 2 ** 26                      # spec §8.1: table [2^26, 128] bf16


def check_config(cfg):
    p = []
    for s in REQUIRED_SECTIONS:
        if s not in cfg:
            p.append(f"missing section: {s}")
    if p:
        return p
    for k, v in cfg["features"].items():
        if not isinstance(v, bool):
            p.append(f"features.{k} is not a bool")
    sg = cfg["saegim"]
    if sg["keys_per_half"] ** 2 != SLOTS:
        p.append("saegim.keys_per_half^2 != 2^26")
    table_gb = SLOTS * sg["value_dim"] * 2 / 1e9
    if not 17.0 <= table_gb <= 17.4:  # spec §12: 17.2 GB
        p.append(f"saegim table {table_gb:.2f} GB, spec says 17.2 GB")
    if cfg["model"]["vocab"] != 260:
        p.append("model.vocab != 260")
    lc = cfg["l3"]["index_dropout"]
    if abs(sum(lc["probs"]) - 1) > 1e-9 or len(lc["probs"]) != len(lc["k_choices"]):
        p.append("l3.index_dropout probs invalid")
    if cfg["l3"]["shards"] * 7.5 != cfg["data"]["l3_gb"]:
        p.append("l3.shards x 7.5 GB != data.l3_gb")
    return p


def check_smoke(base, smoke_over):
    p = []
    m = deep_merge(base, smoke_over)
    if m["model"]["byte_encoder"]["layers"] != 1 or m["model"]["byte_encoder"]["heads"] != base["model"]["byte_encoder"]["heads"]:
        p.append("smoke deep merge of model.byte_encoder is wrong")
    if m["train"]["long_context"]["last_frac"] != base["train"]["long_context"]["last_frac"]:
        p.append("smoke deep merge dropped train.long_context.last_frac")
    if m["saegim"]["keys_per_half"] != 256:
        p.append("smoke saegim.keys_per_half != 256")
    return p


def check_sources(sources, cfg, tol_read=0.005, tol_l3=0.01):
    p = []
    names = [s["name"] for s in sources]
    if len(set(names)) != len(names):
        p.append("duplicate source names")
    w = sum(s["weight"] for s in sources)
    if abs(w - 1.0) > 1e-9:
        p.append(f"weights sum to {w}")
    read_total, l3_total = cfg["data"]["read_gb"], cfg["data"]["l3_gb"]
    big_w = sum(s["weight"] for s in sources if "l3_gb" in s)
    for s in sources:
        if abs(s["read_gb"] - s["weight"] * read_total) > tol_read:
            p.append(f"{s['name']}: read_gb {s['read_gb']} != weight x {read_total}")
        if "l3_gb" in s and abs(s["l3_gb"] - s["weight"] / big_w * l3_total) > tol_l3:
            p.append(f"{s['name']}: l3_gb {s['l3_gb']} is not the renormalised share")
        if bool(s.get("research_only")) != (s["name"] in RESEARCH_ONLY):
            p.append(f"{s['name']}: research_only flag mismatch")
        if not s.get("sources"):
            p.append(f"{s['name']}: no sources")
    l3_sum = sum(s.get("l3_gb", 0) for s in sources)
    if abs(l3_sum - l3_total) > 0.05:
        p.append(f"l3_gb sums to {l3_sum}, expected {l3_total}")
    return p
