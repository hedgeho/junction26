"""Apply model-proposed patches or complete profile replacements."""

import re

ITEM = re.compile(r"(?:^|(?<=\s))-\s\[([A-Za-z_]{1,20})\]\s")
SECTION = re.compile(r"\s+(?=#{1,6} )")
MAX_TERMS = 30


def unresolved_reference(text: str) -> bool:
    words = set(re.findall(r"[^\W_]+", text.casefold()))
    references = {"this", "that", "these", "those", "it", "more", "less", "fewer", "similar"}
    generic = {
        "i",
        "d",
        "m",
        "would",
        "want",
        "like",
        "liked",
        "love",
        "loved",
        "prefer",
        "please",
        "show",
        "me",
        "see",
        "seeing",
        "give",
        "recommend",
        "recommendations",
        "more",
        "less",
        "fewer",
        "much",
        "some",
        "of",
        "on",
        "a",
        "an",
        "lot",
        "similar",
        "to",
        "than",
        "as",
        "these",
        "those",
        "this",
        "that",
        "it",
        "posts",
        "post",
        "content",
        "stuff",
        "thing",
        "things",
        "kind",
        "type",
        "such",
        "not",
        "no",
        "do",
        "don",
        "t",
        "avoid",
        "exclude",
        "never",
        "again",
        "interesting",
        "good",
        "great",
        "really",
        "especially",
        "the",
    }
    return bool(words & references) and words <= generic


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
    if edits.action == "keep":
        return markdown, list(terms)
    if edits.action == "replace":
        if edits.replacement is None:
            raise ValueError("replacement action requires the complete profile")
        additions = edits.model_copy(update={"action": "patch", "replacement": None, "remove": []})
        replacement, replacement_terms = apply(
            edits.replacement.markdown, list(edits.replacement.search_terms), additions
        )
        if not replacement_terms and all(
            not line.strip() or re.match(r"^\s*#{1,6}\s", line) for line in replacement.splitlines()
        ):
            replacement = ""
        return replacement, replacement_terms
    if not (edits.remove or edits.add or edits.add_terms or edits.remove_terms):
        return markdown, list(terms)
    head, items, tail = parse(markdown)
    if any(not 0 <= i < len(items) for i in edits.remove):
        raise ValueError("unknown preference id")
    lines = [line for line in edits.add if line.category != TERM]
    removed_ids = {i for i in edits.remove if items[i][0] != TERM}
    kept = [item for i, item in enumerate(items) if i not in removed_ids]
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
