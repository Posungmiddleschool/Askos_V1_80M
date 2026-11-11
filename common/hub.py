"""Shared state for several Modal accounts (NOTES.md D-23).

Every Modal account has its own Volume, so the Volumes are only caches. The single source of truth is
one Hugging Face namespace (an organization both people belong to):

  <ns>/askos-v1-80m-state  (model repo, private)   locks/, logs, reports, checkpoints
  <ns>/askos-v1-80m-data   (dataset repo, private) stage artifacts, e.g. raw/<source>/...

Locks: one JSON file per unit of work, locks/<key>.json, written with a commit whose parent must be the
head we read (HfApi.create_commit(parent_commit=...)). If anyone committed in between, the commit is
refused and we re-read, so two accounts can never both take the same key. A running lock carries a
heartbeat; a lock whose heartbeat is older than `lock_stale_hours` may be taken over.

This module is imported inside Modal containers (HF_TOKEN from the `huggingface` secret). Store is an
interface so tests/test_hub.py can run the lock logic against an in-memory fake.
"""
import json
import os
import random
import time

from common.config import load_infra


class Conflict(Exception):
    """The repo head moved between our read and our commit."""


class LockHeld(Exception):
    def __init__(self, lock):
        super().__init__(f"held by {lock.get('owner')} since {_ts(lock.get('started'))}, heartbeat {_ts(lock.get('heartbeat'))}")
        self.lock = lock


class AlreadyDone(Exception):
    def __init__(self, lock):
        super().__init__(f"done by {lock.get('owner')} at {_ts(lock.get('finished'))}")
        self.lock = lock


def _ts(t):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t)) if t else "-"


# ------------------------------------------------------------------------------------------ names
def namespace(api=None):
    """ASKOS_HF_NAMESPACE env > configs/infra.yaml hub.namespace > the token owner (single-account mode)."""
    ns = os.environ.get("ASKOS_HF_NAMESPACE") or load_infra()["hub"].get("namespace")
    if ns:
        return ns
    from huggingface_hub import HfApi
    return (api or HfApi()).whoami()["name"]


def shared_mode():
    return bool(os.environ.get("ASKOS_HF_NAMESPACE") or load_infra()["hub"].get("namespace"))


def repo_ids(api=None):
    h = load_infra()["hub"]
    ns = namespace(api)
    return f"{ns}/{h['state_repo']}", f"{ns}/{h['data_repo']}"


def ensure_repos(api=None):
    from huggingface_hub import HfApi
    api = api or HfApi()
    state, data = repo_ids(api)
    api.create_repo(state, private=True, exist_ok=True, repo_type="model")
    api.create_repo(data, private=True, exist_ok=True, repo_type="dataset")
    return state, data


def hf_user(api=None):
    from huggingface_hub import HfApi
    return (api or HfApi()).whoami()["name"]


# ------------------------------------------------------------------------------------------ stores
class Store:
    def head(self):
        raise NotImplementedError

    def read(self, path, rev):
        raise NotImplementedError

    def commit(self, path, data, parent, msg):
        raise NotImplementedError

    def list(self, prefix):
        raise NotImplementedError


class HFStore(Store):
    def __init__(self, repo_id, repo_type="model", api=None):
        from huggingface_hub import HfApi
        self.repo, self.type, self.api = repo_id, repo_type, api or HfApi()

    def head(self):
        return self.api.repo_info(self.repo, repo_type=self.type).sha

    def read(self, path, rev):
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import EntryNotFoundError
        try:
            p = hf_hub_download(self.repo, path, repo_type=self.type, revision=rev, force_download=True,
                                cache_dir="/tmp/askos_hub_cache")
        except EntryNotFoundError:
            return None
        with open(p, "rb") as f:
            return f.read()

    def commit(self, path, data, parent, msg):
        from huggingface_hub import CommitOperationAdd
        from huggingface_hub.utils import HfHubHTTPError
        try:
            self.api.create_commit(self.repo, repo_type=self.type, commit_message=msg, parent_commit=parent,
                                   operations=[CommitOperationAdd(path_in_repo=path, path_or_fileobj=data)])
        except HfHubHTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in (409, 412) or "parent" in str(e).lower() or "precondition" in str(e).lower():
                raise Conflict(str(e)) from e
            raise

    def list(self, prefix):
        from huggingface_hub.utils import EntryNotFoundError, HfHubHTTPError
        try:
            return sorted(f.path for f in self.api.list_repo_tree(self.repo, path_in_repo=prefix, recursive=True,
                                                                  repo_type=self.type) if f.path.endswith(".json"))
        except EntryNotFoundError:
            return []
        except HfHubHTTPError as e:          # folder does not exist yet
            if getattr(getattr(e, "response", None), "status_code", None) == 404:
                return []
            raise


class MemStore(Store):
    """In-memory store with the same head/parent semantics (tests)."""

    def __init__(self):
        self.files, self.n = {}, 0
        self.before_commit = None     # hook to simulate a concurrent writer

    def head(self):
        return str(self.n)

    def read(self, path, rev):
        return self.files.get(path)

    def commit(self, path, data, parent, msg):
        if self.before_commit:
            hook, self.before_commit = self.before_commit, None
            hook()
        if parent != self.head():
            raise Conflict(f"head {self.head()} != parent {parent}")
        self.files[path] = data
        self.n += 1

    def list(self, prefix):
        return sorted(p for p in self.files if p.startswith(prefix.rstrip("/") + "/"))


# ------------------------------------------------------------------------------------------ locks
def lock_path(key):
    return f"locks/{key}.json"


