"""
src/sources/clinvar.py

Universal ClinVar retrieval using NCBI E-utilities (no submission API key needed).
Supports three search strategies, tried in priority order:
  1. Coordinate-based search (chr + position range) — best for SVs / duplications
  2. HGVS / variant name search — best for missense / small indels
  3. Gene + variant term search — fallback for any input

After finding candidate variation IDs, esummary fetches the full structured record
including germline classification, review status, trait, and all HGVS aliases.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

import requests

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH_URL = f"{EUTILS_BASE}/esearch.fcgi"
ESUMMARY_URL = f"{EUTILS_BASE}/esummary.fcgi"

# NCBI rate limit: 3 req/s without API key
REQUEST_DELAY = 0.4


def _get(url: str, params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    time.sleep(REQUEST_DELAY)
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# AA three→one letter conversion for HGVS normalisation
# ---------------------------------------------------------------------------

_AA3 = {
    "Ala": "A", "Arg": "R", "Asn": "N", "Asp": "D", "Cys": "C",
    "Gln": "Q", "Glu": "E", "Gly": "G", "His": "H", "Ile": "I",
    "Leu": "L", "Lys": "K", "Met": "M", "Phe": "F", "Pro": "P",
    "Ser": "S", "Thr": "T", "Trp": "W", "Tyr": "Y", "Val": "V",
    "Ter": "*", "Sec": "U",
}


def _three_to_one_hgvs(term: str) -> Optional[str]:
    converted = re.sub(r"[A-Z][a-z]{2}", lambda m: _AA3.get(m.group(0), m.group(0)), term)
    return converted if converted != term else None


# ---------------------------------------------------------------------------
# Input parser
# ---------------------------------------------------------------------------

def _parse_input(
    variant: str,
    gene: Optional[str],
    chrom: Optional[str],
    start: Optional[int],
    end: Optional[int],
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "gene": gene,
        "chrom": chrom,
        "start": start,
        "end": end,
        "hgvs_terms": [],
        "is_sv": False,
        "sv_type": None,
    }

    v_lower = variant.lower()

    # Detect SV type
    if any(k in v_lower for k in ("dup", "duplication", "tandem", "triplication")):
        result["is_sv"] = True
        result["sv_type"] = "duplication"
    elif any(k in v_lower for k in ("del", "deletion")):
        result["is_sv"] = True
        result["sv_type"] = "deletion"
    elif any(k in v_lower for k in ("inv", "inversion")):
        result["is_sv"] = True
        result["sv_type"] = "inversion"
    elif any(k in v_lower for k in ("ins", "insertion", "cnv")):
        result["is_sv"] = True
        result["sv_type"] = "insertion"

    # Extract gene if not supplied
    if not result["gene"]:
        m = re.match(r"^([A-Z][A-Z0-9]+)\s", variant)
        if m:
            result["gene"] = m.group(1)

    # Extract coordinates from variant string  chr17:43078282-43084362
    if result["chrom"] is None or result["start"] is None:
        coord_m = re.search(
            r"(?:chr)?(\d+|X|Y|MT):(\d[\d,]+)[-_](\d[\d,]+)",
            variant, re.IGNORECASE
        )
        if coord_m:
            result["chrom"] = coord_m.group(1)
            result["start"] = int(coord_m.group(2).replace(",", ""))
            result["end"] = int(coord_m.group(3).replace(",", ""))

    # Extract exon numbers
    exon_m = re.search(r"exon[s]?\s*(\d+)(?:\s*[-–]\s*(\d+))?", variant, re.IGNORECASE)
    result["exon_start"] = int(exon_m.group(1)) if exon_m else None
    result["exon_end"] = int(exon_m.group(2)) if (exon_m and exon_m.group(2)) else result["exon_start"]

    # Extract HGVS-like terms
    for pattern in [
        r"NC_\d+\.\d+:g\.\S+",
        r"NM_\d+\.\d+\([^)]+\):[cp]\.\S+",
        r"[cp]\.[A-Za-z0-9_*]+",
    ]:
        for m in re.finditer(pattern, variant):
            t = m.group(0).rstrip(".,;")
            result["hgvs_terms"].append(t)
            one = _three_to_one_hgvs(t)
            if one and one != t:
                result["hgvs_terms"].append(one)

    result["hgvs_terms"] = list(dict.fromkeys(result["hgvs_terms"]))
    return result


# ---------------------------------------------------------------------------
# esearch strategies
# ---------------------------------------------------------------------------

def _search_by_location(chrom: str, start: int, end: int, pad: int = 5, retmax: int = 20) -> List[str]:
    """
    Search ClinVar by chromosome + GRCh38 position range.
    For SV breakpoints, NCBI stores the *start* coordinate of the event,
    so we use a tight window around the start position rather than the full span.
    """
    params = {
        "db": "clinvar",
        "term": (
            f"{chrom}[Chromosome] AND "
            f"{start - pad}:{start + pad}[Base Position for Assembly38]"
        ),
        "retmode": "json",
        "retmax": retmax,
    }
    data = _get(ESEARCH_URL, params)
    return data.get("esearchresult", {}).get("idlist", [])


def _search_by_term(term: str, retmax: int = 20) -> List[str]:
    params = {"db": "clinvar", "term": term, "retmode": "json", "retmax": retmax}
    data = _get(ESEARCH_URL, params)
    return data.get("esearchresult", {}).get("idlist", [])


def _build_search_terms(parsed: Dict[str, Any], variant: str, gene: Optional[str]) -> List[str]:
    terms: List[str] = []
    g = parsed.get("gene") or gene or ""
    sv_type = parsed.get("sv_type") or ""

    # 1. Explicit HGVS (most precise)
    for h in parsed.get("hgvs_terms", []):
        if g:
            terms.append(f"{g} {h}")
        terms.append(h)

    # 2. SV-specific terms (gene + exon + sv_type)
    if parsed.get("is_sv") and g:
        if parsed.get("exon_start"):
            exon_label = f"exon {parsed['exon_start']}"
            if parsed.get("exon_end") and parsed["exon_end"] != parsed["exon_start"]:
                exon_label = f"exon {parsed['exon_start']}-{parsed['exon_end']}"
            terms.append(f"{g} tandem {sv_type} {exon_label}")
            terms.append(f"{g} {sv_type} {exon_label}")
            terms.append(f"{g} {exon_label} {sv_type}")
        terms.append(f"{g} tandem {sv_type}")
        terms.append(f"{g} {sv_type}")

    # 3. Gene + cleaned variant string
    clean = re.sub(r"(?:chr)?\d+:\d+[-_]\d+", "", variant).strip()
    clean = re.sub(r"\s+", " ", clean)
    if g and clean:
        terms.append(f"{g} {clean[:80]}")
    elif g:
        terms.append(g)

    # 4. Full string as last resort
    if clean:
        terms.append(clean[:100])

    return list(dict.fromkeys(terms))


# ---------------------------------------------------------------------------
# esummary + parsing
# ---------------------------------------------------------------------------

def _fetch_summaries(variation_ids: List[str]) -> List[Dict[str, Any]]:
    summaries = []
    for i in range(0, len(variation_ids), 20):
        batch = variation_ids[i : i + 20]
        params = {"db": "clinvar", "id": ",".join(batch), "retmode": "json"}
        data = _get(ESUMMARY_URL, params)
        result = data.get("result", {})
        for uid in result.get("uids", []):
            summaries.append(result[uid])
    return summaries


def _parse_summary(raw: Dict[str, Any]) -> Dict[str, Any]:
    germline = raw.get("germline_classification", {})
    variation_set = raw.get("variation_set", [])
    first_var = variation_set[0] if variation_set else {}
    locs = first_var.get("variation_loc", [])
    grch38 = next((l for l in locs if l.get("assembly_name") == "GRCh38"), {})
    grch37 = next((l for l in locs if l.get("assembly_name") == "GRCh37"), {})
    traits = germline.get("trait_set", [])

    return {
        "variation_id": raw.get("uid"),
        "accession": raw.get("accession_version"),
        "title": raw.get("title"),
        "obj_type": raw.get("obj_type"),
        "germline_classification": germline.get("description"),
        "review_status": germline.get("review_status"),
        "last_evaluated": germline.get("last_evaluated"),
        "fda_recognized": germline.get("fda_recognized_database") == "true",
        "trait_names": [t.get("trait_name", "") for t in traits],
        "hgvs_name": first_var.get("variation_name"),
        "variant_type": first_var.get("variant_type"),
        "grch38_chr": grch38.get("chr"),
        "grch38_start": grch38.get("start"),
        "grch38_stop": grch38.get("stop"),
        "grch37_start": grch37.get("start"),
        "grch37_stop": grch37.get("stop"),
        "supporting_scvs": raw.get("supporting_submissions", {}).get("scv", []),
        "supporting_rcvs": raw.get("supporting_submissions", {}).get("rcv", []),
    }


# ---------------------------------------------------------------------------
# Scoring / ranking
# ---------------------------------------------------------------------------

def _score(summary: Dict[str, Any], parsed: Dict[str, Any], gene: Optional[str]) -> int:
    score = 0
    title = (summary.get("title") or "").lower()
    hgvs = (summary.get("hgvs_name") or "").lower()
    obj_type = (summary.get("obj_type") or "").lower()
    classification = (summary.get("germline_classification") or "").lower()
    review = (summary.get("review_status") or "").lower()
    is_sv = parsed.get("is_sv", False)
    sv_type = parsed.get("sv_type") or ""
    g = ((gene or parsed.get("gene")) or "").lower()

    # Gene match in title
    if g and g in title:
        score += 20

    # Review tier
    if "expert panel" in review:
        score += 20
    elif "reviewed by" in review:
        score += 12
    elif "criteria provided, multiple" in review:
        score += 8
    elif "criteria provided" in review:
        score += 5

    # Classification present and not VUS
    if classification in ("pathogenic", "likely pathogenic"):
        score += 4
    elif classification in ("benign", "likely benign"):
        score += 2

    # SV type match
    if is_sv and sv_type:
        if sv_type[:3] in obj_type or sv_type[:3] in hgvs:
            score += 15
        if "tandem" in title or "tandem" in hgvs:
            score += 5

    # Coordinate proximity (GRCh38 start)
    if parsed.get("start") and summary.get("grch38_start"):
        try:
            dist = abs(int(summary["grch38_start"]) - int(parsed["start"]))
            if dist <= 5:
                score += 30
            elif dist < 200:
                score += 15
            elif dist < 2000:
                score += 5
        except (TypeError, ValueError):
            pass

    # HGVS term overlap
    for h in parsed.get("hgvs_terms", []):
        if h.lower() in title or h.lower() in hgvs:
            score += 12

    # Exon match
    if parsed.get("exon_start"):
        exon_str = f"exon {parsed['exon_start']}"
        if exon_str in title:
            score += 8

    return score


# ---------------------------------------------------------------------------
# Public function — drop-in replacement for tool_fetch_clinvar_summary
# ---------------------------------------------------------------------------

def fetch_clinvar_summary(
    query: str,
    gene: Optional[str] = None,
    chrom: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    max_results: int = 5,
    debug: bool = False,
) -> str:
    """
    Universal ClinVar evidence retrieval.

    Parameters
    ----------
    query    : variant string — any of:
               "BRCA1 chr17:43078282-43084362 tandem duplication exon 12"
               "BRCA1 p.Val1736Ala"
               "TP53 c.817C>T"
               "NM_007294.4(BRCA1):c.4186-1787_4358-1668dup"
    gene     : optional gene symbol (overrides auto-detection)
    chrom    : optional chromosome number (overrides coordinate parsing)
    start    : optional GRCh38 start coordinate
    end      : optional GRCh38 end coordinate
    max_results : max ranked ClinVar records to return
    debug    : print intermediate search steps

    Returns
    -------
    JSON string with ranked ClinVar records.
    """
    parsed = _parse_input(query, gene, chrom, start, end)
    all_ids: List[str] = []

    if debug:
        print("Parsed:", json.dumps(parsed, indent=2, default=str))

    # Strategy 1: tight location search around SV start breakpoint
    if parsed.get("chrom") and parsed.get("start"):
        ids = _search_by_location(
            str(parsed["chrom"]),
            int(parsed["start"]),
            int(parsed.get("end") or parsed["start"]),
        )
        if debug:
            print(f"Location search → {ids}")
        all_ids.extend(ids)

    # Strategy 2 & 3: term searches
    terms = _build_search_terms(parsed, query, gene)
    if debug:
        print("Terms:", terms)

    for term in terms[:8]:
        ids = _search_by_term(term)
        if debug:
            print(f"  '{term}' → {ids[:5]}")
        all_ids.extend(ids)

    # Deduplicate
    seen: set = set()
    unique_ids: List[str] = []
    for uid in all_ids:
        if uid not in seen:
            seen.add(uid)
            unique_ids.append(uid)

    unique_ids = unique_ids[:50]

    if not unique_ids:
        return json.dumps({
            "query": query,
            "note": "No ClinVar records found.",
            "searches_attempted": terms,
        })

    summaries_raw = _fetch_summaries(unique_ids)
    summaries = [_parse_summary(r) for r in summaries_raw]
    summaries.sort(
        key=lambda s: _score(s, parsed, gene or parsed.get("gene")),
        reverse=True,
    )

    top = summaries[:max_results]

    return json.dumps({
        "query": query,
        "parsed": {
            "gene": parsed.get("gene"),
            "chrom": parsed.get("chrom"),
            "start": parsed.get("start"),
            "end": parsed.get("end"),
            "is_sv": parsed.get("is_sv"),
            "sv_type": parsed.get("sv_type"),
            "hgvs_terms": parsed.get("hgvs_terms"),
        },
        "top_results": top,
        "total_candidates_evaluated": len(summaries),
    }, indent=2)
