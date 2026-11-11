"""Offline tests for notebook 01 (download). No GPU, no network, no zstandard/pyarrow: stdlib + numpy + pyyaml.

python3 tests/test_download.py      (or: python3 -m pytest -q tests)
"""
import ast
import hashlib
import io
import json
import os
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from common.config import load_config, load_sources  # noqa: E402
from data import filters  # noqa: E402
from data.download import build_readers, load_dl_cfg, plan, target_bytes  # noqa: E402
from data.io import read_json  # noqa: E402
from data.polite import Polite, RobotsDisallowed  # noqa: E402
from data.quota import Filler  # noqa: E402
from data.readers import Reader, doc  # noqa: E402
from data.readers.hf import _train_patterns, iter_rows  # noqa: E402
from data.readers.hn import build_threads  # noqa: E402
from data.readers.web import join_posts, parse_wiki_dump  # noqa: E402


class Fake(Reader):
    """units x rows docs of `size` bytes; optional crash (BaseException) after `crash_after` yielded docs."""

    def __init__(self, sub, units, rows, size, crash_after=None, error_after=None):
        self.sub, self.n_units, self.rows, self.size = sub, units, rows, size
        self.crash_after, self.error_after, self.yielded = crash_after, error_after, 0

    def units(self):
        return [f"u{i}" for i in range(self.n_units)]

    def docs(self, unit_idx, start_row):
        for r in range(start_row, self.rows):
            if self.crash_after is not None and self.yielded >= self.crash_after:
                raise KeyboardInterrupt("simulated container kill")
            if self.error_after is not None and self.yielded >= self.error_after:
                raise ConnectionError("simulated network failure")
            self.yielded += 1
            yield r, doc("x" * self.size, "t", f"{self.sub}:{unit_idx}:{r}")


def _all_docs(out):
    ids = []
    for d, _, fs in os.walk(out):
        for f in fs:
            if f.endswith(".jsonl"):
                ids += [json.loads(l)["doc_id"] for l in open(os.path.join(d, f), encoding="utf-8")]
    return ids


def test_fill_redistributes_shortfall():
    with tempfile.TemporaryDirectory() as t:
        small, big = Fake("small", 2, 5, 100), Fake("big", 10, 100, 100)   # 1 KB vs 100 KB available
        st = Filler(t, [small, big], target_bytes=20_000, flush_bytes=3_000, codec="raw", log=lambda m: None).run()
        assert st["subs"]["small"]["exhausted"] and st["subs"]["small"]["bytes"] == 1_000
        total = st["subs"]["small"]["bytes"] + st["subs"]["big"]["bytes"]
        assert 20_000 <= total <= 20_100, total
        ids = _all_docs(t)
        assert len(ids) == len(set(ids)) == total // 100


def test_resume_after_kill_has_no_duplicates():
    with tempfile.TemporaryDirectory() as t:
        a = Fake("a", 3, 50, 100, crash_after=73)
        try:
            Filler(t, [a], 10_000, 1_000, codec="raw", log=lambda m: None).run()
            raise AssertionError("expected simulated kill")
        except KeyboardInterrupt:
            pass
        saved = read_json(os.path.join(t, "_state.json"))["subs"]["a"]
        assert saved["bytes"] == 7_000          # parts flushed every 1 KB: 70 docs committed, 3 lost in the temp part
        st = Filler(t, [Fake("a", 3, 50, 100)], 10_000, 1_000, codec="raw", log=lambda m: None).run()
        ids = _all_docs(t)
        assert len(ids) == len(set(ids)) == 100 and st["subs"]["a"]["bytes"] == 10_000
        assert not [f for _, _, fs in os.walk(t) for f in fs if f.endswith(".tmp")]


