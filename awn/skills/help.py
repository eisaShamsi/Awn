"""Help skill — lists available commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseSkill

if TYPE_CHECKING:
    from ..registry import SkillRegistry


class HelpSkill(BaseSkill):
    """Display help information about available commands."""

    names = ("help", "?", "h")
    description = "List available commands or get help on a specific command."
    usage = "help [command]"

    def __init__(self, registry: "SkillRegistry") -> None:
        self._registry = registry

    def run(self, args: str) -> str:
        cmd = args.strip().lower()
        if cmd:
            skill = self._registry.get(cmd)
            if skill is None:
                return f"No command named '{cmd}'."
            lines = [
                f"Command : {', '.join(skill.names)}",
                f"Summary : {skill.description}",
                f"Usage   : {skill.usage}",
            ]
            return "\n".join(lines)

        # General help
        lines = ["Available commands:", ""]
        for skill in self._registry.all_skills():
            primary = skill.names[0]
            aliases = (
                "  (aliases: " + ", ".join(skill.names[1:]) + ")"
                if len(skill.names) > 1
                else ""
            )
            lines.append(f"  {primary:<12} {skill.description}{aliases}")
        lines += ["", "Type 'help <command>' for details on a specific command."]
        return "\n".join(lines)
