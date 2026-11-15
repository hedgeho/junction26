"""Apply model-proposed edits to a profile, so feedback changes only the lines it is about."""

import re

ITEM = re.compile(r"(?:^|(?<=\s))-\s\[([A-Za-z_]{1,20})\]\s")
SECTION = re.compile(r"\s+(?=#{1,6} )")
MAX_TERMS = 30


def parse(markdown: str) -> tuple[str, list[tuple[str, str]], str]:
    """Leading text, `- [category] text` preference lines, and any section after the last line."""
    starts = list(ITEM.finditer(markdown))
    if not starts:
        return markdown.strip(), [], ""
    head = markdown[: starts[0].start()].strip()
    items = []
    for match, following in zip(starts, [*starts[1:], None], strict=True):
        text = markdown[match.end() : following.start() if following else len(markdown)]
        items.append([match.group(1).lower(), text])
    tail = ""
    last = SECTION.split(items[-1][1], maxsplit=1)
    if len(last) == 2:
        items[-1][1], tail = last
    return head, [(c, " ".join(t.split())) for c, t in items], tail.strip()


def render(head: str, items: list[tuple[str, str]], tail: str) -> str:
    head = SECTION.sub("\n\n", head)
    parts = [head, "\n".join(f"- [{c}] {t}" for c, t in items), tail]
    return "\n\n".join(p for p in parts if p) + "\n"


TERM = "search_term"


def search_terms(markdown: str) -> list[str]:
    """`[search_term]` lines in a profile, deduplicated and capped like model-proposed terms."""
    out = []
    for category, text in parse(markdown)[1]:
        if category == TERM and text and len(text) <= 80 and text.casefold() not in map(str.casefold, out):
            out.append(text)
    return out[:MAX_TERMS]


def numbered(markdown: str) -> tuple[str, list[dict]]:
    """Editable lines for the model. Legacy `[search_term]` lines are terms, not preferences, so they are left out."""
    head, items, _ = parse(markdown)
    return head, [{"id": i, "category": c, "text": t} for i, (c, t) in enumerate(items) if c != TERM]


def apply(markdown: str, terms: list[str], edits) -> tuple[str, list[str]]:
    head, items, tail = parse(markdown)
    if any(not 0 <= i < len(items) for i in edits.remove):
        raise ValueError("unknown preference id")
    # Removals only replace lines; a small model's bare removals were often the opposite of the feedback.
    lines = [line for line in edits.add if line.category != TERM]
    removed_ids = {i for i in edits.remove if items[i][0] != TERM} if lines else set()
    kept = [item for i, item in enumerate(items) if i not in removed_ids]
    for i in sorted(removed_ids):
        category, text = items[i]
        if category == "topic":
            historical = ("history", "Previously: " + text)
            if historical not in kept:
                kept.append(historical)
    for line in lines:
        item = (line.category, " ".join(line.text.split()))
        if item[1] and item not in kept:
            kept.append(item)
    added_terms = [*edits.add_terms, *(line.text for line in edits.add if line.category == TERM)]
    removed = {t.casefold() for t in edits.remove_terms}
    out = []
    for term in [*terms, *added_terms]:
        term = " ".join(term.split())
        if (
            term
            and len(term) <= 80
            and term.casefold() not in removed
            and term.casefold() not in map(str.casefold, out)
        ):
            out.append(term)
    return render(head, kept, tail), out[:MAX_TERMS]
