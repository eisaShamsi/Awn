"""Skill registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .skills.base import BaseSkill


class SkillRegistry:
    """Maps command names to their skill implementations."""

    def __init__(self) -> None:
        self._skills: dict[str, "BaseSkill"] = {}

    def register(self, skill: "BaseSkill") -> None:
        """Register *skill* under each of its command names."""
        for name in skill.names:
            self._skills[name.lower()] = skill

    def get(self, name: str) -> "BaseSkill | None":
        """Return the skill for *name*, or ``None`` if not found."""
        return self._skills.get(name.lower())

    def all_skills(self) -> list["BaseSkill"]:
        """Return a deduplicated list of all registered skills."""
        seen: set[int] = set()
        result = []
        for skill in self._skills.values():
            if id(skill) not in seen:
                seen.add(id(skill))
                result.append(skill)
        return result
