"""
scripts/mine_clingen.py

Downloads the ClinGen Evidence Repository bulk TSV export which contains
fully resolved records: HGVS expressions, gene symbols, applied ACMG evidence
codes (met and not-met), interpretation summaries, PMIDs, and classifications.

Bulk TSV endpoint:
  https://erepo.clinicalgenome.org/evrepo/api/classifications/all?format=tabbed

No pagination, no CAID resolution, no UUID lookups required.

Usage:
    # First run — downloads ~5MB TSV
    python scripts/mine_clingen.py --max-records 2000 --out data/clingen_exemplars.jsonl

    # Re-run with different filters — uses cached file
    python scripts/mine_clingen.py --max-records 5000 --tsv data/clingen_all.tsv --out data/clingen_exemplars_large.jsonl
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
import urllib.request
from pathlib import Path
from typing import Optional

TSV_URL = "https://erepo.clinicalgenome.org/evrepo/api/classifications/all?format=tabbed"

# Column names from the TSV header (confirmed from API probe)
# #Variation  ClinVar Variation Id  Allele Registry Id  HGVS Expressions
# HGNC Gene Symbol  Disease  Mondo Id  Mode of Inheritance  Assertion
# Applied Evidence Codes (Met)  Applied Evidence Codes (Not Met)
# Summary of interpretation  PubMed Articles  Expert Panel  Guideline
# Approval Date  Published Date  Retracted  Evidence Repo Link  Uuid

CLASSIFICATION_MAP = {
    "pathogenic":                        "Pathogenic",
    "likely pathogenic":                 "Likely Pathogenic",
    "uncertain significance":            "Variant of Uncertain Significance",
    "vus":                               "Variant of Uncertain Significance",
    "likely benign":                     "Likely Benign",
    "benign":                            "Benign",
    "likely pathogenic, low penetrance": "Likely Pathogenic",
    "pathogenic, low penetrance":        "Pathogenic",
}


def _normalise_classification(raw: str) -> Optional[str]:
    return CLASSIFICATION_MAP.get(raw.strip().lower())


def _pick_hgvs(hgvs_field: str) -> str:
    """
    HGVS Expressions column contains pipe-separated values.
    Prefer NM_ (transcript) > NC_ (genomic) > anything else.
    """
    if not hgvs_field.strip():
        return ""
    terms = [t.strip() for t in hgvs_field.split("|") if t.strip()]
    for prefix in ("NM_", "NP_", "NC_", "NG_"):
        for t in terms:
            if t.startswith(prefix):
                return t
    return terms[0]


def _parse_codes(codes_field: str) -> list[str]:
    """'PS1, PM2, PP3' → ['PS1', 'PM2', 'PP3']"""
    if not codes_field.strip():
        return []
    return [c.strip() for c in codes_field.replace(";", ",").split(",") if c.strip()]


def _parse_pmids(pmids_field: str) -> list[str]:
    """'12345678, 87654321' → ['12345678', '87654321']"""
    if not pmids_field.strip():
        return []
    pmids = []
    for p in pmids_field.replace(";", ",").split(","):
        p = p.strip()
        if p.isdigit():
            pmids.append(p)
    return pmids


def _build_instruction(row: dict) -> str:
    hgvs    = _pick_hgvs(row.get("HGVS Expressions", "")) \
              or row.get("#Variation", "Unknown variant")
    gene    = row.get("HGNC Gene Symbol", "").strip() or "not specified"
    disease = row.get("Disease", "").strip() or "not specified"
    panel   = row.get("Expert Panel", "").strip()
    moi     = row.get("Mode of Inheritance", "").strip()

    lines = [
        "Interpret the following variant conservatively for research prototyping.",
        "",
        f"Variant: {hgvs}",
        f"Gene: {gene}",
        f"Disease context: {disease}",
    ]
    if moi:
        lines.append(f"Mode of inheritance: {moi}")
    if panel:
        lines.append(f"Expert panel: {panel}")
    lines += [
        "",
        "Provide a markdown report with sections: Variant Summary, "
        "Retrieved Evidence, Functional Evidence, Literature, "
        "Draft ACMG Assessment, Uncertainties.",
    ]
    return "\n".join(lines)


def _build_output(row: dict, classification: str) -> str:
    hgvs        = _pick_hgvs(row.get("HGVS Expressions", "")) \
                  or row.get("#Variation", "—")
    all_hgvs    = row.get("HGVS Expressions", "").replace("|", " | ")
    gene        = row.get("HGNC Gene Symbol", "").strip() or "—"
    disease     = row.get("Disease", "").strip() or "—"
    mondo       = row.get("Mondo Id", "").strip()
    moi         = row.get("Mode of Inheritance", "").strip() or "—"
    panel       = row.get("Expert Panel", "").strip() or "—"
    guideline   = row.get("Guideline", "").strip() or "—"
    approved    = row.get("Approval Date", "").strip() or "—"
    caid        = row.get("Allele Registry Id", "").strip() or "—"
    clinvar_id  = row.get("ClinVar Variation Id", "").strip() or "—"
    summary     = row.get("Summary of interpretation", "").strip()
    erepo_link  = row.get("Evidence Repo Link", "").strip()

    met_codes   = _parse_codes(row.get("Applied Evidence Codes (Met)", ""))
    not_codes   = _parse_codes(row.get("Applied Evidence Codes (Not Met)", ""))
    pmids       = _parse_pmids(row.get("PubMed Articles", ""))

    met_line = ", ".join(met_codes) if met_codes else "None"
    not_line = ", ".join(not_codes) if not_codes else "None"

    # Build per-criterion block from met/not-met code lists
    criteria_lines = ""
    if met_codes or not_codes:
        criteria_lines = "\n".join(
            f"- **{c}** (MET)" for c in met_codes
        )
        if not_codes:
            criteria_lines += "\n" + "\n".join(
                f"- **{c}** (NOT MET)" for c in not_codes
            )
    else:
        criteria_lines = "- No per-criterion evidence codes available."

    pmid_lines = "\n".join(f"- PMID:{p}" for p in pmids[:8]) \
                 or "- No publications listed."

    summary_block = (
        f"\n**Curator interpretation summary:**\n> {summary}\n"
        if summary else ""
    )

    erepo_ref = f"\n**ERepo link:** {erepo_link}" if erepo_link else ""

    return f"""## Variant Summary

