"""Pure-python filters and parsers used by the readers (unit-tested in tests/test_download.py)."""
import math
import re

# ---------------------------------------------------------------- statistics
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


# ---------------------------------------------------------------- versions
def version_key(name):
    return tuple(int(x) for x in re.findall(r"\d+", name))


def latest(names):
    return max(names, key=version_key) if names else None
