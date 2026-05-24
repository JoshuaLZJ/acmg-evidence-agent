"""
scripts/mine_clinvar.py

Downloads and filters the ClinVar variant_summary.txt.gz for
expert-panel-reviewed variants, then formats them as instruction-output pairs.

Usage:
    python scripts/mine_clinvar.py \
        --out data/clinvar_exemplars.jsonl \
        --min-stars 2 \
        --max-rows 5000
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import urllib.request
from pathlib import Path
from typing import Optional

CLINVAR_TSV_URL = (
    "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/"
    "variant_summary.txt.gz"
)

# Review status → star tier (NCBI definitions)
REVIEW_STARS = {
    "practice guideline": 4,
    "reviewed by expert panel": 4,
    "criteria provided, multiple submitters, no conflicts": 3,
    "criteria provided, conflicting classifications": 2,
    "criteria provided, single submitter": 1,
    "no assertion criteria provided": 0,
    "no classification provided": 0,
}

SYSTEM_PROMPT = (
    "You are a variant interpretation assistant for research prototyping. "
    "Use only retrieved evidence. Separate retrieved facts from inferred "
    "conclusions. Be conservative under uncertainty."
)


def _stars(review_status: str) -> int:
    return REVIEW_STARS.get(review_status.lower().strip(), 0)


def _classification_label(clinsig: str) -> Optional[str]:
    """Normalise the ClinicalSignificance field to a clean label."""
    s = clinsig.lower()
    if "pathogenic" in s and "likely" not in s and "benign" not in s:
        return "Pathogenic"
    if "likely pathogenic" in s:
        return "Likely Pathogenic"
    if "uncertain" in s or "vus" in s:
        return "Variant of Uncertain Significance"
    if "likely benign" in s:
        return "Likely Benign"
    if "benign" in s and "likely" not in s:
        return "Benign"
    return None


def _build_instruction(row: dict) -> str:
    gene    = row.get("GeneSymbol", "").split(";")[0].strip()
    name    = row.get("Name", "").strip()
    disease = row.get("PhenotypeList", "").split(";")[0].strip()
    chrom   = row.get("Chromosome", "").strip()
    start   = row.get("Start", "").strip()
    stop    = row.get("Stop",  "").strip()
    assembly= row.get("Assembly", "GRCh38").strip()
    mtype   = row.get("Type", "").strip()

    lines = [
        f"Interpret the following variant conservatively for research prototyping.",
        f"",
        f"Variant: {name}",
        f"Gene: {gene}" if gene else "Gene: not specified",
        f"Disease context: {disease}" if disease else "Disease context: not specified",
    ]
    if chrom and start and stop:
        lines += [
            f"",
            f"Genomic coordinates ({assembly}):",
            f"  Chromosome: {chrom}",
            f"  Start:      {start}",
            f"  End:        {stop}",
        ]
    lines += [
        f"",
        f"Variant type: {mtype}",
        f"",
        "Provide a markdown report with sections: Variant Summary, "
        "Retrieved Evidence, Functional Evidence, Literature, "
        "Draft ACMG Assessment, Uncertainties.",
    ]
    return "\n".join(lines)


def _build_output(row: dict, classification: str) -> str:
    gene        = row.get("GeneSymbol", "").split(";")[0].strip()
    name        = row.get("Name", "").strip()
    review      = row.get("ReviewStatus", "").strip()
    clinsig     = row.get("ClinicalSignificance", "").strip()
    disease     = row.get("PhenotypeList", "").replace(";", "; ").strip()
    variation_id= row.get("VariationID", "").strip()
    accession   = row.get("RCVaccession", "").strip()
    last_eval   = row.get("LastEvaluated", "").strip()
    chrom       = row.get("Chromosome", "")
    start       = row.get("Start", "")
    stop        = row.get("Stop", "")
    assembly    = row.get("Assembly", "GRCh38")

    coord_line = (
        f"chr{chrom}:{start}-{stop} ({assembly})"
        if chrom and start and stop else "Not provided"
    )

    return f"""## Variant Summary

