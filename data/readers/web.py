"""Readers for non-HF sources: archive.org StackExchange, Wikimedia dumps, RFC, man-pages, Jargon File,
Gutenberg, OpenStax, irclogs.ubuntu.com, lists.debian.org, lore.kernel.org (LKML), ConvoKit, HN (BigQuery).

Each reader freezes its unit list at the first run (<state_dir>/_files_<sub>.json) so resume points stay
valid, downloads through data.polite.Polite (robots.txt, per-host interval, retries, checksums) and
records every archive checksum in info().
"""
import csv
import datetime as dt
import email
import email.policy
import gzip
import html
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile

from data.filters import (gutenberg_author_match, is_reply, kowikisource_author, latest, mail_text,
                          mhonarc_message, strip_gutenberg)
from data.io import atomic_write_json, open_lines, read_json, safe_name
from data.readers import Reader, doc


def _local(tag):
    return tag.rsplit("}", 1)[-1]


class Frozen(Reader):
    """Base: unit list computed once by _compute_units() and frozen in state_dir."""

    def __init__(self, source, sub, polite, tmp_dir, state_dir):
        self.source, self.sub, self.polite = source, safe_name(sub), polite
        self.tmp = os.path.join(tmp_dir, self.sub)
        self.state_dir = state_dir
        self._units = None
        self.extra = {}
        self.checksums = {}

    def _cache(self):
        return os.path.join(self.state_dir, f"_files_{self.sub}.json")

    def units(self):
        if self._units is None:
            c = read_json(self._cache())
            if c is None:
                units, extra = self._compute_units()
                c = {"units": units, "extra": extra}
                atomic_write_json(self._cache(), c)
            self._units, self.extra = c["units"], c["extra"]
        return [u if isinstance(u, str) else json.dumps(u, ensure_ascii=False) for u in self._units]

    def unit(self, i):
        self.units()
        return self._units[i]

    def _dl(self, url, name, md5=None):
        dest = os.path.join(self.tmp, name)
        if not os.path.exists(dest):
            rec = self.polite.download(url, dest, md5=md5)
            self.checksums[url] = {"sha256": rec["sha256"], "md5": rec["md5"], "bytes": rec["bytes"]}
        return dest

    def _clean_tmp(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def info(self):
        try:
            n = len(self.units())
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)[:300]}
        return {"units": n, "checksums": self.checksums, **{k: v for k, v in self.extra.items() if k != "big"}}


