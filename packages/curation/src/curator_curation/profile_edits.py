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


def numbered(markdown: str) -> tuple[str, list[dict]]:
    head, items, _ = parse(markdown)
    return head, [{"id": i, "category": c, "text": t} for i, (c, t) in enumerate(items)]


def apply(markdown: str, terms: list[str], edits) -> tuple[str, list[str]]:
    head, items, tail = parse(markdown)
    if any(not 0 <= i < len(items) for i in edits.remove):
        raise ValueError("unknown preference id")
    kept = [item for i, item in enumerate(items) if i not in set(edits.remove)]
    for line in edits.add:
        item = (line.category, " ".join(line.text.split()))
        if item[1] and item not in kept:
            kept.append(item)
    removed = {t.casefold() for t in edits.remove_terms}
    out = []
    for term in [*terms, *edits.add_terms]:
        term = " ".join(term.split())
        if (
            term
            and len(term) <= 80
            and term.casefold() not in removed
            and term.casefold() not in map(str.casefold, out)
        ):
            out.append(term)
    return render(head, kept, tail), out[:MAX_TERMS]
