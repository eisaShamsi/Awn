"""Core Awn assistant."""

from __future__ import annotations

import textwrap
from typing import Any

from .registry import SkillRegistry
from .skills import (
    CalculatorSkill,
    EchoSkill,
    HelpSkill,
    TextSkill,
    TimeSkill,
)


class Awn:
    """A versatile and intelligent aide.

    Awn assists, supports, and carries out a wide range of tasks with
    competence, adaptability, and initiative.
    """

    _GREETING = textwrap.dedent(
        """\
        مرحباً! I'm Awn (عَوْن) — your versatile aide.
        Type a command or 'help' to see what I can do. Type 'quit' to exit.
        """
    )

    def __init__(self) -> None:
        self._registry = SkillRegistry()
        self._register_skills()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def greeting(self) -> str:
        """Return the welcome greeting."""
        return self._GREETING

    def handle(self, user_input: str) -> str:
        """Process *user_input* and return a response string.

        Awn selects the appropriate skill based on the first word of
        the input and delegates execution to it.  Unknown commands are
        handled gracefully.
        """
        text = user_input.strip()
        if not text:
            return ""

        command, _, args = text.partition(" ")
        command = command.lower()

        skill = self._registry.get(command)
        if skill is None:
            return self._unknown(command)

        return skill.run(args.strip())

    def run_interactive(self) -> None:
        """Start an interactive REPL session."""
        print(self.greeting)
        while True:
            try:
                line = input("awn> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nإلى اللقاء! (Goodbye!)")
                break

            if line.lower() in {"quit", "exit", "q"}:
                print("إلى اللقاء! (Goodbye!)")
                break

            response = self.handle(line)
            if response:
                print(response)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _register_skills(self) -> None:
        for skill in [
            HelpSkill(self._registry),
            EchoSkill(),
            TimeSkill(),
            CalculatorSkill(),
            TextSkill(),
        ]:
            self._registry.register(skill)

    def _unknown(self, command: str) -> str:
        return (
            f"I don't know how to '{command}'. "
            "Try 'help' to see available commands."
        )
