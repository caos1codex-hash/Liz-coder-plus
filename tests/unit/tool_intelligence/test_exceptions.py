"""Tests for tool_intelligence.exceptions.

Sprint 4.1 — Tool Intelligence Foundation.
"""

from __future__ import annotations

import pytest
from tool_intelligence.exceptions import (
    CapabilityMissing,
    RankingError,
    ToolIntelligenceError,
    ToolNotCompatible,
    ToolSelectionError,
)


class TestToolIntelligenceError:
    def test_base_error_message(self) -> None:
        err = ToolIntelligenceError("something went wrong")
        assert err.message == "something went wrong"
        assert err.details == {}
        assert str(err) == "something went wrong"

    def test_base_error_with_details(self) -> None:
        err = ToolIntelligenceError("oops", details={"code": 42})
        assert err.details == {"code": 42}

    def test_to_dict(self) -> None:
        err = ToolIntelligenceError("bad", details={"k": "v"})
        d = err.to_dict()
        assert d["error"] == "ToolIntelligenceError"
        assert d["message"] == "bad"
        assert d["details"] == {"k": "v"}

    def test_all_subclasses_inherit(self) -> None:
        for exc_cls in (
            CapabilityMissing,
            RankingError,
            ToolNotCompatible,
            ToolSelectionError,
        ):
            assert issubclass(exc_cls, ToolIntelligenceError)


class TestCapabilityMissing:
    def test_default_message(self) -> None:
        err = CapabilityMissing("filesystem.read", available_tools=["a", "b"])
        assert err.required_capability == "filesystem.read"
        assert err.available_tools == ["a", "b"]
        assert "filesystem.read" in str(err)
        assert "a" in str(err)

    def test_no_available_tools(self) -> None:
        err = CapabilityMissing("code.generate")
        assert err.available_tools == []
        assert "none" in str(err)

    def test_custom_message(self) -> None:
        err = CapabilityMissing(
            "code.generate", message="custom error message"
        )
        assert str(err) == "custom error message"
        assert err.required_capability == "code.generate"

    def test_to_dict_includes_details(self) -> None:
        err = CapabilityMissing("cap.x", available_tools=["t1"])
        d = err.to_dict()
        assert d["error"] == "CapabilityMissing"
        assert d["details"]["required_capability"] == "cap.x"
        assert d["details"]["available_tools"] == ["t1"]


class TestToolNotCompatible:
    def test_message_and_details(self) -> None:
        err = ToolNotCompatible(
            "tool X is incompatible",
            details={"failures": ["platform"]},
        )
        assert err.message == "tool X is incompatible"
        assert err.details == {"failures": ["platform"]}


class TestToolSelectionError:
    def test_message(self) -> None:
        err = ToolSelectionError("no candidates")
        assert err.message == "no candidates"
        assert isinstance(err, ToolIntelligenceError)


class TestRankingError:
    def test_message(self) -> None:
        err = RankingError("cannot score")
        assert err.message == "cannot score"
        assert isinstance(err, ToolIntelligenceError)


class TestExceptionChaining:
    def test_can_raise_and_catch_as_base(self) -> None:
        with pytest.raises(ToolIntelligenceError):
            raise ToolNotCompatible("x")

    def test_can_raise_and_catch_specific(self) -> None:
        with pytest.raises(CapabilityMissing):
            raise CapabilityMissing("cap")
