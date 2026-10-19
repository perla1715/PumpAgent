"""Focused offline acceptance tests; writes are confined to pytest temporary paths."""
import ast
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import errno
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from pumpagent.pilot.checkpoint import (
    ValidationError, canonical, decode_record, map_recorded_snapshot,
)
from pumpagent.pilot.journal import (
    AppendStatus, CorruptionError, IdentityConflict, append_checkpoint, recover,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/pilot/two_checkpoints_v1.json"
COMMIT_TIME = "2026-09-13T12:03:00Z"


@pytest.fixture
def inputs():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / "pilot_shadow"
    path.mkdir()
    return path


def append(path, value):
    return append_checkpoint(path, value, committed_at=COMMIT_TIME)


def test_01_first_canonical_roundtrip(journal, inputs):
    result = append(journal, inputs[0])
    assert result.status is AppendStatus.APPENDED
    raw = (journal / "00000000000000000001.json").read_bytes()
    assert raw == result.record.canonical_bytes == canonical(result.record.to_dict())
    assert decode_record(raw) == result.record
    with pytest.raises(FrozenInstanceError):
        result.record.canonical_bytes = b"changed"


def test_02_second_link_and_recovery(journal, inputs):
    first = append(journal, inputs[0]).record
    second = append(journal, inputs[1]).record
    assert second.to_dict()["input"]["previous_checkpoint_id"] == first.checkpoint_id
    recovered = recover(journal, symbol="DEMOUSDT")
    assert recovered.last_valid == second
    assert len(recovered.records) == 2
    assert recovered.uncommitted_files == ()


def test_03_replay_old_checkpoint_noop(journal, inputs):
    first = append(journal, inputs[0]).record
    append(journal, inputs[1])
    before = {p.name: p.read_bytes() for p in journal.iterdir()}
    replay = append_checkpoint(journal, inputs[0], committed_at="2026-09-14T00:00:00Z")
    assert replay.status is AppendStatus.DUPLICATE_NOOP and replay.record == first
    assert before == {p.name: p.read_bytes() for p in journal.iterdir()}


def test_04_same_identity_different_content_conflicts(journal, inputs):
    append(journal, inputs[0])
    changed = deepcopy(inputs[0])
    changed["assessments"]["geometry_state"] = "EXPLICITLY_SUPPLIED_LABEL"
    assert map_recorded_snapshot(changed).checkpoint_id == map_recorded_snapshot(inputs[0]).checkpoint_id
    with pytest.raises(IdentityConflict, match="HARD CONFLICT"):
        append(journal, changed)


@pytest.mark.parametrize("field", ["candle_open_time", "candle_close_time", "received_time", "recorded_time"])
@pytest.mark.parametrize("bad", [None, "2026-09-13T12:00:00"])
def test_05_missing_or_naive_timestamp(inputs, field, bad):
    inputs[0][field] = bad
    with pytest.raises(ValidationError):
        map_recorded_snapshot(inputs[0])


def test_05_optional_timestamp_requires_reason(inputs):
    inputs[0]["provider_event_time"] = {"status": "UNAVAILABLE", "reason": ""}
    with pytest.raises(ValidationError):
        map_recorded_snapshot(inputs[0])


@pytest.mark.parametrize("field", ["outcome", "pnl", "P&L", "future_return", "MFE", "MAE", "later_observation"])
@pytest.mark.parametrize("section", [None, "assessments", "normalized_values"])
def test_06_reject_future_fields(inputs, field, section):
    destination = inputs[0] if section is None else inputs[0][section]
    destination[field] = 1
    with pytest.raises(ValidationError):
        map_recorded_snapshot(inputs[0])


@pytest.mark.parametrize("number", [float("nan"), float("inf"), -float("inf"), True])
def test_07_nonfinite_or_boolean_metric(inputs, number):
    inputs[0]["normalized_values"]["price"]["value"] = number
    with pytest.raises(ValidationError):
        map_recorded_snapshot(inputs[0])


@pytest.mark.parametrize("field", ["schema_version", "quality", "versions"])
def test_07_invalid_versions_and_quality(inputs, field):
    if field == "schema_version":
        inputs[0][field] = "v999"
    elif field == "quality":
        inputs[0][field]["status"] = "perfect"
    else:
        inputs[0][field]["schema"] = "v999"
    with pytest.raises(ValidationError):
        map_recorded_snapshot(inputs[0])


@pytest.mark.parametrize("field", ["sha256", "provider", "contract_id", "market_id", "derivation_version"])
def test_08_required_provenance(inputs, field):
    value = inputs[0]
    if field == "sha256":
        del value["source_evidence"][field]
    elif field == "derivation_version":
        provenance = value["normalized_values"]["price"]["provenance"]
        provenance["kind"] = "DERIVED"
        del provenance[field]
    else:
        del value[field]
    with pytest.raises(ValidationError):
        map_recorded_snapshot(value)


def test_09_wrong_link(journal, inputs):
    append(journal, inputs[0])
    inputs[1]["previous_checkpoint_id"] = "pc1:" + "0" * 64
    with pytest.raises(ValidationError, match="previous checkpoint"):
        append(journal, inputs[1])
    assert len(recover(journal, symbol="DEMOUSDT").records) == 1


def test_10_interruption_before_publish(journal, inputs, monkeypatch):
    first = append(journal, inputs[0]).record
    before = {p.name: p.read_bytes() for p in journal.iterdir()}
    def interrupt(*args, **kwargs):
        raise OSError(errno.EIO, "simulated interruption before publication")
    monkeypatch.setattr(os, "link", interrupt)
    with pytest.raises(OSError, match="simulated interruption"):
        append(journal, inputs[1])
    assert before == {p.name: p.read_bytes() for p in journal.iterdir()}
    assert recover(journal, symbol="DEMOUSDT").last_valid == first


@pytest.mark.parametrize("corruption", ["truncated", "changed", "sequence_gap", "noncanonical"])
def test_11_corruption_explicit_failure(journal, inputs, corruption):
    first = append(journal, inputs[0]).record
    append(journal, inputs[1])
    path = journal / "00000000000000000002.json"
    raw = path.read_bytes()
    if corruption == "truncated":
        path.write_bytes(raw[:-9])
    elif corruption == "changed":
        path.write_bytes(raw.replace(b"UNASSESSED", b"ALTERED"))
    elif corruption == "sequence_gap":
        path.rename(journal / "00000000000000000003.json")
    else:
        path.write_bytes(b" " + raw)
    with pytest.raises(CorruptionError) as error:
        recover(journal, symbol="DEMOUSDT")
    assert error.value.last_valid == first
    with pytest.raises(CorruptionError):
        append(journal, inputs[1])


def test_12_unwritable_directory(journal, inputs):
    journal.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            append(journal, inputs[0])
        assert list(journal.iterdir()) == []
    finally:
        journal.chmod(0o700)


def test_13_offline_boundary(journal, inputs, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Forbidden side effect invoked")
    with monkeypatch.context() as patch:
        for owner, name in [(socket, "socket"), (socket, "getaddrinfo"),
                            (subprocess, "Popen"), (os, "system"), (os, "putenv"), (os, "unsetenv")]:
            patch.setattr(owner, name, forbidden)
        append(journal, inputs[0])
        recover(journal, symbol="DEMOUSDT")
    pilot = Path(__file__).resolve().parents[2] / "src/pumpagent/pilot"
    allowed = {"__future__", "contextlib", "csv", "dataclasses", "datetime", "enum", "fcntl", "hashlib",
               "json", "math", "os", "pathlib", "re", "stat", "tempfile", "typing"}
    allowed_project_imports = {
        "pumpagent.live_data.adapters.market_cap_rank",
        "pumpagent.live_data.adapters.bybit_public_market",
        "pumpagent.live_data.adapters.coinalyze_pilot_price",
        "pumpagent.live_data.domain",
        "pumpagent.live_data.validation.validator",
        "pumpagent.pilot.pilot_search_profile",
        "pumpagent.pilot.runtime_handoff",
        "pumpagent.pilot.shadow_admission",
        "pumpagent.runtime.domain",
        "pumpagent.runtime.domain.enums",
        "pumpagent.runtime.domain.observation_episode",
        "pumpagent.runtime.domain.observation_policy",
        "pumpagent.runtime.modules.observation_lifecycle.executor",
        "pumpagent.runtime.modules.observation_lifecycle.runtime_cycle",
        "pumpagent.runtime.modules.watchlist.manager",
        "pumpagent.runtime.modules.watchlist.observation_boundary",
        "pumpagent.runtime.orchestrator.runtime_loop",
    }
    for source in pilot.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.Import):
                assert all(alias.name in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                assert node.module in allowed or node.module in allowed_project_imports


def test_15_fixed_commit_time_identical(journal, tmp_path, inputs):
    other = tmp_path / "other" / "pilot_shadow"
    other.mkdir(parents=True)
    for value in inputs:
        left, right = append(journal, value), append(other, value)
        assert left.record.canonical_bytes == right.record.canonical_bytes
        assert left.record.to_dict()["canonical_digest"] == right.record.to_dict()["canonical_digest"]


def test_missing_optional_value_not_fabricated(journal, inputs):
    result = append(journal, inputs[0])
    assert result.record.to_dict()["input"]["normalized_values"]["cvd"] == inputs[0]["normalized_values"]["cvd"]
    inputs[1]["quality"]["missing_metrics"] = []
    with pytest.raises(ValidationError, match="missingness"):
        append(journal, inputs[1])


def test_pending_crash_file_reported_without_repair(journal, inputs):
    first = append(journal, inputs[0]).record
    path = journal / ".pending-interrupted"
    path.write_bytes(b'{"incomplete')
    result = recover(journal, symbol="DEMOUSDT")
    assert result.last_valid == first and result.uncommitted_files == (path.name,)
    assert path.read_bytes() == b'{"incomplete'


def test_symbol_run_and_path_isolation(journal, inputs):
    append(journal, inputs[0])
    with pytest.raises(ValidationError):
        recover(journal, symbol="OTHER")
    inputs[1]["run_id"] = "other-run"
    with pytest.raises(ValidationError, match="exactly one"):
        append(journal, inputs[1])
    with pytest.raises(ValidationError, match="absolute"):
        recover(Path("pilot_shadow"), symbol="DEMOUSDT")


def test_concurrent_exact_replays_publish_once(journal, inputs):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: append(journal, inputs[0]), range(8)))
    assert sum(r.status is AppendStatus.APPENDED for r in results) == 1
    assert sum(r.status is AppendStatus.DUPLICATE_NOOP for r in results) == 7
    assert len(recover(journal, symbol="DEMOUSDT").records) == 1


