"""
src/cache.py

Persistent JSON cache for all API call results.
Usage:
    from .cache import ResponseCache
    cache = ResponseCache()                        # default: .cache/responses.json
    result = cache.get_or_fetch("clinvar", key, fetch_fn)
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional


DEFAULT_CACHE_DIR = Path(".cache")
DEFAULT_CACHE_FILE = DEFAULT_CACHE_DIR / "responses.json"


class ResponseCache:
    """
    Simple persistent JSON cache keyed by (namespace, content_hash).

    Each entry stores:
      - result   : the raw return value from the fetch function
      - key      : the human-readable key (for inspection)
      - namespace: which tool/source produced it
      - fetched_at: ISO timestamp
      - hit_count : how many times this entry was served from cache
    """

    def __init__(self, path: Path | str = DEFAULT_CACHE_FILE, ttl_seconds: Optional[int] = None):
        self.path = Path(path)
        self.ttl = ttl_seconds          # None = cache forever (good for dev)
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_fetch(
        self,
        namespace: str,
        key: str,
        fetch_fn: Callable[[], str],
        force_refresh: bool = False,
    ) -> str:
        """
        Return cached result for (namespace, key) if it exists and is fresh.
        Otherwise call fetch_fn(), store the result, and return it.

        Parameters
        ----------
        namespace    : e.g. "clinvar", "pubmed_search", "pubmed_abstracts"
        key          : human-readable identifier, e.g. the query string
        fetch_fn     : zero-argument callable that returns a str result
        force_refresh: bypass cache even if a valid entry exists
        """
        cache_key = self._make_key(namespace, key)
        entry = self._data.get(cache_key)

        if not force_refresh and entry and self._is_fresh(entry):
            entry["hit_count"] = entry.get("hit_count", 0) + 1
            self._save()
            return entry["result"]

        # Cache miss — call the real function
        result = fetch_fn()
        self._data[cache_key] = {
            "namespace": namespace,
            "key": key,
            "result": result,
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "hit_count": 0,
        }
        self._save()
        return result

    def invalidate(self, namespace: str, key: str) -> None:
        cache_key = self._make_key(namespace, key)
        self._data.pop(cache_key, None)
        self._save()

    def clear_namespace(self, namespace: str) -> int:
        before = len(self._data)
        self._data = {
            k: v for k, v in self._data.items()
            if v.get("namespace") != namespace
        }
        self._save()
        return before - len(self._data)

    def stats(self) -> dict[str, Any]:
        namespaces: dict[str, int] = {}
        for entry in self._data.values():
            ns = entry.get("namespace", "unknown")
            namespaces[ns] = namespaces.get(ns, 0) + 1
        return {
            "total_entries": len(self._data),
            "cache_file": str(self.path),
            "ttl_seconds": self.ttl,
            "by_namespace": namespaces,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _make_key(namespace: str, key: str) -> str:
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return "{0}:{1}".format(namespace, digest)

    def _is_fresh(self, entry: dict[str, Any]) -> bool:
        if self.ttl is None:
            return True
        fetched = entry.get("fetched_at", "")
        try:
            fetched_ts = time.mktime(time.strptime(fetched, "%Y-%m-%dT%H:%M:%SZ"))
            return (time.time() - fetched_ts) < self.ttl
        except Exception:
            return False

    def _load(self) -> None:
        if self.path.exists():
            try:
                with open(self.path) as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)
