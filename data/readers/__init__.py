"""Readers for each `kind` in data/sources.yaml. See data/quota.py for the Reader protocol."""


class Reader:
    sub = "base"

    def units(self):
        raise NotImplementedError

    def docs(self, unit_idx, start_row):
        raise NotImplementedError

    def info(self):
        return {}


def doc(text, source, doc_id, **meta):
    """Raw document. `meta` keeps only small fields that later steps use (decay stream, report)."""
    return {"text": text, "source": source, "doc_id": str(doc_id),
            "meta": {k: v for k, v in meta.items() if v is not None}}
