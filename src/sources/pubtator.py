from typing import List
import requests


def get_pubtator_annotations(pmids: List[str]) -> dict:
    if not pmids:
        return {}
    url = "https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson"
    params = {"pmids": ",".join(pmids)}
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"raw_text": r.text}
