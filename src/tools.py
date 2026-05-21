"""
src/tools.py  (cache-enabled version)

Drop-in replacement — wraps every tool function with ResponseCache so
repeated identical calls are served from disk, not from the live APIs.

Set ACMG_CACHE_TTL_SECONDS in your environment to control freshness:
  export ACMG_CACHE_TTL_SECONDS=86400   # 24 h
  export ACMG_CACHE_TTL_SECONDS=0       # always re-fetch (disables cache)
Unset = cache forever (best for report-building sessions).
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

from .acmg.rules import map_acmg_rules
from .cache import ResponseCache
from .models import EvidenceItem
from .normalizer import normalize_variant
from .sources.clinvar import fetch_clinvar_summary as _fetch_clinvar
from .sources.litvar import search_litvar as _search_litvar
from .sources.pubmed import fetch_pubmed_abstracts as _fetch_abstracts
from .sources.pubmed import search_pubmed as _search_pubmed
from .sources.pubtator import get_pubtator_annotations as _get_pubtator


# ---------------------------------------------------------------------------
# Cache singleton — shared across all tool calls in a session
# ---------------------------------------------------------------------------

def _make_cache() -> Optional[ResponseCache]:
    ttl_env = os.environ.get("ACMG_CACHE_TTL_SECONDS")
    if ttl_env == "0":
        return None                         # caching disabled explicitly
    ttl = int(ttl_env) if ttl_env else None # None = cache forever
    return ResponseCache(ttl_seconds=ttl)


_cache: Optional[ResponseCache] = _make_cache()


def _cached(namespace: str, key: str, fetch_fn) -> str:
    if _cache is None:
        return fetch_fn()
    return _cache.get_or_fetch(namespace, key, fetch_fn)


# ---------------------------------------------------------------------------
# Safety wrapper
# ---------------------------------------------------------------------------

def _safe_str(result) -> str:
    if result is None:
        return json.dumps({"error": "Tool returned None"})
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, indent=2)
    except (TypeError, ValueError) as exc:
        return json.dumps({"error": "Could not serialise tool result: {0}".format(exc)})


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def tool_normalize_variant(variant: str, gene: Optional[str] = None) -> str:
    key = "{0}|{1}".format(variant, gene or "")
    try:
        return _cached("normalize_variant", key,
                       lambda: normalize_variant(variant, gene).model_dump_json(indent=2))
    except Exception as exc:
        return json.dumps({"error": str(exc), "variant": variant})


def tool_search_litvar(variant_query: str) -> str:
    try:
        return _cached("litvar", variant_query,
                       lambda: _safe_str(_search_litvar(variant_query)))
    except Exception as exc:
        return json.dumps({"error": str(exc), "variant_query": variant_query})


def tool_search_pubmed(query: str, retmax: int = 5) -> str:
    # Strip genomic coordinates — they don't match PubMed records
    clean = re.sub(r"(?:chr)?\d+:\d+[-_]\d+", "", query).strip()
    clean = re.sub(r"\s+", " ", clean)
    key = "{0}|{1}".format(clean, retmax)
    try:
        return _cached("pubmed_search", key,
                       lambda: _safe_str(_search_pubmed(clean, retmax=retmax)))
    except Exception as exc:
        return json.dumps({"error": str(exc), "query": clean})


def tool_fetch_pubmed_abstracts(pmids: list[str]) -> str:
    key = ",".join(sorted(pmids))
    try:
        return _cached("pubmed_abstracts", key,
                       lambda: _safe_str(_fetch_abstracts(pmids)))
    except Exception as exc:
        return json.dumps({"error": str(exc), "pmids": pmids})


def tool_get_pubtator_annotations(pmids: list[str]) -> str:
    key = ",".join(sorted(pmids))
    try:
        return _cached("pubtator", key,
                       lambda: _safe_str(_get_pubtator(pmids)))
    except Exception as exc:
        return json.dumps({"error": str(exc), "pmids": pmids})


def tool_fetch_clinvar_summary(query: str) -> str:
    try:
        return _cached("clinvar", query,
                       lambda: _safe_str(_fetch_clinvar(query=query)))
    except Exception as exc:
        return json.dumps({"error": str(exc), "query": query})


def tool_map_acmg_rules(evidence_json: str) -> str:
    try:
        raw = json.loads(evidence_json)
        evidence = [EvidenceItem(**item) for item in raw]
        return map_acmg_rules(evidence).model_dump_json(indent=2)
    except json.JSONDecodeError as exc:
        return json.dumps({"error": "Invalid JSON in evidence_json: {0}".format(exc)})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
