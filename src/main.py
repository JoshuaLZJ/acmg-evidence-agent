from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from anthropic import Anthropic
from dotenv import load_dotenv

from .prompts import SYSTEM_PROMPT
from .tools import (
    tool_fetch_clinvar_summary,
    tool_fetch_pubmed_abstracts,
    tool_get_pubtator_annotations,
    tool_map_acmg_rules,
    tool_normalize_variant,
    tool_search_litvar,
    tool_search_pubmed,
    tool_fetch_mavedb_scores,
    tool_fetch_spliceai_scores,
)

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "normalize_variant",
        "description": "Normalize a variant string and generate search aliases. Call at most once per run.",
        "input_schema": {
            "type": "object",
            "properties": {
                "variant": {"type": "string"},
                "gene": {"type": ["string", "null"]},
            },
            "required": ["variant"],
        },
    },
    {
        "name": "fetch_clinvar_summary",
        "description": (
            "Search ClinVar for a variant and return summary JSON. Call at most once per run. "
            "Pass gene + variant type only — do NOT include genomic coordinates in this query."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_litvar",
        "description": "Search LitVar for a normalized variant query. Call at most once per run.",
        "input_schema": {
            "type": "object",
            "properties": {"variant_query": {"type": "string"}},
            "required": ["variant_query"],
        },
    },
    {
        "name": "search_pubmed",
        "description": (
            "Search PubMed for a query and return PMIDs. Call at most once per run. "
            "Use gene name + variant type + clinical terms only. "
            "Do NOT include genomic coordinates — they do not match PubMed records. "
            "If the first search returns sparse results, continue with what you have rather than retrying."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "retmax": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_pubmed_abstracts",
        "description": "Fetch PubMed abstracts for a list of PMIDs. Call at most once per run and use no more than 3 PMIDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pmids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                }
            },
            "required": ["pmids"],
        },
    },
    {
        "name": "get_pubtator_annotations",
        "description": "Fetch PubTator annotations for a list of PMIDs. Call at most once per run and use no more than 3 PMIDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pmids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                }
            },
            "required": ["pmids"],
        },
    },
    {
        "name": "fetch_mavedb_scores",
        "description": (
            "Search MaveDB for deep mutational scanning (DMS) functional scores for a variant. "
            "Call at most once per run. Use gene symbol + HGVS terms. "
            "Supports PS3/BS3 functional evidence. Only useful for missense or small coding variants — skip for SVs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string"},
                "gene":        {"type": ["string", "null"]},
                "hgvs_terms":  {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_spliceai_scores",
        "description": (
            "Retrieve SpliceAI splice-effect delta scores from the Broad Institute public API. "
            "Call at most once per run. Requires a SNV or small indel with known VCF coordinates. "
            "Do NOT call for SVs (duplications, deletions >50bp) or when ref/alt alleles are unknown. "
            "Returns delta scores (DS_AG, DS_AL, DS_DG, DS_DL) and an ACMG PS3/BP4 hint."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "variant":  {"type": "string"},
                "assembly": {"type": "string", "enum": ["GRCh38", "GRCh37"]},
                "chrom":    {"type": ["string", "null"]},
                "pos":      {"type": ["integer", "null"]},
                "ref":      {"type": ["string", "null"]},
                "alt":      {"type": ["string", "null"]},
            },
            "required": ["variant"],
        },
    },
    {
        "name": "map_acmg_rules",
        "description": "Map structured evidence JSON to a draft ACMG assessment. Call only if structured evidence has already been extracted.",
        "input_schema": {
            "type": "object",
            "properties": {"evidence_json": {"type": "string"}},
            "required": ["evidence_json"],
        },
    },
]

