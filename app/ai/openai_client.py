"""OpenAI explanation helper for structured myTrade snapshots."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


ANALYST_INSTRUCTIONS = """
You are the explanation layer inside a personal market-research application.
Use only the structured market snapshot supplied by the application.
Explain supporting and conflicting evidence concisely.
Do not invent prices, indicators, news, or probabilities.
Do not place orders, claim guaranteed outcomes, or override risk controls.
When information is insufficient, say what is missing.
""".strip()


class OpenAIMarketAnalyst:
    """Optional natural-language explanation layer.

    The OpenAI SDK reads OPENAI_API_KEY from the environment automatically.
    """

    def __init__(self, model: str = "gpt-5") -> None:
        self._client = OpenAI()
        self._model = model

    def explain_snapshot(self, snapshot: dict[str, Any]) -> str:
        response = self._client.responses.create(
            model=self._model,
            instructions=ANALYST_INSTRUCTIONS,
            input=(
                "Explain this myTrade market snapshot. Separate trend, momentum, "
                "risk/contradictions, and confidence considerations.\n\n"
                + json.dumps(snapshot, indent=2, default=str)
            ),
            store=False,
        )
        return response.output_text.strip()
