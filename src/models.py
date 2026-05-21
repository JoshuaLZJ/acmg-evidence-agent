from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VariantInput(BaseModel):
    variant: str
    gene: Optional[str] = None
    disease: Optional[str] = None


class NormalizedVariant(BaseModel):
    original_input: str
    gene: Optional[str] = None
    canonical_variant: str
    aliases: List[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    source: str
    evidence_type: str
    statement: str
    candidate_acmg_codes: List[str] = Field(default_factory=list)
    strength: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PaperRecord(BaseModel):
    pmid: str
    title: Optional[str] = None
    abstract: Optional[str] = None
    journal: Optional[str] = None
    year: Optional[str] = None
    relevance_score: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ACMGAssessment(BaseModel):
    triggered_codes: List[str] = Field(default_factory=list)
    proposed_classification: str = "Uncertain significance"
    rationale: List[str] = Field(default_factory=list)
    uncertainties: List[str] = Field(default_factory=list)
