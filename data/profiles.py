"""Per-dataset field mapping and filters for the HF sources of data/sources.yaml.

Each profile: columns, keep(rec, reader), make(rec, unit, row, reader), optional calibrate(reader).
The filter strings in sources.yaml are prose; this file is their exact implementation.
"""
import ast
import hashlib

from data.filters import quantile
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
        did = f"{self.prefix}{did}" if did is not None else f"{self.prefix}{_h(unit)}:{row}"
        return doc(t, self.source, did, **{k: rec.get(k) for k in self.meta_fields})


class MinScore(Simple):
    def __init__(self, source, field, minimum, **kw):
        super().__init__(source, meta_fields=kw.pop("meta_fields", ()) + (field,), **kw)
        self.field, self.minimum = field, minimum

    def keep(self, rec, reader):
        v = rec.get(self.field)
        return bool(rec.get(self.text)) and v is not None and float(v) >= self.minimum


class TopFrac(Simple):
    """Keep the top `top_frac` by a score field; threshold = (1 - top_frac) quantile over the first N files
    (after the reader's file order, which may be shuffled), frozen in state."""

    def __init__(self, source, field, top_frac, calib_files, id_field=None, prefix="", meta_fields=(), cast=float):
        super().__init__(source, id_field=id_field, prefix=prefix, meta_fields=meta_fields)
        self.columns = None
        self.field, self.top_frac, self.calib_files, self.cast = field, top_frac, calib_files, cast

    def calibrate(self, reader):
        scores = []
        for i in range(min(self.calib_files, len(reader.units()))):
            for _, rec in iter_rows(reader.local(i), None, 0):
                if rec is not None and rec.get(self.field) is not None:
                    scores.append(self.cast(rec[self.field]))
        if not scores:
            raise ValueError(f"verify failed: score field {self.field} not found")
        return {"field": self.field, "threshold": quantile(scores, 1 - self.top_frac), "n": len(scores),
                "files": self.calib_files, "keep_frac": self.top_frac}

    def keep(self, rec, reader):
        v = rec.get(self.field)
        return bool(rec.get(self.text)) and v is not None and self.cast(v) >= reader.calib["threshold"]


class DCLMTop(TopFrac):
    def __init__(self, source, field, top_frac, calib_files):
        super().__init__(source, field, top_frac, calib_files)

    def make(self, rec, unit, row, reader):
        return doc(rec["text"], self.source, f"dclm:{_h(unit)}:{row}", url=rec.get("url"),
                   dclm_score=round(float(rec[self.field]), 6))


class StackEdu(TopFrac):
    """common-pile/stackv2_edu_filtered: Stack-Edu classifier `score`, openly licensed files only."""

    def make(self, rec, unit, row, reader):
        m = _meta(rec)
        return doc(rec["text"], self.source, f"stackedu:{rec.get('id') or _h(unit) + ':' + str(row)}",
                   language=m.get("language"), license=m.get("license") or m.get("license_type"),
                   repo=m.get("repo_name"), path=m.get("path"), code_score=round(float(rec["score"]), 4))


class CommonPile(Simple):
    """Common Pile v0.1 *_filtered subsets (dolma json.gz: id, text, source, metadata)."""

    def __init__(self, source, prefix):
        super().__init__(source, prefix=prefix)
        self.columns = None

    def make(self, rec, unit, row, reader):
        m = _meta(rec)
        lic = m.get("license")
        return doc(rec["text"], self.source, f"{self.prefix}{rec.get('id') or _h(unit) + ':' + str(row)}",
                   cp_source=rec.get("source"), license=str(lic)[:80] if lic else None, url=m.get("url"))


class JsonMeta(Simple):
    """json-lines with a nested `meta` dict (proof-pile-2)."""

    def __init__(self, source, prefix, id_keys):
        super().__init__(source, prefix=prefix)
        self.columns = None
        self.id_keys = id_keys

    def make(self, rec, unit, row, reader):
        m = _meta(rec)
        did = next((m[k] for k in self.id_keys if m.get(k)), None)
        return doc(rec["text"], self.source, f"{self.prefix}{did}" if did else f"{self.prefix}{_h(unit)}:{row}",
                   ext=m.get("ext"))


def profile_for(source, repo, config, fcfg):
    """(profile, key into configs/download.yaml hf.files / hf.shuffle_files) for one HF sub-source."""
    if repo == "HuggingFaceFW/fineweb-edu":
        return MinScore(source, "int_score", fcfg["fineweb_edu_min_int_score"], id_field="id", meta_fields=("url",)), repo
    if repo == "HuggingFaceFW/fineweb-2":
        return MinScore(source, "language_score", fcfg["fineweb2_min_language_score"], id_field="id", meta_fields=("url",)), repo
    if repo == "HuggingFaceFW/finepdfs-edu":
        return Simple(source, id_field="id", prefix=f"finepdfs-edu.{config}:", meta_fields=("url",)), repo
    if repo == "eliceai/korean-webtext-edu":
        return MinScore(source, "score", fcfg["korean_webtext_edu_min_score"], prefix="kwe:"), repo
    if repo == "mlfoundations/dclm-baseline-1.0":
        return DCLMTop(source, fcfg["dclm_score_field"], fcfg["dclm_top_frac"], fcfg["dclm_calibration_files"]), repo
    if repo == "common-pile/stackv2_edu_filtered":
        return StackEdu(source, "score", fcfg["code_keep_frac"], fcfg["code_calibration_files"]), repo
    if repo.startswith("common-pile/"):
        return CommonPile(source, prefix=repo.split("/", 1)[1].replace("_filtered", "") + ":"), repo
    if repo == "allenai/peS2o":
        p = Simple(source, id_field="id", prefix="s2:", meta_fields=("source",))
        p.columns = None
        return p, repo
    if repo == "HuggingFaceTB/finemath":
        return Simple(source, id_field="url", meta_fields=("int_score",)), repo
    if repo == "openbmb/UltraData-Math":
        return Simple(source, text="content", prefix="ultramath:", meta_fields=("quality_label",)), repo
    if repo == "EleutherAI/proof-pile-2":
        return JsonMeta(source, "pp2:", ("hexsha",)), f"{repo}/{config}"
    if repo == "heegyu/namuwiki-extracted":
        return Simple(source, title="title", id_field="title", prefix="namu:"), repo
    if repo == "HuggingFaceFW/finewiki":    # text already starts with "# <title>"
        return Simple(source, id_field="id", prefix=f"finewiki.{config}:", meta_fields=("url",)), repo
    raise ValueError(f"no profile for {repo} ({source}); add one to data/profiles.py")
