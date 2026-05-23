"""
src/reporting/render_report.py

Renders the ACMG evidence report as a self-contained HTML file.

New features (MaveDB + SpliceAI):
  - MaveDB functional score card: score set metadata, matched variant rows,
    functional tier badge (pathogenic / benign / intermediate / no data)
  - SpliceAI delta-score gauge card: DS_AG, DS_AL, DS_DG, DS_DL bars
    with ACMG tier annotation (strong splice / moderate / minimal)
  - Both cards are cache-aware: reads .cache/responses.json
  - Both are injected before the Draft ACMG Assessment card
  - Existing IGV track, ClinVar pop-out, and reference links unchanged
  - CLI unchanged
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { -webkit-font-smoothing: antialiased; scroll-behavior: smooth; font-size: 16px; }
body {
    font-family: 'Inter', 'Helvetica Neue', sans-serif;
    background: #f7f6f2;
    color: #28251d;
    min-height: 100vh;
    padding: 2rem 1rem;
}
.page { max-width: 900px; margin: 0 auto; }

/* Header */
header { border-bottom: 1px solid #dcd9d5; padding-bottom: 1.25rem; margin-bottom: 2rem; }
header .badge {
    display: inline-block; font-size: 0.75rem; font-weight: 600;
    letter-spacing: 0.08em; text-transform: uppercase; color: #01696f;
    background: #cedcd8; padding: 0.2rem 0.65rem; border-radius: 9999px;
    margin-bottom: 0.75rem;
}
header h1 { font-size: clamp(1.4rem, 2.5vw, 2rem); font-weight: 700; line-height: 1.25; color: #28251d; margin-bottom: 0.4rem; }
header .meta { font-size: 0.82rem; color: #7a7974; }

/* Cards */
.card {
    background: #fff; border: 1px solid #dcd9d5; border-radius: 0.75rem;
    padding: 1.5rem 1.75rem; margin-bottom: 1.25rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}
.card h2 {
    font-size: 1rem; font-weight: 700; color: #01696f;
    text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 1rem;
    display: flex; align-items: center; gap: 0.5rem;
}
.card h2 .icon { width: 1.1rem; height: 1.1rem; opacity: 0.8; }
.card h3 { font-size: 0.95rem; font-weight: 600; color: #28251d; margin: 1rem 0 0.4rem; }
.card p, .card li { font-size: 0.92rem; line-height: 1.65; color: #3d3a32; margin-bottom: 0.5rem; }
.card ul, .card ol { padding-left: 1.25rem; margin-bottom: 0.5rem; }
.card code {
    font-family: 'JetBrains Mono', 'Fira Mono', monospace; font-size: 0.82rem;
    background: #f3f0ec; padding: 0.15rem 0.4rem; border-radius: 0.3rem; color: #964219;
}
.card pre {
    background: #1c1b19; color: #cdccca; border-radius: 0.5rem;
    padding: 1rem 1.25rem; overflow-x: auto;
    font-size: 0.82rem; font-family: 'JetBrains Mono','Fira Mono',monospace;
    line-height: 1.6; margin: 0.75rem 0;
}
.card pre code { background: none; color: inherit; padding: 0; }
.card blockquote {
    border-left: 3px solid #cedcd8; padding-left: 1rem; margin: 0.75rem 0;
    color: #7a7974; font-style: italic; font-size: 0.9rem;
}

/* ACMG pills */
.acmg-row { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.75rem 0; }
.acmg-code {
    display: inline-block; font-size: 0.78rem; font-weight: 700;
    letter-spacing: 0.05em; padding: 0.25rem 0.7rem; border-radius: 9999px;
    background: #cedcd8; color: #0c4e54;
}
.classification-band {
    display: inline-block; font-size: 0.85rem; font-weight: 600;
    padding: 0.35rem 1rem; border-radius: 9999px; margin-top: 0.5rem;
}
.band-pathogenic        { background: #fde8e8; color: #a12c7b; }
.band-likely-pathogenic { background: #ffe9d7; color: #964219; }
.band-vus               { background: #f3f0ec; color: #7a7974; }
.band-likely-benign     { background: #e6f0ef; color: #01696f; }
.band-benign            { background: #d0e8d8; color: #437a22; }

/* Uncertainty list */
.uncertainty-list li {
    font-size: 0.88rem; color: #964219; background: #fdf6f0;
    border: 1px solid #e7d7c4; border-radius: 0.4rem;
    padding: 0.5rem 0.75rem; margin-bottom: 0.4rem; list-style: none;
}

/* Reference links */
.ref-link {
    display: inline-flex; align-items: center; gap: 0.3rem;
    font-size: 0.82rem; color: #01696f; text-decoration: none;
    border: 1px solid #cedcd8; border-radius: 0.35rem;
    padding: 0.2rem 0.55rem; margin: 0.15rem;
    transition: background 150ms ease, border-color 150ms ease;
}
.ref-link:hover { background: #e6f0ef; border-color: #01696f; }
.ref-link svg { width: 0.75rem; height: 0.75rem; }
.ref-block { display: flex; flex-wrap: wrap; gap: 0.25rem; margin-top: 0.75rem; }

/* IGV track card */
#igv-card { padding: 1rem 1.25rem; }
#igv-card h2 { margin-bottom: 0.75rem; }
#igv-track { width: 100%; min-height: 280px; border-radius: 0.5rem; overflow: hidden; }

/* ClinVar pop-out */
.clinvar-popup-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.35); z-index: 1000;
    align-items: center; justify-content: center;
}
.clinvar-popup-overlay.open { display: flex; }
.clinvar-popup {
    background: #fff; border-radius: 0.75rem; padding: 1.5rem 1.75rem;
    max-width: 520px; width: 90%; box-shadow: 0 12px 40px rgba(0,0,0,0.18);
    position: relative; max-height: 80vh; overflow-y: auto;
}
.clinvar-popup h3 { font-size: 1rem; font-weight: 700; color: #28251d; margin-bottom: 0.75rem; }
.clinvar-popup table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.clinvar-popup td { padding: 0.35rem 0.5rem; border-bottom: 1px solid #f0ede9; vertical-align: top; }
.clinvar-popup td:first-child { color: #7a7974; width: 40%; font-weight: 500; }
.clinvar-popup .close-btn {
    position: absolute; top: 1rem; right: 1rem;
    background: none; border: none; cursor: pointer;
    color: #7a7974; font-size: 1.25rem; line-height: 1;
}
.clinvar-popup .clinvar-link {
    display: inline-block; margin-top: 0.75rem; font-size: 0.85rem;
    color: #01696f; text-decoration: none; border-bottom: 1px solid #cedcd8;
}
.clinvar-popup .clinvar-link:hover { border-color: #01696f; }

/* ------------------------------------------------------------------ */
/* MaveDB card                                                          */
/* ------------------------------------------------------------------ */
.mavedb-score-set {
    border: 1px solid #dcd9d5; border-radius: 0.55rem;
    padding: 1rem 1.25rem; margin-bottom: 0.85rem;
    background: #fafaf8;
}
.mavedb-score-set-title {
    font-size: 0.9rem; font-weight: 600; color: #28251d; margin-bottom: 0.35rem;
}
.mavedb-score-set-meta {
    font-size: 0.78rem; color: #7a7974; margin-bottom: 0.6rem;
}
.mavedb-tier {
    display: inline-block; font-size: 0.78rem; font-weight: 700;
    letter-spacing: 0.04em; padding: 0.2rem 0.65rem; border-radius: 9999px;
    margin-bottom: 0.65rem;
}
.mavedb-tier-pathogenic  { background: #fde8e8; color: #a12c7b; }
.mavedb-tier-benign      { background: #d0e8d8; color: #437a22; }
.mavedb-tier-intermediate{ background: #ffe9d7; color: #964219; }
.mavedb-tier-nodata      { background: #f3f0ec; color: #7a7974; }
.mavedb-variant-table {
    width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 0.4rem;
}
.mavedb-variant-table th {
    text-align: left; font-weight: 600; color: #7a7974; font-size: 0.75rem;
    text-transform: uppercase; letter-spacing: 0.05em;
    padding: 0.3rem 0.5rem; border-bottom: 1px solid #dcd9d5;
}
.mavedb-variant-table td {
    padding: 0.3rem 0.5rem; border-bottom: 1px solid #f0ede9;
    font-family: 'JetBrains Mono', 'Fira Mono', monospace;
    color: #3d3a32;
}
.mavedb-no-match {
    font-size: 0.82rem; color: #7a7974; font-style: italic; padding: 0.25rem 0;
}
.mavedb-doi-link {
    font-size: 0.78rem; color: #01696f; text-decoration: none;
    border-bottom: 1px solid #cedcd8;
}
.mavedb-doi-link:hover { border-color: #01696f; }

/* ------------------------------------------------------------------ */
/* SpliceAI card                                                        */
/* ------------------------------------------------------------------ */
.spliceai-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 0.75rem;
    margin: 0.75rem 0;
}
.spliceai-delta {
    background: #fafaf8; border: 1px solid #dcd9d5; border-radius: 0.55rem;
    padding: 0.75rem 1rem;
}
.spliceai-delta-label {
    font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.07em; color: #7a7974; margin-bottom: 0.35rem;
}
.spliceai-delta-value {
    font-size: 1.35rem; font-weight: 700;
    font-family: 'JetBrains Mono', 'Fira Mono', monospace;
}
.spliceai-bar-track {
    height: 5px; background: #e6e4df; border-radius: 9999px;
    margin-top: 0.4rem; overflow: hidden;
}
.spliceai-bar-fill {
    height: 100%; border-radius: 9999px;
    transition: width 0.4s ease;
}
.spliceai-tier {
    display: inline-block; font-size: 0.78rem; font-weight: 700;
    letter-spacing: 0.04em; padding: 0.2rem 0.65rem; border-radius: 9999px;
    margin: 0.65rem 0 0.4rem;
}
.spliceai-tier-strong      { background: #fde8e8; color: #a12c7b; }
.spliceai-tier-moderate    { background: #ffe9d7; color: #964219; }
.spliceai-tier-minimal     { background: #d0e8d8; color: #437a22; }
.spliceai-tier-skipped     { background: #f3f0ec; color: #7a7974; }
.spliceai-acmg-hint {
    font-size: 0.82rem; color: #3d3a32; margin-top: 0.4rem; line-height: 1.5;
}
.spliceai-variant-label {
    font-family: 'JetBrains Mono', 'Fira Mono', monospace;
    font-size: 0.8rem; color: #7a7974; margin-bottom: 0.75rem;
}

/* Footer */
footer {
    text-align: center; font-size: 0.75rem; color: #bab9b4;
    margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid #dcd9d5;
}
@media (max-width: 600px) {
    body { padding: 1rem 0.5rem; }
    .card { padding: 1.1rem 1rem; }
    .spliceai-grid { grid-template-columns: 1fr 1fr; }
}
"""


