"""
scripts/mine_lovd.py

Fetches public variants from the Global Variome shared LOVD3 database.

Correct API endpoint (confirmed from LOVD3 docs):
  https://databases.lovd.nl/shared/api/rest.php/variants/{GENE}?format=application/json

Usage:
    python scripts/mine_lovd.py \
        --genes BRCA1 BRCA2 TP53 PTEN MLH1 MSH2 \
        --out data/lovd_exemplars.jsonl \
        --max-per-gene 300
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

LOVD_BASE     = "https://databases.lovd.nl/shared/api/rest.php"
PAGE_SIZE     = 200
REQUEST_DELAY = 0.5

EFFECT_MAP = {
    "5/5": "Pathogenic",
    "4/4": "Likely Pathogenic",
    "3/3": "Variant of Uncertain Significance",
    "2/2": "Likely Benign",
    "1/1": "Benign",
    "5":   "Pathogenic",
    "4":   "Likely Pathogenic",
    "3":   "Variant of Uncertain Significance",
    "2":   "Likely Benign",
    "1":   "Benign",
    "pathogenic":              "Pathogenic",
    "likely pathogenic":       "Likely Pathogenic",
    "vus":                     "Variant of Uncertain Significance",
    "uncertain significance":  "Variant of Uncertain Significance",
    "likely benign":           "Likely Benign",
    "benign":                  "Benign",
}


def _get(url: str, params: Dict[str, Any] = {}) -> Any:
    time.sleep(REQUEST_DELAY)
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _effect_label(variant: dict) -> Optional[str]:
    for key in ("effect_reported", "effect_concluded",
                "VariantOnGenome/ClinicalClassification",
                "Pathogenicity"):
        val = str(variant.get(key, "")).strip()
        if val in EFFECT_MAP:
            return EFFECT_MAP[val]
        if val.lower() in EFFECT_MAP:
            return EFFECT_MAP[val.lower()]
    return None


def _pick_field(variant: dict, *keys: str, default: str = "—") -> str:
    for k in keys:
        v = variant.get(k, "")
        if v and str(v).strip() not in ("", "—", "NULL", "None"):
            return str(v).strip()
    return default


def _build_instruction(gene: str, variant: dict) -> str:
    dna     = _pick_field(variant,
                          "VariantOnTranscript/DNA",
                          "Variant/DNA",
                          "DNA",
                          default="")
    protein = _pick_field(variant,
                          "VariantOnTranscript/Protein",
                          "Variant/Protein",
                          "Protein",
                          default="")
    hgvs    = f"{gene}:{dna}" if dna else gene
    if protein and protein != "—":
        hgvs += f" ({protein})"

    return (
        f"Interpret the following variant conservatively for research prototyping.\n\n"
        f"Variant: {hgvs}\n"
        f"Gene: {gene}\n"
        f"Disease context: not specified — infer from gene context.\n\n"
        "Provide a markdown report with sections: Variant Summary, "
        "Retrieved Evidence, Functional Evidence, Literature, "
        "Draft ACMG Assessment, Uncertainties."
    )


def _build_output(gene: str, variant: dict, classification: str) -> str:
    dna       = _pick_field(variant,
                            "VariantOnTranscript/DNA", "Variant/DNA", "DNA")
    protein   = _pick_field(variant,
                            "VariantOnTranscript/Protein", "Variant/Protein", "Protein")
    cdna      = _pick_field(variant,
                            "VariantOnTranscript/RNA", "Variant/RNA", "RNA")
    dbid      = _pick_field(variant, "id", "DBID", "VariantOnGenome/DBID")
    effect_r  = _pick_field(variant, "effect_reported",
                            "VariantOnGenome/ClinicalClassification")
    effect_c  = _pick_field(variant, "effect_concluded")
    position  = _pick_field(variant,
                            "VariantOnGenome/DNA",
                            "Variant/VariantOnGenome/DNA")
    lovd_url  = f"https://databases.lovd.nl/shared/variants/{dbid}"

    return f"""## Variant Summary

