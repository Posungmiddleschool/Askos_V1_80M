"""Infra tests for notebook 00. No GPU, no Modal, no network: numpy + pyyaml only.

python3 tests/test_infra.py      (or: python3 -m pytest -q tests)
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from common.config import config_hash, deep_merge, load_config, load_infra, load_sources, load_yaml, path  # noqa: E402
from common.redact import TOK_PATTERNS, redact  # noqa: E402
from common.validate import check_config, check_smoke, check_sources  # noqa: E402


def _module_constants(fname):
    tree = ast.parse(open(os.path.join(ROOT, fname), encoding="utf-8").read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def test_main_config():
    assert check_config(load_config("askos_80m")) == []


def test_smoke_merge():
    base = load_config("askos_80m")
    assert check_smoke(base, load_yaml(path("configs", "smoke.yaml"))) == []
    sm = load_config("smoke")
    assert sm["model"]["d_model"] == 192 and sm["model"]["backbone"]["rope_base"] == 50000
    assert sm["cursor"]["K"] == 8 and sm["cursor"]["hazard"] == base["cursor"]["hazard"]


def test_every_feature_flag_is_on():
    assert all(load_config("askos_80m")["features"].values())


def test_config_hash():
    a = load_config("askos_80m")
    assert config_hash(a) == config_hash(load_config("askos_80m"))
    assert config_hash(a) != config_hash(deep_merge(a, {"run": {"seed": 1}}))
    assert config_hash(a) != config_hash(load_config("smoke"))


def test_sources():
    assert check_sources(load_sources(), load_config("askos_80m")) == []


def test_redact():
    tok = "hf_" + "aB3" * 10
    assert tok not in redact(f"token={tok} done") and "hf_***" in redact(tok)
    assert "modal_***" in redact("ak-" + "x1" * 10)
    assert redact("스텝 50: 손실 3.21") == "스텝 50: 손실 3.21"


def test_modal_app_matches_infra():
    c = _module_constants("modal_app.py")
    infra = load_infra()
    assert c["APP_NAME"] == infra["modal"]["app"]
    assert c["VOLUME_NAME"] == infra["modal"]["volume"]
    assert c["HF_SECRET"] == infra["modal"]["hf_secret"]
    assert c["STATE_REPO_NAME"] == infra["hub"]["state_repo"]
    assert c["LOG_UPLOAD_EVERY_MIN"] == infra["hub"]["log_upload_every_min"]
    assert c["TOK_PATTERNS"] == TOK_PATTERNS
    o = _module_constants("ops.py")
    assert o["APP_NAME"] == c["APP_NAME"] and o["HF_SECRET"] == c["HF_SECRET"]


def test_gpu_probes_match_infra():
    src = open(os.path.join(ROOT, "modal_app.py"), encoding="utf-8").read()
    for g in load_infra()["modal"]["gpus"]["setup_probe"]:
        assert f'gpu="{g}"' in src, g


def test_no_token_in_repo():
    import re
    pat = re.compile(r"hf_[A-Za-z0-9]{20,}")
    for d, _, files in os.walk(ROOT):
        if ".git" in d.split(os.sep) or "__pycache__" in d:
            continue
        for f in files:
            if f.endswith((".py", ".yaml", ".md", ".sh", ".ipynb", ".json", ".txt")):
                text = open(os.path.join(d, f), encoding="utf-8", errors="replace").read()
                assert not pat.search(text), os.path.join(d, f)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print("all passed" if not fails else f"{fails} failed")
    sys.exit(1 if fails else 0)
