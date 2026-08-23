"""Echo skill — repeats input back to the user."""

from .base import BaseSkill


class EchoSkill(BaseSkill):
    """Repeat the given text back to the user."""

    names = ("echo",)
    description = "Repeat text back to you."
    usage = "echo <text>"

    def run(self, args: str) -> str:
        if not args:
            return "Usage: echo <text>"
        return args