TOOL_FUNCTIONS: Dict[str, Any] = {
    "normalize_variant": tool_normalize_variant,
    "fetch_clinvar_summary": tool_fetch_clinvar_summary,
    "search_litvar": tool_search_litvar,
    "search_pubmed": tool_search_pubmed,
    "fetch_pubmed_abstracts": tool_fetch_pubmed_abstracts,
    "get_pubtator_annotations": tool_get_pubtator_annotations,
    "fetch_mavedb_scores":  tool_fetch_mavedb_scores,
    "fetch_spliceai_scores": tool_fetch_spliceai_scores,
    "map_acmg_rules": tool_map_acmg_rules,
}

MAX_ITERATIONS = 12  # raised slightly to account for parallel tool turns
MAX_TOOL_CALLS_PER_NAME: Dict[str, int] = {
    "normalize_variant": 1,
    "fetch_clinvar_summary": 1,
    "search_litvar": 1,
    "search_pubmed": 1,
    "fetch_pubmed_abstracts": 1,
    "get_pubtator_annotations": 1,
    "fetch_mavedb_scores":  1,
    "fetch_spliceai_scores": 1,
    "map_acmg_rules": 1,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def debug_print(enabled: bool, message: str) -> None:
    if enabled:
        print(message)


def make_call_key(name: str, tool_input: Dict[str, Any]) -> Tuple[str, str]:
    return name, json.dumps(tool_input, sort_keys=True)


def _preview(result: str, chars: int = 1500) -> str:
    """Truncated pretty-print of a tool result for debug output."""
    try:
        pretty = json.dumps(json.loads(result), indent=2)
    except Exception:
        pretty = result
    if len(pretty) > chars:
        return pretty[:chars] + "\n... [truncated — {0} total chars]".format(len(pretty))
    return pretty


def _save_debug_log(
    path: Optional[str], messages: List[Dict[str, Any]], debug: bool
) -> None:
    """Method 3 — write full message history to JSON."""
    if not path:
        return
    try:
        with open(path, "w") as f:
            json.dump(messages, f, indent=2, default=str)
        debug_print(debug, "Full message history saved to {0}".format(path))
    except Exception as exc:
        debug_print(debug, "Warning: could not save debug log: {0}".format(exc))


# ---------------------------------------------------------------------------
# Core: execute ALL tool_use blocks in a single response turn
# ---------------------------------------------------------------------------

def _execute_tool_turn(
    response_content: List[Any],
    tool_call_counts: Dict[str, int],
    seen_calls: Set[Tuple[str, str]],
    debug: bool,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Execute every tool_use block in a response and return:
      - tool_results : list of tool_result content blocks (one per tool_use)
      - error        : non-None string if a hard stop condition was triggered

    The Anthropic API requires that EVERY tool_use in a turn has a matching
    tool_result in the very next user message. This function guarantees that.
    """
    tool_results: List[Dict[str, Any]] = []

    for block in response_content:
        if block.type != "tool_use":
            continue

        name = block.name
        tool_input = block.input

        debug_print(debug, "Tool requested: {0}".format(name))
        debug_print(debug, "Tool input:\n{0}".format(
            json.dumps(tool_input, indent=2, sort_keys=True)
        ))

        # --- per-tool call-count guard ---
        current_count = tool_call_counts.get(name, 0)
        allowed_count = MAX_TOOL_CALLS_PER_NAME.get(name, MAX_ITERATIONS)
        if current_count >= allowed_count:
            # Must still return a tool_result for this block or the API will
            # reject the next request.  Return an error payload instead.
            error_msg = (
                "Stopped: tool '{0}' requested more than the allowed number of times. "
                "Inspect debug_messages.json and the tool description."
            ).format(name)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps({"error": error_msg}),
            })
            return tool_results, error_msg

        # --- deduplication guard ---
        call_key = make_call_key(name, tool_input)
        if call_key in seen_calls:
            error_msg = (
                "Stopped: Claude repeated the same tool call for '{0}'. "
                "Check debug_messages.json — the tool output may be too noisy or "
                "the stopping instructions unclear."
            ).format(name)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps({"error": error_msg}),
            })
            return tool_results, error_msg

        seen_calls.add(call_key)
        tool_call_counts[name] = current_count + 1

        # --- execute ---
        try:
            result = TOOL_FUNCTIONS[name](**tool_input)
        except Exception as exc:
            result = json.dumps({"error": str(exc)})

        # Method 1 — preview
        debug_print(debug, "Tool result preview:\n{0}".format(_preview(result)))

        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result if isinstance(result, str) else json.dumps(result),
        })
        debug_print(debug, "Tool result returned to Claude.")

    return tool_results, None


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(
    user_prompt: str,
    model: str = "claude-sonnet-4-5",
    debug: bool = True,
    debug_log: Optional[str] = None,
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return "ANTHROPIC_API_KEY is not set. Add it to your .env file and rerun."

    client = Anthropic(api_key=api_key)
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    seen_calls: Set[Tuple[str, str]] = set()
    tool_call_counts: Dict[str, int] = {}

    for iteration in range(MAX_ITERATIONS):
        debug_print(debug, "\n=== Iteration {0} ===".format(iteration + 1))

        response = client.messages.create(
            model=model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        debug_print(debug, "Stop reason: {0}".format(response.stop_reason))

        # Log any text blocks
        for block in response.content:
            if block.type == "text":
                debug_print(debug, "Assistant text chunk received.")

        # Always append the full assistant turn to history first
        messages.append({"role": "assistant", "content": response.content})

        # --- Final answer (no tool_use blocks) ---
        if response.stop_reason == "end_turn" or not any(
            b.type == "tool_use" for b in response.content
        ):
            final_text = "\n".join(
                b.text for b in response.content if b.type == "text"
            ).strip()
            _save_debug_log(debug_log, messages, debug)
            return final_text or "Claude returned no tool use and no final text."

        # --- Execute ALL tool_use blocks in this turn ---
        tool_results, error = _execute_tool_turn(
            response.content, tool_call_counts, seen_calls, debug
        )

        # Append ALL tool_results in a single user message (API requirement)
        messages.append({"role": "user", "content": tool_results})

        # If a guard triggered, surface the error after the history is valid
        if error:
            _save_debug_log(debug_log, messages, debug)
            return error

    _save_debug_log(debug_log, messages, debug)
    return (
        "Agent stopped after {0} iterations. "
        "Try reducing tool scope or simplifying the prompt. "
        "Check debug_messages.json for the full trace."
    ).format(MAX_ITERATIONS)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def build_prompt(
    variant: str,
    gene: Optional[str],
    disease: Optional[str],
    chrom: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    assembly: str = "GRCh38",
) -> str:
    disease_line = (
        "Disease context: {0}".format(disease)
        if disease
        else "Disease context: not specified — infer from gene/variant context."
    )
    gene_line = (
        "Gene: {0}".format(gene)
        if gene
        else "Gene: not specified — infer from variant string."
    )

    if chrom and start and end:
        coord_block = (
            "Genomic coordinates ({assembly}):\n"
            "  Chromosome: {chrom}\n"
            "  Start:      {start:,}\n"
            "  End:        {end:,}\n"
            "\n"
            "IMPORTANT — coordinate usage rules:\n"
            "  - Pass coordinates to fetch_clinvar_summary for precise SV lookup.\n"
            "  - Do NOT include coordinates in search_pubmed or search_litvar queries.\n"
            "  - For PubMed/LitVar use gene name + variant type + clinical terms only."
        ).format(assembly=assembly, chrom=chrom, start=start, end=end)
    else:
        coord_block = "Genomic coordinates: not provided."

    return """
Interpret the variant conservatively for research prototyping.

Variant: {variant}
{gene_line}
{disease_line}

{coord_block}

Workflow requirements:
1. Call normalize_variant once with the variant string and gene.
2. Call fetch_clinvar_summary once. If coordinates are provided above, build the
   query as: "<GENE> chr<CHROM>:<START>-<END> <variant_type>".
3. Call search_litvar at most once using gene + HGVS or variant type only (no coordinates).
4. Call search_pubmed at most once using gene + variant type + clinical terms (no coordinates).
5. If PubMed returns PMIDs, call fetch_pubmed_abstracts once for up to 3 PMIDs.
6. If useful PMIDs were found, call get_pubtator_annotations once for up to 3 PMIDs.
7. Only call map_acmg_rules if you have already constructed structured evidence JSON.
8. Do not repeat the same tool call with near-identical inputs.
9. If evidence is incomplete, stop and return a partial markdown report.

Return a markdown report with these sections:
- Variant Summary
- Retrieved Evidence
- Literature
- Draft ACMG Assessment
- Uncertainties

In the report, clearly separate retrieved facts from inferred conclusions.
""".strip().format(
        variant=variant,
        gene_line=gene_line,
        disease_line=disease_line,
        coord_block=coord_block,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="ACMG variant interpretation agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Missense variant
  python -m src.main --variant "BRCA1 p.Val1736Ala" --gene BRCA1

  # SV with explicit coordinates (recommended for duplications/deletions)
  python -m src.main \\
      --variant "PALB2 tandem duplication" \\
      --gene PALB2 \\
      --chrom 16 --start 23605575 --end 23615114 \\
      --html report_palb2_dup.html

  # Full run with debug log
  python -m src.main \\
      --variant "BRCA1 p.Val1736Ala" --gene BRCA1 \\
      --disease "hereditary breast and ovarian cancer" \\
      --debug-log debug_messages.json --html report.html
""",
    )

    parser.add_argument("--variant", required=True)
    parser.add_argument("--gene", default=None)
    parser.add_argument("--disease", default=None)
    parser.add_argument("--chrom", default=None,
        help="Chromosome number (no 'chr' prefix), e.g. 16")
    parser.add_argument("--start", type=lambda x: int(x.replace(",", "")), default=None,
        help="GRCh38 start coordinate, e.g. 55029215 or 55,029,215")
    parser.add_argument("--end",   type=lambda x: int(x.replace(",", "")), default=None,
        help="GRCh38 end coordinate, e.g. 55043368 or 55,043,368")
    parser.add_argument("--assembly", default="GRCh38", choices=["GRCh38", "GRCh37"])
    parser.add_argument("--model", default="claude-sonnet-4-5")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--html", default=None)
    parser.add_argument("--debug-log", default=None, dest="debug_log",
        help="Path to save full message history JSON, e.g. debug_messages.json")
    parser.add_argument("--cache", default=".cache/responses.json",
        help="Path to responses cache JSON for report rendering")
    parser.add_argument("--save-md", default=None, dest="save_md",
        help="Save raw markdown output to this file for later re-rendering")

    args = parser.parse_args()

    coords = [args.chrom, args.start, args.end]
    if any(c is not None for c in coords) and not all(c is not None for c in coords):
        parser.error("--chrom, --start, and --end must all be supplied together.")

    prompt = build_prompt(
        variant=args.variant,
        gene=args.gene,
        disease=args.disease,
        chrom=args.chrom,
        start=args.start,
        end=args.end,
        assembly=args.assembly,
    )

    result = run_agent(
        prompt,
        model=args.model,
        debug=not args.quiet,
        debug_log=args.debug_log,
    )

    print(result)

    # Save raw markdown (useful for re-rendering without re-running the agent)
    if args.save_md:
        with open(args.save_md, "w") as f:
            f.write(result)
        print("Markdown saved: {0}".format(args.save_md))

    if args.html:
        from .reporting.render_report import render_html
        html_out = render_html(
            markdown_text=result,
            variant=args.variant,
            gene=args.gene or "",
            disease=args.disease or "",
            chrom=args.chrom,
            start=args.start,
            end=args.end,
            assembly=args.assembly,
            cache_path=args.cache,
        )
        with open(args.html, "w") as f:
            f.write(html_out)
        print("HTML report saved: {0}".format(args.html))
