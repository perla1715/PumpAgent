"""Task 2 acceptance 1–18; 19–20 run the unchanged existing regression suites."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pumpagent.pilot.raw_evidence import RawEvidenceArchive, Status
from pumpagent.pilot.raw_evidence import archive as implementation
from pumpagent.pilot.raw_evidence.contract import canonical
from pumpagent.pilot.raw_evidence.contract import REQUIRED


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "pilot" / "raw_evidence"
RAW = b' [ {"symbol":"BTCUSDT_PERP.A", "history":[{"t":1789430400,"c":100,"v":2}]} ]\r\n'


@pytest.fixture
def capture():
    return json.loads((FIXTURES / "capture_v1.json").read_bytes()) | {"raw_response_bytes": RAW}


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return RawEvidenceArchive(root)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def body(capture, value):
    capture["raw_response_bytes"] = json.dumps(value).encode()
    return capture


def test_01_exact_bytes(archive, capture):
    result = archive.archive(capture)
    assert result.status == Status.ARCHIVED
    assert (archive.root / result.evidence["raw_object_reference"]).read_bytes() == RAW
    assert archive.replay().records[0].raw_response_bytes == RAW


def test_02_digest_from_actual_bytes(archive, capture):
    result = archive.archive(capture)
    assert result.evidence["raw_sha256"] == hashlib.sha256(RAW).hexdigest()
    assert result.evidence["raw_size_bytes"] == len(RAW)
    meta = (archive.root / result.evidence["metadata_object_reference"]).read_bytes()
    assert result.evidence["metadata_digest"] == hashlib.sha256(meta).hexdigest()
    capture["raw_sha256"] = "caller-supplied-digest"
    assert archive.archive(capture).status == Status.VALIDATION_ERROR


def test_03_canonical_determinism(archive, capture, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    first = archive.archive(capture)
    reordered = dict(reversed(list(capture.items())))
    reordered["request_parameters"] = dict(reversed(list(capture["request_parameters"].items())))
    reordered["request_started_at"] = "2026-09-15T02:10:00+02:00"
    reordered["response_received_at"] = "2026-09-15T02:10:02+02:00"
    reordered["recorded_at"] = "2026-09-15T02:10:03+02:00"
    second = RawEvidenceArchive(other).archive(reordered)
    assert first == second
    assert snapshot(archive.root) == snapshot(other)
    assert first.evidence["recorded_at"] == "2026-09-15T00:10:03.000000Z"


def test_04_first_archive(archive, capture):
    assert archive.replay().complete
    result = archive.archive(capture)
    assert result.status == Status.ARCHIVED
    assert result.evidence["manifest_sequence"] == 1
    assert set(snapshot(archive.root)) == {
        "00000000000000000001/raw.bin", "00000000000000000001/metadata.json",
        "00000000000000000001/manifest.json",
    }
    assert archive.replay().records[0].evidence == result.evidence
    assert result.evidence["canonical_request_identity"]["request_parameters"] == capture["request_parameters"]


def test_05_exact_replay_noop(archive, capture):
    first = archive.archive(capture)
    before = snapshot(archive.root)
    stats = {str(p): p.stat().st_mtime_ns for p in archive.root.rglob("*")}
    again = archive.archive(copy.deepcopy(capture))
    assert again.status == Status.DUPLICATE_NOOP
    assert again.evidence == first.evidence
    assert snapshot(archive.root) == before
    assert {str(p): p.stat().st_mtime_ns for p in archive.root.rglob("*")} == stats


def test_06_same_identity_different_bytes(archive, capture):
    archive.archive(capture)
    before = snapshot(archive.root)
    capture["raw_response_bytes"] = RAW + b" "
    result = archive.archive(capture)
    assert result.status == Status.IDENTITY_CONFLICT
    assert snapshot(archive.root) == before


@pytest.mark.parametrize("key,value", [
    ("recorded_at", "2026-09-15T00:11:00Z"),
    ("response_received_at", "2026-09-15T00:10:01Z"),
    ("capture_code_version", "different"), ("capture_config_version", "different"),
    ("capture_status", "INCOMPLETE_CAPTURE"),
    ("content_type_or_unavailable_reason", {"value": None, "unavailable_reason": "not supplied"}),
    ("provider_api_version_or_unavailable_reason", {"value": "v2", "unavailable_reason": None}),
])
def test_07_provenance_conflict(archive, capture, key, value):
    archive.archive(capture)
    before = snapshot(archive.root)
    capture[key] = value
    assert archive.archive(capture).status == Status.IDENTITY_CONFLICT
    assert snapshot(archive.root) == before


@pytest.mark.parametrize("key", ["request_started_at", "response_received_at", "recorded_at"])
@pytest.mark.parametrize("value", [None, "", "bad", "2026-09-15T00:10:00", "2026-13-15T00:10:00Z",
                                   "2026-09-15T00:10:00+00:60", "2026-09-15T00:10:00+24:00"])
def test_08_bad_timestamps(archive, capture, key, value):
    capture[key] = value
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    assert list(archive.root.iterdir()) == []


@pytest.mark.parametrize("key", ["request_started_at", "response_received_at", "recorded_at"])
def test_08_missing_timestamp(archive, capture, key):
    del capture[key]
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    assert list(archive.root.iterdir()) == []


@pytest.mark.parametrize("key,value", [("request_started_at", "2026-09-15T00:10:04Z"),
                                      ("recorded_at", "2026-09-15T00:10:01Z")])
def test_08_reversed_timestamps(archive, capture, key, value):
    capture[key] = value
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    assert not snapshot(archive.root)


@pytest.mark.parametrize("key", ["api_key", "API-KEY", "Authorization", "cookies", "access_token",
                               "password", "credentials", "secret", "unrecognized"])
def test_09_secret_request_keys_never_persist(archive, capture, key):
    capture["request_parameters"][key] = "DO-NOT-PERSIST"
    result = archive.archive(capture)
    assert result.status == Status.VALIDATION_ERROR
    assert "DO-NOT-PERSIST" not in repr(result)
    assert list(archive.root.iterdir()) == []


@pytest.mark.parametrize("key,value", [
    ("endpoint_identity", "/v1/ohlcv-history?api_key=DO-NOT-PERSIST"),
    ("requested_symbol", "BTCUSDT_PERP.A?token=DO-NOT-PERSIST"),
    ("capture_code_version", "Bearer DO-NOT-PERSIST"),
    ("headers", {"Authorization": "DO-NOT-PERSIST"}),
    ("raw_response_bytes", b'{"api_key":"DO-NOT-PERSIST"}'),
    ("raw_response_bytes", b'{"api\\u005fkey":"DO-NOT-PERSIST"}'),
])
def test_09_secret_values_never_persist(archive, capture, key, value):
    capture[key] = value
    capture["capture_status"] = "PROVIDER_ERROR"
    result = archive.archive(capture)
    assert result.status == Status.VALIDATION_ERROR
    assert "DO-NOT-PERSIST" not in repr(result)
    assert list(archive.root.iterdir()) == []


@pytest.mark.parametrize("t", [None, "bad", "2026-09-15T00:00:00", -1, True])
def test_10_malformed_provider_timestamp(archive, capture, t):
    body(capture, [{"symbol": "BTCUSDT_PERP.A", "history": [{"t": t, "c": 1}]}])
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture["capture_status"] = "MALFORMED_RESPONSE"
    assert archive.archive(capture).status == Status.ARCHIVED
    assert archive.replay().records[0].raw_response_bytes == capture["raw_response_bytes"]


@pytest.mark.parametrize("raw", [b'{"truncated":', b'\xff\xfe\x00', b'[{"a":1,"a":2}]', b'[1e999]'])
def test_10_truncated_or_undecodable_requires_incomplete_status(archive, capture, raw):
    capture["raw_response_bytes"] = raw
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture["capture_status"] = "MALFORMED_RESPONSE"
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture["capture_status"] = "INCOMPLETE_CAPTURE"
    assert archive.archive(capture).status == Status.ARCHIVED
    assert archive.replay().records[0].raw_response_bytes == raw


def test_10_provider_error_preserved(archive, capture):
    capture["http_status_or_transport_error"] = {"http_status": 503, "transport_error": None}
    body(capture, {"error": "temporarily unavailable"})
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture["capture_status"] = "PROVIDER_ERROR"
    assert archive.archive(capture).status == Status.ARCHIVED


def test_11_missing_duplicate_gapped_records_not_repaired(archive, capture):
    raw = (FIXTURES / "gapped_duplicate_1m.json").read_bytes()
    capture["raw_response_bytes"] = raw
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture["quality_flags"] += ["MISSING_RECORDS", "GAPS", "DUPLICATE_RECORDS",
                                 "CONFLICTING_DUPLICATES", "OPEN_OR_INCOMPLETE_CANDLES",
                                 "UNAVAILABLE_METRICS", "LATE_RECORDS"]
    assert archive.archive(capture).status == Status.ARCHIVED
    replay = archive.replay().records[0]
    assert replay.raw_response_bytes == raw
    records = json.loads(replay.raw_response_bytes)[0]["history"]
    assert [r["t"] for r in records] == [1789430400, 1789430400, 1789430400, 1789430580, 1789430520]
    assert records[3]["c"] is None
    assert records[3]["closed"] is False
    assert set(replay.evidence["quality_flags"]) == set(capture["quality_flags"])


@pytest.mark.parametrize("key,value", [("symbols", "ETHUSDT_PERP.A"), ("interval", "5min"),
                                      ("exchange", "other"), ("contract", "spot")])
def test_12_request_identity_mismatch(archive, capture, key, value):
    capture["request_parameters"][key] = value
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    assert list(archive.root.iterdir()) == []


@pytest.mark.parametrize("key,value", [("symbol", "ETHUSDT_PERP.A"), ("exchange", "other"),
                                      ("contract", "spot")])
@pytest.mark.parametrize("status", ["VALID_RESPONSE", "MALFORMED_RESPONSE", "PROVIDER_ERROR"])
def test_12_payload_identity_mismatch(archive, capture, key, value, status):
    payload = json.loads(RAW)
    payload[0][key] = value
    body(capture, payload)
    capture["capture_status"] = status
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    assert not snapshot(archive.root)


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_13_partial_object_failure_is_never_committed(archive, capture, monkeypatch, fail_at):
    real = implementation._write_object
    calls = 0
    def fail(directory, name, content):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            real(directory, name, content[:5])
            raise OSError("injected disk failure")
        real(directory, name, content)
    monkeypatch.setattr(implementation, "_write_object", fail)
    result = archive.archive(capture)
    assert result.status == Status.PERSISTENCE_ERROR
    assert len(result.pending) == 1
    replay = RawEvidenceArchive(archive.root).replay()
    assert replay.records == () and replay.pending == result.pending
    assert replay.error_status is None and not replay.complete


def test_13_rename_failure_preserves_complete_pending_bundle(archive, capture, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("injected rename failure")
    monkeypatch.setattr(implementation.os, "rename", fail)
    result = archive.archive(capture)
    assert result.status == Status.PERSISTENCE_ERROR
    assert len(snapshot(archive.root)) == 3
    assert not archive.replay().records
    assert archive.replay().pending == result.pending


@pytest.mark.parametrize("fail_at", [1, 2, 3, 4, 5, 6])
def test_13_fsync_failures(archive, capture, monkeypatch, fail_at):
    real = implementation.os.fsync
    calls = 0
    def fail(fd):
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise OSError("injected fsync failure")
        real(fd)
    monkeypatch.setattr(implementation.os, "fsync", fail)
    result = archive.archive(capture)
    assert result.status == Status.PERSISTENCE_ERROR
    replay = archive.replay()
    if fail_at == 6:  # rename committed all three objects; durability acknowledgment failed
        assert len(replay.records) == 1 and replay.complete
        assert archive.archive(capture).status == Status.DUPLICATE_NOOP
    else:
        assert not replay.records and replay.pending


@pytest.mark.parametrize("filename", ["raw.bin", "metadata.json", "manifest.json"])
def test_14_corruption_detected_and_append_blocked(archive, capture, filename):
    archive.archive(capture)
    path = archive.root / "00000000000000000001" / filename
    path.write_bytes(path.read_bytes() + b"corrupt")
    before = snapshot(archive.root)
    replay = archive.replay()
    assert replay.error_status == Status.CORRUPTION_ERROR and not replay.records
    assert archive.archive(capture).status == Status.CORRUPTION_ERROR
    assert snapshot(archive.root) == before


@pytest.mark.parametrize("key,value", [("raw_evidence_id", "re1:wrong"), ("raw_sha256", "wrong"),
                                      ("raw_object_reference", "../outside"), ("manifest_sequence", 2)])
def test_14_inconsistent_manifest_identity(archive, capture, key, value):
    archive.archive(capture)
    path = archive.root / "00000000000000000001" / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest[key] = value
    path.write_bytes(canonical(manifest))
    assert archive.replay().error_status == Status.CORRUPTION_ERROR


@pytest.mark.parametrize("filename", ["raw.bin", "metadata.json", "manifest.json"])
def test_15_missing_objects(archive, capture, filename):
    archive.archive(capture)
    (archive.root / "00000000000000000001" / filename).unlink()
    assert archive.replay().error_status == Status.CORRUPTION_ERROR
    assert archive.archive(capture).status == Status.CORRUPTION_ERROR


def test_16_restart_rebuilds_sequence_and_identity(archive, capture):
    first = archive.archive(capture)
    restart = RawEvidenceArchive(archive.root)
    assert restart.archive(capture).status == Status.DUPLICATE_NOOP
    capture["request_started_at"] = "2026-09-15T00:10:01Z"
    capture["quality_flags"].append("LATE_RECORDS")
    second = restart.archive(capture)
    assert second.status == Status.ARCHIVED and second.evidence["manifest_sequence"] == 2
    assert second.evidence["raw_evidence_id"] != first.evidence["raw_evidence_id"]
    replay = RawEvidenceArchive(archive.root).replay()
    assert [r.evidence for r in replay.records] == [first.evidence, second.evidence]
    manifest1 = (archive.root / "00000000000000000001/manifest.json").read_bytes()
    manifest2 = json.loads((archive.root / "00000000000000000002/manifest.json").read_bytes())
    assert manifest2["previous_manifest_digest"] == hashlib.sha256(manifest1).hexdigest()


def test_17_unknown_pending_entries_reported_untouched(archive, capture):
    archive.archive(capture)
    (archive.root / ".pending-unknown").mkdir()
    (archive.root / ".pending-unknown/partial").write_bytes(b"incomplete")
    (archive.root / "unknown-file").write_bytes(b"unknown")
    before = snapshot(archive.root)
    replay = archive.replay()
    assert len(replay.records) == 1
    assert replay.pending == (".pending-unknown", "unknown-file")
    assert not replay.complete
    assert archive.archive(capture).status == Status.DUPLICATE_NOOP
    capture["request_started_at"] = "2026-09-15T00:10:01Z"
    assert archive.archive(capture).status == Status.PERSISTENCE_ERROR
    assert snapshot(archive.root) == before


def test_18_offline_no_socket_subprocess_or_integration(archive, capture, monkeypatch):
    import ast
    package = Path(implementation.__file__).parent
    allowed = {"__future__", "hashlib", "json", "math", "re", "datetime", "enum", "typing",
               "fcntl", "os", "uuid", "contextlib", "dataclasses", "pathlib", "stat"}
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(n.name.split(".")[0] in allowed for n in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                assert node.module.split(".")[0] in allowed
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline boundary crossed")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    assert archive.archive(capture).status == Status.ARCHIVED
    assert archive.replay().complete
    assert archive.archive(capture).status == Status.DUPLICATE_NOOP


def test_concurrent_writers_publish_once(archive, capture):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: RawEvidenceArchive(archive.root).archive(capture), range(12)))
    assert sum(r.status == Status.ARCHIVED for r in results) == 1
    assert sum(r.status == Status.DUPLICATE_NOOP for r in results) == 11
    assert len(archive.replay().records) == 1


@pytest.mark.parametrize("target", ["raw.bin", "metadata.json", "manifest.json"])
def test_symlink_object_is_corruption(archive, capture, tmp_path, target):
    archive.archive(capture)
    path = archive.root / "00000000000000000001" / target
    outside = tmp_path / "outside"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    assert archive.replay().error_status == Status.CORRUPTION_ERROR


def test_nonexistent_and_symlink_root_are_not_created_or_followed(tmp_path, capture):
    absent = RawEvidenceArchive(tmp_path / "absent")
    assert absent.archive(capture).status == Status.PERSISTENCE_ERROR
    assert not absent.root.exists()
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert RawEvidenceArchive(link).archive(capture).status == Status.PERSISTENCE_ERROR
    assert not list(target.iterdir())


def test_manifest_sequence_gap_is_corruption(archive, capture):
    archive.archive(capture)
    (archive.root / "00000000000000000001").rename(archive.root / "00000000000000000002")
    assert archive.replay().error_status == Status.CORRUPTION_ERROR


def test_duplicate_records_across_groups_are_marked(archive, capture):
    payload = json.loads(RAW)
    payload.append(copy.deepcopy(payload[0]))
    payload[1]["history"][0]["c"] = 101
    body(capture, payload)
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture["quality_flags"] += ["DUPLICATE_RECORDS", "CONFLICTING_DUPLICATES"]
    assert archive.archive(capture).status == Status.ARCHIVED


def test_empty_history_explicitly_missing(archive, capture):
    body(capture, [{"symbol": "BTCUSDT_PERP.A", "history": []}])
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture["quality_flags"].append("MISSING_RECORDS")
    assert archive.archive(capture).status == Status.ARCHIVED


@pytest.mark.parametrize("field", ["provider_api_version_or_unavailable_reason", "content_type_or_unavailable_reason"])
def test_optional_values_require_reason(archive, capture, field):
    capture[field] = {"value": None, "unavailable_reason": None}
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture[field] = {"value": None, "unavailable_reason": "not supplied"}
    assert archive.archive(capture).status == Status.ARCHIVED


def test_no_timestamp_derived_from_file_mtime(archive, capture):
    first = archive.archive(capture)
    for path in archive.root.rglob("*"):
        os.utime(path, (0, 0))
    assert archive.replay().records[0].evidence == first.evidence


def test_transport_error_is_explicit(archive, capture):
    capture["http_status_or_transport_error"] = {"http_status": None, "transport_error": "timeout"}
    capture["raw_response_bytes"] = b""
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    capture["capture_status"] = "INCOMPLETE_CAPTURE"
    assert archive.archive(capture).status == Status.ARCHIVED


@pytest.mark.parametrize("field", sorted(REQUIRED))
def test_required_fields_rejected_before_any_write(archive, capture, field):
    del capture[field]
    assert archive.archive(capture).status == Status.VALIDATION_ERROR
    assert list(archive.root.iterdir()) == []


def test_long_multiline_provider_error_is_preserved(archive, capture):
    capture["capture_status"] = "PROVIDER_ERROR"
    body(capture, {"error": "details\n" * 1000})
    assert archive.archive(capture).status == Status.ARCHIVED
    assert archive.replay().records[0].raw_response_bytes == capture["raw_response_bytes"]


def test_failed_next_append_preserves_committed_evidence(archive, capture, monkeypatch):
    first = archive.archive(capture)
    before = snapshot(archive.root)
    capture["request_started_at"] = "2026-09-15T00:10:01Z"
    real = implementation._write_object
    def fail(directory, name, content):
        real(directory, name, content)
        raise OSError("injected write interruption")
    monkeypatch.setattr(implementation, "_write_object", fail)
    assert archive.archive(capture).status == Status.PERSISTENCE_ERROR
    replay = archive.replay()
    assert len(replay.records) == 1 and replay.records[0].evidence == first.evidence
    assert replay.pending and not replay.complete
    after = snapshot(archive.root)
    assert all(after[path] == content for path, content in before.items())


def test_concurrent_distinct_captures_have_contiguous_sequence(archive, capture):
    captures = []
    for i in range(8):
        item = copy.deepcopy(capture)
        item["request_started_at"] = f"2026-09-15T00:09:{i:02d}Z"
        captures.append(item)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda c: RawEvidenceArchive(archive.root).archive(c), captures))
    assert all(r.status == Status.ARCHIVED for r in results)
    assert sorted(r.evidence["manifest_sequence"] for r in results) == list(range(1, 9))
    replay = archive.replay()
    assert replay.complete and len(replay.records) == 8


def test_altered_metadata_with_updated_digest_still_fails_identity_check(archive, capture):
    archive.archive(capture)
    directory = archive.root / "00000000000000000001"
    metadata = json.loads((directory / "metadata.json").read_bytes())
    metadata["canonical_request_identity"]["requested_symbol"] = "OTHER"
    altered = canonical(metadata)
    (directory / "metadata.json").write_bytes(altered)
    manifest = json.loads((directory / "manifest.json").read_bytes())
    manifest["metadata_digest"] = hashlib.sha256(altered).hexdigest()
    (directory / "manifest.json").write_bytes(canonical(manifest))
    assert archive.replay().error_status == Status.CORRUPTION_ERROR


def test_no_valid_prefix_returned_when_later_bundle_is_corrupt(archive, capture):
    archive.archive(capture)
    capture["request_started_at"] = "2026-09-15T00:10:01Z"
    archive.archive(capture)
    (archive.root / "00000000000000000002/raw.bin").write_bytes(b"wrong")
    replay = archive.replay()
    assert replay.error_status == Status.CORRUPTION_ERROR
    assert replay.records == ()


def test_unknown_symlink_reported_without_following(archive, capture, tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"untouched")
    (archive.root / ".pending-link").symlink_to(outside)
    replay = archive.replay()
    assert replay.pending == (".pending-link",) and not replay.records
    assert archive.archive(capture).status == Status.PERSISTENCE_ERROR
    assert outside.read_bytes() == b"untouched"


def test_nonregular_committed_object_fails_without_blocking(archive, capture):
    archive.archive(capture)
    path = archive.root / "00000000000000000001/raw.bin"
    path.unlink()
    os.mkfifo(path)
    assert archive.replay().error_status == Status.CORRUPTION_ERROR