def test_fsync_failure_before_publication(journal, inputs, monkeypatch):
    def fail(*args):
        raise OSError(errno.EIO, "staging fsync failed")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="staging fsync"):
        append(journal, inputs[0])
    assert recover(journal, symbol="DEMOUSDT").records == ()
    assert list(journal.iterdir()) == []


def test_postpublication_failure_never_fake_success(journal, inputs, monkeypatch):
    real_fsync = os.fsync
    calls = []
    def fail_directory(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError(errno.EIO, "directory fsync failed")
        return real_fsync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_directory)
        with pytest.raises(OSError, match="directory fsync"):
            append(journal, inputs[0])
    assert len(recover(journal, symbol="DEMOUSDT").records) == 1
    assert append(journal, inputs[0]).status is AppendStatus.DUPLICATE_NOOP


def test_equivalent_timezone_representation_same_identity(inputs):
    changed = deepcopy(inputs[0])
    changed["candle_open_time"] = "2026-09-13T14:00:00+02:00"
    assert map_recorded_snapshot(changed) == map_recorded_snapshot(inputs[0])


def test_numeric_and_interval_overflow_are_validation_errors(inputs):
    invalid = deepcopy(inputs[0])
    invalid["normalized_values"]["price"]["value"] = 10 ** 400
    with pytest.raises(ValidationError):
        map_recorded_snapshot(invalid)
    invalid = deepcopy(inputs[0])
    invalid["timeframe"] = "99999999999999999999999999999d"
    with pytest.raises(ValidationError):
        map_recorded_snapshot(invalid)


@pytest.mark.parametrize("field", ["candle_close_time", "received_time", "recorded_time"])
def test_invalid_interval_or_timestamp_order(inputs, field):
    inputs[0][field] = "2026-09-13T11:00:00Z"
    with pytest.raises(ValidationError):
        map_recorded_snapshot(inputs[0])


def test_provider_time_and_nested_future_fields(inputs):
    inputs[0]["provider_event_time"] = {"status": "AVAILABLE", "value": "2026-09-14T00:00:00Z"}
    with pytest.raises(ValidationError, match="Future"):
        map_recorded_snapshot(inputs[0])
    inputs[0]["provider_event_time"]["value"] = "2026-09-13T12:01:00Z"
    map_recorded_snapshot(inputs[0])
    inputs[0]["normalized_values"]["price"]["provenance"]["later_observation"] = "forbidden"
    with pytest.raises(ValidationError):
        map_recorded_snapshot(inputs[0])


def test_committed_symlink_rejected_without_following(journal, inputs, tmp_path):
    target = tmp_path / "not_a_record"
    target.write_text("not a record")
    (journal / "00000000000000000001.json").symlink_to(target)
    with pytest.raises(CorruptionError, match="not a regular file"):
        recover(journal, symbol="DEMOUSDT")