def test_empty_docs_are_skipped():
    class Empty(Fake):
        def docs(self, unit_idx, start_row):
            for r in range(start_row, self.rows):
                yield r, doc("   " if r % 2 else "y" * self.size, "t", f"e:{unit_idx}:{r}")
    with tempfile.TemporaryDirectory() as t:
        st = Filler(t, [Empty("e", 1, 10, 100)], 10_000, 10_000, codec="raw", log=lambda m: None).run()
        assert st["subs"]["e"]["docs"] == 5 and st["subs"]["e"]["doc_errors"] == 0


def test_failed_sub_is_recorded_then_retried():
    with tempfile.TemporaryDirectory() as t:
        st = Filler(t, [Fake("a", 2, 10, 100, error_after=5), Fake("b", 5, 10, 100)], 3_000, 10_000,
                    codec="raw", log=lambda m: None).run()
        assert st["subs"]["a"]["failed"] and "ConnectionError" in st["subs"]["a"]["errors"][0]["error"]
        assert st["subs"]["a"]["bytes"] + st["subs"]["b"]["bytes"] >= 3_000 - 500   # b covers a's shortfall
        st2 = Filler(t, [Fake("a", 2, 10, 100), Fake("b", 5, 10, 100)], 3_000, 10_000, codec="raw", log=lambda m: None)
        assert not st2.state["subs"]["a"]["failed"]   # a new run retries it


def test_targets_match_spec():
    dl = load_dl_cfg()
    s = {x["name"]: x for x in load_sources()}
    assert abs(target_bytes(s["fineweb_edu"], dl["overshoot"]) / 1e9 - (6.40 + 8.33) * 1.6) < 1e-6
    assert abs(target_bytes(s["stackexchange"], dl["overshoot"]) / 1e9 - 2.56 * 1.6) < 1e-6
    rows = plan(load_sources(), load_config("askos_80m"), dl)
    assert abs(sum(r["target_gb"] for r in rows) - (32 + 30) * 1.6) < 0.1


def test_every_source_has_a_reader():
    dl = load_dl_cfg()
    pol = Polite("t", 0)
    with tempfile.TemporaryDirectory() as t:
        for spec in load_sources():
            rs = build_readers(spec, dl, pol, t, t)
            assert rs, spec["name"]
            assert len({r.sub for r in rs}) == len(rs), spec["name"]


def test_modal_source_names_match_manifest():
    tree = ast.parse(open(os.path.join(ROOT, "modal_app.py"), encoding="utf-8").read())
    names = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], "id", "") == "SOURCE_NAMES")
    assert names == [s["name"] for s in load_sources()]


def test_gutenberg_and_wikisource_filters():
    assert filters.gutenberg_author_match("Twain, Mark, 1835-1910", ["Twain, Mark"])
    assert not filters.gutenberg_author_match("Kemble, E. W. [Illustrator]; Smith, Jo", ["Kemble, E. W."])
    assert not filters.gutenberg_author_match("Swiftly, Tom", ["Swift, Jonathan"])
    assert filters.gutenberg_author_match("Wells, H. G. (Herbert George), 1866-1946", ["Wells, H. G."])
    txt = "header\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\nBODY\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\nlicense"
    assert filters.strip_gutenberg(txt) == "BODY"
    page = "{{머리말\n | title    =동백꽃\n | author   =[[글쓴이:김유정|김유정]]\n}}\n오늘도"
    assert filters.kowikisource_author(page, ["이상", "채만식", "김유정"]) == "김유정"
    assert filters.kowikisource_author(page.replace("김유정", "현진건"), ["이상", "채만식", "김유정"]) is None
    page2 = "{{머리말\n|제목 = 날개\n|지은이 = [[저자:이상|이상]] \n|역자 =\n}}"     # real 2026 dump format
    assert filters.kowikisource_author(page2, ["이상", "채만식", "김유정"]) == "이상"


def test_quantile_and_versions():
    assert filters.quantile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9) == 9
    assert filters.quantile([5], 0.5) == 5
    assert filters.latest(["man-pages-6.9.1.tar.xz", "man-pages-6.10.tar.xz", "man-pages-6.9.tar.xz"]) == "man-pages-6.10.tar.xz"


