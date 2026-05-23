# src/sources/mavedb.py
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

import requests

BASE_URL = "https://api.mavedb.org/api/v1"
REQUEST_DELAY = 0.4


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(path: str, params: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Any:
    time.sleep(REQUEST_DELAY)
    r = requests.get(f"{BASE_URL}{path}", params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: Dict[str, Any], timeout: int = 30) -> Any:
    time.sleep(REQUEST_DELAY)
    r = requests.post(f"{BASE_URL}{path}", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def _search_score_sets(gene: str, retmax: int = 5) -> List[Dict[str, Any]]:
    data = _post("/score-sets/search", {"targetName": gene})
    all_sets: List[Dict[str, Any]] = data.get("scoreSets", [])

    # Try exact match on targetGenes[].name first
    exact = [
        ss for ss in all_sets
        if any(
            tg.get("name", "").upper() == gene.upper()
            for tg in ss.get("targetGenes", [])
        )
    ]
    if exact:
        return exact[:retmax]

    # Try match on mappedHgncName (how human genes are often stored)
    hgnc_match = [
        ss for ss in all_sets
        if any(
            (tg.get("mappedHgncName") or "").upper() == gene.upper()
            for tg in ss.get("targetGenes", [])
        )
    ]
    if hgnc_match:
        return hgnc_match[:retmax]

    # No match — return empty rather than unrelated sets
    return []


def _get_variant_scores(urn: str, page_size: int = 100) -> List[Dict[str, Any]]:
    time.sleep(REQUEST_DELAY)
    r = requests.get(
        f"{BASE_URL}/score-sets/{urn}/variants/data",
        params={"pageSize": page_size},
        timeout=30,
    )
    r.raise_for_status()
    if not r.content or not r.content.strip():
        return []
    return r.json().get("data", [])


# ---------------------------------------------------------------------------
# Variant matching
# ---------------------------------------------------------------------------

def _normalise_variant_row(v: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalise a raw API variant row to consistent snake_case keys.
    The MaveDB v2 API returns camelCase (hgvsPro, hgvsNt); earlier versions
    used snake_case.  We accept both and always output snake_case so that
    render_report.py and map_acmg_rules see a stable schema.
    """
    return {
        "hgvs_pro": v.get("hgvsPro") or v.get("hgvs_pro") or "",
        "hgvs_nt":  v.get("hgvsNt")  or v.get("hgvs_nt")  or "",
        "score":    v.get("score"),
        **{
            k: v[k] for k in v
            if k not in ("hgvsPro", "hgvsNt", "hgvs_pro", "hgvs_nt", "score")
        },
    }


def _match_variant(
    variants: List[Dict[str, Any]],
    hgvs_terms: List[str],
) -> List[Dict[str, Any]]:
    """
    Return normalised rows whose hgvs_pro or hgvs_nt contains any query term.
    Comparison is case-insensitive substring match, which handles both
    one-letter ('p.V1736A') and three-letter ('p.Val1736Ala') HGVS forms.
    """
    hits = []
    for raw in variants:
        v   = _normalise_variant_row(raw)
        pro = v["hgvs_pro"].lower()
        nt  = v["hgvs_nt"].lower()
        for term in hgvs_terms:
            if term.lower() in pro or term.lower() in nt:
                hits.append(v)
                break
    return hits


# ---------------------------------------------------------------------------
# Score-set metadata normalisation
# ---------------------------------------------------------------------------

def _normalise_score_set_meta(ss: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a score set record from the POST /score-sets/search response
    (camelCase) into a stable snake_case metadata dict consumed by
    fetch_mavedb_scores and render_report.py.
    """
    # Publication DOIs — field name differs across API versions
    dois = [
        p.get("identifier") or p.get("doi") or ""
        for p in (
            ss.get("primaryPublicationIdentifiers")
            or ss.get("primary_publication_identifiers")
            or []
        )
        if p.get("identifier") or p.get("doi")
    ]

    return {
        "urn":             ss.get("urn", ""),
        "title":           ss.get("title"),
        "num_variants":    ss.get("numVariants") or ss.get("num_variants"),
        # Actual score column names are only available via the variants/data
        # endpoint; default to ['score'] — overridden after variant fetch.
        "dataset_columns": {"score_columns": ["score"]},
        "publication_dois": dois,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_mavedb_scores(
    query: str,
    gene: Optional[str] = None,
    hgvs_terms: Optional[List[str]] = None,
    max_score_sets: int = 3,
) -> str:
    """
    Retrieve MaveDB functional scores for a variant.

    Parameters
    ----------
    query          : plain-text variant description (used for gene inference
                     when `gene` is not supplied)
    gene           : gene symbol, e.g. 'BRCA1'
    hgvs_terms     : HGVS strings to match against variant rows,
                     e.g. ['p.Val1736Ala', 'p.V1736A']
    max_score_sets : maximum number of score sets to query

    Returns
    -------
    JSON string with keys:
      gene, query, results  — on success
      note                  — when no score sets exist for the gene
      error                 — on unrecoverable failure
    """
    # Resolve gene symbol
    gene_sym: str = gene or ""
    if not gene_sym:
        m = re.match(r"^([A-Z][A-Z0-9]+)\s", query or "")
        gene_sym = m.group(1) if m else ""

    if not gene_sym:
        return json.dumps({"error": "No gene symbol could be determined.", "query": query})

    # Search for score sets
    try:
        score_sets = _search_score_sets(gene_sym, retmax=max_score_sets)
    except Exception as exc:
        return json.dumps({"error": str(exc), "query": query, "gene": gene_sym})

    if not score_sets:
        return json.dumps({
            "note": f"No MaveDB score sets found for gene {gene_sym}.",
            "query": query,
            "gene": gene_sym,
        })

    search_terms = hgvs_terms or []
    results: List[Dict[str, Any]] = []

    for ss in score_sets:
        meta = _normalise_score_set_meta(ss)
        urn  = meta["urn"]
        matched_variants: List[Dict[str, Any]] = []

        if search_terms and urn:
            try:
                raw_variants = _get_variant_scores(urn)
                matched_variants = _match_variant(raw_variants, search_terms)

                # If we got variant rows, extract actual score column names
                # from the first row's keys (minus the standard HGVS fields).
                if raw_variants:
                    skip = {"hgvsPro", "hgvsNt", "hgvs_pro", "hgvs_nt", "urn"}
                    score_cols = [
                        k for k in raw_variants[0]
                        if k not in skip and isinstance(raw_variants[0][k], (int, float, type(None)))
                    ] or ["score"]
                    meta["dataset_columns"] = {"score_columns": score_cols}

            except Exception as exc:
                meta["fetch_error"] = str(exc)

        results.append({"score_set": meta, "matched_variants": matched_variants})

    return json.dumps({"gene": gene_sym, "query": query, "results": results}, indent=2)