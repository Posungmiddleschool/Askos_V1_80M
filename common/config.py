"""Config loading. configs/smoke.yaml is deep-merged onto configs/askos_80m.yaml (spec §14)."""
import copy
import hashlib
import json
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "askos_80m"


def path(*parts):
    return os.path.join(ROOT, *parts)


def load_yaml(p):
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(name_or_path=BASE):
    """'askos_80m', 'smoke' or a path. Anything other than the base is merged onto the base."""
    p = name_or_path if name_or_path.endswith(".yaml") else path("configs", name_or_path + ".yaml")
    cfg = load_yaml(p)
    if os.path.basename(p) != BASE + ".yaml":
        cfg = deep_merge(load_yaml(path("configs", BASE + ".yaml")), cfg)
    return cfg


def load_infra():
    return load_yaml(path("configs", "infra.yaml"))


def load_sources():
    return load_yaml(path("data", "sources.yaml"))


def config_hash(cfg):
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def file_hash(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def manifest_hash():
    return file_hash(path("data", "sources.yaml"))
