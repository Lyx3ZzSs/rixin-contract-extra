"""DSPy-based prompt optimizer for contract field extraction.

This module provides an optional optimization path: instead of manually
crafting the extraction prompt, DSPy can auto-optimize few-shot examples
and prompt instructions from a small set of annotated contract samples.

Usage (offline / one-shot — not called during normal pipeline execution):
    python -m app.extraction.llm.dspy_optimizer --examples data/extraction_examples.jsonl

The optimized prompt is saved as a JSON artifact and loaded by QwenLLMProvider
when ``settings.llm_use_dspy`` is True.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DSPy Signature — defines the input/output contract for the extraction task
# ---------------------------------------------------------------------------

try:
    import dspy
    HAS_DSPY = True
except ImportError:
    HAS_DSPY = False
    dspy = None  # type: ignore


if HAS_DSPY:

    class ContractFieldExtraction(dspy.Signature):
        """Extract structured fields and key clauses from a contract full text.

        Given the full contract text, the detected contract type, and
        a list of field specifications to extract, return an ExtractionResult
        with filled-in fields and identified key clauses.
        """
        full_text: str = dspy.InputField(desc="Full contract text")
        contract_type: str = dspy.InputField(desc="Detected contract type (e.g. service, purchase)")
        field_specs_json: str = dspy.InputField(
            desc="JSON array of {field_key, field_name, description}"
        )
        extraction_json: str = dspy.OutputField(
            desc="JSON object matching the RawExtractionResult schema"
        )


def load_examples(examples_path: str | Path) -> list[dspy.Example]:
    """Load annotated extraction examples from a JSONL file.

    Each line is a JSON object:
    {
      "full_text": "...",
      "contract_type": "service",
      "field_specs": [{"field_key": "party-a-name", "field_name": "甲方名称", ...}],
      "extraction_json": "{...}"  // expected RawExtractionResult JSON
    }
    """
    if not HAS_DSPY:
        raise ImportError("dspy-ai is not installed. Run: pip install dspy-ai")

    path = Path(examples_path)
    if not path.exists():
        logger.warning("DSPy examples file not found: %s", path)
        return []

    examples: list[dspy.Example] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSON line in %s", path)
                continue
            examples.append(
                dspy.Example(
                    full_text=data["full_text"],
                    contract_type=data.get("contract_type", "unknown"),
                    field_specs_json=json.dumps(
                        data.get("field_specs", []), ensure_ascii=False
                    ),
                    extraction_json=json.dumps(
                        data.get("expected_output", {}), ensure_ascii=False
                    ),
                ).with_inputs("full_text", "contract_type", "field_specs_json")
            )
    logger.info("Loaded %d DSPy examples from %s", len(examples), path)
    return examples


def optimize_prompt(
    examples_path: str | Path,
    model_name: str = "openai/gpt-4o-mini",
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run DSPy optimization on the provided examples.

    Uses BootstrapFewShot to select the best few-shot examples,
    then saves the optimized program state.

    Returns a dict with optimization stats.
    """
    if not HAS_DSPY:
        raise ImportError("dspy-ai is not installed")

    examples = load_examples(examples_path)
    if not examples:
        return {"status": "skipped", "reason": "no_examples"}

    # Configure DSPy with the target LM
    lm = dspy.LM(model_name)
    dspy.configure(lm=lm)

    # Build the extraction program
    program = dspy.ChainOfThought(ContractFieldExtraction)

    # Optimize with BootstrapFewShot
    optimizer = dspy.BootstrapFewShot(
        metric=None,  # use default exact-match on extraction_json
        max_bootstrapped_demos=4,
        max_labeled_demos=8,
        max_rounds=3,
    )
    optimized = optimizer.compile(program, trainset=examples)

    # Save the optimized state
    if output_path is None:
        output_path = Path(examples_path).parent / "dspy_optimized.json"
    optimized.save(str(output_path))

    return {
        "status": "optimized",
        "examples_used": len(examples),
        "output_path": str(output_path),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Optimize contract extraction prompt with DSPy")
    parser.add_argument(
        "--examples", required=True,
        help="Path to JSONL file with annotated examples",
    )
    parser.add_argument(
        "--model", default="openai/gpt-4o-mini",
        help="LLM model for optimization (default: openai/gpt-4o-mini)",
    )
    parser.add_argument(
        "--output",
        help="Path to save optimized prompt (default: <examples_dir>/dspy_optimized.json)",
    )
    args = parser.parse_args()

    result = optimize_prompt(
        examples_path=args.examples,
        model_name=args.model,
        output_path=args.output,
    )
    print(json.dumps(result, indent=2))
