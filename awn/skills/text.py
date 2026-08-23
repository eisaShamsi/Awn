"""Text skill — basic text manipulation utilities."""

from __future__ import annotations

from .base import BaseSkill

_SUBCOMMANDS = {
    "upper": str.upper,
    "lower": str.lower,
    "title": str.title,
    "reverse": lambda s: s[::-1],
    "len": lambda s: str(len(s)),
    "words": lambda s: str(len(s.split())),
}


class TextSkill(BaseSkill):
    """Perform text transformations."""

    names = ("text",)
    description = "Manipulate text (upper, lower, title, reverse, len, words)."
    usage = "text <subcommand> <text>"

    def run(self, args: str) -> str:
        sub, _, rest = args.partition(" ")
        sub = sub.strip().lower()
        text = rest.strip()

        if sub not in _SUBCOMMANDS:
            available = ", ".join(sorted(_SUBCOMMANDS))
            return f"Unknown subcommand '{sub}'. Available: {available}."

        if not text:
            return f"Usage: text {sub} <text>"

        fn = _SUBCOMMANDS[sub]
        return fn(text)