# ------------------------------------------------------------------------------------ StackExchange
class StackExchange(Frozen):
    """archive.org/details/stackexchange: answers with Score >= min joined to their question.
    One document per question with all qualifying answers (best first), HTML kept for clean (§5.3)."""

    def __init__(self, source, spec, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "archive_stackexchange", polite, tmp_dir, state_dir)
        self.sites, self.item = spec["sites"], cfg["stackexchange"]["archive_item"]
        self.min_score = cfg["filters"]["stackexchange_min_answer_score"]
        self._cache_docs = None

    def _compute_units(self):
        meta = json.loads(self.polite.get(f"https://archive.org/metadata/{self.item}"))
        files = {f["name"]: f for f in meta["files"]}
        units, missing = [], []
        for s in self.sites:
            name = f"{s}.stackexchange.com.7z"
            if name in files:
                units.append({"site": s, "file": name, "md5": files[name].get("md5"), "size": int(files[name].get("size", 0))})
            else:
                missing.append(s)
        return units, {"missing_sites": missing, "item": self.item}

    def _parse(self, path):
        p = subprocess.Popen(["7z", "e", "-so", path, "Posts.xml"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def rows():
            for _, el in ET.iterparse(p.stdout, events=("end",)):
                if el.tag == "row":
                    yield dict(el.attrib)
                el.clear()
        out = join_posts(rows(), self.min_score)
        if p.wait() != 0:
            raise RuntimeError(f"7z failed on {path}")
        return out

    def docs(self, unit_idx, start_row):
        u = self.unit(unit_idx)
        path = self._dl(f"https://archive.org/download/{self.item}/{u['file']}", u["file"], md5=u["md5"])
        try:
            rows = self._parse(path)
        finally:
            os.remove(path)
        for row, (qid, text, qs, best, n, tags) in enumerate(rows):
            if row >= start_row:
                yield row, doc(text, self.source, f"se.{u['site']}:{qid}", site=u["site"], q_score=qs,
                               se_score=best, n_answers=n, tags=tags[:200])


def join_posts(rows, min_score):
    """Posts.xml rows -> [(qid, text, q_score, best_answer_score, n_answers, tags)] for questions that have
    at least one answer with Score >= min_score, ordered by question id."""
    q, ans = {}, {}
    for a in rows:
        t = a.get("PostTypeId")
        if t == "1":
            q[a["Id"]] = (a.get("Title", ""), a.get("Body", ""), a.get("Tags", ""), int(a.get("Score", 0)))
        elif t == "2" and int(a.get("Score", 0)) >= min_score and a.get("ParentId"):
            ans.setdefault(a["ParentId"], []).append((int(a["Score"]), a.get("Body", "")))
    out = []
    for qid in sorted((k for k in ans if k in q), key=int):
        title, body, tags, qs = q[qid]
        a = sorted(ans[qid], key=lambda x: -x[0])
        text = f"{title}\n\n{body}\n\n" + "\n\n".join(f"<hr/>\n{b}" for _, b in a)
        out.append((qid, text, qs, a[0][0], len(a), tags))
    return out


# ------------------------------------------------------------------------------------ Wikimedia dumps
class WikiDump(Frozen):
    """pages-articles dump: main namespace, no redirects. Optional author filter (kowikisource)."""

    def __init__(self, source, wiki, polite, tmp_dir, state_dir, authors=None):
        super().__init__(source, f"dump_{wiki}", polite, tmp_dir, state_dir)
        self.wiki, self.authors = wiki, authors

    def _compute_units(self):
        url = f"https://dumps.wikimedia.org/{self.wiki}/latest/{self.wiki}-latest-pages-articles.xml.bz2"
        return [url], {"author_filter": self.authors}

    def docs(self, unit_idx, start_row):
        url = self.unit(unit_idx)
        path = self._dl(url, os.path.basename(url))
        try:
            yield from parse_wiki_dump(path, self.source, self.wiki, start_row, self.authors)
        finally:
            os.remove(path)


def parse_wiki_dump(path_or_file, source, wiki, start_row=0, authors=None):
    """(row, doc) for main-namespace, non-redirect pages. row = page index in the dump."""
    import bz2
    f = bz2.open(path_or_file) if isinstance(path_or_file, str) and path_or_file.endswith(".bz2") else path_or_file
    row = -1
    for _, el in ET.iterparse(f, events=("end",)):
        if _local(el.tag) != "page":
            continue
        row += 1
        if row < start_row:
            el.clear()
            continue
        top = {_local(c.tag): c for c in el}                      # direct children only
        rev = top.get("revision")
        txt = next((c for c in rev if _local(c.tag) == "text"), None) if rev is not None else None
        ns = top["ns"].text if "ns" in top else "0"
        text = txt.text if txt is not None else None
        if ns == "0" and "redirect" not in top and text:
            title, pid = top["title"].text, top["id"].text
            a = kowikisource_author(text, authors) if authors is not None else None
            if authors is None or a:
                yield row, doc(f"{title}\n\n{text}", source, f"{wiki}:{pid}", wiki=wiki, author=a)
        el.clear()


# ------------------------------------------------------------------------------------ hacker docs
class RFC(Frozen):
    """All RFC .txt files: rsync text-only module first, HTTP per-file fallback (rfc-index.txt)."""

    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "rfc_editor", polite, tmp_dir, state_dir)
        self.c = cfg["hacker_docs"]

    def _compute_units(self):
        return ["rfc-all"], {}

    def _numbers_http(self):
        idx = self.polite.text(self.c["rfc_index"])
        return sorted({int(m.group(1)) for m in re.finditer(r"^(\d{4})\s+(?!Not Issued)", idx, re.M)})

    def docs(self, unit_idx, start_row):
        d = os.path.join(self.tmp, "rsync")
        os.makedirs(d, exist_ok=True)
        files = {}
        r = subprocess.run(["rsync", "-a", "--timeout=300", "--include=rfc[0-9]*.txt", "--exclude=*",
                            self.c["rfc_rsync"] + "/", d + "/"], capture_output=True, text=True)
        if r.returncode == 0:
            for f in os.listdir(d):
                m = re.fullmatch(r"rfc(\d+)\.txt", f)
                if m:
                    files[int(m.group(1))] = os.path.join(d, f)
            self.extra["method"] = "rsync"
        if not files:
            self.extra["method"] = f"http (rsync rc={r.returncode}: {r.stderr.strip()[:200]})"
            nums = self._numbers_http()
        else:
            nums = sorted(files)
        self.extra["rfcs"] = len(nums)
        for row, n in enumerate(nums):
            if row < start_row:
                continue
            try:
                text = open(files[n], encoding="utf-8", errors="replace").read() if n in files \
                    else self.polite.text(self.c["rfc_http"].format(n=n))
            except Exception:  # noqa: BLE001 - e.g. 404 for an RFC published only as PDF
                yield row, None
                continue
            yield row, doc(text, self.source, f"rfc:{n}", rfc=n)
        self._clean_tmp()


class ManPages(Frozen):
    """Latest man-pages tarball (kernel.org), each page rendered with groff to UTF-8 text."""

    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "man_pages", polite, tmp_dir, state_dir)
        self.dir = cfg["hacker_docs"]["manpages_dir"]

    def _compute_units(self):
        names = set(re.findall(r'man-pages-[\d.]+\.tar\.xz', self.polite.text(self.dir)))
        name = latest(sorted(names))
        if not name:
            raise ValueError("no man-pages tarball found")
        return [self.dir + name], {"tarball": name, "debian_manpages": "skipped: overlaps the upstream project (NOTES.md D-13)"}

    @staticmethod
    def render(path):
        r = subprocess.run(["groff", "-t", "-mandoc", "-Tutf8", "-P-cbou", path], capture_output=True)
        if r.returncode == 0 and r.stdout.strip():
            return re.sub(r"\x1b\[[0-9;]*m", "", r.stdout.decode("utf-8", "replace"))
        return None

    def docs(self, unit_idx, start_row):
        url = self.unit(unit_idx)
        tb = self._dl(url, os.path.basename(url))
        root = os.path.join(self.tmp, "x")
        with tarfile.open(tb) as t:
            t.extractall(root, filter="data")
        pages = []
        for d, _, fs in os.walk(root):
            if re.search(r"/man\d[a-z]*$", d):
                pages += [os.path.join(d, f) for f in fs]
        for row, p in enumerate(sorted(pages)):
            if row < start_row:
                continue
            src = open(p, encoding="utf-8", errors="replace").read()
            if src.lstrip().startswith(".so "):       # alias stub
                continue
            text = self.render(p) or src
            yield row, doc(text, self.source, f"man:{os.path.basename(p)}", rendered=text is not src)
        self._clean_tmp()


