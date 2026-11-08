"""Client HTTP condiviso: cache su disco con TTL, rate limiting, retry con backoff,
deduplica delle richieste concorrenti (single-flight) e tracciamento della provenienza.

Ogni risposta restituita è un `Fetched` con URL sorgente (chiavi API oscurate) e
timestamp di recupero: è la base della regola "ogni dato ha fonte e timestamp".
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("fbi.http")

USER_AGENT = "football-intel/1.0 (research; no betting automation)"
_SECRET_PARAMS = re.compile(r"(apiKey|api_key|key|token)=[^&]+", re.I)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def redact(url: str) -> str:
    return _SECRET_PARAMS.sub(lambda m: m.group(1) + "=***", url)


@dataclass(frozen=True)
class Fetched:
    data: Any
    source: str  # URL senza segreti
    fetched_at: str  # ISO UTC
    from_cache: bool
    headers: dict


class HttpError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{status or 'ERR'} {redact(url)}: {message}")
        self.status = status
        self.url = redact(url)


class HttpClient:
    def __init__(
        self,
        cache_dir: Path | None,
        concurrency: int = 6,
        min_interval_s: float = 0.08,
        timeout_s: float = 20.0,
        retries: int = 3,
        offline: bool = False,
    ):
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self._sem = threading.BoundedSemaphore(concurrency)
        self._min_interval = min_interval_s
        self._last_request = 0.0
        self._rate_lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()
        self._mem: dict[str, tuple[float, Fetched]] = {}
        self.timeout = timeout_s
        self.retries = retries
        self.offline = offline
        self.stats = {"requests": 0, "cache_hits": 0, "errors": 0}
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------ cache
    def _key(self, url: str, headers: dict | None) -> str:
        h = hashlib.sha256((url + json.dumps(headers or {}, sort_keys=True)).encode()).hexdigest()
        return h[:32]

    def _cache_path(self, key: str) -> Path | None:
        return self.cache_dir / f"{key}.json" if self.cache_dir else None

    def _cache_get(self, key: str, ttl: int) -> Fetched | None:
        now = time.time()
        hit = self._mem.get(key)
        if hit and (self.offline or now - hit[0] <= ttl):
            return hit[1]
        p = self._cache_path(key)
        if p and p.exists():
            try:
                blob = json.loads(p.read_text(encoding="utf-8"))
                if self.offline or now - blob["stored"] <= ttl:
                    f = Fetched(blob["data"], blob["source"], blob["fetched_at"], True, blob.get("headers", {}))
                    self._mem[key] = (blob["stored"], f)
                    return f
            except (OSError, ValueError, KeyError):
                log.warning("cache corrotta ignorata: %s", p)
        return None

    def _cache_put(self, key: str, f: Fetched) -> None:
        stored = time.time()
        self._mem[key] = (stored, f)
        p = self._cache_path(key)
        if p:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"stored": stored, "source": f.source, "fetched_at": f.fetched_at, "headers": f.headers, "data": f.data}),
                encoding="utf-8",
            )
            tmp.replace(p)

    # --------------------------------------------------------------- network
    def _throttle(self) -> None:
        with self._rate_lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _do_request(self, url: str, headers: dict | None) -> Fetched:
        if self.offline:
            raise HttpError(url, None, "modalità offline: risorsa non presente in cache")
        req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "gzip"}
        req_headers.update(headers or {})
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            with self._sem:
                self._throttle()
                with self._stats_lock:
                    self.stats["requests"] += 1
                try:
                    req = urllib.request.Request(url, headers=req_headers)
                    with urllib.request.urlopen(req, timeout=self.timeout) as r:
                        body = r.read()
                        if r.headers.get("Content-Encoding") == "gzip":
                            body = gzip.decompress(body)
                        hdrs = {k.lower(): v for k, v in r.headers.items() if k.lower().startswith(("x-", "ratelimit"))}
                        data = json.loads(body.decode("utf-8"))
                        return Fetched(data, redact(url), iso(utcnow()), False, hdrs)
                except urllib.error.HTTPError as e:
                    last_err = HttpError(url, e.code, e.reason or "")
                    if e.code in (400, 401, 403, 404, 422):
                        break  # errori non transitori: inutile riprovare
                except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
                    last_err = HttpError(url, None, str(e))
            time.sleep(min(8.0, 0.5 * 2**attempt))
        with self._stats_lock:
            self.stats["errors"] += 1
        assert last_err is not None
        raise last_err

    def get_json(self, url: str, ttl: int = 600, headers: dict | None = None, params: dict | None = None) -> Fetched:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        key = self._key(url, headers)
        cached = self._cache_get(key, ttl)
        if cached:
            with self._stats_lock:
                self.stats["cache_hits"] += 1
            return cached
        # single-flight: se un altro thread sta già scaricando lo stesso URL, aspetta
        with self._inflight_lock:
            ev = self._inflight.get(key)
            leader = ev is None
            if leader:
                ev = threading.Event()
                self._inflight[key] = ev
        if not leader:
            ev.wait(self.timeout * (self.retries + 2))
            cached = self._cache_get(key, ttl)
            if cached:
                with self._stats_lock:
                    self.stats["cache_hits"] += 1
                return cached
            return self.get_json(url, ttl, headers)  # il leader ha fallito: riprova in proprio
        try:
            f = self._do_request(url, headers)
            self._cache_put(key, f)
            return f
        finally:
            with self._inflight_lock:
                self._inflight.pop(key, None)
            ev.set()