def _read_lock(store, key, head):
    b = store.read(lock_path(key), head)
    return json.loads(b) if b else None


def _write(store, key, lock, head, msg):
    store.commit(lock_path(key), json.dumps(lock, ensure_ascii=False, indent=1).encode(), head, msg)


def _retry(fn, tries=10):
    for i in range(tries):
        try:
            return fn()
        except Conflict:
            time.sleep(min(30.0, 0.5 * 2 ** i) * (0.5 + random.random()))
    raise RuntimeError("lock: too much contention on the state repo")


def acquire(store, key, owner, stale_hours, force=False, info=None, now=None):
    """Take `key` for `owner`. Raises LockHeld / AlreadyDone. `force` takes over a done lock (a human-approved
    rerun); a running lock of another owner is never forced unless its heartbeat is stale."""
    def attempt():
        t = now if now is not None else time.time()
        head = store.head()
        cur = _read_lock(store, key, head)
        if cur:
            alive = t - cur.get("heartbeat", 0) < stale_hours * 3600
            if cur["status"] == "running" and cur["owner"] != owner and alive:
                raise LockHeld(cur)
            if cur["status"] == "done" and not force:
                raise AlreadyDone(cur)
        hist = (cur or {}).get("history", [])
        if cur:
            hist = (hist + [{k: cur.get(k) for k in ("owner", "status", "started", "finished", "summary")}])[-10:]
        lock = {"key": key, "owner": owner, "status": "running", "started": t, "heartbeat": t, "finished": None,
                "attempt": (cur or {}).get("attempt", 0) + 1, "info": info or {}, "progress": {}, "summary": None,
                "history": hist, "took_over_stale": bool(cur and cur["status"] == "running" and cur["owner"] != owner)}
        _write(store, key, lock, head, f"lock {key}: {owner} starts")
        return lock
    return _retry(attempt)


def heartbeat(store, key, owner, progress=None, now=None):
    """Refresh our lock. Returns False if the lock is no longer ours (taken over after going stale)."""
    def attempt():
        t = now if now is not None else time.time()
        head = store.head()
        cur = _read_lock(store, key, head)
        if not cur or cur["owner"] != owner or cur["status"] != "running":
            return False
        cur["heartbeat"] = t
        if progress is not None:
            cur["progress"] = progress
        _write(store, key, cur, head, f"lock {key}: heartbeat")
        return True
    return _retry(attempt)


def release(store, key, owner, status, summary=None, now=None):
    """status: 'done' (nobody needs to run it again) or 'failed' (anyone may retry)."""
    assert status in ("done", "failed")

    def attempt():
        t = now if now is not None else time.time()
        head = store.head()
        cur = _read_lock(store, key, head)
        if not cur or cur["owner"] != owner:
            return False
        cur.update(status=status, finished=t, heartbeat=t, summary=summary)
        _write(store, key, cur, head, f"lock {key}: {status} by {owner}")
        return True
    return _retry(attempt)


def locks(store, prefix=""):
    head = store.head()
    out = {}
    for p in store.list("locks/" + prefix if prefix else "locks"):
        b = store.read(p, head)
        if b:
            lk = json.loads(b)
            out[lk["key"]] = lk
    return out


def lock_table(lks, now=None, stale_hours=None):
    t = now or time.time()
    stale_hours = stale_hours or load_infra()["hub"]["lock_stale_hours"]
    rows = []
    for k in sorted(lks):
        lk = lks[k]
        st = lk["status"]
        if st == "running" and t - lk.get("heartbeat", 0) >= stale_hours * 3600:
            st = "running(stale)"
        prog = lk.get("progress") or {}
        p = f"{prog.get('bytes', 0) / 1e9:.2f}/{prog.get('target', 0) / 1e9:.2f} GB" if prog.get("target") else ""
        rows.append(f"{k:<28} {st:<15} {lk['owner']:<32} hb {_ts(lk.get('heartbeat'))}  {p}")
    return "\n".join(rows) if rows else "(잠금 없음: 아직 아무 계정도 시작하지 않음)"


# ------------------------------------------------------------------------------------------ artifacts
def push_dir(local_root, rel_prefix, api=None, ignore=("*.tmp", "**/*.tmp", ".tmp_*", "**/.tmp_*")):
    """Upload <local_root>/<rel_prefix>/ to the data repo at the same paths. Files already on the Hub with the
    same content are not sent again (hf-xet deduplicates), so pushing the same folder repeatedly is cheap."""
    from huggingface_hub import HfApi
    api = api or HfApi()
    _, data = ensure_repos(api)
    src = os.path.join(local_root, rel_prefix)
    if not os.path.isdir(src):
        return None
    api.upload_folder(repo_id=data, repo_type="dataset", folder_path=src, path_in_repo=rel_prefix,
                      ignore_patterns=list(ignore), commit_message=f"push {rel_prefix}")
    return f"{data}/{rel_prefix}"


def pull_dir(local_root, rel_prefix, api=None, patterns=None):
    """Download <rel_prefix>/** (or `patterns`) from the data repo into local_root at the same paths."""
    from huggingface_hub import HfApi, snapshot_download
    api = api or HfApi()
    _, data = ensure_repos(api)
    snapshot_download(data, repo_type="dataset", local_dir=local_root,
                      allow_patterns=patterns or [f"{rel_prefix}/**"])
    return f"{data}/{rel_prefix}"


def remote_json(rel_path, api=None):
    """A small JSON file from the data repo, or None."""
    from huggingface_hub import HfApi
    store = HFStore(repo_ids(api or HfApi())[1], "dataset", api)
    b = store.read(rel_path, None)
    return json.loads(b) if b else None
