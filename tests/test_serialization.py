from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from frame_timing_agent.contracts import (
    POLICY_REVISION,
    SCHEMA_VERSION,
    PolicyName,
    RiskLevel,
    StrategyCandidate,
    StrategyRequest,
)
from frame_timing_agent.serialization import canonical_json_bytes, sha256_digest


@dataclass(frozen=True)
class _Payload:
    policy: PolicyName
    sources: tuple[int, ...]
    report_path: Path


def _candidate(*, confidence: float = 0.9) -> StrategyCandidate:
    return StrategyCandidate(
        schema_version=SCHEMA_VERSION,
        strategy_id="sha256:strategy",
        policy_revision=POLICY_REVISION,
        input_digest="sha256:input",
        policy=PolicyName.BALANCED,
        request=StrategyRequest(PolicyName.BALANCED, 0.65, 4),
        selected_sources=(0, 2, 4),
        estimated_output_count=3,
        retention_ratio=0.6,
        maximum_consecutive_drops=1,
        maximum_source_index_gap=2,
        maximum_time_gap_seconds=0.1,
        estimated_jitter_reduction=0.2,
        estimated_quality_change=0.05,
        confidence=confidence,
        risk_level=RiskLevel.MEDIUM,
        reasons=("high_confidence_jitter_removed",),
    )


def test_canonical_json_is_independent_of_mapping_order_and_whitespace() -> None:
    first = {"name": "帧策略", "values": [1, 2], "nested": {"b": True, "a": None}}
    second = json.loads(' { "nested" : { "a": null, "b": true }, "values": [1,2], "name": "帧策略" } ')

    encoded = canonical_json_bytes(first)

    assert encoded == canonical_json_bytes(second)
    assert encoded.decode("utf-8") == '{"name":"帧策略","nested":{"a":null,"b":true},"values":[1,2]}'
    assert b"\\u" not in encoded


def test_canonical_json_normalizes_supported_structured_types() -> None:
    payload = _Payload(PolicyName.COVERAGE_FIRST, (1, 3), Path("analysis/report.json"))

    decoded = json.loads(canonical_json_bytes(payload))

    assert decoded == {
        "policy": "coverage_first",
        "report_path": "analysis/report.json",
        "sources": [1, 3],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": float("-inf")},
        {"path": Path.cwd().resolve() / "private.json"},
        {"path": Path("/rooted.json")},
        {"path": Path("../outside.json")},
        {"unsupported": {1, 2}},
        {1: "non-string-key"},
        object(),
    ],
)
def test_canonical_json_rejects_unsafe_or_unsupported_values(payload: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_json_bytes(payload)


def test_digest_changes_for_close_float_values_without_rounding() -> None:
    first = _candidate(confidence=0.1234567890123456)
    second = _candidate(confidence=0.1234567890123457)

    assert sha256_digest(first) != sha256_digest(second)


def test_candidate_digest_survives_canonical_json_round_trip() -> None:
    candidate = _candidate()
    payload = json.loads(canonical_json_bytes(candidate))

    assert payload["policy_revision"] == POLICY_REVISION
    assert sha256_digest(candidate) == sha256_digest(payload)
    assert sha256_digest(candidate).startswith("sha256:")
