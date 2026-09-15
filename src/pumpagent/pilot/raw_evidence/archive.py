"""Append-only POSIX directory transactions, with validation on every replay."""

from __future__ import annotations

import fcntl
import os
import re
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .contract import REQUIRED, InvalidCapture, Status, _json, canonical, digest, envelope, validate


@dataclass(frozen=True)
class ArchiveResult:
    status: Status
    evidence: dict[str, Any] | None = None
    pending: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class ReplayRecord:
    evidence: dict[str, Any]
    raw_response_bytes: bytes


@dataclass(frozen=True)
class ReplayResult:
    records: tuple[ReplayRecord, ...] = ()
    pending: tuple[str, ...] = ()
    error_status: Status | None = None
    error: str | None = None

    @property
    def complete(self) -> bool:
        """Archive recovery is complete; this says nothing about market completeness."""
        return self.error_status is None and not self.pending


class CorruptArchive(ValueError):
    pass


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
BUNDLE_FILES = {"raw.bin", "metadata.json", "manifest.json"}


def _read(directory: int, name: str) -> bytes:
    descriptor = os.open(name, FILE_FLAGS, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise CorruptArchive("Committed object is not a regular file")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _write_object(directory: int, name: str, content: bytes) -> None:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    try:
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("Short write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _manifest(metadata: dict[str, Any], metadata_bytes: bytes, previous: str | None) -> dict[str, Any]:
    return {key: metadata[key] for key in (
        "archive_schema_version", "raw_evidence_id", "raw_sha256", "raw_size_bytes",
        "raw_object_reference", "metadata_object_reference", "manifest_sequence",
    )} | {"metadata_digest": digest(metadata_bytes), "previous_manifest_digest": previous}


class RawEvidenceArchive:
    """Use an existing dedicated local directory. Construction performs no I/O.

    Committed directories are never opened for writing. Each invocation rebuilds
    and checks the manifest chain; no in-memory index is trusted across calls.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)

    @contextmanager
    def _locked(self) -> Iterator[int]:
        descriptor = os.open(self.root, DIRECTORY_FLAGS)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield descriptor
        finally:
            os.close(descriptor)

    def _scan(self, root: int) -> tuple[ReplayResult, str | None]:
        names = sorted(os.listdir(root))
        committed = [n for n in names if re.fullmatch(r"[0-9]{20}", n)]
        pending = tuple(n for n in names if n not in committed)
        records: list[ReplayRecord] = []
        identities: set[str] = set()
        previous = None
        try:
            for sequence, name in enumerate(committed, 1):
                if name != f"{sequence:020d}":
                    raise CorruptArchive("Manifest sequences are not contiguous")
                directory = os.open(name, DIRECTORY_FLAGS, dir_fd=root)
                try:
                    if set(os.listdir(directory)) != BUNDLE_FILES:
                        raise CorruptArchive("Committed bundle has missing or unexpected objects")
                    raw = _read(directory, "raw.bin")
                    metadata_bytes = _read(directory, "metadata.json")
                    manifest_bytes = _read(directory, "manifest.json")
                finally:
                    os.close(directory)
                metadata = _json(metadata_bytes)
                manifest = _json(manifest_bytes)
                if not isinstance(metadata, dict) or not isinstance(manifest, dict):
                    raise CorruptArchive("Invalid committed envelope")
                capture = {k: metadata[k] for k in REQUIRED if k != "raw_response_bytes"}
                capture["raw_response_bytes"] = raw
                provenance, _ = validate(capture)
                expected = envelope(provenance, raw, sequence)
                if metadata_bytes != canonical(expected):
                    raise CorruptArchive("Raw bytes, metadata or capture identity are inconsistent")
                expected_manifest = _manifest(expected, metadata_bytes, previous)
                if manifest_bytes != canonical(expected_manifest):
                    raise CorruptArchive("Manifest does not match committed objects or chain")
                identity = expected["raw_evidence_id"]
                if identity in identities:
                    raise CorruptArchive("Capture identity is committed more than once")
                identities.add(identity)
                records.append(ReplayRecord(expected | {"metadata_digest": digest(metadata_bytes)}, raw))
                previous = digest(manifest_bytes)
        except (OSError, ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            # Never return a seemingly valid prefix of a corrupt archive.
            return ReplayResult(pending=pending, error_status=Status.CORRUPTION_ERROR,
                                error="Committed archive failed integrity validation"), None
        return ReplayResult(tuple(records), pending), previous

    def replay(self) -> ReplayResult:
        """Read exact bytes in manifest order; report unknown/pending entries unchanged."""
        try:
            with self._locked() as root:
                result, _ = self._scan(root)
                return result
        except OSError:
            return ReplayResult(error_status=Status.PERSISTENCE_ERROR,
                                error="Cannot access or lock the archive directory")

    def archive(self, capture: Mapping[str, Any]) -> ArchiveResult:
        """Validate before any write, then publish all three objects together."""
        try:
            provenance, raw = validate(capture)
        except (InvalidCapture, ValueError, TypeError, UnicodeError, RecursionError):
            return ArchiveResult(Status.VALIDATION_ERROR, error="Capture failed v1 validation")
        pending: tuple[str, ...] = ()
        try:
            with self._locked() as root:
                recovered, previous = self._scan(root)
                pending = recovered.pending
                if recovered.error_status is not None:
                    return ArchiveResult(recovered.error_status, pending=pending, error=recovered.error)
                candidate = envelope(provenance, raw, len(recovered.records) + 1)
                for record in recovered.records:
                    if record.evidence["raw_evidence_id"] != candidate["raw_evidence_id"]:
                        continue
                    expected = envelope(provenance, raw, record.evidence["manifest_sequence"])
                    expected["metadata_digest"] = digest(canonical(expected))
                    if expected != record.evidence or raw != record.raw_response_bytes:
                        return ArchiveResult(Status.IDENTITY_CONFLICT, pending=pending,
                                             error="Capture identity already has different content or provenance")
                    return ArchiveResult(Status.DUPLICATE_NOOP, record.evidence, pending)
                if pending:
                    return ArchiveResult(Status.PERSISTENCE_ERROR, pending=pending,
                                         error="Pending or unknown entries require explicit external review")
                metadata_bytes = canonical(candidate)
                manifest_bytes = canonical(_manifest(candidate, metadata_bytes, previous))
                name = f".pending-{uuid.uuid4().hex}"
                os.mkdir(name, 0o700, dir_fd=root)
                pending = (name,)
                # Persist the pending name before writing; interruption never gets hidden.
                os.fsync(root)
                directory = os.open(name, DIRECTORY_FLAGS, dir_fd=root)
                try:
                    _write_object(directory, "raw.bin", raw)
                    _write_object(directory, "metadata.json", metadata_bytes)
                    _write_object(directory, "manifest.json", manifest_bytes)
                    os.fsync(directory)
                finally:
                    os.close(directory)
                final = f"{candidate['manifest_sequence']:020d}"
                # Lock serializes cooperating writers. A committed nonempty directory
                # cannot be overwritten by POSIX rename, even by a racing writer.
                if final in os.listdir(root):
                    raise OSError("Destination already exists")
                os.rename(name, final, src_dir_fd=root, dst_dir_fd=root)
                pending = ()
                os.fsync(root)
                evidence = candidate | {"metadata_digest": digest(metadata_bytes)}
                return ArchiveResult(Status.ARCHIVED, evidence)
        except OSError:
            # After a post-rename fsync failure the transaction may already be visible.
            # Return no success claim; replay determines its actual committed state.
            return ArchiveResult(Status.PERSISTENCE_ERROR, pending=pending,
                                 error="Archive write failed; replay before retrying")
