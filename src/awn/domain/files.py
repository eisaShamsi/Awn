"""Validated contracts for files created inside an Awn workspace boundary."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_WINDOWS_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$",
    re.IGNORECASE,
)
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*')


class FileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=1_000_000)

    @field_validator("path")
    @classmethod
    def path_must_be_safe_and_relative(cls, value: str) -> str:
        path = value.strip()
        if not path or path.startswith("/") or "\\" in path or "\0" in path:
            raise ValueError("path must be a non-empty POSIX-style relative path")
        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("path may not contain empty, current, or parent segments")
        for part in parts:
            if part.endswith((" ", ".")):
                raise ValueError("path segments may not end with a space or dot")
            if any(ord(character) < 32 for character in part):
                raise ValueError("path may not contain control characters")
            if any(character in _WINDOWS_FORBIDDEN for character in part):
                raise ValueError("path contains a platform-reserved character")
            if _WINDOWS_RESERVED.fullmatch(part):
                raise ValueError("path contains a platform-reserved name")
        return path


class FileCreateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    bytes_written: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created: bool