# ---------------------------------------------------------------------------
# Icons
# ---------------------------------------------------------------------------

SECTION_ICONS = {
    "variant summary":       '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/></svg>',
    "retrieved evidence":    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>',
    "functional evidence":   '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>',
    "literature":            '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    "draft acmg assessment": '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
    "uncertainties":         '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/></svg>',
}

EXTERNAL_LINK_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>'


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

def clinvar_url(accession: str) -> str:
    m = re.search(r"VCV0*(\d+)", accession or "")
    if m:
        return "https://www.ncbi.nlm.nih.gov/clinvar/variation/{0}/".format(m.group(1))
    return "https://www.ncbi.nlm.nih.gov/clinvar/"


def pubmed_url(pmid: str) -> str:
    return "https://pubmed.ncbi.nlm.nih.gov/{0}/".format(pmid.strip())


def mavedb_url(urn: str) -> str:
    return "https://www.mavedb.org/score-sets/{0}/".format(urn)


# ---------------------------------------------------------------------------
# Cache reader
# ---------------------------------------------------------------------------

def _load_cache(cache_path: Optional[str]) -> Dict[str, Any]:
    if not cache_path or not Path(cache_path).exists():
        return {}
    try:
        with open(cache_path) as f:
            return json.load(f)
    except Exception:
        return {}


