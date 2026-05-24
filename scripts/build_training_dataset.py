"""
scripts/build_training_dataset.py

Merges mined exemplar files, deduplicates, balances class distribution,
and writes a final train/val split for QLoRA fine-tuning.

Usage:
    python scripts/build_training_dataset.py \
        --inputs data/clinvar_exemplars.jsonl \
                 data/clingen_exemplars.jsonl \
                 data/lovd_exemplars.jsonl \
        --out-train data/train.jsonl \
        --out-val   data/val.jsonl \
        --max-total 3000
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import List, Dict

CLASSIFICATION_KEYS = [
    "Pathogenic",
    "Likely Pathogenic",
    "Variant of Uncertain Significance",
    "Likely Benign",
    "Benign",
]

QUALITY_WEIGHTS = {
    "clingen_erepo": 3.0,   # highest — structured per-criterion evidence
    "clinvar":       2.0,   # expert panel reviewed
    "lovd3":         1.0,   # community curated
}


def load_jsonl(path: Path) -> List[dict]:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def deduplicate(records: List[dict]) -> List[dict]:
    """Remove exact duplicate instructions (same variant queried twice)."""
    seen  = set()
    dedup = []
    for r in records:
        key = r.get("instruction", "")[:300]
        if key not in seen:
            seen.add(key)
            dedup.append(r)
    return dedup


def balance_classes(
    records: List[dict],
    max_total: int,
    target_fractions: Dict[str, float] = None,
) -> List[dict]:
    """
    Sample records to balance class distribution.
    Default target fractions reflect approximate ClinVar distribution
    (weighted toward P/LP since that's where exemplar quality is highest).
    """
    if target_fractions is None:
        # In build_training_dataset.py, replace the target_fractions default:
        target_fractions = {
            "Pathogenic":                        0.25,
            "Likely Pathogenic":                 0.25,
            "Variant of Uncertain Significance": 0.25,
            "Likely Benign":                     0.13,
            "Benign":                            0.12,
        }

    by_class = defaultdict(list)
    for r in records:
        cls = r.get("metadata", {}).get("classification", "Unknown")
        # Normalise variants
        if cls and "uncertain" in cls.lower():
            cls = "Variant of Uncertain Significance"
        by_class[cls].append(r)

    sampled = []
    for cls, frac in target_fractions.items():
        target_n = int(max_total * frac)
        pool     = by_class.get(cls, [])
        # Weight by source quality
        pool.sort(
            key=lambda r: QUALITY_WEIGHTS.get(
                r.get("metadata", {}).get("source", ""), 1.0
            ),
            reverse=True,
        )
        sampled.extend(pool[:target_n])

    random.shuffle(sampled)
    return sampled[:max_total]


def format_for_training(record: dict) -> dict:
    return {
        "instruction": record["instruction"],
        "output":      record["output"],
        "metadata":    record.get("metadata", {}),
    }


def main(
    input_paths: List[Path],
    out_train: Path,
    out_val: Path,
    max_total: int,
    val_frac: float,
    seed: int,
) -> None:
    random.seed(seed)

    all_records = []
    for p in input_paths:
        if not p.exists():
            print(f"Warning: {p} not found, skipping.")
            continue
        recs = load_jsonl(p)
        print(f"  Loaded {len(recs)} records from {p}")
        all_records.extend(recs)

    print(f"Total before dedup: {len(all_records)}")
    all_records = deduplicate(all_records)
    print(f"After dedup: {len(all_records)}")

    # Class distribution before balancing
    counts = Counter(
        r.get("metadata", {}).get("classification", "Unknown")
        for r in all_records
    )
    print("Class distribution before balancing:")
    for cls, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {cls}: {n}")

    balanced = balance_classes(all_records, max_total)
    print(f"After balancing: {len(balanced)}")

    # Source distribution
    sources = Counter(
        r.get("metadata", {}).get("source", "unknown") for r in balanced
    )
    print("Source distribution in final dataset:")
    for src, n in sorted(sources.items(), key=lambda x: -x[1]):
        print(f"  {src}: {n}")

    # Train / val split
    n_val   = max(1, int(len(balanced) * val_frac))
    val     = balanced[:n_val]
    train   = balanced[n_val:]

    out_train.parent.mkdir(parents=True, exist_ok=True)
    with open(out_train, "w") as f:
        for r in train:
            f.write(json.dumps(format_for_training(r)) + "\n")

    with open(out_val, "w") as f:
        for r in val:
            f.write(json.dumps(format_for_training(r)) + "\n")

    print(f"\nTrain: {len(train)} → {out_train}")
    print(f"Val:   {len(val)}   → {out_val}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--out-train", type=Path,
                        default=Path("data/train.jsonl"))
    parser.add_argument("--out-val",   type=Path,
                        default=Path("data/val.jsonl"))
    parser.add_argument("--max-total", type=int, default=3000)
    parser.add_argument("--val-frac",  type=float, default=0.1)
    parser.add_argument("--seed",      type=int, default=42)
    args = parser.parse_args()
    main(
        args.inputs, args.out_train, args.out_val,
        args.max_total, args.val_frac, args.seed,
    )