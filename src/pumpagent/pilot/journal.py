"""Single-stream, offline, append-only directory journal for a local POSIX filesystem.

Each fsynced record is published using an exclusive atomic hard link. Only the
numbered final names are committed; interrupted staging files are reported, not
replayed or repaired. Directory flock serializes cooperating readers/writers.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import fcntl
import os
from pathlib import Path
import re
import stat
import tempfile

from .checkpoint import (
    PilotCheckpointRecordV1, ValidationError, build_record, decode_record,
    map_recorded_snapshot, timestamp,
)


class IdentityConflict(ValidationError):
    """Same point-in-time identity, different supplied content: hard conflict."""


class CorruptionError(ValidationError):
    def __init__(self, message: str, last_valid: PilotCheckpointRecordV1 | None):
        super().__init__(message)
        self.last_valid = last_valid


class AppendStatus(str, Enum):
    APPENDED = "APPENDED"
    DUPLICATE_NOOP = "DUPLICATE_NOOP"


@dataclass(frozen=True)
class AppendResult:
    status: AppendStatus
    record: PilotCheckpointRecordV1


@dataclass(frozen=True)
class RecoveryResult:
    records: tuple[PilotCheckpointRecordV1, ...]
    uncommitted_files: tuple[str, ...]

    @property
    def last_valid(self) -> PilotCheckpointRecordV1 | None:
        return self.records[-1] if self.records else None


STREAM_FIELDS = ("run_id", "symbol", "provider", "market_id", "contract_id", "timeframe")


@contextmanager
def _locked(directory: Path, exclusive: bool):
    directory = Path(directory)
    if not directory.is_absolute() or directory.name != "pilot_shadow":
        raise ValidationError("Supply an absolute dedicated directory named pilot_shadow.")
    if any(part in ("research", "cases") for part in directory.resolve().parts):
        raise ValidationError("Pilot journal cannot reside in research or cases.")
    # The caller creates the dedicated directory explicitly; no implicit mkdir/CWD.
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield fd
    finally:
        os.close(fd)


def _validate_link(data: dict, previous: PilotCheckpointRecordV1 | None) -> None:
    if previous is None:
        if data["previous_checkpoint_id"] is not None:
            raise ValidationError("First checkpoint must have a null previous link.")
        return
    old = previous.to_dict()["input"]
    if any(data[k] != old[k] for k in STREAM_FIELDS):
        raise ValidationError("Journal supports exactly one run/symbol/instrument/timeframe stream.")
    if data["previous_checkpoint_id"] != previous.checkpoint_id:
        raise ValidationError("Wrong previous checkpoint link.")
    if timestamp(data["candle_close_time"]) <= timestamp(old["candle_close_time"]):
        raise ValidationError("Checkpoint candle must advance strictly in time.")
    if timestamp(data["recorded_time"]) < timestamp(old["recorded_time"]):
        raise ValidationError("Recorded time moved backwards.")


def _read_locked(fd: int) -> RecoveryResult:
    names = sorted(os.listdir(fd))
    pending = tuple(n for n in names if re.fullmatch(r"\.pending-[a-z0-9_]+", n))
    finals = [n for n in names if n not in pending]
    records = []
    seen = set()
    for name in pending:
        if not stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
            raise CorruptionError("Invalid staging entry: " + name, None)
    for sequence, name in enumerate(finals, 1):
        last = records[-1] if records else None
        if name != f"{sequence:020d}.json":
            raise CorruptionError("Unexpected journal entry or sequence gap: " + name, last)
        if not stat.S_ISREG(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode):
            raise CorruptionError("Committed entry is not a regular file: " + name, last)
        try:
            record_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(record_fd, "rb") as stream:
                record = decode_record(stream.read())
            data = record.to_dict()
            if data["commit_sequence"] != sequence or record.checkpoint_id in seen:
                raise ValidationError("Invalid sequence or repeated checkpoint identity.")
            _validate_link(data["input"], last)
            if last and timestamp(data["committed_at"]) < timestamp(last.to_dict()["committed_at"]):
                raise ValidationError("Commit time moved backwards.")
            seen.add(record.checkpoint_id)
            records.append(record)
        except ValidationError as exc:
            raise CorruptionError(f"Corrupt journal record {name}: {exc}", last) from exc
    return RecoveryResult(tuple(records), pending)


def recover(journal_directory: Path, *, symbol: str) -> RecoveryResult:
    """Validate every committed record; errors never return partial success."""
    with _locked(journal_directory, False) as fd:
        result = _read_locked(fd)
        if result.last_valid and result.last_valid.to_dict()["input"]["symbol"] != symbol:
            raise ValidationError("Requested symbol does not match journal.")
        return result


def append_checkpoint(journal_directory: Path, recorded_snapshot: dict, *, committed_at: str) -> AppendResult:
    """Explicit append only. OSError propagates, including uncertain post-publication durability.

    Replay compares canonical input, not a newly supplied commit timestamp. If
    publication succeeded but directory fsync failed, caller must recover/replay;
    the original call raises rather than claiming success.
    """
    mapped = map_recorded_snapshot(recorded_snapshot)
    with _locked(journal_directory, True) as fd:
        recovered = _read_locked(fd)
        for record in recovered.records:
            if record.checkpoint_id == mapped.checkpoint_id:
                if map_recorded_snapshot(record.to_dict()["input"]) != mapped:
                    raise IdentityConflict("HARD CONFLICT: same identity has different content.")
                return AppendResult(AppendStatus.DUPLICATE_NOOP, record)
        _validate_link(mapped.to_dict(), recovered.last_valid)
        record = build_record(mapped, len(recovered.records) + 1, committed_at)
        if recovered.last_valid and timestamp(committed_at) < timestamp(recovered.last_valid.to_dict()["committed_at"]):
            raise ValidationError("Commit time moved backwards.")
        final_name = f"{len(recovered.records) + 1:020d}.json"
        # Staging and final link share a filesystem. No replace/update of a record.
        temp_fd, temp_path = tempfile.mkstemp(prefix=".pending-", dir=journal_directory)
        try:
            with os.fdopen(temp_fd, "wb") as stream:
                stream.write(record.canonical_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp_path, final_name, dst_dir_fd=fd, follow_symlinks=False)
            os.fsync(fd)
        finally:
            os.unlink(temp_path)  # Only our uncommitted staging name; never a final record.
        return AppendResult(AppendStatus.APPENDED, record)