**Variant:** {hgvs}
**All HGVS expressions:** {all_hgvs}
**Gene:** {gene}
**ClinGen Allele ID:** {caid}
**ClinVar Variation ID:** {clinvar_id}
**Disease context:** {disease}{' (' + mondo + ')' if mondo else ''}
**Mode of inheritance:** {moi}
**Expert panel:** {panel}
**Guideline:** {guideline}
**Approval date:** {approved}{erepo_ref}

## Retrieved Evidence

*Derived from the ClinGen Evidence Repository (erepo.clinicalgenome.org) \
expert panel curation.*

**ACMG criteria applied (MET):** {met_line}
**ACMG criteria not met:** {not_line}
{summary_block}
## Functional Evidence

- **MaveDB:** Not queried for this exemplar.
- **SpliceAI:** Not queried for this exemplar.

## Literature

{pmid_lines}

## Draft ACMG Assessment

**Provisional classification:** {classification}

### Evidence Criteria

{criteria_lines}

> This assessment is directly from a ClinGen expert panel curation.
> Research exemplar only — not for clinical use.

## Uncertainties

- Classification is based solely on ClinGen expert panel curation.
- No independent functional or computational evidence was retrieved.
- Variant pathogenicity may be disease- and inheritance-mode-specific.
"""


def mine(max_records: int, out_path: Path, tsv_path: Optional[Path]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if tsv_path and tsv_path.exists():
        print(f"Reading from local TSV: {tsv_path}")
        raw = tsv_path.read_text(encoding="utf-8", errors="replace")
    else:
        print(f"Downloading ClinGen bulk TSV from:\n  {TSV_URL}")
        print("  (This is a ~5 MB file — may take 10-30 s on HPC login node)")
        req = urllib.request.Request(
            TSV_URL,
            headers={"Accept": "text/tab-separated-values, */*"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        # Optionally cache it
        cache = out_path.parent / "clingen_all.tsv"
        cache.write_text(raw, encoding="utf-8")
        print(f"  Cached raw TSV to {cache}")

    reader = csv.DictReader(
        io.StringIO(raw),
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
    )

    written  = 0
    skipped  = 0
    retracted= 0

    with open(out_path, "w") as out_f:
        for row in reader:
            if written >= max_records:
                break

            # Skip retracted classifications
            if row.get("Retracted", "").strip().lower() in ("true", "yes", "1"):
                retracted += 1
                continue

            classification = _normalise_classification(
                row.get("Assertion", "")
            )
            if not classification:
                skipped += 1
                continue

            # Skip if no HGVS and no gene — not enough context for training
            hgvs = _pick_hgvs(row.get("HGVS Expressions", ""))
            gene = row.get("HGNC Gene Symbol", "").strip()
            if not hgvs and not gene:
                skipped += 1
                continue

            instruction = _build_instruction(row)
            output      = _build_output(row, classification)

            met_codes = _parse_codes(row.get("Applied Evidence Codes (Met)", ""))
            record = {
                "instruction": instruction,
                "output":      output,
                "metadata": {
                    "source":         "clingen_erepo",
                    "uuid":           row.get("Uuid", "").strip(),
                    "caid":           row.get("Allele Registry Id", "").strip(),
                    "clinvar_id":     row.get("ClinVar Variation Id", "").strip(),
                    "gene":           gene,
                    "hgvs":           hgvs,
                    "disease":        row.get("Disease", "").strip(),
                    "panel":          row.get("Expert Panel", "").strip(),
                    "classification": classification,
                    "criteria_met":   met_codes,
                    "criteria_not_met": _parse_codes(
                        row.get("Applied Evidence Codes (Not Met)", "")
                    ),
                    "approval_date":  row.get("Approval Date", "").strip(),
                },
            }
            out_f.write(json.dumps(record) + "\n")
            written += 1

            if written % 200 == 0:
                print(f"  {written} written ...")

    print(
        f"\nDone. {written} exemplars written to {out_path}\n"
        f"  Skipped (no classification or context): {skipped}\n"
        f"  Skipped (retracted): {retracted}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path,
                        default=Path("data/clingen_exemplars.jsonl"))
    parser.add_argument("--max-records", type=int, default=2000)
    parser.add_argument("--tsv", type=Path, default=None,
        help="Local path to cached clingen_all.tsv (skips download)")
    args = parser.parse_args()
    mine(args.max_records, args.out, args.tsv)