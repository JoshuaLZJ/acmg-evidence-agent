# LLM-driven ACMG Variant Interpretation Agent

A research prototype for ACMG-guided variant evidence retrieval, literature synthesis, and HTML report generation.

## Features
- Python wrappers for ClinVar, PubMed, LitVar, and PubTator
- A basic ACMG rule-mapping scaffold
- Typed data models with Pydantic
- Anthropic tool-calling pipeline for evidence synthesis
- Cache-backed response storage for faster reruns
- HTML report generation with structured evidence sections

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set `ANTHROPIC_API_KEY` in `.env`.

## Run
```bash
python -m src.main \
  --variant "BRCA1 p.Val1736Ala" \
  --gene BRCA1 \
  --disease "hereditary breast and ovarian cancer" \
  --cache .cache/responses.json \
  --debug-log debug_messages.json \
  --html report_brca1_val1736ala.html
```

## Example output
The pipeline produces:
- a structured text interpretation
- cached API responses for reuse
- an HTML report for review

## Notes
- This is a research prototype, not a clinical decision tool.
- Outputs require independent expert review before any clinical interpretation.
- Some source-specific parsing may need adjustment depending on upstream API schema changes.

## Repository structure
```text
src/
  main.py
  tools.py
  cache.py
  reporting/
    render_report.py
docs/
  architecture.md
```

## Architecture
A short overview of the system design, module layout, and data flow is available in [docs/architecture.md](docs/architecture.md).


## Future work
- Fine-tune open LLMs on variant interpretation exemplars using parameter-efficient methods such as QLoRA, with the goal of supporting an in-house model for evidence synthesis and reducing reliance on external hosted models.
- Evaluate local deployment pathways through Ollama for private, institution-controlled inference.
- Expand structured output generation and benchmarking against expert-curated ACMG interpretations.
