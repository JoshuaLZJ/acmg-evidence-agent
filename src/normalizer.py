import re
from .models import NormalizedVariant
from typing import Optional


def normalize_variant(variant: str, gene: Optional[str] = None) -> NormalizedVariant:
    text = variant.strip()
    aliases = [text]

    protein_match = re.search(r"p\.([A-Za-z]{3}\d+[A-Za-z]{3})", text)
    cdna_match = re.search(r"c\.\d+[ACGT>delinsdup_+-]+", text)
    rsid_match = re.search(r"rs\d+", text, re.IGNORECASE)

    canonical = text
    if rsid_match:
        canonical = rsid_match.group(0).lower()
    elif protein_match:
        canonical = f"p.{protein_match.group(1)}"
    elif cdna_match:
        canonical = cdna_match.group(0)

    if gene and canonical not in aliases:
        aliases.append(f"{gene} {canonical}")
    if gene and text not in aliases:
        aliases.append(f"{gene} {text}")

    return NormalizedVariant(
        original_input=variant,
        gene=gene,
        canonical_variant=canonical,
        aliases=list(dict.fromkeys(aliases)),
    )
