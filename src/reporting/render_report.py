"""
src/reporting/render_report.py

Renders the ACMG evidence report as a self-contained HTML file.

New features:
  - IGV.js gene/exon track with variant region and ClinVar hits as BED features
  - Clickable ClinVar pop-out cards (accession, classification, review status)
  - Linked references: ClinVar entries → ncbi.nlm.nih.gov, PMIDs → pubmed
  - Cache-aware: reads .cache/responses.json to pull structured data for the track
  - CLI: python -m src.reporting.render_report --cache .cache/responses.json ...
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

/* Footer */
footer {
    text-align: center; font-size: 0.75rem; color: #bab9b4;
    margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid #dcd9d5;
}
@media (max-width: 600px) {
    body { padding: 1rem 0.5rem; }
    .card { padding: 1.1rem 1rem; }
}
"""


SECTION_ICONS = {
    "variant summary":       '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18"/></svg>',
    "retrieved evidence":    '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>',
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


# ---------------------------------------------------------------------------
# Cache reader — extract structured data for IGV and reference links
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
    """Return the first cache entry matching a namespace."""
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


# ---------------------------------------------------------------------------
# IGV.js track builder
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
    locus = "chr{0}:{1}-{2}".format(
        chrom,
        max(1, start - padding),
        end + padding,
    )
    igv_genome = "hg38" if "38" in assembly else "hg19"

    this_feature = json.dumps({
        "chr": "chr{0}".format(chrom),
        "start": start - 1,
        "end": end,
        "name": "{0} variant".format(gene or ""),
        "color": "rgb(161, 44, 123)",
    })

    cv_features = []
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
            "start": s,
            "end": e,
            "name": hit.get("accession", "ClinVar"),
            "color": color,
        })

    cv_features_json = json.dumps(cv_features)
    cv_data_json     = json.dumps({
        h.get("accession", ""): h for h in clinvar_hits if h.get("accession")
    })
    gene_escaped_js  = html.escape(gene or "").replace("'", "\\'")

    # Build the IGV HTML using concatenation so JS braces never touch .format()
    igv_html = (
        '''
<div class="card" id="igv-card">
  <h2>
    <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>
    </svg>
    Genomic Context
  </h2>
  <div id="igv-track"></div>
</div>

<div class="clinvar-popup-overlay" id="cv-overlay">
  <div class="clinvar-popup" id="cv-popup">
    <button class="close-btn" onclick="document.getElementById('cv-overlay').classList.remove('open')">&times;</button>
    <h3 id="cv-popup-title">ClinVar Entry</h3>
    <table id="cv-popup-table"></table>
    <a id="cv-popup-link" class="clinvar-link" href="#" target="_blank" rel="noopener noreferrer">
      View on ClinVar &#8594;
    </a>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/igv@3.0.2/dist/igv.min.js"></script>
<script>
(function() {
  var cvData = ''' + cv_data_json + ''';
  var thisFeature = ''' + this_feature + ''';
  var cvFeatures = ''' + cv_features_json + ''';
  var thisVariantName = ''' + repr(gene_escaped_js + " variant") + ''';

  function showCVPopup(accession) {
    var d = cvData[accession];
    if (!d) return;
    document.getElementById('cv-popup-title').textContent = accession;
    var rows = [
      ['Classification', d.germline_classification || '—'],
      ['Review status',  d.review_status || '—'],
      ['Trait',          (d.trait_names || []).join(', ') || '—'],
      ['HGVS',           d.hgvs_name || d.title || '—'],
      ['Variant type',   d.variant_type || '—'],
      ['GRCh38',         d.grch38_chr ? 'chr' + d.grch38_chr + ':' + d.grch38_start + '-' + d.grch38_stop : '—'],
      ['SCVs',           (d.supporting_scvs || []).length + ' submission(s)'],
    ];
    document.getElementById('cv-popup-table').innerHTML = rows.map(function(r) {
      return '<tr><td>' + r[0] + '</td><td>' + (r[1] || '—') + '</td></tr>';
    }).join('');
    var vid = (accession.match(/VCV0*(\\d+)/) || [])[1];
    var link = document.getElementById('cv-popup-link');
    link.href = vid
      ? 'https://www.ncbi.nlm.nih.gov/clinvar/variation/' + vid + '/'
      : 'https://www.ncbi.nlm.nih.gov/clinvar/';
    document.getElementById('cv-overlay').classList.add('open');
  }

  document.getElementById('cv-overlay').addEventListener('click', function(e) {
    if (e.target === this) this.classList.remove('open');
  });

  igv.createBrowser(document.getElementById('igv-track'), {
    genome: ''' + repr(igv_genome) + ''',
    locus:  ''' + repr(locus) + ''',
    tracks: [
      {
        name: 'This variant',
        type: 'annotation', format: 'bed',
        displayMode: 'EXPANDED',
        color: 'rgb(161,44,123)',
        features: [thisFeature],
      },
      {
        name: 'ClinVar hits',
        type: 'annotation', format: 'bed',
        displayMode: 'EXPANDED',
        features: cvFeatures,
        color: function(feature) { return feature.color || 'rgb(122,121,116)'; },
      },
    ],
  }).then(function(browser) {
    browser.on('trackclick', function(track, popoverData) {
      if (!popoverData) return;
      var nameField = popoverData.find(function(d) { return d.name === 'Name'; });
      if (!nameField) return;
      var accession = nameField.value;
      if (accession && accession !== thisVariantName) {
        showCVPopup(accession);
        return false;
      }
    });
  });
}());
</script>
'''
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
        url = clinvar_url(acc)
        label = acc.split(".")[0]  # strip version suffix for display
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

    if not links:
        return ""

    return '<div class="ref-block">{0}</div>'.format("".join(links))


# ---------------------------------------------------------------------------
# Markdown → HTML helpers (unchanged logic, minor formatting fixes)
# ---------------------------------------------------------------------------

def escape(text: str) -> str:
    return html.escape(text)


def _linkify_pmids(text: str) -> str:
    """Turn bare PMIDs and PMID:XXXXXXX patterns into hyperlinks."""
    text = re.sub(
        r"PMID:?\s*(\d{6,9})",
        lambda m: '<a href="{0}" target="_blank" rel="noopener noreferrer" '
                  'class="ref-link">PMID:{1} {2}</a>'.format(
                      pubmed_url(m.group(1)), m.group(1), EXTERNAL_LINK_SVG),
        text,
    )
    return text


def _linkify_accessions(text: str) -> str:
    """Turn VCV/SCV accessions into ClinVar hyperlinks."""
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
    in_pre = False

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

        if stripped.startswith("### ") or stripped.startswith("## ") or stripped.startswith("# "):
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
    title_str = title or "Notes"
    title_lower = title_str.lower()
    icon = SECTION_ICONS.get(title_lower, "")

    # Classification band
    extra = ""
    if "acmg" in title_lower:
        band_match = re.search(
            r"(pathogenic|likely pathogenic|uncertain significance|likely benign|benign)",
            content, re.IGNORECASE,
        )
        if band_match:
            band = band_match.group(1)
            css = classify_band_css(band)
            extra += '<div class="acmg-row"><span class="classification-band {0}">{1}</span></div>'.format(
                css, escape(band.title())
            )

    # ACMG code pills
    codes = list(dict.fromkeys(
        re.findall(r"\b(PVS1|PS[1-6]|PM[1-6]|PP[1-6]|BA1|BS[1-4]|BP[1-7])\b", content)
    ))
    if codes:
        pills = "".join('<span class="acmg-code">{0}</span>'.format(c) for c in codes)
        extra += '<div class="acmg-row">{0}</div>'.format(pills)

    # Reference links injected into retrieved-evidence and literature sections
    refs_block = ""
    if ref_links_html and title_lower in ("retrieved evidence", "literature"):
        refs_block = ref_links_html

    # Uncertainties as styled list
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

    # Load cache for structured data
    cache = _load_cache(cache_path)
    clinvar_hits = _extract_clinvar_hits(cache)
    pmids = _extract_pubmed_ids(cache)

    # Build reference link bar
    ref_links_html = _build_ref_links(clinvar_hits, pmids)

    # Build IGV track (only when coordinates are available)
    igv_html = _build_igv_card(chrom, start, end, gene, assembly, clinvar_hits)

    # Render report sections
    sections = split_sections(markdown_text)
    if not sections or (len(sections) == 1 and sections[0][0] is None):
        cards_html = render_section("Report", markdown_text, ref_links_html)
    else:
        cards_html = "\n".join(
            render_section(title or "Notes", body, ref_links_html)
            for title, body in sections
            if title or body.strip()
        )

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
        now=now,
        css=CSS,
        igv_html=igv_html,
        cards_html=cards_html,
    )


