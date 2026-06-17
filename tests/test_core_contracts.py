"""Construction and serialization tests for APEX V3 canonical contracts."""

from __future__ import annotations

import importlib
import json

from core.contracts import (
    ATSStructureSnapshot,
    ExecutionIntent,
    ExecutionReport,
    MarketSnapshot,
    OrderFlowBehavior,
    PhaseClassification,
    PositionDecision,
    SignalCandidate,
    StrategySelection,
    TelemetryEvent,
    WyckoffICTContext,
)
from core.contracts.enums import Instrument


CONTRACT_TYPES = [
    MarketSnapshot,
    ATSStructureSnapshot,
    WyckoffICTContext,
    OrderFlowBehavior,
    PhaseClassification,
    StrategySelection,
    SignalCandidate,
    PositionDecision,
    ExecutionIntent,
    ExecutionReport,
    TelemetryEvent,
]


def test_contracts_construct_and_serialize_to_json() -> None:
    for contract_type in CONTRACT_TYPES:
        contract = contract_type()
        payload = contract.to_dict()
        encoded = contract.to_json()

        assert isinstance(payload, dict)
        assert json.loads(encoded) == payload
        if "instrument" in payload and payload["instrument"] is not None:
            assert payload["instrument"] == "GC"


def test_core_instrument_vocabulary_is_gc_only() -> None:
    assert [instrument.value for instrument in Instrument] == ["GC"]


def test_contract_modules_do_not_import_broker_sdks() -> None:
    modules = [
        "core.contracts.enums",
        "core.contracts.market",
        "core.contracts.context",
        "core.contracts.phase",
        "core.contracts.signal",
        "core.contracts.risk",
        "core.contracts.execution",
        "core.contracts.telemetry",
    ]
    for module_name in modules:
        module = importlib.import_module(module_name)
        assert "MetaTrader5" not in vars(module)
        assert "mt5" not in vars(module)
