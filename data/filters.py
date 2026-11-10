"""Pure-python filters and parsers used by the readers (unit-tested in tests/test_download.py)."""
import html
import math
import re

# ---------------------------------------------------------------- code (spec §5.1 fallback heuristic)
_COMMENT = re.compile(r"^\s*(#|//|/\*|\*|--|;|%|<!--|\"\"\"|''')")


def code_quality(code):
    """Spec fallback for the code source: 'comment-ratio and line-length heuristics'.
    Score in [0, 1]: comment density (0.6), typical line length near 40 (0.2), no very long lines (0.2).
    Continuous, so the 'top half' threshold is well defined."""
    lines = [l for l in code.splitlines() if l.strip()]
    if not lines:
        return 0.0
    n = len(lines)
    comment = sum(1 for l in lines if _COMMENT.match(l)) / n
    lengths = [len(l) for l in lines]
    mean = sum(lengths) / n
    longest = max(lengths)
    s = 0.6 * min(comment, 0.4) / 0.4
    s += 0.2 * math.exp(-((mean - 40.0) / 30.0) ** 2)
    s += 0.2 * (1.0 if longest <= 200 else max(0.0, 1.0 - (longest - 200) / 800))
    return round(s, 6)


def quantile(values, q):
    """Nearest-rank quantile (no numpy needed)."""
    if not values:
        raise ValueError("no values for quantile")
    v = sorted(values)
    k = min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))
    return v[k]


# ---------------------------------------------------------------- Gutenberg
def gutenberg_author_match(authors_field, prefixes):
    """authors_field: catalog 'Authors' column, e.g. 'Twain, Mark, 1835-1910; Clemens, ...'.
    Matches only real authors (not 'Illustrator'/'Translator'/'Editor' roles)."""
    for a in (x.strip() for x in authors_field.split(";")):
        if re.search(r"\[(Illustrator|Translator|Editor|Contributor|Commentator|Annotator)\]", a):
            continue
        for p in prefixes:
            if a == p or a.startswith(p + ",") or a.startswith(p + " "):
                return True
    return False


_PG_START = re.compile(r"^\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.M | re.I)
_PG_END = re.compile(r"^\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.M | re.I)


def strip_gutenberg(text):
    """Keep only the book between the PG START/END markers (license boilerplate is not the book)."""
    s, e = _PG_START.search(text), _PG_END.search(text)
    if s:
        text = text[s.end():]
        e = _PG_END.search(text)
    if e:
        text = text[:e.start()]
    return text.strip()


# ---------------------------------------------------------------- Wikisource (ko)
def kowikisource_author(wikitext, authors):
    """Returns the author if the page header ({{머리말 | author = [[글쓴이:X|X]]}}) names one of `authors`."""
    m = re.search(r"\|\s*(?:author|저자|지은이)\s*=\s*\[\[\s*(?:글쓴이|저자|作家)?\s*:?\s*([^|\]]+)", wikitext)
    if not m:
        return None
    name = m.group(1).strip()
    return name if name in authors else None


# ---------------------------------------------------------------- mail
def is_reply(subject):
    return bool(re.match(r"^\s*(re|aw|sv)\s*:", subject or "", re.I))


_MH_BODY = re.compile(r"<!--X-Body-of-Message-->(.*?)<!--X-Body-of-Message-End-->", re.S)
_MH_SUBJ = re.compile(r"<!--X-Subject:\s*(.*?)\s*-->", re.S)


def mhonarc_message(page_html):
    """(subject, body_text) from a MHonArc message page (lists.debian.org). Body keeps quotes;
    quote/signature removal belongs to clean (spec §5.3)."""
    b = _MH_BODY.search(page_html)
    if not b:
        return None, None
    s = _MH_SUBJ.search(page_html)
    body = re.sub(r"<[^>]+>", "", b.group(1))
    return (html.unescape(s.group(1)) if s else ""), html.unescape(body).strip()


def mail_text(subject, body):
    return f"Subject: {subject}\n\n{body}".strip()


# ---------------------------------------------------------------- versions
def version_key(name):
    return tuple(int(x) for x in re.findall(r"\d+", name))


def latest(names):
    return max(names, key=version_key) if names else None