class Jargon(Frozen):
    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "jargon_file", polite, tmp_dir, state_dir)
        self.dir = cfg["hacker_docs"]["jargon_dir"]

    @staticmethod
    def version(name):
        """jarg447.txt -> (4, 4, 7); jarg2912.txt -> (2, 9, 12): major, minor, rest."""
        d = re.search(r"jarg(\d+)\.txt", name).group(1)
        return int(d[0]), int(d[1]), int(d[2:] or 0)

    def _compute_units(self):
        names = set(re.findall(r'jarg\d+\.txt', self.polite.text(self.dir)))
        name = max(names, key=self.version) if names else None
        if not name:
            raise ValueError("no jargNNN.txt found")
        return [self.dir + name], {"file": name}

    def docs(self, unit_idx, start_row):
        if start_row > 0:
            return
        url = self.unit(unit_idx)
        yield 0, doc(self.polite.text(url), self.source, f"jargon:{os.path.basename(url)}")


# ------------------------------------------------------------------------------------ books
class Gutenberg(Frozen):
    """Project Gutenberg books (Type=Text) whose author matches the spec's list. One unit per book."""

    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "gutenberg", polite, tmp_dir, state_dir)
        self.c = cfg["rebel_classics"]

    def _compute_units(self):
        raw = self.polite.text(self.c["gutenberg_catalog"])
        books, per = [], {}
        for r in csv.DictReader(io.StringIO(raw)):
            if r.get("Type") != "Text":
                continue
            for surname, prefixes in self.c["gutenberg_authors"].items():
                if gutenberg_author_match(r.get("Authors", ""), prefixes):
                    books.append({"id": int(r["Text#"]), "title": r["Title"][:200], "lang": r["Language"], "author": surname})
                    per[surname] = per.get(surname, 0) + 1
                    break
        books.sort(key=lambda b: b["id"])
        return books, {"books_per_author": per, "catalog": self.c["gutenberg_catalog"]}

    def docs(self, unit_idx, start_row):
        if start_row > 0:
            return
        b = self.unit(unit_idx)
        try:
            text = strip_gutenberg(self.polite.text(self.c["gutenberg_text"].format(id=b["id"])))
        except Exception:  # noqa: BLE001
            yield 0, None
            return
        if text:
            yield 0, doc(text, self.source, f"pg:{b['id']}", author=b["author"], title=b["title"], lang=b["lang"])


