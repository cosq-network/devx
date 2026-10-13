"""Per-model token pricing and cost estimation.

Prices are expressed in **USD per 1,000,000 tokens** and are best-effort list
prices intended for budgeting and visibility, not billing. Identifiers are
matched by substring (longest match wins) so versioned model names such as
``gpt-4o-2024-08-06`` resolve to their family. Unknown models return ``None``
from :func:`estimate_cost` so callers can degrade gracefully instead of
inventing a number.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    """Input/output price in USD per 1,000,000 tokens."""

    input_per_million: float
    output_per_million: float


# Best-effort public list prices (USD per 1M tokens).
PRICING: dict[str, ModelPrice] = {
    "gpt-4o-mini": ModelPrice(0.15, 0.60),
    "gpt-4o": ModelPrice(2.50, 10.00),
    "gpt-4.1-mini": ModelPrice(0.40, 1.60),
    "gpt-4.1-nano": ModelPrice(0.10, 0.40),
    "gpt-4.1": ModelPrice(2.00, 8.00),
    "gpt-4-turbo": ModelPrice(10.00, 30.00),
    "o4-mini": ModelPrice(1.10, 4.40),
    "o3-mini": ModelPrice(1.10, 4.40),
    "o3": ModelPrice(2.00, 8.00),
    "claude-3-5-sonnet": ModelPrice(3.00, 15.00),
    "claude-3-5-haiku": ModelPrice(0.80, 4.00),
    "claude-3-7-sonnet": ModelPrice(3.00, 15.00),
    "claude-3.7-sonnet": ModelPrice(3.00, 15.00),
    "claude-3-opus": ModelPrice(15.00, 75.00),
    "claude-sonnet-4": ModelPrice(3.00, 15.00),
    "claude-opus-4": ModelPrice(15.00, 75.00),
    "claude-haiku": ModelPrice(0.80, 4.00),
    "qwen-2.5-coder": ModelPrice(0.20, 0.60),
    # Local (Ollama) models cost nothing to run.
    "qwen2.5-coder": ModelPrice(0.0, 0.0),
    "llama3.1:8b": ModelPrice(0.0, 0.0),
    "gemini-1.5-flash": ModelPrice(0.075, 0.30),
    "gemini-1.5-pro": ModelPrice(1.25, 5.00),
    "gemini-2.0-flash": ModelPrice(0.10, 0.40),
    "gemini-2.5-flash": ModelPrice(0.30, 2.50),
    "gemini-2.5-pro": ModelPrice(1.25, 10.00),
    "mistral-large": ModelPrice(2.00, 6.00),
    "deepseek-chat": ModelPrice(0.27, 1.10),
}

# Markers that identify a free (zero-cost) identifier, e.g. OpenRouter's
# ``openrouter/free`` router or the ``:free`` suffixes. ``-free`` is
# intentionally NOT matched: it is too loose and can hit unrelated names.
FREE_MARKERS = (":free", "openrouter/free")


def price_for(model: str) -> ModelPrice | None:
    """Return the configured price for ``model``, or ``None`` if unknown."""
    if not model:
        return None
    lower = model.lower()
    if any(marker in lower for marker in FREE_MARKERS):
        return ModelPrice(0.0, 0.0)
    for name in sorted(PRICING, key=len, reverse=True):
        if name in lower:
            return PRICING[name]
    return None


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimate USD cost for a single call, or ``None`` when unpriced."""
    price = price_for(model)
    if price is None:
        return None
    return (
        prompt_tokens / 1_000_000 * price.input_per_million
        + completion_tokens / 1_000_000 * price.output_per_million
    )


def format_cost(amount: float | None) -> str:
    """Format a USD amount for display; ``None`` renders as ``n/a``."""
    if amount is None:
        return "n/a"
    if amount == 0:
        return "$0.00"
    if abs(amount) < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def render_usage_report(report: dict, *, title: str = "Token usage") -> str:
    """Render an aggregated usage report (from ``SessionStore.aggregate_usage``)
    as a portable Markdown receipt."""
    lines = [f"# {title}", ""]
    lines.append(f"- Calls: **{report['calls']}**")
    lines.append(
        f"- Tokens: prompt **{report['prompt_tokens']}** + "
        f"completion **{report['completion_tokens']}** = **{report['total_tokens']}**"
    )
    lines.append(f"- Estimated cost: **{format_cost(report['estimated_cost_usd'])}**")
    if report.get("unpriced_models"):
        lines.append(f"- Unpriced models (cost excluded): {', '.join(report['unpriced_models'])}")
    if report.get("by_model"):
        lines += ["", "| provider | model | calls | prompt | completion | total | est. cost |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        for item in report["by_model"]:
            lines.append(
                f"| {item['provider']} | {item['model']} | {item['calls']} | "
                f"{item['prompt_tokens']} | {item['completion_tokens']} | "
                f"{item['total_tokens']} | {format_cost(item['estimated_cost_usd'])} |"
            )
    lines.append("")
    return "\n".join(lines)
