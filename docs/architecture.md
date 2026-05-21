# Architecture

## Overview

ACMG Variant Interpretation Assistant is a research prototype for retrieving variant-related evidence, summarising literature, and producing structured ACMG-style interpretation reports. It is designed as an evidence assistance tool rather than an autonomous clinical decision system.

## Goals

- Retrieve variant-relevant evidence from public sources
- Summarise evidence in a consistent ACMG-style format
- Generate a readable HTML report for review
- Support caching and rerendering without repeated API calls

## Non-goals

- Clinical-grade classification
- Automated final diagnosis
- Replacement of expert variant curation

## Core components

### 1. CLI entrypoint

`src/main.py`

Responsible for:
- parsing user arguments,
- orchestrating the pipeline,
- invoking retrieval and report generation.

### 2. Retrieval and tool layer

`src/tools.py`

Responsible for:
- querying external resources,
- normalising returned data,
- passing evidence into the summarisation workflow.

### 3. Cache layer

`src/cache.py`

Responsible for:
- storing API responses locally,
- reducing repeated external calls,
- enabling rerendering from cached outputs.

### 4. Report rendering

`src/reporting/render_report.py`

Responsible for:
- converting markdown-like output into HTML,
- rendering ACMG evidence sections,
- adding genomic context visualisation and reference links.

## Data flow

1. User provides a variant, gene, and optional genomic coordinates.
2. The CLI sends requests to retrieval utilities.
3. Retrieved evidence is normalised and optionally cached.
4. The summarisation stage produces structured markdown output.
5. The report renderer converts the result into a styled HTML report.
6. The final report is reviewed by a human user.

## External dependencies

The system may depend on:
- ClinVar
- PubMed
- local cache files
- optional LLM APIs
- IGV.js for interactive genome-context rendering

## Outputs

Primary outputs:
- structured text interpretation
- HTML report
- cached API responses for reuse

## Limitations

- Research prototype only
- Not validated for clinical use
- Output quality depends on source availability and prompt behavior
- ACMG reasoning is only partially automated and requires human review

## Future work

- Local model support through Ollama
- Fine-tuned in-house evidence summarisation model
- Expanded structured output schema
- Benchmarking against expert-curated variant sets