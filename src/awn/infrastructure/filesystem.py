"""Filesystem boundary for internal workspace files."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

from awn.domain.cancellations import CancellationEvidenceCode
from awn.domain.files import FileCreate, FileCreateResult
from awn.tools.contracts import EffectVerification, EffectVerificationStatus


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
        existing_receipt = self._read_receipt(
            workspace_id,
            tool_call_id,
            relative_path=relative_path,
            content=content,
        )
        if existing_receipt is not None:
            return existing_receipt

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

        result = FileCreateResult(
            path=relative_path,
            bytes_written=len(payload),
            sha256=digest,
            created=created,
        )
        self._write_receipt(workspace_id, tool_call_id, result)
        return result

    def verify_create_effect(
        self,
        workspace_id: UUID,
        command: FileCreate,
        *,
        tool_call_id: UUID,
    ) -> EffectVerification:
        """Read durable effect evidence without invoking or repeating the file effect.

        Target absence is deliberately non-conclusive: a worker that crossed the
        effect boundary may still publish the file, or a completed file may have
        been removed later. Only the private durable receipt proves this effect.
        """

        receipt = self._read_receipt(
            workspace_id,
            tool_call_id,
            relative_path=command.path,
            content=command.content,
        )
        if receipt is None:
            return EffectVerification(
                EffectVerificationStatus.UNKNOWN,
                CancellationEvidenceCode.FILE_NOT_FOUND_AT_SAFE_PATH,
            )
        return EffectVerification(
            EffectVerificationStatus.EFFECT_PRESENT,
            CancellationEvidenceCode.VALIDATED_TOOL_OUTPUT,
            output=receipt,
        )

    def _receipt_path(self, workspace_id: UUID, tool_call_id: UUID) -> Path:
        evidence_root = (self._root / ".awn-effect-evidence").resolve(strict=False)
        self._require_within(evidence_root, self._root)
        workspace_evidence = evidence_root / str(workspace_id)
        receipt = workspace_evidence / f"{tool_call_id}.json"
        self._require_within(receipt.resolve(strict=False), evidence_root)
        return receipt

    def _write_receipt(
        self,
        workspace_id: UUID,
        tool_call_id: UUID,
        result: FileCreateResult,
    ) -> None:
        receipt = self._receipt_path(workspace_id, tool_call_id)
        receipt.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "version": 1,
                "workspace_id": str(workspace_id),
                "tool_call_id": str(tool_call_id),
                "output": result.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary = receipt.with_name(f".{receipt.name}.{uuid4().hex}.tmp")
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
                temporary.unlink(missing_ok=True)
                raise
            try:
                os.link(temporary, receipt)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)
        except FileExistsError:
            temporary.unlink(missing_ok=True)

    def _read_receipt(
        self,
        workspace_id: UUID,
        tool_call_id: UUID,
        *,
        relative_path: str,
        content: str,
    ) -> FileCreateResult | None:
        receipt = self._receipt_path(workspace_id, tool_call_id)
        if receipt.is_symlink() or not receipt.is_file():
            return None
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            if (
                payload.get("version") != 1
                or payload.get("workspace_id") != str(workspace_id)
                or payload.get("tool_call_id") != str(tool_call_id)
            ):
                return None
            result = FileCreateResult.model_validate(payload.get("output"))
        except (OSError, ValueError, TypeError):
            return None
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if (
            result.path != relative_path
            or result.sha256 != digest
            or result.bytes_written != len(content.encode("utf-8"))
        ):
            return None
        return result

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