def _find_cache_entry(cache: Dict[str, Any], namespace: str) -> Optional[Dict[str, Any]]:
    for entry in cache.values():
        if entry.get("namespace") == namespace:
            result_str = entry.get("result", "")
            try:
                return json.loads(result_str)
            except Exception:
                pass
    return None


def _extract_clinvar_hits(cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = _find_cache_entry(cache, "clinvar")
    if data:
        return data.get("top_results", [])
    return []


def _extract_pubmed_ids(cache: Dict[str, Any]) -> List[str]:
    data = _find_cache_entry(cache, "pubmed_search")
    if isinstance(data, list):
        return [str(p) for p in data[:5]]
    if isinstance(data, dict):
        return [str(p) for p in data.get("pmids", data.get("idlist", []))[:5]]
    return []


def _extract_mavedb_data(cache: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _find_cache_entry(cache, "mavedb")


def _extract_spliceai_data(cache: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _find_cache_entry(cache, "spliceai")


# ---------------------------------------------------------------------------
# MaveDB card builder
# ---------------------------------------------------------------------------

def _mavedb_tier(score: Optional[float], score_columns: List[str]) -> Tuple[str, str]:
    """
    Returns (tier_key, tier_label).
    Scoring heuristic: uses 'score' column if present; falls back to first
    numeric column.  Thresholds are dataset-dependent — we flag intermediate
    conservatively; callers should read the score-set methods.
    """
    if score is None:
        return "nodata", "No matched variant"
    if score <= -1.0:
        return "pathogenic", "Likely damaging (PS3 candidate)"
    if score >= 0.5:
        return "benign", "Likely functional (BS3 candidate)"
    return "intermediate", "Intermediate / uncertain"


def _build_mavedb_card(cache: Dict[str, Any]) -> str:
    data = _extract_mavedb_data(cache)

    icon = SECTION_ICONS["functional evidence"]
    header = (
        '\n<div class="card">'
        '\n  <h2>{icon} MaveDB — Functional Scores</h2>'.format(icon=icon)
    )
    footer_note = (
        '\n  <p style="font-size:0.78rem;color:#7a7974;margin-top:0.75rem;">'
        'Scores are dataset-specific. Thresholds shown are indicative only — '
        'consult score-set methods before applying ACMG PS3/BS3.</p>'
        '\n</div>\n'
    )

    if not data:
        return (
            header
            + '\n  <p class="mavedb-no-match">MaveDB data not available '
            '(tool was skipped or returned no results).</p>'
            + footer_note
        )

    results = data.get("results", [])
    if not results:
        note = data.get("note", "No MaveDB score sets found for this gene.")
        return (
            header
            + '\n  <p class="mavedb-no-match">{0}</p>'.format(html.escape(note))
            + footer_note
        )

    blocks: List[str] = []
    for entry in results:
        ss   = entry.get("score_set", {})
        urn  = ss.get("urn", "")
        title_text = ss.get("title") or urn or "Untitled score set"
        num_v = ss.get("num_variants", "?")
        dois  = [d for d in (ss.get("publication_dois") or []) if d]
        matched = entry.get("matched_variants", [])

        # Determine score column names from dataset_columns
        score_cols = list((ss.get("dataset_columns") or {}).get("score_columns", ["score"]))

        # Build tier from best matched variant score
        best_score: Optional[float] = None
        if matched:
            raw = matched[0].get("score") or matched[0].get(score_cols[0] if score_cols else "score")
            try:
                best_score = float(raw)
            except (TypeError, ValueError):
                pass

        tier_key, tier_label = _mavedb_tier(best_score, score_cols)
        tier_html = '<span class="mavedb-tier mavedb-tier-{0}">{1}</span>'.format(
            tier_key, html.escape(tier_label)
        )

        # DOI links
        doi_links = " ".join(
            '<a class="mavedb-doi-link" href="https://doi.org/{doi}" '
            'target="_blank" rel="noopener noreferrer">{doi}</a>'.format(
                doi=html.escape(d)
            )
            for d in dois[:3]
        )

        # Score set link
        urn_link = (
            '<a class="ref-link" href="{url}" target="_blank" rel="noopener noreferrer">'
            '{urn} {icon}</a>'.format(
                url=html.escape(mavedb_url(urn)), urn=html.escape(urn),
                icon=EXTERNAL_LINK_SVG,
            )
            if urn else ""
        )

        # Variant rows table
        if matched:
            col_headers = "".join(
                "<th>{0}</th>".format(html.escape(c))
                for c in ["hgvs_pro", "hgvs_nt"] + score_cols
            )
            rows_html = ""
            for v in matched[:5]:
                cells = "".join(
                    "<td>{0}</td>".format(html.escape(str(v.get(c, "—"))))
                    for c in ["hgvs_pro", "hgvs_nt"] + score_cols
                )
                rows_html += "<tr>{0}</tr>".format(cells)
            table_html = (
                '<table class="mavedb-variant-table">'
                "<thead><tr>{0}</tr></thead>"
                "<tbody>{1}</tbody>"
                "</table>"
            ).format(col_headers, rows_html)
        else:
            table_html = (
                '<p class="mavedb-no-match">'
                "No exact variant match found in this score set. "
                "Score set may still contain relevant functional context."
                "</p>"
            )

        blocks.append(
            '\n  <div class="mavedb-score-set">'
            '\n    <div class="mavedb-score-set-title">{title}</div>'
            '\n    <div class="mavedb-score-set-meta">'
            '{num_v} variants &nbsp;·&nbsp; {urn_link}'
            '{doi_block}'
            "</div>"
            "\n    {tier}"
            "\n    {table}"
            "\n  </div>"
        ).format(
            title=html.escape(title_text),
            num_v=html.escape(str(num_v)),
            urn_link=urn_link,
            doi_block=(
                " &nbsp;·&nbsp; " + doi_links if doi_links else ""
            ),
            tier=tier_html,
            table=table_html,
        )

    return header + "".join(blocks) + footer_note


# ---------------------------------------------------------------------------
# SpliceAI card builder
# ---------------------------------------------------------------------------

_SPLICEAI_LABELS: Dict[str, str] = {
    "DS_AG": "Acceptor Gain",
    "DS_AL": "Acceptor Loss",
    "DS_DG": "Donor Gain",
    "DS_DL": "Donor Loss",
}

_SPLICEAI_POSITIONS: Dict[str, str] = {
    "DS_AG": "DP_AG",
    "DS_AL": "DP_AL",
    "DS_DG": "DP_DG",
    "DS_DL": "DP_DL",
}


def _delta_color(value: float) -> str:
    if value >= 0.5:
        return "#a12c7b"
    if value >= 0.2:
        return "#964219"
    return "#437a22"


def _build_spliceai_card(cache: Dict[str, Any]) -> str:
    data = _extract_spliceai_data(cache)

    icon = SECTION_ICONS["functional evidence"]
    header = (
        '\n<div class="card">'
        '\n  <h2>{icon} SpliceAI — Splice Effect Scores</h2>'.format(icon=icon)
    )
    footer_note = (
        '\n  <p style="font-size:0.78rem;color:#7a7974;margin-top:0.75rem;">'
        'Delta ≥ 0.5 → strong splice disruption (PS3/PP3 level). '
        'Delta &lt; 0.2 → minimal splice effect (BP4 level). '
        'Intermediate (0.2–0.5) → uncertain, requires functional validation.</p>'
        '\n</div>\n'
    )

    if not data:
        return (
            header
            + '\n  <p style="font-size:0.88rem;color:#7a7974;">'
            "SpliceAI data not available (tool was skipped — expected for SVs "
            "or variants without explicit ref/alt alleles).</p>"
            + footer_note
        )

    # Graceful skip message from the tool itself
    note = data.get("note")
    if note:
        return (
            header
            + '\n  <p style="font-size:0.88rem;color:#7a7974;">{0}</p>'.format(
                html.escape(note)
            )
            + footer_note
        )

    error = data.get("error")
    if error:
        return (
            header
            + '\n  <p style="font-size:0.88rem;color:#964219;">'
            "SpliceAI API error: {0}</p>".format(html.escape(str(error)))
            + footer_note
        )

    raw   = data.get("raw_scores", {})
    interp = data.get("interpretation", {})
    variant_str = data.get("variant", "")
    assembly    = data.get("assembly", "GRCh38")
    mask_flag   = data.get("mask", 1)

    tier_map = {
        "strong_splice_effect": ("spliceai-tier-strong",   "Strong splice effect"),
        "moderate_splice_effect":("spliceai-tier-moderate", "Moderate splice effect"),
        "minimal_splice_effect": ("spliceai-tier-minimal",  "Minimal splice effect"),
    }
    tier_key   = interp.get("tier", "minimal_splice_effect")
    tier_css, tier_label = tier_map.get(tier_key, ("spliceai-tier-skipped", "Unknown"))
    acmg_hint  = interp.get("acmg_hint", "")
    delta_max  = interp.get("delta_max", 0.0)

    # Four delta-score gauge tiles
    tiles_html = ""
    for ds_key, ds_label in _SPLICEAI_LABELS.items():
        raw_val = raw.get(ds_key, 0.0)
        try:
            val = float(raw_val)
        except (TypeError, ValueError):
            val = 0.0

        dp_key = _SPLICEAI_POSITIONS[ds_key]
        pos_val = raw.get(dp_key, "—")
        color   = _delta_color(val)
        pct     = min(100, int(val * 100))

        tiles_html += (
            '\n    <div class="spliceai-delta">'
            '\n      <div class="spliceai-delta-label">{label}</div>'
            '\n      <div class="spliceai-delta-value" style="color:{color};">{val:.4f}</div>'
            '\n      <div class="spliceai-bar-track">'
            '\n        <div class="spliceai-bar-fill" '
            'style="width:{pct}%;background:{color};"></div>'
            '\n      </div>'
            '\n      <div style="font-size:0.72rem;color:#7a7974;margin-top:0.3rem;">'
            'pos offset: {pos}</div>'
            '\n    </div>'
        ).format(
            label=html.escape(ds_label), color=color,
            val=val, pct=pct, pos=html.escape(str(pos_val)),
        )

    return (
        header
        + '\n  <div class="spliceai-variant-label">{var} &nbsp;·&nbsp; {asm}'
        ' &nbsp;·&nbsp; mask={mask}</div>'.format(
            var=html.escape(variant_str),
            asm=html.escape(assembly),
            mask=html.escape(str(mask_flag)),
        )
        + '\n  <span class="spliceai-tier {css}">{label}</span>'.format(
            css=tier_css, label=html.escape(tier_label)
        )
        + '\n  <p class="spliceai-acmg-hint">{0}</p>'.format(html.escape(acmg_hint))
        + '\n  <div class="spliceai-grid">{0}\n  </div>'.format(tiles_html)
        + footer_note
    )


# ---------------------------------------------------------------------------
# IGV.js track builder (unchanged logic)
# ---------------------------------------------------------------------------

def _build_igv_card(
    chrom: Optional[str],
    start: Optional[int],
    end: Optional[int],
    gene: str,
    assembly: str,
    clinvar_hits: List[Dict[str, Any]],
) -> str:
    if not chrom or not start or not end:
        return ""

    padding = max(5000, (end - start) // 2)
    locus = "chr{0}:{1}-{2}".format(chrom, max(1, start - padding), end + padding)
    igv_genome = "hg38" if "38" in assembly else "hg19"

    this_feature = json.dumps({
        "chr": "chr{0}".format(chrom),
        "start": start - 1,
        "end": end,
        "name": "{0} variant".format(gene or ""),
        "color": "rgb(161, 44, 123)",
    })

    cv_features: List[Dict[str, Any]] = []
    for hit in clinvar_hits:
        cv_chrom = hit.get("grch38_chr") or hit.get("grch37_chr") or ""
        cv_start = hit.get("grch38_start") or hit.get("grch37_start")
        cv_end   = hit.get("grch38_stop")  or hit.get("grch37_stop") or cv_start
        if not cv_chrom or not cv_start:
            continue
        try:
            s = int(cv_start) - 1
            e = int(cv_end) if cv_end else s + 1
        except (TypeError, ValueError):
            continue
        classification = (hit.get("germline_classification") or "").lower()
        color = (
            "rgb(161,44,123)" if "pathogenic" in classification and "likely" not in classification
            else "rgb(150,66,25)" if "likely pathogenic" in classification
            else "rgb(67,122,34)" if "benign" in classification
            else "rgb(122,121,116)"
        )
        cv_features.append({
            "chr": "chr{0}".format(cv_chrom),
            "start": s, "end": e,
            "name": hit.get("accession", "ClinVar"),
            "color": color,
        })

    cv_features_json = json.dumps(cv_features)
    cv_data_json     = json.dumps({h.get("accession", ""): h for h in clinvar_hits if h.get("accession")})
    gene_escaped_js  = html.escape(gene or "").replace("'", "\\'")

    igv_html = (
        '\n<div class="card" id="igv-card">'
        '\n  <h2>'
        '\n    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '\n      <rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>'
        '\n    </svg>'
        '\n    Genomic Context'
        '\n  </h2>'
        '\n  <div id="igv-track"></div>'
        '\n</div>'
        '\n<div class="clinvar-popup-overlay" id="cv-overlay">'
        '\n  <div class="clinvar-popup" id="cv-popup">'
        '\n    <button class="close-btn" '
        'onclick="document.getElementById(\'cv-overlay\').classList.remove(\'open\')">&times;</button>'
        '\n    <h3 id="cv-popup-title">ClinVar Entry</h3>'
        '\n    <table id="cv-popup-table"></table>'
        '\n    <a id="cv-popup-link" class="clinvar-link" href="#" target="_blank" '
        'rel="noopener noreferrer">View on ClinVar &#8594;</a>'
        '\n  </div>'
        '\n</div>'
        '\n<script src="https://cdn.jsdelivr.net/npm/igv@3.0.2/dist/igv.min.js"></script>'
        '\n<script>'
        '\n(function() {'
        '\n  var cvData = ' + cv_data_json + ';'
        '\n  var thisFeature = ' + this_feature + ';'
        '\n  var cvFeatures = ' + cv_features_json + ';'
        '\n  var thisVariantName = ' + repr(gene_escaped_js + " variant") + ';'
        '\n  function showCVPopup(accession) {'
        '\n    var d = cvData[accession]; if (!d) return;'
        '\n    document.getElementById(\'cv-popup-title\').textContent = accession;'
        '\n    var rows = ['
        '\n      [\'Classification\', d.germline_classification || \'—\'],'
        '\n      [\'Review status\',  d.review_status || \'—\'],'
        '\n      [\'Trait\',          (d.trait_names || []).join(\', \') || \'—\'],'
        '\n      [\'HGVS\',           d.hgvs_name || d.title || \'—\'],'
        '\n      [\'Variant type\',   d.variant_type || \'—\'],'
        '\n      [\'GRCh38\',         d.grch38_chr ? \'chr\' + d.grch38_chr + \':\' + d.grch38_start + \'-\' + d.grch38_stop : \'—\'],'
        '\n      [\'SCVs\',           (d.supporting_scvs || []).length + \' submission(s)\'],'
        '\n    ];'
        '\n    document.getElementById(\'cv-popup-table\').innerHTML = rows.map(function(r) {'
        '\n      return \'<tr><td>\' + r[0] + \'</td><td>\' + (r[1] || \'—\') + \'</td></tr>\';'
        '\n    }).join(\'\');'
        '\n    var vid = (accession.match(/VCV0*(\\\\d+)/) || [])[1];'
        '\n    var link = document.getElementById(\'cv-popup-link\');'
        '\n    link.href = vid'
        '\n      ? \'https://www.ncbi.nlm.nih.gov/clinvar/variation/\' + vid + \'/\''
        '\n      : \'https://www.ncbi.nlm.nih.gov/clinvar/\';'
        '\n    document.getElementById(\'cv-overlay\').classList.add(\'open\');'
        '\n  }'
        '\n  document.getElementById(\'cv-overlay\').addEventListener(\'click\', function(e) {'
        '\n    if (e.target === this) this.classList.remove(\'open\');'
        '\n  });'
        '\n  igv.createBrowser(document.getElementById(\'igv-track\'), {'
        '\n    genome: ' + repr(igv_genome) + ','
        '\n    locus:  ' + repr(locus) + ','
        '\n    tracks: ['
        '\n      { name: \'This variant\', type: \'annotation\', format: \'bed\','
        '\n        displayMode: \'EXPANDED\', color: \'rgb(161,44,123)\','
        '\n        features: [thisFeature] },'
        '\n      { name: \'ClinVar hits\', type: \'annotation\', format: \'bed\','
        '\n        displayMode: \'EXPANDED\', features: cvFeatures,'
        '\n        color: function(feature) { return feature.color || \'rgb(122,121,116)\'; } },'
        '\n    ],'
        '\n  }).then(function(browser) {'
        '\n    browser.on(\'trackclick\', function(track, popoverData) {'
        '\n      if (!popoverData) return;'
        '\n      var nameField = popoverData.find(function(d) { return d.name === \'Name\'; });'
        '\n      if (!nameField) return;'
        '\n      var accession = nameField.value;'
        '\n      if (accession && accession !== thisVariantName) {'
        '\n        showCVPopup(accession); return false;'
        '\n      }'
        '\n    });'
        '\n  });'
        '\n}());'
        '\n</script>\n'
    )
    return igv_html


# ---------------------------------------------------------------------------
# Reference link builder
# ---------------------------------------------------------------------------

def _build_ref_links(clinvar_hits: List[Dict[str, Any]], pmids: List[str]) -> str:
    if not clinvar_hits and not pmids:
        return ""

    links = []
    for hit in clinvar_hits[:6]:
        acc = hit.get("accession", "")
        if not acc:
            continue
        url   = clinvar_url(acc)
        label = acc.split(".")[0]
        classification = hit.get("germline_classification", "")
        title = "{0} — {1}".format(acc, classification) if classification else acc
        links.append(
            '<a class="ref-link" href="{url}" target="_blank" rel="noopener noreferrer" '
            'title="{title}">{label} {icon}</a>'.format(
                url=html.escape(url), title=html.escape(title),
                label=html.escape(label), icon=EXTERNAL_LINK_SVG,
            )
        )

    for pmid in pmids[:5]:
        url = pubmed_url(pmid)
        links.append(
            '<a class="ref-link" href="{url}" target="_blank" rel="noopener noreferrer" '
            'title="PubMed {pmid}">PMID:{pmid} {icon}</a>'.format(
                url=html.escape(url), pmid=html.escape(pmid), icon=EXTERNAL_LINK_SVG,
            )
        )

    return '<div class="ref-block">{0}</div>'.format("".join(links)) if links else ""


# ---------------------------------------------------------------------------
# Markdown → HTML helpers
# ---------------------------------------------------------------------------

def escape(text: str) -> str:
    return html.escape(text)


def _linkify_pmids(text: str) -> str:
    return re.sub(
        r"PMID:?\s*(\d{6,9})",
        lambda m: '<a href="{0}" target="_blank" rel="noopener noreferrer" '
                  'class="ref-link">PMID:{1} {2}</a>'.format(
                      pubmed_url(m.group(1)), m.group(1), EXTERNAL_LINK_SVG),
        text,
    )


def _linkify_accessions(text: str) -> str:
    def _replace(m: re.Match) -> str:
        acc = m.group(0)
        url = clinvar_url(acc)
        return '<a href="{0}" target="_blank" rel="noopener noreferrer" ' \
               'class="ref-link">{1} {2}</a>'.format(
                   html.escape(url), html.escape(acc), EXTERNAL_LINK_SVG)
    return re.sub(r"[VS]CV\d{9}(?:\.\d+)?", _replace, text)


def markdown_inline(text: str) -> str:
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*",     r"<em>\1</em>",         text)
    text = re.sub(r"`(.+?)`",       r"<code>\1</code>",     text)
    text = _linkify_pmids(text)
    text = _linkify_accessions(text)
    return text


def markdown_to_html_body(md: str) -> str:
    lines = md.splitlines()
    html_parts: List[str] = []
    in_list = False
    in_pre  = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for line in lines:
        if line.strip().startswith("```"):
            close_list()
            if in_pre:
                html_parts.append("</code></pre>")
                in_pre = False
            else:
                html_parts.append("<pre><code>")
                in_pre = True
            continue

        if in_pre:
            html_parts.append(escape(line))
            continue

        stripped = line.strip()

        if re.match(r"^#{1,3}\s+", stripped):
            close_list()
            text = re.sub(r"^#+\s+", "", stripped)
            html_parts.append("<h3>{0}</h3>".format(markdown_inline(text)))
        elif stripped.startswith("> "):
            close_list()
            html_parts.append("<blockquote>{0}</blockquote>".format(markdown_inline(stripped[2:])))
        elif re.match(r"^[-*]\s+", stripped):
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            item = re.sub(r"^[-*]\s+", "", stripped)
            html_parts.append("<li>{0}</li>".format(markdown_inline(item)))
        elif stripped == "":
            close_list()
            html_parts.append("")
        else:
            close_list()
            html_parts.append("<p>{0}</p>".format(markdown_inline(stripped)))

    close_list()
    return "\n".join(html_parts)


# ---------------------------------------------------------------------------
# Section splitter
# ---------------------------------------------------------------------------

def split_sections(md: str) -> List[Tuple[Optional[str], str]]:
    sections: List[Tuple[Optional[str], str]] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []

    for line in md.splitlines():
        if re.match(r"^##\s+", line):
            if current_title is not None or current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = re.sub(r"^##\s+", "", line).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_title is not None or current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))

    return sections


# ---------------------------------------------------------------------------
# Section renderer
# ---------------------------------------------------------------------------

def classify_band_css(text: str) -> str:
    t = text.lower()
    if "likely pathogenic" in t: return "band-likely-pathogenic"
    if "pathogenic" in t:        return "band-pathogenic"
    if "likely benign" in t:     return "band-likely-benign"
    if "benign" in t:            return "band-benign"
    return "band-vus"


def render_section(
    title: Optional[str],
    content: str,
    ref_links_html: str = "",
) -> str:
    title_str  = title or "Notes"
    title_lower = title_str.lower()
    icon = SECTION_ICONS.get(title_lower, "")

    extra = ""
    if "acmg" in title_lower:
        band_match = re.search(
            r"(pathogenic|likely pathogenic|uncertain significance|likely benign|benign)",
            content, re.IGNORECASE,
        )
        if band_match:
            band = band_match.group(1)
            css  = classify_band_css(band)
            extra += (
                '<div class="acmg-row">'
                '<span class="classification-band {0}">{1}</span>'
                '</div>'
            ).format(css, escape(band.title()))

    codes = list(dict.fromkeys(
        re.findall(r"\b(PVS1|PS[1-6]|PM[1-6]|PP[1-6]|BA1|BS[1-4]|BP[1-7])\b", content)
    ))
    if codes:
        pills = "".join('<span class="acmg-code">{0}</span>'.format(c) for c in codes)
        extra += '<div class="acmg-row">{0}</div>'.format(pills)

    refs_block = ""
    if ref_links_html and title_lower in ("retrieved evidence", "literature"):
        refs_block = ref_links_html

    if title_lower == "uncertainties":
        items = [
            l.lstrip("-*• ").strip()
            for l in content.splitlines()
            if l.lstrip("-*• ").strip()
        ]
        if items:
            li_items = "".join(
                "<li>{0}</li>".format(markdown_inline(item)) for item in items
            )
            return (
                '\n<div class="card">'
                '\n  <h2>{icon}{title}</h2>'
                '\n  {extra}'
                '\n  <ul class="uncertainty-list">{li_items}</ul>'
                '\n</div>\n'
            ).format(icon=icon, title=escape(title_str), extra=extra, li_items=li_items)

    # The "Functional Evidence" section from the markdown gets its own card
    # but MaveDB and SpliceAI structured cards are injected separately (see
    # render_html), so here we just render the prose summary as-is.
    body = markdown_to_html_body(content)
    return (
        '\n<div class="card">'
        '\n  <h2>{icon}{title}</h2>'
        '\n  {extra}'
        '\n  {body}'
        '\n  {refs_block}'
        '\n</div>\n'
    ).format(
        icon=icon, title=escape(title_str),
        extra=extra, body=body, refs_block=refs_block,
    )


# ---------------------------------------------------------------------------
# Section ordering helper
# ---------------------------------------------------------------------------

# Canonical order of report sections as produced by the updated SYSTEM_PROMPT.
# Sections not in this list are appended at the end in original order.
_SECTION_ORDER = [
    "variant summary",
    "retrieved evidence",
    "functional evidence",
    "literature",
    "draft acmg assessment",
    "uncertainties",
]


def _sort_sections(
    sections: List[Tuple[Optional[str], str]],
) -> List[Tuple[Optional[str], str]]:
    def _rank(title: Optional[str]) -> int:
        key = (title or "").lower()
        try:
            return _SECTION_ORDER.index(key)
        except ValueError:
            return len(_SECTION_ORDER)
    return sorted(sections, key=lambda s: _rank(s[0]))


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_html(
    markdown_text: str,
    variant: str,
    gene: str,
    disease: str,
    chrom: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    assembly: str = "GRCh38",
    cache_path: Optional[str] = None,
) -> str:
    now = datetime.datetime.now().strftime("%d %b %Y, %H:%M")

    cache        = _load_cache(cache_path)
    clinvar_hits = _extract_clinvar_hits(cache)
    pmids        = _extract_pubmed_ids(cache)

    ref_links_html = _build_ref_links(clinvar_hits, pmids)
    igv_html       = _build_igv_card(chrom, start, end, gene, assembly, clinvar_hits)

    # Build structured MaveDB / SpliceAI cards from cache
    mavedb_card   = _build_mavedb_card(cache)
    spliceai_card = _build_spliceai_card(cache)

    # Render markdown sections
    raw_sections = split_sections(markdown_text)
    sorted_sections = _sort_sections(raw_sections)

    cards_parts: List[str] = []
    functional_evidence_injected = False

    for title, body in sorted_sections:
        if not title and not body.strip():
            continue

        title_lower = (title or "").lower()

        # After the prose Functional Evidence card, inject the structured
        # MaveDB and SpliceAI cards from the cache.
        if title_lower == "functional evidence" and not functional_evidence_injected:
            cards_parts.append(render_section(title, body, ref_links_html))
            cards_parts.append(mavedb_card)
            cards_parts.append(spliceai_card)
            functional_evidence_injected = True
            continue

        cards_parts.append(render_section(title or "Notes", body, ref_links_html))

    # If the agent omitted the Functional Evidence section entirely,
    # inject the structured cards before Draft ACMG Assessment.
    if not functional_evidence_injected:
        insert_at = len(cards_parts)
        for i, (title, _) in enumerate(sorted_sections):
            if (title or "").lower() == "draft acmg assessment":
                insert_at = i
                break
        cards_parts.insert(insert_at, spliceai_card)
        cards_parts.insert(insert_at, mavedb_card)

    cards_html = "\n".join(cards_parts)

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ACMG Evidence Report — {variant_escaped}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
{css}
</style>
</head>
<body>
<div class="page">
  <header>
    <div class="badge">ACMG Evidence Agent — Research Prototype</div>
    <h1>{variant_escaped}</h1>
    <div class="meta">
      Gene: <strong>{gene_escaped}</strong> &nbsp;·&nbsp;
      Disease: <strong>{disease_escaped}</strong> &nbsp;·&nbsp;
      Assembly: <strong>{assembly}</strong> &nbsp;·&nbsp;
      Generated: {now}
    </div>
  </header>

  {igv_html}
  {cards_html}

  <footer>
    Research prototype only. Not for clinical use.
    Review all evidence independently before any interpretation.
  </footer>
</div>
</body>
</html>""".format(
        variant_escaped=escape(variant),
        gene_escaped=escape(gene or "—"),
        disease_escaped=escape(disease or "—"),
        assembly=escape(assembly),
        now=now,
        css=CSS,
        igv_html=igv_html,
        cards_html=cards_html,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render ACMG evidence report HTML from a markdown file and cache.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m src.reporting.render_report \\
      --input report.md --cache .cache/responses.json \\
      --variant "PALB2 tandem dup" --gene PALB2 \\
      --chrom 16 --start 23605575 --end 23615114 \\
      --output report.html

  echo "## Variant Summary\\nNo data." | \\
      python -m src.reporting.render_report --variant "BRCA1 p.Val1736Ala" --gene BRCA1
""",
    )
    parser.add_argument("--input",    default=None)
    parser.add_argument("--output",   default="report.html")
    parser.add_argument("--variant",  default="Unknown variant")
    parser.add_argument("--gene",     default=None)
    parser.add_argument("--disease",  default=None)
    parser.add_argument("--chrom",    default=None)
    parser.add_argument("--start",    type=int, default=None)
    parser.add_argument("--end",      type=int, default=None)
    parser.add_argument("--assembly", default="GRCh38", choices=["GRCh38", "GRCh37"])
    parser.add_argument("--cache",    default=".cache/responses.json")
    args = parser.parse_args()

    md = open(args.input).read() if args.input else sys.stdin.read()

    out = render_html(
        markdown_text=md,
        variant=args.variant,
        gene=args.gene or "",
        disease=args.disease or "",
        chrom=args.chrom,
        start=args.start,
        end=args.end,
        assembly=args.assembly,
        cache_path=args.cache,
    )
    with open(args.output, "w") as f:
        f.write(out)
    print("Report saved to: {0}".format(args.output))


if __name__ == "__main__":
    main()