**Variant:** {name}
**Gene:** {gene}
**Type:** {row.get('Type', '—')}
**Genomic location:** {coord_line}
**ClinVar accession:** {accession} (Variation ID: {variation_id})

## Retrieved Evidence

**ClinVar classification:** {clinsig}
**Review status:** {review}
**Last evaluated:** {last_eval}
**Disease association:** {disease}

*Note: This exemplar is derived directly from ClinVar submission data. \
No additional literature or functional evidence has been retrieved.*

## Functional Evidence

- **MaveDB:** Not queried for this exemplar.
- **SpliceAI:** Not queried for this exemplar.

## Literature

No literature was retrieved for this exemplar. See ClinVar submission \
{accession} for submitter-cited evidence.

## Draft ACMG Assessment

**Provisional classification:** {classification}
*(Directly from ClinVar expert panel / multi-submitter review)*

**Confidence:** High (≥{_stars(row.get('ReviewStatus',''))} star ClinVar review)

> This assessment reflects the ClinVar curated classification. \
It is a research exemplar only and must not be used for clinical decisions.

## Uncertainties

- No independent functional or literature evidence was retrieved for this exemplar.
- Classification is based solely on ClinVar submission data.
- Review status: {review}
"""


def mine(tsv_gz_path: Optional[Path], min_stars: int, max_rows: int, out_path: Path) -> None:
    if tsv_gz_path and tsv_gz_path.exists():
        print(f"Reading from local file: {tsv_gz_path}")
        fh = gzip.open(tsv_gz_path, "rt", encoding="utf-8", errors="replace")
    else:
        print(f"Downloading ClinVar TSV from {CLINVAR_TSV_URL} ...")
        response = urllib.request.urlopen(CLINVAR_TSV_URL, timeout=120)
        fh = gzip.open(io.BytesIO(response.read()), "rt", encoding="utf-8", errors="replace")

    header = None
    written = 0
    skipped = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with fh, open(out_path, "w") as out_f:
        for line in fh:
            line = line.rstrip("\n")
            if header is None:
                header = line.lstrip("#").split("\t")
                continue

            cols = line.split("\t")
            if len(cols) < len(header):
                continue
            row = dict(zip(header, cols))

            # Filter: assembly, review stars, germline only
            if row.get("Assembly") != "GRCh38":
                continue
            if row.get("Origin", "").lower() not in ("germline", "unknown", ""):
                continue
            stars = _stars(row.get("ReviewStatus", ""))
            if stars < min_stars:
                skipped += 1
                continue

            classification = _classification_label(row.get("ClinicalSignificance", ""))
            if not classification:
                continue

            instruction = _build_instruction(row)
            output      = _build_output(row, classification)

            record = {
                "instruction": instruction,
                "output": output,
                "metadata": {
                    "source": "clinvar",
                    "variation_id": row.get("VariationID"),
                    "gene": row.get("GeneSymbol", "").split(";")[0],
                    "classification": classification,
                    "review_stars": stars,
                    "assembly": row.get("Assembly"),
                },
            }
            out_f.write(json.dumps(record) + "\n")
            written += 1
            if written % 500 == 0:
                print(f"  {written} written, {skipped} skipped ...")
            if written >= max_rows:
                break

    print(f"Done. {written} exemplars written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsv-gz", type=Path, default=None,
        help="Local path to variant_summary.txt.gz (skips download)")
    parser.add_argument("--out", type=Path, default=Path("data/clinvar_exemplars.jsonl"))
    parser.add_argument("--min-stars", type=int, default=2,
        help="Minimum ClinVar review star rating (0-4, default 2)")
    parser.add_argument("--max-rows", type=int, default=5000)
    args = parser.parse_args()
    mine(args.tsv_gz, args.min_stars, args.max_rows, args.out)