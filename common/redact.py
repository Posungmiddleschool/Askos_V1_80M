"""Token redaction for every printed and uploaded line (guide §2).

modal_app.py keeps its own copy of these patterns (it must not import local modules);
tests/test_infra.py checks that both copies are identical.
"""
import re

TOK_PATTERNS = [
    (r"hf_[A-Za-z0-9]{20,}", "hf_***"),
    (r"\b(?:ak|as)-[A-Za-z0-9]{16,}", "modal_***"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", "<private key>"),
]
_COMPILED = [(re.compile(p), r) for p, r in TOK_PATTERNS]


def redact(text):
    for pat, rep in _COMPILED:
        text = pat.sub(rep, text)
    return text
