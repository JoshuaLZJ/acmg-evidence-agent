# src/acmg/rules.py
"""
ACMG/ClinGen variant classification rules.

Two frameworks are implemented and auto-selected based on variant type:

  1. Sequence variant framework (Richards et al. 2015, Genet Med 17:405-424)
     — for SNVs, small indels, and any non-CNV variant
     — code-based: triggered codes are counted by tier and combined

  2. CNV scoring framework (Riggs et al. 2020, Genet Med 22:245-257)
     — for copy number losses (deletions) and gains (duplications/triplications)
     — point-based: evidence scores are summed to a float, then classified
     — evidence items carry a 'cnv_category' field (e.g. "2A", "4A_confirmed_dn")
       and a 'points' field with the numeric score contribution

Classification thresholds
  CNV:  >= 0.99  Pathogenic
        0.90-0.98 Likely Pathogenic
        -0.89-0.89 VUS
        -0.90 to -0.98 Likely Benign
        <= -0.99 Benign

  SNV:  Richards 2015 combining rules (see _classify_sequence_variant)
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..models import ACMGAssessment, EvidenceItem


# ---------------------------------------------------------------------------
# CNV point table (Riggs et al. 2020, Tables 1 & 2)
# Suggested/default point values; actual value is taken from EvidenceItem.points
# if provided, otherwise the default below is used.
# ---------------------------------------------------------------------------

# Maps cnv_category label -> (default_points, max_points, description)
_CNV_LOSS_TABLE: dict[str, tuple[float, float, str]] = {
    # Section 1
    "1A": (0.00,  0.00, "Contains protein-coding/functionally important elements"),
    "1B": (-0.60,-0.60, "No protein-coding or functionally important elements"),
    # Section 2
    "2A": (1.00,  1.00, "Complete overlap with established HI gene/region"),
    "2B": (0.00,  0.00, "Partial overlap — critical region unclear or not affected"),
    "2C1":(0.90,  1.00, "Partial 5' overlap with HI gene, coding sequence involved"),
    "2C2":(0.00,  0.45, "Partial 5' overlap with HI gene, only 5' UTR involved"),
    "2D1":(0.00,  0.00, "Partial 3' overlap, only 3' UTR involved"),
    "2D2":(0.90,  0.90, "Partial 3' overlap, last exon only — other P variants exist"),
    "2D3":(0.30,  0.45, "Partial 3' overlap, last exon only — no other P variants"),
    "2D4":(0.90,  1.00, "Partial 3' overlap, includes other exons — NMD expected"),
    "2E_pvs1":    (0.90,  0.90, "Intragenic: PVS1"),
    "2E_pvs1_str":(0.45,  0.90, "Intragenic: PVS1_Strong"),
    "2E_pvs1_mod":(0.30,  0.45, "Intragenic: PVS1_Moderate / PM4"),
    "2E_pvs1_sup":(0.15,  0.30, "Intragenic: PVS1_Supporting"),
    "2F": (-1.00, -1.00, "Completely within established benign CNV region"),
    "2G": (0.00,  0.00, "Overlaps benign CNV but includes additional material"),
    "2H": (0.15,  0.15, "≥2 HI predictors suggest ≥1 gene is HI"),
    # Section 3
    "3A": (0.00,  0.00, "0-24 protein-coding genes"),
    "3B": (0.45,  0.45, "25-34 protein-coding genes"),
    "3C": (0.90,  0.90, "35+ protein-coding genes"),
    # Section 4
    "4A_confirmed_dn": (0.45, 0.90, "Proband: specific/unique phenotype, confirmed de novo"),
    "4A_assumed_dn":   (0.30, 0.90, "Proband: specific/unique phenotype, assumed de novo"),
    "4B_confirmed_dn": (0.30, 0.90, "Proband: specific phenotype, confirmed de novo"),
    "4B_assumed_dn":   (0.15, 0.90, "Proband: specific phenotype, assumed de novo"),
    "4C_confirmed_dn": (0.15, 0.90, "Proband: non-specific phenotype, confirmed de novo"),
    "4C_assumed_dn":   (0.10, 0.90, "Proband: non-specific phenotype, assumed de novo"),
    "4D":  (0.00, -0.30, "Proband: inconsistent phenotype"),
    "4E":  (0.10,  0.30, "Proband: specific phenotype, inheritance unknown"),
    "4F":  (0.15,  0.45, "3-4 segregations"),
    "4G":  (0.30,  0.45, "5-6 segregations"),
    "4H":  (0.45,  0.45, "7+ segregations"),
    "4I":  (-0.45,-0.90, "Non-segregation: affected relative doesn't have variant"),
    "4J":  (-0.30,-0.90, "Non-segregation: unaffected relative has variant (specific phenotype)"),
    "4K":  (-0.15,-0.30, "Non-segregation: unaffected relative has variant (non-specific)"),
    "4L":  (0.45,  0.45, "Case-control: significant increase, specific phenotype"),
    "4M":  (0.30,  0.45, "Case-control: significant increase, non-specific phenotype"),
    "4N":  (-0.90,-0.90, "Case-control: no significant difference"),
    "4O":  (-1.00,-1.00, "Overlap with common population variation"),
    # Section 5
    "5A":  (0.00,  0.45, "De novo (use Section 4 de novo scoring)"),
    "5B":  (-0.30,-0.45, "Inherited from unaffected parent — specific phenotype"),
    "5C":  (-0.15,-0.30, "Inherited from unaffected parent — non-specific phenotype"),
    "5D":  (0.00,  0.45, "Segregates with phenotype in family (use Section 4 4F-4H)"),
    "5E":  (0.00, -0.45, "Non-segregation (use Section 4 4I-4K)"),
    "5F":  (0.00,  0.00, "Inheritance unknown/uninformative"),
    "5G":  (0.10,  0.15, "Inheritance unknown; non-specific phenotype consistent"),
    "5H":  (0.30,  0.30, "Inheritance unknown; highly specific phenotype consistent"),
}

# Gain table differs in sections 2 and 3; sections 4 and 5 are shared with loss.
_CNV_GAIN_TABLE: dict[str, tuple[float, float, str]] = {
    "1A": (0.00,  0.00, "Contains protein-coding/functionally important elements"),
    "1B": (-0.60,-0.60, "No protein-coding or functionally important elements"),
    "2A": (1.00,  1.00, "Complete overlap with established TS gene/region"),
    "2B": (0.00,  0.00, "Partial overlap with TS region — critical region unclear"),
    "2C": (-1.00,-1.00, "Identical gene content to established benign gain"),
    "2D": (-1.00,-1.00, "Smaller than benign gain, breakpoint doesn't interrupt genes"),
    "2E": (0.00,  0.00, "Smaller than benign gain, breakpoint interrupts gene"),
    "2F": (-1.00,-1.00, "Larger than benign gain, no additional protein-coding genes"),
    "2G": (0.00,  0.00, "Overlaps benign gain but includes additional material"),
    "2H": (0.00,  0.00, "HI gene fully within gain (continue evaluation)"),
    "2I_pvs1":    (0.90,  0.90, "Both breakpoints within same HI gene — PVS1"),
    "2I_pvs1_str":(0.45,  0.90, "Both breakpoints within same HI gene — PVS1_Strong"),
    "2J": (0.00,  0.00, "One breakpoint in HI gene — phenotype inconsistent or unknown"),
    "2K": (0.45,  0.45, "One breakpoint in HI gene — phenotype specific and consistent"),
    "2L": (0.00,  0.00, "Breakpoint in gene of no established clinical significance"),
    "3A": (0.00,  0.00, "0-34 protein-coding genes"),
    "3B": (0.45,  0.45, "35-49 protein-coding genes"),
    "3C": (0.90,  0.90, "50+ protein-coding genes"),
    # Sections 4 and 5 are identical to loss table
    **{k: v for k, v in _CNV_LOSS_TABLE.items() if k[0] in ("4", "5")},
}


# ---------------------------------------------------------------------------
# CNV classification thresholds (Riggs 2020, Tables 1 & 2)
# ---------------------------------------------------------------------------

def _classify_cnv_score(score: float) -> str:
    if score >= 0.99:
        return "Pathogenic"
    if score >= 0.90:
        return "Likely Pathogenic"
    if score >= -0.89:
        return "Variant of Uncertain Significance"
    if score >= -0.98:
        return "Likely Benign"
    return "Benign"


# ---------------------------------------------------------------------------
# Sequence variant combining rules (Richards et al. 2015, Table 3)
# ---------------------------------------------------------------------------

# Each rule is (pvs_min, ps_min, pm_min, pp_min)
_PATH_RULES = [
    (1, 1, 0, 0),   # PVS1 + ≥1 PS
    (1, 0, 2, 0),   # PVS1 + ≥2 PM
    (1, 0, 1, 2),   # PVS1 + 1 PM + ≥2 PP  (Richards: "≥2 PP" note: original says ≥2)
    (1, 0, 0, 2),   # PVS1 + ≥2 PP
    (0, 2, 0, 0),   # ≥2 PS
    (0, 1, 3, 0),   # 1 PS + ≥3 PM
    (0, 1, 2, 2),   # 1 PS + 2 PM + ≥2 PP
    (0, 1, 1, 4),   # 1 PS + 1 PM + ≥4 PP
    (0, 1, 0, 4),   # 1 PS + ≥4 PP  (common extension)
]

_LIKELY_PATH_RULES = [
    (1, 0, 1, 0),   # PVS1 + 1 PM
    (0, 1, 1, 0),   # 1 PS + 1 PM
    (0, 1, 0, 2),   # 1 PS + ≥2 PP
    (0, 0, 3, 0),   # ≥3 PM
    (0, 0, 2, 2),   # 2 PM + ≥2 PP
    (0, 0, 1, 4),   # 1 PM + ≥4 PP
]


def _classify_sequence_variant(
    pvs: int, ps: int, pm: int, pp: int,
    ba: int, bs: int, bp: int,
) -> str:
    # Benign — BA1 alone is sufficient
    if ba >= 1:
        return "Benign"
    if bs >= 2:
        return "Benign"
    # Likely Benign
    if bs >= 1 and bp >= 1:
        return "Likely Benign"
    if bp >= 2:
        return "Likely Benign"
    # Pathogenic
    for (v, s, m, p) in _PATH_RULES:
        if pvs >= v and ps >= s and pm >= m and pp >= p:
            return "Pathogenic"
    # Likely Pathogenic
    for (v, s, m, p) in _LIKELY_PATH_RULES:
        if pvs >= v and ps >= s and pm >= m and pp >= p:
            return "Likely Pathogenic"
    return "Variant of Uncertain Significance"


# ---------------------------------------------------------------------------
# Variant type detection
# ---------------------------------------------------------------------------

_CNV_KEYWORDS = re.compile(
    r"\b(del|deletion|dup|duplication|tandem|triplication|cnv|loss|gain|copy.?number)\b",
    re.IGNORECASE,
)


def _is_cnv(items: List[EvidenceItem]) -> bool:
    """
    Returns True if any evidence item signals this is a CNV.
    Detection uses:
      1. Any item with a cnv_category field set
      2. Any item whose variant_type field contains CNV keywords
      3. Any item whose statement contains CNV keywords (fallback)
    """
    for item in items:
        if getattr(item, "cnv_category", None):
            return True
        vtype = getattr(item, "variant_type", "") or ""
        if _CNV_KEYWORDS.search(vtype):
            return True
    # Fallback: scan statements
    for item in items:
        if _CNV_KEYWORDS.search(item.statement or ""):
            return True
    return False


def _cnv_type(items: List[EvidenceItem]) -> str:
    """Returns 'loss' or 'gain' based on evidence items. Defaults to 'loss'."""
    gain_kw = re.compile(r"\b(dup|duplication|gain|tandem|triplication)\b", re.IGNORECASE)
    loss_kw = re.compile(r"\b(del|deletion|loss)\b", re.IGNORECASE)
    gain_score, loss_score = 0, 0
    for item in items:
        text = " ".join(filter(None, [
            getattr(item, "variant_type", ""),
            item.statement or "",
            getattr(item, "cnv_category", "") or "",
        ]))
        if gain_kw.search(text):
            gain_score += 1
        if loss_kw.search(text):
            loss_score += 1
    return "gain" if gain_score > loss_score else "loss"


# ---------------------------------------------------------------------------
# MaveDB / SpliceAI functional evidence → sequence variant code injection
# ---------------------------------------------------------------------------

def _apply_functional_evidence(items: List[EvidenceItem]) -> List[EvidenceItem]:
    """
    Synthesise ACMG sequence variant codes from MaveDB and SpliceAI evidence
    items that carry structured functional data but no explicit ACMG code yet.

    Rules (conservative, per SYSTEM_PROMPT thresholds):
      MaveDB score <= -1.0  → PS3 (strong functional evidence of pathogenicity)
      MaveDB score >= 0.5   → BS3 (strong functional evidence of benign effect)
      SpliceAI delta_max >= 0.5 → PS3 (strong splice disruption)
      SpliceAI delta_max 0.2-0.49 → PP3 (moderate computational evidence)
      SpliceAI delta_max < 0.2  → BP4 (computational evidence against effect)
    """
    augmented = []
    for item in items:
        source = (getattr(item, "source", "") or "").lower()
        codes = list(item.candidate_acmg_codes or [])

        if source == "mavedb":
            score = getattr(item, "functional_score", None)
            if score is not None:
                try:
                    score = float(score)
                    if score <= -1.0 and "PS3" not in codes:
                        codes.append("PS3")
                    elif score >= 0.5 and "BS3" not in codes:
                        codes.append("BS3")
                except (TypeError, ValueError):
                    pass

        elif source == "spliceai":
            delta = getattr(item, "delta_max", None)
            if delta is not None:
                try:
                    delta = float(delta)
                    if delta >= 0.5 and "PS3" not in codes:
                        codes.append("PS3")
                    elif delta >= 0.2 and "PP3" not in codes:
                        codes.append("PP3")
                    elif delta < 0.2 and "BP4" not in codes:
                        codes.append("BP4")
                except (TypeError, ValueError):
                    pass

        # Return a copy with updated codes
        augmented.append(item.model_copy(update={"candidate_acmg_codes": codes}))
    return augmented


# ---------------------------------------------------------------------------
# CNV scoring engine
# ---------------------------------------------------------------------------

def _score_cnv(items: List[EvidenceItem], cnv_type: str) -> tuple[float, list[str], list[str]]:
    """
    Sum CNV evidence points per Riggs 2020.

    Returns (total_score, applied_categories, rationale_lines).
    """
    table = _CNV_GAIN_TABLE if cnv_type == "gain" else _CNV_LOSS_TABLE
    total = 0.0
    applied: list[str] = []
    rationale: list[str] = []

    for item in items:
        cat = getattr(item, "cnv_category", None)
        if not cat:
            continue

        cat_key = cat.upper()
        default_pts, _, description = table.get(cat_key, (0.0, 0.0, cat))

        # Prefer explicitly provided points, fall back to table default
        pts_raw = getattr(item, "points", None)
        try:
            pts = float(pts_raw) if pts_raw is not None else default_pts
        except (TypeError, ValueError):
            pts = default_pts

        total = round(total + pts, 4)
        applied.append(cat_key)
        rationale.append(
            "{cat} ({pts:+.2f}): {desc} — {stmt}".format(
                cat=cat_key,
                pts=pts,
                desc=description,
                stmt=(item.statement or "").strip() or "no statement provided",
            )
        )

    return total, applied, rationale


# ---------------------------------------------------------------------------
# Sequence variant scoring engine
# ---------------------------------------------------------------------------

def _score_sequence_variant(
    items: List[EvidenceItem],
) -> tuple[str, list[str], list[str], list[str]]:
    """
    Apply Richards 2015 combining rules.

    Returns (classification, triggered_codes, rationale_lines, uncertainties).
    """
    pvs = ps = pm = pp = ba = bs = bp = 0
    triggered: list[str] = []
    rationale: list[str] = []

    for item in items:
        for code in (item.candidate_acmg_codes or []):
            code = code.upper().strip()
            if code in triggered:
                continue
            triggered.append(code)
            rationale.append("{code}: {stmt}".format(
                code=code,
                stmt=(item.statement or "").strip() or "no statement provided",
            ))
            if code == "PVS1":                      pvs += 1
            elif re.match(r"^PS\d$", code):         ps  += 1
            elif re.match(r"^PM\d$", code):         pm  += 1
            elif re.match(r"^PP\d$", code):         pp  += 1
            elif code == "BA1":                      ba  += 1
            elif re.match(r"^BS\d$", code):         bs  += 1
            elif re.match(r"^BP\d$", code):         bp  += 1

    classification = _classify_sequence_variant(pvs, ps, pm, pp, ba, bs, bp)

    uncertainties: list[str] = []
    if classification == "Variant of Uncertain Significance":
        uncertainties.append(
            "Insufficient validated ACMG evidence for a stronger classification "
            "(PVS={pvs}, PS={ps}, PM={pm}, PP={pp}, BA={ba}, BS={bs}, BP={bp}).".format(
                pvs=pvs, ps=ps, pm=pm, pp=pp, ba=ba, bs=bs, bp=bp,
            )
        )
    if not triggered:
        uncertainties.append("No ACMG evidence codes were extracted from the available evidence.")

    return classification, triggered, rationale, uncertainties


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def map_acmg_rules(evidence: List[EvidenceItem]) -> ACMGAssessment:
    """
    Classify a variant using ACMG/ClinGen evidence.

    Auto-detects CNV vs sequence variant based on evidence item content:
      - CNV items should have a 'cnv_category' field (e.g. "2A", "4A_confirmed_dn")
        and optionally a 'points' field.
      - Sequence variant items should have 'candidate_acmg_codes' (e.g. ["PS3", "PM2"]).
      - MaveDB/SpliceAI items with 'source' + 'functional_score'/'delta_max' fields
        have codes synthesised automatically before combining.

    Returns an ACMGAssessment with:
      - triggered_codes / cnv_categories_applied
      - proposed_classification
      - cnv_total_score (CNV only)
      - rationale
      - uncertainties
    """
    if not evidence:
        return ACMGAssessment(
            triggered_codes=[],
            proposed_classification="Variant of Uncertain Significance",
            rationale=[],
            uncertainties=["No evidence items were provided to map_acmg_rules."],
        )

    # Augment with synthesised functional codes before classification
    evidence = _apply_functional_evidence(evidence)

    if _is_cnv(evidence):
        cnv_type = _cnv_type(evidence)
        total_score, applied_cats, rationale = _score_cnv(evidence, cnv_type)
        classification = _classify_cnv_score(total_score)

        uncertainties: list[str] = []
        if classification == "Variant of Uncertain Significance":
            uncertainties.append(
                "CNV total score {s:.3f} falls within VUS range (−0.89 to 0.89). "
                "Additional dosage sensitivity data or case evidence may resolve this.".format(
                    s=total_score
                )
            )
        if not applied_cats:
            uncertainties.append(
                "No CNV evidence categories were scored. "
                "Ensure evidence items include a 'cnv_category' field "
                "(e.g. '2A', '4A_confirmed_dn') per Riggs et al. 2020."
            )

        return ACMGAssessment(
            triggered_codes=applied_cats,
            proposed_classification=classification,
            cnv_total_score=total_score,
            cnv_type=cnv_type,
            rationale=rationale,
            uncertainties=uncertainties,
        )

    # Sequence variant path
    classification, triggered, rationale, uncertainties = _score_sequence_variant(evidence)
    return ACMGAssessment(
        triggered_codes=triggered,
        proposed_classification=classification,
        rationale=rationale,
        uncertainties=uncertainties,
    )