def test_profiles_for_v11_sources():
    from data.profiles import profile_for
    f = load_dl_cfg()["filters"]
    p, _ = profile_for("code", "common-pile/stackv2_edu_filtered", None, f)
    class R:   # reader stand-in with a frozen calibration
        calib = {"threshold": 3.0}
    rec = {"id": "b1", "text": "def f(): pass", "score": "3.4", "metadata": "{'language': 'Python', 'license': 'MIT', 'repo_name': 'a/b', 'path': 'x.py'}"}
    assert p.keep(rec, R) and not p.keep(dict(rec, score="2.9"), R)
    d = p.make(rec, "u", 0, R)
    assert d["doc_id"] == "stackedu:b1" and d["meta"]["language"] == "Python" and d["meta"]["code_score"] == 3.4
    p, _ = profile_for("papers_books", "common-pile/doab_filtered", None, f)
    d = p.make({"id": "x", "text": "book", "source": "doab", "metadata": "{'license': 'CC BY 4.0'}"}, "u", 0, R)
    assert d["doc_id"] == "doab:x" and d["meta"]["license"] == "CC BY 4.0"
    p, _ = profile_for("math", "openbmb/UltraData-Math", "UltraData-Math-L2-preview", f)
    assert p.columns == ["content", "quality_label"] and p.make({"content": "x", "quality_label": "4"}, "u", 3, R)["meta"]["quality_label"] == "4"
    p, _ = profile_for("korean_web", "eliceai/korean-webtext-edu", None, f)
    assert p.keep({"text": "가", "score": 3.2}, R) and not p.keep({"text": "가", "score": 2.5}, R)


def test_hf_hints_are_well_formed_and_cover_cardless_repos():
    # 2026-11-11: namuwiki's files hint had slipped under shuffle_files (run found by the all-sources test)
    dl = load_dl_cfg()
    assert all(isinstance(v, str) and "*" in v for v in dl["hf"]["files"].values()), dl["hf"]["files"]
    assert all(isinstance(v, int) for v in dl["hf"]["shuffle_files"].values()), dl["hf"]["shuffle_files"]
    cardless = ["allenai/peS2o", "EleutherAI/proof-pile-2/algebraic-stack", "mlfoundations/dclm-baseline-1.0",
                "heegyu/namuwiki-extracted", "open-index/hacker-news"]
    cardless += [x["id"] for s in load_sources() for x in s["sources"] if x.get("id", "").startswith("common-pile/")]
    assert not [r for r in cardless if r not in dl["hf"]["files"]]


def test_every_hf_profile_builds_a_document():
    # 2026-11-11: peS2o's meta field `source` collided with doc()'s `source` argument
    from data.profiles import profile_for
    f = load_dl_cfg()["filters"]

    class R:
        calib = {"threshold": 0.0}
    rec = {"text": "t", "content": "t", "code": "t", "title": "T", "id": "i", "url": "u", "source": "s2ag/train",
           "score": 4.0, "int_score": 4, "language_score": 0.95, "quality_label": "4",
           f["dclm_score_field"]: 0.9, "metadata": "{'language': 'Python', 'license': 'MIT'}", "meta": {"hexsha": "h"}}
    seen = set()
    for s in load_sources():
        for x in s["sources"]:
            if x["kind"] != "hf" or x["id"] == "open-index/hacker-news":
                continue
            cfgs = x.get("config")
            for c in (cfgs if isinstance(cfgs, list) else [cfgs]):
                p, _ = profile_for(s["name"], x["id"], c, f)
                assert p.keep(dict(rec), R), (x["id"], c)
                d = p.make(dict(rec, _q=1.0), "unit", 3, R)
                assert d["text"] and d["source"] == s["name"] and d["doc_id"], (x["id"], c)
                seen.add(x["id"])
    assert "allenai/peS2o" in seen and len(seen) >= 15