# ---------------------------------------------------------------------------
# CLI — standalone renderer (reads from cache, no agent re-run needed)
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render ACMG evidence report HTML from a markdown file and cache.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Render from markdown file + cache (no agent re-run)
  python -m src.reporting.render_report \\
      --input report.md --cache .cache/responses.json \\
      --variant "PALB2 tandem dup" --gene PALB2 \\
      --chrom 16 --start 23605575 --end 23615114 \\
      --output report.html

  # Pipe markdown from stdin
  echo "## Variant Summary\\nNo data." | \\
      python -m src.reporting.render_report --variant "BRCA1 p.Val1736Ala" --gene BRCA1
""",
    )
    parser.add_argument("--input",    default=None, help="Markdown file (default: stdin)")
    parser.add_argument("--output",   default="report.html")
    parser.add_argument("--variant",  default="Unknown variant")
    parser.add_argument("--gene",     default=None)
    parser.add_argument("--disease",  default=None)
    parser.add_argument("--chrom",    default=None)
    parser.add_argument("--start",    type=int, default=None)
    parser.add_argument("--end",      type=int, default=None)
    parser.add_argument("--assembly", default="GRCh38", choices=["GRCh38", "GRCh37"])
    parser.add_argument("--cache",    default=".cache/responses.json",
                        help="Path to responses.json cache (default: .cache/responses.json)")
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
