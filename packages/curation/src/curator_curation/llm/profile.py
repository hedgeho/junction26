"""Turn freeform feedback into structured, editable preferences (the doorss pattern).

`search_term` preferences also drive full-text candidate retrieval, so the
description "I want posts about rust compilers" becomes cheap SQL recall
before any per-post LLM call happens.
"""

from typing import Literal

import anthropic
from pydantic import BaseModel

from ..config import settings

Category = Literal["rule", "topic", "author_boost", "author_block", "search_term"]


class NewPreference(BaseModel):
    category: Category
    summary: str
    content: str


class ProfileUpdate(BaseModel):
    add: list[NewPreference]
    deactivate_ids: list[int]


PROMPT = """\
You maintain a reader's taste profile for a personal Bluesky feed. Turn their feedback \
into preference changes.

Categories:
- rule: standing curation instruction ("one post per day from outside my bubble")
- topic: a topic they want more or less of; say which in content
- author_boost / author_block: a specific account (handle or DID in summary)
- search_term: a short full-text query used to find candidate posts; add 3-10 of these \
when the feedback introduces a new interest, covering synonyms and jargon a post would \
actually contain. Use plain words, no operators.

Don't duplicate existing preferences. If the feedback reverses or replaces one, put its id \
in deactivate_ids. Return empty lists if nothing is actionable.

<current_preferences>
{existing}
</current_preferences>

<feedback>
{feedback}
</feedback>"""


def interpret_feedback(client: anthropic.Anthropic, feedback: str, current: list[dict]) -> ProfileUpdate:
    existing = (
        "\n".join(f"- id={p['id']} [{p['category']}] {p['summary']}: {p['content']}" for p in current) or "(none)"
    )
    response = client.beta.messages.parse(
        model=settings().profile_model,
        max_tokens=16000,
        messages=[{"role": "user", "content": PROMPT.format(existing=existing, feedback=feedback)}],
        output_format=ProfileUpdate,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return ProfileUpdate(add=[], deactivate_ids=[])
    return response.parsed_output