**Variant:** {gene}:{dna}
**Protein change:** {protein}
**cDNA / RNA change:** {cdna}
**Genomic position:** {position}
**Gene:** {gene}
**LOVD ID:** {dbid}
**Source:** Global Variome shared LOVD ({lovd_url})

## Retrieved Evidence

**LOVD reported effect:** {effect_r}
**LOVD concluded effect:** {effect_c}
**DNA change:** {dna}
**Protein change:** {protein}

*Note: This exemplar is derived from LOVD3 community curation. \
Evidence quality varies by submitter and gene-specific curator.*

## Functional Evidence

- **MaveDB:** Not queried for this exemplar.
- **SpliceAI:** Not queried for this exemplar.

## Literature

- No publications automatically extracted for this LOVD record.
- Refer to LOVD entry {lovd_url} for submitter-cited references.

## Draft ACMG Assessment

**Provisional classification:** {classification}
*(Derived from LOVD community curation — treat as preliminary)*

> Research exemplar only. Verify independently before any clinical use.

## Uncertainties

- Classification derived from community LOVD curation; expert panel review not confirmed.
- No functional or literature evidence was retrieved for this exemplar.
- Effect fields may reflect only reported (not independently concluded) pathogenicity.
"""


def mine_gene(gene: str, max_per_gene: int, out_f) -> int:
    written  = 0
    position = 0     # LOVD3 uses 'position' for offset, not 'offset'

    while written < max_per_gene:
        params = {
            "format":   "application/json",
            "position": position,
            "limit":    PAGE_SIZE,
        }
        try:
            data = _get(f"{LOVD_BASE}/variants/{gene}", params)
        except requests.HTTPError as exc:
            print(f"  HTTP error for {gene}: {exc}")
            break
        except Exception as exc:
            print(f"  Error for {gene}: {exc}")
            break

        # Response is a list of variant dicts
        variants = data if isinstance(data, list) else data.get("data", [])
        if not variants:
            break

        for v in variants:
            classification = _effect_label(v)
            if not classification:
                continue

            # Skip entries with no DNA change — not useful for training
            dna = _pick_field(v, "VariantOnTranscript/DNA", "Variant/DNA", "DNA", default="")
            if not dna:
                continue

            instruction = _build_instruction(gene, v)
            output      = _build_output(gene, v, classification)
            record      = {
                "instruction": instruction,
                "output":      output,
                "metadata": {
                    "source":         "lovd3",
                    "gene":           gene,
                    "dbid":           _pick_field(v, "id", "DBID"),
                    "dna":            dna,
                    "protein":        _pick_field(v,
                                          "VariantOnTranscript/Protein",
                                          "Variant/Protein", "Protein"),
                    "classification": classification,
                },
            }
            out_f.write(json.dumps(record) + "\n")
            written += 1
            if written >= max_per_gene:
                break

        # LOVD3 pagination: advance by number of records returned
        if len(variants) < PAGE_SIZE:
            break           # last page
        position += len(variants)

    return written


def mine(genes: List[str], max_per_gene: int, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(out_path, "w") as out_f:
        for gene in genes:
            print(f"Mining LOVD for {gene} ...", end=" ", flush=True)
            n = mine_gene(gene, max_per_gene, out_f)
            print(f"{n} variants written")
            total += n
    print(f"\nDone. {total} total exemplars written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--genes", nargs="+",
        default=["BRCA1","BRCA2","TP53","PTEN","MLH1","MSH2","APC","CDH1"])
    parser.add_argument("--out", type=Path,
                        default=Path("data/lovd_exemplars.jsonl"))
    parser.add_argument("--max-per-gene", type=int, default=300)
    args = parser.parse_args()
    mine(args.genes, args.max_per_gene, args.out)