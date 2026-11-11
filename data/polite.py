"""Polite HTTP: robots.txt per host, minimum interval per host, retries with backoff, resumable downloads.

Spec §5.2: "robots와 속도 제한을 지키고 체크섬을 기록한다".
"""
import hashlib
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser


class RobotsDisallowed(Exception):
    pass


class Polite:
    def __init__(self, user_agent, min_interval_s=1.0, retries=5, backoff_s=4.0, timeout_s=120, opener=None,
                 per_host_interval_s=None):
        self.ua = user_agent
        self.min_interval = min_interval_s
        self.per_host = dict(per_host_interval_s or {})   # host -> interval, for static bulk hosts
        self.retries = retries
        self.backoff = backoff_s
        self.timeout = timeout_s
        self._last = {}
        self._robots = {}
        self._lock = threading.Lock()
        self._open = opener or urllib.request.urlopen
        self.requests = 0

    def allowed(self, url):
        p = urllib.parse.urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            # RFC 9309: 4xx -> no restrictions; 5xx or unreachable (after retries) -> disallow everything
            rp = urllib.robotparser.RobotFileParser()
            lines = ["User-agent: *", "Disallow: /"]
            for i in range(3):
                try:
                    with self._open(urllib.request.Request(host + "/robots.txt", headers={"User-Agent": self.ua}),
                                    timeout=self.timeout) as r:
                        lines = r.read().decode("utf-8", "replace").splitlines()
                    break
                except urllib.error.HTTPError as e:
                    if 400 <= e.code < 500:
                        lines = []
                        break
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(self.backoff * (2 ** i))
            rp.parse(lines)
            self._robots[host] = rp
        return self._robots[host].can_fetch(self.ua, url)

    def _wait(self, url):
        host = urllib.parse.urlsplit(url).netloc
        gap = self.per_host.get(host, self.min_interval)
        with self._lock:
            now = time.time()
            wait = self._last.get(host, 0) + gap - now
            self._last[host] = max(now, self._last.get(host, 0) + gap)
        if wait > 0:
            time.sleep(wait)

    def _request(self, url, headers=None, method="GET"):
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        err = None
        for i in range(self.retries):
            self._wait(url)
            self.requests += 1
            try:
                return self._open(urllib.request.Request(url, method=method, headers={"User-Agent": self.ua, **(headers or {})}),
                                  timeout=self.timeout)
            except urllib.error.HTTPError as e:
                if e.code in (400, 401, 403, 404, 410, 416):
                    raise
                err = e
            except Exception as e:  # noqa: BLE001
                err = e
            time.sleep(self.backoff * (2 ** i))
        raise err

    def get(self, url, headers=None):
        with self._request(url, headers) as r:
            return r.read()

    def text(self, url):
        return self.get(url).decode("utf-8", "replace")

    def download(self, url, dest, md5=None):
        """Download to dest (resumes a partial .part file with Range). Returns {sha256, md5, bytes}.
        Raises if an expected md5 does not match."""
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        part = dest + ".part"
        for attempt in range(self.retries):
            have = os.path.getsize(part) if os.path.exists(part) else 0
            try:
                with self._request(url, headers={"Range": f"bytes={have}-"} if have else None) as r:
                    mode = "ab" if have and getattr(r, "status", 200) == 206 else "wb"
                    with open(part, mode) as f:
                        for b in iter(lambda: r.read(1 << 20), b""):
                            f.write(b)
                break
            except urllib.error.HTTPError as e:
                if e.code == 416 and have:   # already complete
                    break
                raise
            except Exception:  # noqa: BLE001 - connection cut: retry, resuming from the partial file
                if attempt == self.retries - 1:
                    raise
                time.sleep(self.backoff * (2 ** attempt))
        h_sha, h_md5 = hashlib.sha256(), hashlib.md5()
        with open(part, "rb") as f:
            for b in iter(lambda: f.read(1 << 22), b""):
                h_sha.update(b)
                h_md5.update(b)
        if md5 and h_md5.hexdigest() != md5:
            os.remove(part)
            raise ValueError(f"md5 mismatch for {url}: got {h_md5.hexdigest()}, expected {md5}")
        os.replace(part, dest)
        return {"url": url, "sha256": h_sha.hexdigest(), "md5": h_md5.hexdigest(), "bytes": os.path.getsize(dest)}