class OpenStax(Frozen):
    """OpenStax textbooks under CC BY only: PDF -> text with pdftotext. One unit per book."""

    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "openstax", polite, tmp_dir, state_dir)
        self.c = cfg["papers_books"]

    def _compute_units(self):
        lst = json.loads(self.polite.get(self.c["openstax_api"]))
        books, seen = [], {}
        for it in lst.get("items", []):
            d = json.loads(self.polite.get(it["meta"]["detail_url"]))
            lic = d.get("license_name") or ""
            seen[d.get("title", "?")] = f"{lic} [{d.get('book_state')}]"
            pdf = d.get("high_resolution_pdf_url") or d.get("pdf_url")
            if lic == self.c["openstax_license"] and pdf and d.get("book_state") in self.c["openstax_states"]:
                books.append({"title": d["title"], "pdf": pdf, "license": f"{lic} {d.get('license_version', '')}".strip()})
        return books, {"licenses_seen": seen, "kept": len(books)}

    def docs(self, unit_idx, start_row):
        if start_row > 0:
            return
        b = self.unit(unit_idx)
        pdf = self._dl(b["pdf"], safe_name(b["title"]) + ".pdf")
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", pdf, "-"], capture_output=True)
        os.remove(pdf)
        if r.returncode != 0:
            yield 0, None
            return
        yield 0, doc(r.stdout.decode("utf-8", "replace"), self.source, f"openstax:{safe_name(b['title'])}",
                     title=b["title"], license=b["license"])


# ------------------------------------------------------------------------------------ geek debate
class IrcLogs(Frozen):
    """irclogs.ubuntu.com: every channel log of each day, newest day first from a fixed last day."""

    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "irclogs_ubuntu", polite, tmp_dir, state_dir)
        self.c = cfg["geek_debate"]

    def _compute_units(self):
        last = dt.date.fromisoformat(self.c["irclogs_last_day"])
        return [(last - dt.timedelta(days=i)).isoformat() for i in range(self.c["irclogs_max_days"])], {"last_day": str(last)}

    def docs(self, unit_idx, start_row):
        day = self.unit(unit_idx)
        base = "https://irclogs.ubuntu.com/" + day.replace("-", "/") + "/"
        try:
            names = sorted(set(re.findall(r'href="(%23[^"/]+\.txt)"', self.polite.text(base))))
        except Exception:  # noqa: BLE001 - day without logs
            return
        for row, n in enumerate(names):
            if row < start_row:
                continue
            try:
                text = self.polite.text(base + n)
            except Exception:  # noqa: BLE001
                yield row, None
                continue
            ch = html.unescape(n).replace("%23", "#")[:-4]
            text = "\n".join(l for l in text.splitlines() if not l.startswith("=== "))   # join/part/nick notices
            if text.strip():
                yield row, doc(text, self.source, f"irc:{day}:{ch}", channel=ch, day=day)