def test_shuffle_hint_is_deterministic():
    import random
    files = [f"f{i:03d}" for i in range(95)]
    a, b = list(files), list(files)
    random.Random(1234).shuffle(a)
    random.Random(1234).shuffle(b)
    assert a == b and a != files
    dl = load_dl_cfg()
    assert dl["hf"]["shuffle_files"]["common-pile/stackv2_edu_filtered"] == 1234
    assert dl["hf"]["files"]["common-pile/stackv2_edu_filtered"] == "*.json.gz"


def test_jargon_version_order():
    from data.readers.web import Jargon
    names = ["jarg2912.txt", "jarg447.txt", "jarg446.txt", "jarg300.txt"]
    assert max(names, key=Jargon.version) == "jarg447.txt"


def test_hn_threads():
    def r(i, t, parent=0, score=0, text="", title="", dead=0):
        return {"id": i, "type": t, "parent": parent, "score": score, "text": text, "title": title, "url": "", "dead": dead, "deleted": 0}
    m0 = [r(1, 1, score=150, title="Big story"), r(2, 1, score=5, title="small"), r(3, 2, parent=1, text="first &amp; top"),
          r(4, 2, parent=3, text="reply<p>two paras"), r(5, 2, parent=2, text="on small"), r(6, 2, parent=1, text="dead", dead=1)]
    m1 = [r(9, 2, parent=4, text="late deep reply"), r(8, 2, parent=1, text="second top"), r(7, 2, parent=6, text="child of dead")]
    out = build_threads([m0, m1], 100)
    assert [o[0] for o in out] == [1]
    sid, text, score, n = out[0]
    assert score == 150 and n == 4 and "dead" not in text and "child of dead" not in text and "on small" not in text
    assert text.index("first & top") < text.index("  reply") < text.index("    late deep reply") < text.index("second top")
    assert "  reply\n\n  two paras" in text


def test_join_posts():
    rows = [{"Id": "1", "PostTypeId": "1", "Title": "Q1", "Body": "<p>q</p>", "Score": "3"},
            {"Id": "2", "PostTypeId": "2", "ParentId": "1", "Body": "low", "Score": "4"},
            {"Id": "3", "PostTypeId": "2", "ParentId": "1", "Body": "good", "Score": "9"},
            {"Id": "4", "PostTypeId": "2", "ParentId": "1", "Body": "ok", "Score": "5"},
            {"Id": "5", "PostTypeId": "1", "Title": "Q2", "Body": "x", "Score": "1"},
            {"Id": "6", "PostTypeId": "2", "ParentId": "5", "Body": "meh", "Score": "2"}]
    out = join_posts(rows, 5)
    assert [o[0] for o in out] == ["1"]
    qid, text, qs, best, n, _ = out[0]
    assert best == 9 and n == 2 and text.index("good") < text.index("ok") and "low" not in text


