"""Token usage tracker for LLM cost monitoring (CLI-friendly).

Provides a LangChain ``BaseCallbackHandler`` that captures token usage
from every LLM call, accumulates per-model and total statistics, and
renders a terminal-formatted cost summary — no GUI required.

Usage::

    from tradingagents.utils.token_tracker import TokenTracker

    tracker = TokenTracker()
    ta = TradingAgentsGraph(callbacks=[tracker])
    ta.propagate("000933.SZ", "2026-06-14")

    print(tracker.summary())
    # ============================================================
    # 💰 TOKEN COST SUMMARY
    # ============================================================
    #   deepseek-v4-flash      6 calls   45K in / 18K out  $0.0562
    #   deepseek-v4-pro        2 calls   23K in / 11K out  $0.1340
    #   -----------------------------------------------------------
    #   TOTAL                   8 calls   68K in / 29K out  $0.1902
    # ============================================================

Per-model pricing is looked up from ``MODEL_PRICING``; unrecognised
models are tracked but shown with ``$?`` cost. Pricing data is
best-effort — always verify against your provider's current rates.

Thread-safe: all tracking methods acquire ``_lock`` so a single
``TokenTracker`` instance can be shared across parallel workers.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Dict, List, Optional

from langchain_core.callbacks import BaseCallbackHandler

# ── Model pricing (per 1M tokens: input, output) ──────────────────────────
# Best-effort snapshot. Verify against provider docs for current rates.
MODEL_PRICING: Dict[str, tuple[float, float]] = {
    # DeepSeek (deepseek.com/api-docs)
    "deepseek-v4-pro":          (2.00, 8.00),
    "deepseek-v4-flash":        (0.50, 2.00),
    "deepseek-v3":              (0.27, 1.10),
    "deepseek-v3-0324":         (0.27, 1.10),
    "deepseek-r1":              (0.55, 2.19),
    "deepseek-r1-0528":         (0.55, 2.19),
    "deepseek-chat":            (0.27, 1.10),
    "deepseek-reasoner":        (0.55, 2.19),
    # OpenAI (platform.openai.com/docs/pricing)
    "gpt-5.5":                  (2.50, 10.00),
    "gpt-5.4-mini":             (0.75,  3.00),
    "gpt-5.1":                  (2.00,  8.00),
    "gpt-5.1-mini":             (0.50,  2.00),
    "gpt-4.1":                  (2.00,  8.00),
    "gpt-4.1-mini":             (0.40,  1.60),
    "gpt-4.1-nano":             (0.10,  0.40),
    "gpt-4o":                   (2.50, 10.00),
    "gpt-4o-mini":              (0.15,  0.60),
    # Anthropic (anthropic.com/pricing)
    "claude-fable-5":           (3.00, 15.00),
    "claude-opus-4-8":          (15.00,75.00),
    "claude-sonnet-4-6":        (3.00, 15.00),
    "claude-haiku-4-5":         (0.80,  4.00),
    # Google
    "gemini-2.5-pro":           (1.25, 10.00),
    "gemini-2.5-flash":         (0.15,  0.60),
    # xAI
    "grok-4":                   (3.00, 15.00),
    "grok-4-mini":              (0.80,  4.00),
    # Qwen (百炼)
    "qwen3-235b-a22b":          (0.55,  2.19),
    "qwen3-235b-a22b-thinking":(0.55,  2.19),
    "qwen3-32b":                (0.14,  0.55),
    # GLM (智谱)
    "glm-4.5":                  (0.55,  2.19),
    "glm-4.5-flash":            (0.14,  0.55),
}


def _extract_usage(response: Any) -> tuple[int, int, str]:
    """Extract (input_tokens, output_tokens, model_name) from an LLM response.

    Handles the three most common LangChain response shapes:
    1. ``response.llm_output["token_usage"]`` — OpenAI-compatible APIs
    2. ``response.generations[0][0].message.usage_metadata`` — Anthropic-style
    3. ``response.generations[0][0].message.response_metadata`` — fallback
    """
    input_t = 0
    output_t = 0

    # Path 1: llm_output (most common for OpenAI-compatible providers)
    llm_out = getattr(response, "llm_output", None) or {}
    if isinstance(llm_out, dict):
        tu = llm_out.get("token_usage", {})
        if isinstance(tu, dict):
            input_t = tu.get("prompt_tokens", 0) or tu.get("input_tokens", 0)
            output_t = tu.get("completion_tokens", 0) or tu.get("output_tokens", 0)

    # Path 2: usage_metadata on the message (Anthropic, newer LangChain)
    if input_t == 0 and output_t == 0:
        for gen in getattr(response, "generations", [[]])[0]:
            msg = getattr(gen, "message", None)
            if msg is None:
                continue
            um = getattr(msg, "usage_metadata", None) or {}
            if isinstance(um, dict):
                input_t = (
                    um.get("input_tokens", 0)
                    or um.get("prompt_tokens", 0)
                    or um.get("input_token_count", 0)
                )
                output_t = (
                    um.get("output_tokens", 0)
                    or um.get("completion_tokens", 0)
                    or um.get("output_token_count", 0)
                )
                if input_t or output_t:
                    break

    # Model name
    model = "unknown"
    if isinstance(llm_out, dict):
        model = llm_out.get("model_name", model)
    if model == "unknown":
        for gen in getattr(response, "generations", [[]])[0]:
            msg = getattr(gen, "message", None)
            if msg:
                rm = getattr(msg, "response_metadata", None) or {}
                if isinstance(rm, dict) and rm.get("model_name"):
                    model = rm["model_name"]
                    break

    return input_t, output_t, model


class TokenTracker(BaseCallbackHandler):
    """LangChain callback that tracks per-call token usage and cost.

    Thread-safe — can be shared across parallel analysis workers.
    Call ``summary()`` at the end of a run to print totals.
    """

    def __init__(self):
        super().__init__()
        self.calls: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._total_input = 0
        self._total_output = 0
        self._total_cost = 0.0
        self._by_model: Dict[str, dict] = defaultdict(
            lambda: {"calls": 0, "input": 0, "output": 0, "cost": 0.0}
        )

    # ── LangChain callback interface ───────────────────────────────────

    def on_llm_end(self, response, **kwargs) -> None:
        """Capture token usage after each LLM invocation completes."""
        input_t, output_t, model = _extract_usage(response)

        price = MODEL_PRICING.get(model, (0.0, 0.0))
        cost = (input_t / 1_000_000) * price[0] + (output_t / 1_000_000) * price[1]

        short_model = model
        # Strip provider prefixes for cleaner display
        for prefix in ("deepseek/", "openai/", "anthropic/", "google/", "xai/"):
            if short_model.startswith(prefix):
                short_model = short_model[len(prefix):]
                break

        with self._lock:
            self.calls.append({
                "model": short_model,
                "input_tokens": input_t,
                "output_tokens": output_t,
                "cost": cost,
            })
            self._total_input += input_t
            self._total_output += output_t
            self._total_cost += cost
            self._by_model[short_model]["calls"] += 1
            self._by_model[short_model]["input"] += input_t
            self._by_model[short_model]["output"] += output_t
            self._by_model[short_model]["cost"] += cost

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def total_calls(self) -> int:
        return len(self.calls)

    @property
    def total_input_tokens(self) -> int:
        return self._total_input

    @property
    def total_output_tokens(self) -> int:
        return self._total_output

    @property
    def total_cost(self) -> float:
        return self._total_cost

    # ── Formatting ─────────────────────────────────────────────────────

    def summary(self) -> str:
        """Return a terminal-formatted cost summary block."""
        if not self.calls:
            return "💰 No LLM calls tracked."

        lines = [
            "=" * 60,
            "💰 TOKEN COST SUMMARY",
            "=" * 60,
        ]

        model_names = sorted(self._by_model.keys())
        for model in model_names:
            m = self._by_model[model]
            priced = model in MODEL_PRICING or any(
                model.startswith(p) for p in MODEL_PRICING
            )
            cost_str = f"${m['cost']:.4f}" if priced else f"${m['cost']:.4f}?"
            lines.append(
                f"  {model:30s} {m['calls']:>4d} calls  "
                f"{self._fmt_tokens(m['input'])} in / {self._fmt_tokens(m['output'])} out  "
                f"{cost_str}"
            )

        if len(model_names) > 1:
            lines.append("  " + "-" * 56)
            lines.append(
                f"  {'TOTAL':30s} {self.total_calls:>4d} calls  "
                f"{self._fmt_tokens(self._total_input)} in / "
                f"{self._fmt_tokens(self._total_output)} out  "
                f"${self._total_cost:.4f}"
            )

        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def _fmt_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.0f}K"
        return str(n)

    def one_line(self) -> str:
        """Return a compact one-line summary for per-stock progress logs."""
        if not self.calls:
            return "💰 (no LLM calls)"
        return (
            f"💰 {self.total_calls}calls / "
            f"{self._fmt_tokens(self._total_input)}in / "
            f"{self._fmt_tokens(self._total_output)}out / "
            f"${self._total_cost:.4f}"
        )

    def reset(self) -> None:
        """Reset all counters (for reuse across runs)."""
        with self._lock:
            self.calls.clear()
            self._total_input = 0
            self._total_output = 0
            self._total_cost = 0.0
            self._by_model.clear()