class DebianLists(Frozen):
    """lists.debian.org MHonArc archives, newest month first, capped by a total request budget."""

    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "lists_debian", polite, tmp_dir, state_dir)
        self.c = cfg["geek_debate"]
        self.budget_path = os.path.join(state_dir, f"_budget_{self.sub}.json")

    def _compute_units(self):
        y, m = map(int, self.c["debian_last_month"].split("-"))
        months = []
        for _ in range(360):
            months.append(f"{y:04d}/{m:02d}")
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        return [{"list": l, "month": mo} for mo in months for l in self.c["debian_lists"]], {}

    def _spend(self):
        b = read_json(self.budget_path, {"used": 0})
        if b["used"] >= self.c["debian_max_requests"]:
            return False
        b["used"] += 1
        atomic_write_json(self.budget_path, b)
        return True

    def docs(self, unit_idx, start_row):
        u = self.unit(unit_idx)
        base = f"https://lists.debian.org/{u['list']}/{u['month']}/"
        if not self._spend():
            return
        try:
            idx = self.polite.text(base + "maillist.html")
        except Exception:  # noqa: BLE001
            return
        msgs = sorted(set(re.findall(r'href="(msg\d+\.html)"', idx)))
        for row, mname in enumerate(msgs):
            if row < start_row:
                continue
            if not self._spend():
                return
            try:
                subj, body = mhonarc_message(self.polite.text(base + mname))
            except Exception:  # noqa: BLE001
                yield row, None
                continue
            if body:
                yield row, doc(mail_text(subj, body), self.source, f"debian:{u['list']}/{u['month']}/{mname}",
                               mlist=u["list"], reply=is_reply(subj))

    def info(self):
        i = super().info()
        i["requests_used"] = read_json(self.budget_path, {"used": 0})["used"]
        return i


class LKML(Frozen):
    """lore.kernel.org/lkml public-inbox epochs (git), newest first; discussion replies only by default."""

    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "lore_lkml", polite, tmp_dir, state_dir)
        self.c = cfg["geek_debate"]

    def _compute_units(self):
        if not self.polite.allowed(self.c["lkml_mirror"] + "/manifest.js.gz"):
            return [], {"skipped": "lore.kernel.org robots.txt: 'User-agent: * / Disallow: /' (also names AI crawlers). "
                                   "Not collected (spec §5.2: respect robots). NOTES.md D-14"}
        man = json.loads(gzip.decompress(self.polite.get(self.c["lkml_mirror"] + "/manifest.js.gz")))
        eps = sorted((int(re.search(r"/(\d+)\.git$", k).group(1)) for k in man if re.search(r"/(\d+)\.git$", k)), reverse=True)
        return [f"{self.c['lkml_mirror']}/git/{e}.git" for e in eps[: self.c["lkml_epochs_newest_first"]]], {"epochs_total": len(eps)}

    def docs(self, unit_idx, start_row):
        url = self.unit(unit_idx)
        repo = os.path.join(self.tmp, f"epoch{unit_idx}.git")
        if not os.path.exists(os.path.join(repo, "HEAD")):
            shutil.rmtree(repo, ignore_errors=True)
            if not self.polite.allowed(url):
                raise RuntimeError(f"robots.txt disallows {url}")
            subprocess.run(["git", "clone", "--mirror", "--quiet", url, repo], check=True)
        shas = subprocess.run(["git", "-C", repo, "rev-list", "--reverse", "--all"], capture_output=True,
                              text=True, check=True).stdout.split()
        cat = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            for row, sha in enumerate(shas):
                if row < start_row:
                    continue
                cat.stdin.write(f"{sha}:m\n".encode())
                cat.stdin.flush()
                header = cat.stdout.readline().split()
                if len(header) < 3 or header[1] != b"blob":   # deletion commits have no "m"
                    continue
                raw = cat.stdout.read(int(header[2]))
                cat.stdout.read(1)
                try:
                    msg = email.message_from_bytes(raw, policy=email.policy.default)
                    subj = str(msg.get("Subject", ""))
                    if self.c["lkml_only_replies"] and not is_reply(subj):
                        continue
                    part = msg.get_body(preferencelist=("plain",))
                    body = part.get_content() if part else ""
                except Exception:  # noqa: BLE001
                    yield row, None
                    continue
                if body.strip():
                    yield row, doc(mail_text(subj, body), self.source, f"lkml:{msg.get('Message-ID', sha)}", epoch_unit=unit_idx)
        finally:
            cat.stdin.close()
            cat.wait()
        shutil.rmtree(repo, ignore_errors=True)


