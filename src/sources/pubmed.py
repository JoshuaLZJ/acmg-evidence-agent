from typing import Any, Dict, List, Optional
import re
import requests

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def search_pubmed(query: str, retmax: int = 5) -> List[str]:
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": retmax,
    }
    r = requests.get(f"{BASE}/esearch.fcgi", params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("esearchresult", {}).get("idlist", [])


def _extract_text(xml: str, tag: str) -> Optional[str]:
    pattern = f"<{tag}[^>]*>(.*?)</{tag}>"
    match = re.search(pattern, xml, re.DOTALL)
    if match:
        text = re.sub(r"<[^>]+>", " ", match.group(1))
        return " ".join(text.split()).strip()
    return None


def _parse_article_xml(pmid: str, xml: str) -> Dict[str, Any]:
    pattern = rf"<PubmedArticle>.*?<PMID[^>]*>{pmid}</PMID>.*?</PubmedArticle>"
    match = re.search(pattern, xml, re.DOTALL)
    if not match:
        return {"pmid": pmid, "error": "Article block not found in XML"}

    article_xml = match.group(0)
    title = _extract_text(article_xml, "ArticleTitle")
    abstract = _extract_text(article_xml, "AbstractText")
    journal = _extract_text(article_xml, "Title")
    year_match = re.search(r"<Year>(\d{4})</Year>", article_xml)
    year = year_match.group(1) if year_match else None

    return {
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "year": year,
    }


def fetch_pubmed_abstracts(pmids: List[str]) -> List[Dict[str, Any]]:
    if not pmids:
        return []

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "rettype": "abstract",
    }
    r = requests.get(f"{BASE}/efetch.fcgi", params=params, timeout=30)
    r.raise_for_status()
    xml = r.text

    return [_parse_article_xml(pmid, xml) for pmid in pmids]