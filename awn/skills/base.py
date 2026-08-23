"""Base class for all Awn skills."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseSkill(ABC):
    """Abstract base for a skill that Awn can perform."""

    #: Primary command name and any aliases.
    names: tuple[str, ...] = ()

    #: One-line description shown in help.
    description: str = ""

    #: Usage example shown in help.
    usage: str = ""

    @abstractmethod
    def run(self, args: str) -> str:
        """Execute the skill with the given *args* and return a response."""