def test_wiki_dump_parser():
    xml = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
    <page><title>A</title><ns>0</ns><id>10</id><revision><id>999</id><contributor><id>5</id></contributor><text>Alpha</text></revision></page>
    <page><title>R</title><ns>0</ns><id>11</id><redirect title="A"/><revision><id>1000</id><text>#REDIRECT</text></revision></page>
    <page><title>T</title><ns>1</ns><id>12</id><revision><id>1001</id><text>talk</text></revision></page>
    <page><title>B</title><ns>0</ns><id>13</id><revision><id>1002</id><text>{{머리말 | author =[[글쓴이:이상|이상]]}} 날개</text></revision></page>
    </mediawiki>"""
    got = list(parse_wiki_dump(io.BytesIO(xml.encode()), "s", "w"))
    assert [(r, d["doc_id"]) for r, d in got] == [(0, "w:10"), (3, "w:13")]
    got = list(parse_wiki_dump(io.BytesIO(xml.encode()), "s", "w", authors=["이상"]))
    assert [d["doc_id"] for _, d in got] == ["w:13"] and got[0][1]["meta"]["author"] == "이상"
    assert [r for r, _ in parse_wiki_dump(io.BytesIO(xml.encode()), "s", "w", start_row=1)] == [3]


def test_iter_rows_arrow_stream():
    try:
        import pyarrow as pa
    except ImportError:          # local machines without pyarrow: covered in the Modal image (setup unit_tests)
        return
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "data-00000-of-00002.arrow")
        tb = pa.table({"text": ["a", "b", "c"], "score": [3.0, 4.0, 2.0]})
        with pa.OSFile(p, "wb") as f, pa.ipc.new_stream(f, tb.schema) as w:
            w.write_table(tb, max_chunksize=2)
        assert [r for r, _ in iter_rows(p, ["text"], 1)] == [1, 2]
        assert list(iter_rows(p, ["text"]))[0] == (0, {"text": "a"})


def test_iter_rows_jsonl_and_card_patterns():
    with tempfile.TemporaryDirectory() as t:
        p = os.path.join(t, "a.jsonl")
        open(p, "w").write('{"text":"a"}\nnot json\n{"text":"c"}\n')
        assert list(iter_rows(p)) == [(0, {"text": "a"}), (1, None), (2, {"text": "c"})]
        assert list(iter_rows(p, start=2)) == [(2, {"text": "c"})]
    card = {"configs": [{"config_name": "kor_Hang", "data_files": [{"split": "test", "path": "data/kor_Hang/test/*"},
                                                                    {"split": "train", "path": "data/kor_Hang/train/*"}]}]}
    assert _train_patterns(card, "kor_Hang") == ["data/kor_Hang/train/*"]
    assert _train_patterns(card, "x") == []


class _Resp(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_polite_per_host_interval():
    import time
    calls = []

    def opener(req, timeout=None):
        calls.append(time.time())
        return _Resp(b"" if req.full_url.endswith("/robots.txt") else b"x")
    pol = Polite("t", 0.2, opener=opener, per_host_interval_s={"fast.example": 0.0})
    t = time.time()
    for _ in range(3):
        pol.get("https://fast.example/a")
    fast = time.time() - t
    t = time.time()
    for _ in range(3):
        pol.get("https://slow.example/a")
    slow = time.time() - t
    assert fast < 0.15 and slow >= 0.35, (fast, slow)


def test_polite_robots_and_checksum():
    body = b"0123456789" * 1000

    def opener(req, timeout=None):
        u = req.full_url
        if u.endswith("/robots.txt"):
            if "norobots" in u:
                raise urllib.error.HTTPError(u, 404, "nf", {}, None)
            return _Resp(b"User-agent: *\nDisallow: /private/\n")
        rng = req.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return _Resp(body[start:], 206)
        return _Resp(body)

    pol = Polite("t", 0, retries=2, backoff_s=0, opener=opener)
    try:
        pol.get("https://h.example/private/x")
        raise AssertionError("robots not enforced")
    except RobotsDisallowed:
        pass
    assert pol.get("https://norobots.example/anything") == body
    with tempfile.TemporaryDirectory() as t:
        dest = os.path.join(t, "f.bin")
        open(dest + ".part", "wb").write(body[:3000])          # interrupted earlier download
        rec = pol.download("https://h.example/pub/f.bin", dest, md5=hashlib.md5(body).hexdigest())
        assert rec["bytes"] == len(body) and rec["sha256"] == hashlib.sha256(body).hexdigest()
        try:
            pol.download("https://h.example/pub/g.bin", os.path.join(t, "g.bin"), md5="0" * 32)
            raise AssertionError("md5 mismatch not detected")
        except ValueError:
            pass


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print("all passed" if not fails else f"{fails} failed")
    sys.exit(1 if fails else 0)
