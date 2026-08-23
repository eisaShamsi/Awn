"""Filesystem boundary for internal workspace files."""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from awn.domain.files import FileCreateResult


class UnsafeWorkspacePathError(ValueError):
    pass


class SafeWorkspaceFiles:
    """Create UTF-8 files without allowing a tool input to escape its workspace."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def create_text(
        self,
        workspace_id: UUID,
        relative_path: str,
        content: str,
        *,
        tool_call_id: UUID,
    ) -> FileCreateResult:
        workspace = self._root / str(workspace_id)
        workspace.mkdir(mode=0o700, parents=False, exist_ok=True)
        workspace = workspace.resolve()
        self._require_within(workspace, self._root)

        parts = relative_path.split("/")
        parent = workspace
        for part in parts[:-1]:
            candidate = parent / part
            if candidate.is_symlink():
                raise UnsafeWorkspacePathError("symbolic links are not allowed in tool paths")
            candidate.mkdir(mode=0o700, exist_ok=True)
            parent = candidate.resolve()
            self._require_within(parent, workspace)

        target = parent / parts[-1]
        if target.is_symlink():
            raise UnsafeWorkspacePathError("symbolic-link targets are not allowed")
        self._require_within(target.resolve(strict=False), workspace)

        payload = content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        if target.exists():
            return self._existing_result(target, relative_path, payload, digest)

        temporary = parent / f".awn-{tool_call_id.hex}.tmp"
        self._require_within(temporary.resolve(strict=False), workspace)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(temporary, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb", closefd=True) as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                if temporary.exists():
                    temporary.unlink()
                raise

            try:
                os.link(temporary, target)
                created = True
            except FileExistsError:
                existing = self._existing_result(target, relative_path, payload, digest)
                created = existing.created
            finally:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        except FileExistsError:
            if temporary.exists():
                temporary.unlink()
            if target.exists():
                return self._existing_result(target, relative_path, payload, digest)
            return self.create_text(
                workspace_id,
                relative_path,
                content,
                tool_call_id=tool_call_id,
            )

        return FileCreateResult(
            path=relative_path,
            bytes_written=len(payload),
            sha256=digest,
            created=created,
        )

    @staticmethod
    def _require_within(candidate: Path, boundary: Path) -> None:
        if not candidate.is_relative_to(boundary):
            raise UnsafeWorkspacePathError("tool path escaped its workspace boundary")

    @staticmethod
    def _existing_result(
        target: Path,
        relative_path: str,
        payload: bytes,
        digest: str,
    ) -> FileCreateResult:
        if target.is_symlink():
            raise UnsafeWorkspacePathError("symbolic-link targets are not allowed")
        existing = target.read_bytes()
        if existing != payload:
            raise FileExistsError("a different file already exists at the requested path")
        return FileCreateResult(
            path=relative_path,
            bytes_written=len(existing),
            sha256=digest,
            created=False,
        )
