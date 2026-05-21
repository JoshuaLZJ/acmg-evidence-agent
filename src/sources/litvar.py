from typing import Any, Dict, List
import re
import requests

RELATIONS_URL = "https://www.ncbi.nlm.nih.gov/research/bionlp/litvar/api/v1/public/relations"


def search_litvar(variant_query: str, debug: bool = False) -> List[Dict[str, Any]]:
    rsids = re.findall(r"rs\\d+", variant_query, flags=re.IGNORECASE)
    rsids = [r.lower() for r in rsids]

    if not rsids:
        return [{
            "query": variant_query,
            "note": "LitVar skipped: this endpoint expects accessions/rsIDs. No rsID found in query."
        }]

    payload = {"accessions": rsids[:5]}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        r = requests.post(RELATIONS_URL, json=payload, headers=headers, timeout=60)

        if debug:
            print("LitVar status:", r.status_code)
            print("LitVar payload:", payload)
            print("LitVar response preview:", r.text[:1000])

        r.raise_for_status()

        try:
            data = r.json()
        except Exception:
            return [{
                "query": variant_query,
                "accessions": rsids,
                "error": "Non-JSON response from LitVar",
                "response_preview": r.text[:1000],
            }]

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return [{
            "query": variant_query,
            "accessions": rsids,
            "note": "Unexpected LitVar response type"
        }]

    except Exception as exc:
        return [{
            "query": variant_query,
            "accessions": rsids,
            "error": str(exc),
        }]