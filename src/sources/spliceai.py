# src/sources/spliceai.py
from __future__ import annotations
import json, re, time
from typing import Any, Dict, Optional
import requests

# Broad Institute hosted SpliceAI-lookup API [web:8]
_API_BASES = {
    "GRCh37": "https://spliceai-37-xwkwwwxdwq-uc.a.run.app",
    "GRCh38": "https://spliceai-38-xwkwwwxdwq-uc.a.run.app",
}
REQUEST_DELAY = 0.5   # conservative — API is rate-limited to a few req/min

# ACMG splice evidence thresholds (Jaganathan et al., Cell 2019)
THRESHOLD_STRONG   = 0.5   # PS3/BS3 level
THRESHOLD_MODERATE = 0.2   # PM4-equivalent

def _parse_variant_for_spliceai(
    variant: str,
    chrom: Optional[str],
    pos: Optional[int],
    ref: Optional[str],
    alt: Optional[str],
) -> Optional[str]:
    """
    Build a 'chrom-pos-ref-alt' string.
    Accepts explicit args first, then tries to parse from variant string
    (e.g. 'chr17:43094692 C>T' or 'BRCA1 c.5266dupC').
    Returns None if a VCF-style representation cannot be determined.
    """
    if chrom and pos and ref and alt:
        c = str(chrom).lstrip("chr")
        return f"chr{c}-{pos}-{ref}-{alt}"

    # Try to extract from string like "chr17-43094692-C-T" or "17:43094692 C>T"
    m = re.search(
        r"(?:chr)?(\d+|X|Y|MT)[:\-](\d+)[:\- ]+([ACGTacgt]+)[>\-/]([ACGTacgt]+)",
        variant,
    )
    if m:
        c = m.group(1)
        return f"chr{c}-{m.group(2)}-{m.group(3).upper()}-{m.group(4).upper()}"

    return None

def _interpret_delta(scores: Dict[str, Any]) -> Dict[str, Any]:
    """Map raw delta scores to ACMG-relevant evidence labels."""
    delta_max = max(
        scores.get("DS_AG", 0),
        scores.get("DS_AL", 0),
        scores.get("DS_DG", 0),
        scores.get("DS_DL", 0),
    )
    if delta_max >= THRESHOLD_STRONG:
        tier = "strong_splice_effect"
        acmg_hint = "Consider PS3 (functional evidence of splice disruption)"
    elif delta_max >= THRESHOLD_MODERATE:
        tier = "moderate_splice_effect"
        acmg_hint = "Consider PP3 or BP4 depending on direction"
    else:
        tier = "minimal_splice_effect"
        acmg_hint = "Low predicted splice impact (BP4 supporting benign)"

    return {"delta_max": round(delta_max, 4), "tier": tier, "acmg_hint": acmg_hint}

def fetch_spliceai_scores(
    variant: str,
    assembly: str = "GRCh38",
    chrom: Optional[str] = None,
    pos: Optional[int] = None,
    ref: Optional[str] = None,
    alt: Optional[str] = None,
    distance: int = 50,
    mask: int = 1,          # 1 = masked, recommended for variant interpretation
) -> str:
    """
    Retrieve SpliceAI delta scores from the Broad Institute public API.

    Parameters
    ----------
    variant  : variant description string (fallback for coordinate parsing)
    assembly : 'GRCh38' or 'GRCh37'
    chrom, pos, ref, alt : explicit VCF-style coordinates (preferred)
    distance : max distance to splice site to consider (default 50)
    mask     : 1 = masked scores (recommended for ACMG), 0 = raw

    Returns JSON string with delta scores and ACMG interpretation hints.
    Only SNVs and simple indels are supported by the model.
    """
    vcf_str = _parse_variant_for_spliceai(variant, chrom, pos, ref, alt)
    if not vcf_str:
        return json.dumps({
            "note": "SpliceAI requires a SNV/indel in VCF format (chrom-pos-ref-alt). "
                    "Could not parse coordinates from the input. "
                    "Skipping SpliceAI — this is expected for SVs and intronic-only variants.",
            "variant": variant,
        })

    base = _API_BASES.get(assembly, _API_BASES["GRCh38"])
    hg = "37" if assembly == "GRCh37" else "38"
    url = f"{base}/spliceai/"
    params = {"variant": vcf_str, "hg": hg, "distance": distance, "mask": mask}

    time.sleep(REQUEST_DELAY)
    try:
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except requests.HTTPError as exc:
        return json.dumps({"error": f"SpliceAI API HTTP error: {exc}", "variant": vcf_str})
    except Exception as exc:
        return json.dumps({"error": str(exc), "variant": vcf_str})

    # Flatten the result — the API nests predictions under 'scores'
    scores_raw = data.get("scores", [{}])[0] if isinstance(data.get("scores"), list) else data
    interpretation = _interpret_delta(scores_raw)

    return json.dumps({
        "variant": vcf_str,
        "assembly": assembly,
        "mask": mask,
        "raw_scores": scores_raw,
        "interpretation": interpretation,
    }, indent=2)