from typing import List
from ..models import ACMGAssessment, EvidenceItem


def map_acmg_rules(evidence: List[EvidenceItem]) -> ACMGAssessment:
    triggered = []
    rationale = []
    uncertainties = []

    for item in evidence:
        for code in item.candidate_acmg_codes:
            if code not in triggered:
                triggered.append(code)
                rationale.append(f"{code}: {item.statement}")

    if any(code in triggered for code in ["BA1", "BS1", "BS3"]):
        classification = "Likely benign"
    elif any(code in triggered for code in ["PVS1", "PS1", "PS3"]):
        classification = "Likely pathogenic"
    else:
        classification = "Uncertain significance"
        uncertainties.append("Insufficient validated ACMG evidence for a stronger classification.")

    return ACMGAssessment(
        triggered_codes=triggered,
        proposed_classification=classification,
        rationale=rationale,
        uncertainties=uncertainties,
    )