class Convokit(Frozen):
    """ConvoKit winning-args-corpus: one document per conversation, utterances in time order."""

    def __init__(self, source, spec_id, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, f"convokit_{spec_id}", polite, tmp_dir, state_dir)
        self.url = cfg["geek_debate"]["convokit_url"]

    def _compute_units(self):
        return [self.url], {}

    def docs(self, unit_idx, start_row):
        z = self._dl(self.url, os.path.basename(self.url))
        conv = {}
        with zipfile.ZipFile(z) as zf:
            name = next(n for n in zf.namelist() if n.endswith("utterances.jsonl"))
            with zf.open(name) as f:
                for line in io.TextIOWrapper(f, encoding="utf-8"):
                    u = json.loads(line)
                    if u.get("text") and u["text"] not in ("[deleted]", "[removed]"):
                        conv.setdefault(str(u.get("conversation_id") or u.get("root")), []).append((u.get("timestamp") or 0, u["text"]))
        for row, cid in enumerate(sorted(conv)):
            if row < start_row:
                continue
            text = "\n\n".join(t for _, t in sorted(conv[cid], key=lambda x: x[0]))
            yield row, doc(text, self.source, f"cmv:{cid}", n_utterances=len(conv[cid]))
        self._clean_tmp()


class HackerNews(Frozen):
    """bigquery-public-data.hacker_news.full: threads of stories with score >= min, one unit per year.
    Needs the optional Modal secret `gcp` (GCP_SERVICE_ACCOUNT_JSON); without it the sub-source is skipped."""

    def __init__(self, source, cfg, polite, tmp_dir, state_dir):
        super().__init__(source, "bigquery_hacker_news", polite, tmp_dir, state_dir)
        self.min_score = cfg["filters"]["hn_min_story_score"]
        self.max_bytes = int(cfg["geek_debate"]["bigquery_max_bytes_billed"])

    def _compute_units(self):
        if not os.environ.get("GCP_SERVICE_ACCOUNT_JSON"):
            return [], {"skipped": "no GCP_SERVICE_ACCOUNT_JSON (optional Modal secret 'gcp' not attached)"}
        return [str(y) for y in range(2025, 2006, -1)], {}

    def _client(self):
        from google.cloud import bigquery
        from google.oauth2 import service_account
        info = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
        creds = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(credentials=creds, project=info["project_id"])

    SQL = """
    WITH RECURSIVE t AS (
      SELECT id, id AS root, 0 AS depth FROM `bigquery-public-data.hacker_news.full`
      WHERE type = 'story' AND score >= @min AND EXTRACT(YEAR FROM timestamp) = @year
        AND (dead IS NULL OR NOT dead) AND (deleted IS NULL OR NOT deleted)
      UNION ALL
      SELECT c.id, t.root, t.depth + 1 FROM `bigquery-public-data.hacker_news.full` c JOIN t ON c.parent = t.id
      WHERE c.type = 'comment' AND t.depth < 60 AND (c.dead IS NULL OR NOT c.dead) AND (c.deleted IS NULL OR NOT c.deleted)
    )
    SELECT t.root, t.depth, f.id, f.parent, f.title, f.text, f.url, f.score, f.timestamp
    FROM t JOIN `bigquery-public-data.hacker_news.full` f ON f.id = t.id
    ORDER BY t.root, f.timestamp
    """

    @staticmethod
    def _txt(s):
        s = html.unescape(s or "")
        s = re.sub(r"<p>", "\n\n", s)
        return re.sub(r"<[^>]+>", "", s).strip()

    def docs(self, unit_idx, start_row):
        from google.cloud import bigquery
        year = int(self.unit(unit_idx))
        job = self._client().query(self.SQL, job_config=bigquery.QueryJobConfig(maximum_bytes_billed=self.max_bytes, query_parameters=[
            bigquery.ScalarQueryParameter("min", "INT64", self.min_score),
            bigquery.ScalarQueryParameter("year", "INT64", year)]))
        threads = {}
        for r in job.result(page_size=50_000):
            threads.setdefault(r["root"], []).append(dict(r.items()))
        for row, root in enumerate(sorted(threads)):
            if row < start_row:
                continue
            items = threads[root]
            story = next((x for x in items if x["id"] == root), None)
            if story is None:
                continue
            kids = {}
            for x in items:
                if x["id"] != root:
                    kids.setdefault(x["parent"], []).append(x)
            out = [story.get("title") or "", story.get("url") or "", self._txt(story.get("text"))]

            def walk(pid, depth):
                for c in kids.get(pid, []):
                    t = self._txt(c.get("text"))
                    if t:
                        out.append("  " * depth + t.replace("\n", "\n" + "  " * depth))
                    walk(c["id"], depth + 1)
            walk(root, 0)
            yield row, doc("\n\n".join(x for x in out if x), self.source, f"hn:{root}", hn_score=story.get("score"),
                           n_comments=len(items) - 1, year=year)
