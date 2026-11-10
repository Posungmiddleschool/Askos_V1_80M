"""Per-dataset field mapping and filters for the HF sources of data/sources.yaml.

Each profile: columns, keep(rec, reader), make(rec, unit, row, reader), optional calibrate(reader).
The filter strings in sources.yaml are prose; this file is their exact implementation.
"""
import ast
import hashlib

from data.filters import code_quality, quantile
from data.readers import doc
from data.readers.hf import iter_rows


def _h(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def _meta(rec):
    m = rec.get("meta") or rec.get("metadata")
    if isinstance(m, str):
        try:
            m = ast.literal_eval(m)
        except (ValueError, SyntaxError):
            m = {}
    return m if isinstance(m, dict) else {}


class Simple:
    """text field + optional title prefix + id field; keep everything."""

    def __init__(self, source, text="text", id_field=None, title=None, meta_fields=(), prefix=""):
        self.source, self.text, self.id_field, self.title = source, text, id_field, title
        self.meta_fields, self.prefix = tuple(meta_fields), prefix
        cols = {text} | ({id_field} if id_field else set()) | ({title} if title else set()) | set(meta_fields)
        self.columns = sorted(cols)

    def keep(self, rec, reader):
        return bool(rec.get(self.text))

    def make(self, rec, unit, row, reader):
        t = rec[self.text]
        if self.title and rec.get(self.title):
            t = f"{rec[self.title]}\n\n{t}"
        did = rec.get(self.id_field) if self.id_field else None
        did = f"{self.prefix}{did}" if did is not None else f"{self.prefix}{unit}:{row}"
        return doc(t, self.source, did, **{k: rec.get(k) for k in self.meta_fields})


class MinScore(Simple):
    def __init__(self, source, field, minimum, **kw):
        super().__init__(source, meta_fields=kw.pop("meta_fields", ()) + (field,), **kw)
        self.field, self.minimum = field, minimum

    def keep(self, rec, reader):
        v = rec.get(self.field)
        return bool(rec.get(self.text)) and v is not None and v >= self.minimum


class DCLMTop(Simple):
    """Top `top_frac` by the dataset's fastText score; threshold = quantile over the first N files."""

    def __init__(self, source, field, top_frac, calib_files):
        super().__init__(source, meta_fields=("url",))
        self.columns = None
        self.field, self.top_frac, self.calib_files = field, top_frac, calib_files

    def calibrate(self, reader):
        scores = []
        for i in range(min(self.calib_files, len(reader.units()))):
            for _, rec in iter_rows(reader.local(i), None, 0):
                if rec is not None and self.field in rec:
                    scores.append(float(rec[self.field]))
        if not scores:
            raise ValueError(f"verify failed: field {self.field} not found in dclm records")
        return {"field": self.field, "threshold": quantile(scores, 1 - self.top_frac), "n": len(scores),
                "files": self.calib_files}

    def keep(self, rec, reader):
        v = rec.get(self.field)
        return bool(rec.get("text")) and v is not None and float(v) >= reader.calib["threshold"]

    def make(self, rec, unit, row, reader):
        return doc(rec["text"], self.source, f"dclm:{_h(unit)}:{row}", url=rec.get("url"),
                   dclm_score=round(float(rec[self.field]), 6))


class Code(Simple):
    """Permissive licenses only; top `keep_frac` by filters.code_quality (spec fallback heuristic)."""

    def __init__(self, source, licenses, keep_frac, calib_files):
        super().__init__(source, text="code")
        self.columns = ["code", "repo_name", "path", "language", "license"]
        self.licenses, self.keep_frac, self.calib_files = set(licenses), keep_frac, calib_files

    def calibrate(self, reader):
        q = []
        for i in range(min(self.calib_files, len(reader.units()))):
            for _, rec in iter_rows(reader.local(i), self.columns, 0):
                if rec and rec.get("license") in self.licenses and rec.get("code"):
                    q.append(code_quality(rec["code"]))
        if not q:
            raise ValueError("verify failed: no permissive-license code in calibration files")
        return {"heuristic": "filters.code_quality", "threshold": quantile(q, 1 - self.keep_frac), "n": len(q)}

    def keep(self, rec, reader):
        if rec.get("license") not in self.licenses or not rec.get("code"):
            return False
        rec["_q"] = code_quality(rec["code"])
        return rec["_q"] >= reader.calib["threshold"]

    def make(self, rec, unit, row, reader):
        return doc(rec["code"], self.source, f"gh:{rec['repo_name']}/{rec['path']}", language=rec.get("language"),
                   license=rec.get("license"), code_score=rec["_q"])


class JsonMeta(Simple):
    """json-lines with a nested `meta` dict (proof-pile-2, RedPajama arxiv)."""

    def __init__(self, source, prefix, id_keys):
        super().__init__(source, prefix=prefix)
        self.columns = None
        self.id_keys = id_keys

    def make(self, rec, unit, row, reader):
        m = _meta(rec)
        did = next((m[k] for k in self.id_keys if m.get(k)), None)
        return doc(rec["text"], self.source, f"{self.prefix}{did}" if did else f"{self.prefix}{_h(unit)}:{row}",
                   ext=m.get("ext"), arxiv_id=m.get("arxiv_id"))


def profile_for(source, repo, config, fcfg):
    """(profile, glob_hint key) for one HF sub-source."""
    if repo == "HuggingFaceFW/fineweb-edu":
        return MinScore(source, "int_score", fcfg["fineweb_edu_min_int_score"], id_field="id", meta_fields=("url",)), repo
    if repo == "HuggingFaceFW/fineweb-2":
        return MinScore(source, "language_score", fcfg["fineweb2_min_language_score"], id_field="id", meta_fields=("url",)), repo
    if repo == "mlfoundations/dclm-baseline-1.0":
        return DCLMTop(source, fcfg["dclm_score_field"], fcfg["dclm_top_frac"], fcfg["dclm_calibration_files"]), repo
    if repo == "codeparrot/github-code-clean":
        return Code(source, fcfg["code_licenses"], fcfg["code_keep_frac"], fcfg["code_calibration_files"]), repo
    if repo == "allenai/peS2o":
        p = Simple(source, id_field="id", prefix="s2:", meta_fields=("source",))
        p.columns = None
        return p, repo
    if repo == "HuggingFaceTB/finemath":
        return Simple(source, id_field="url", meta_fields=("int_score",)), repo
    if repo == "open-web-math/open-web-math":
        return Simple(source, id_field="url"), repo
    if repo == "EleutherAI/proof-pile-2":
        return JsonMeta(source, "pp2:", ("hexsha",)), f"{repo}/{config}"
    if repo == "heegyu/namuwiki-extracted":
        return Simple(source, title="title", id_field="title", prefix="namu:"), repo
    if repo == "wikimedia/wikipedia":
        return Simple(source, title="title", id_field="id", prefix=f"wiki.{config}:", meta_fields=("url",)), repo
    if repo == "togethercomputer/RedPajama-Data-1T":
        return JsonMeta(source, "rp-arxiv:", ("arxiv_id",)), None
    raise ValueError(f"no profile for {repo} ({source}); add one to data/profiles.py")
