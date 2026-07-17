"""Tests for the Tool Selection Engine — exceptions module.

Sprint 4.2
"""

from __future__ import annotations

import sys
import textwrap

sys.path.insert(0, "packages/tools")
from tests.conftest import clear_src_cache  # noqa: E402
clear_src_cache()

import pytest  # noqa: E402

from src.exceptions import (  # noqa: E402
    AmbiguousSelectionError,
    FilterError,
    NoToolFoundError,
    RankingError,
    ToolSelectionError,
)


# ======================================================================
# ToolSelectionError
# ======================================================================


class TestToolSelectionError:
    def test_is_base_exception(self) -> None:
        assert issubclass(ToolSelectionError, Exception)

    def test_default_details_empty(self) -> None:
        err = ToolSelectionError("oops")
        assert err.details == {}

    def test_custom_details(self) -> None:
        err = ToolSelectionError("oops", details={"key": "value"})
        assert err.details == {"key": "value"}

    def test_message_preserved(self) -> None:
        err = ToolSelectionError("custom message")
        assert str(err) == "custom message"

    def test_catch_with_base(self) -> None:
        with pytest.raises(ToolSelectionError):
            raise NoToolFoundError()
        with pytest.raises(ToolSelectionError):
            raise AmbiguousSelectionError()
        with pytest.raises(ToolSelectionError):
            raise RankingError()

    def test_details_not_in_str(self) -> None:
        """Details dict is attached but not in the string repr."""
        err = ToolSelectionError("msg", details={"a": "b"})
        assert "msg" in str(err)
        assert "details" not in str(err)


# ======================================================================
# NoToolFoundError
# ======================================================================


class TestNoToolFoundError:
    def test_inherits_base(self) -> None:
        assert issubclass(NoToolFoundError, ToolSelectionError)

    def test_default_message(self) -> None:
        err = NoToolFoundError()
        assert "No tool found" in str(err)

    def test_custom_message(self) -> None:
        err = NoToolFoundError("nothing here")
        assert str(err) == "nothing here"

    def test_criteria_stored(self) -> None:
        err = NoToolFoundError(
            criteria={"task": "read_file", "caps": "file"}
        )
        assert err.criteria == {"task": "read_file", "caps": "file"}

    def test_criteria_in_details(self) -> None:
        err = NoToolFoundError(
            criteria={"caps": "file,web"}
        )
        assert "caps" in err.details["criteria"]


# ======================================================================
# AmbiguousSelectionError
# ======================================================================


class TestAmbiguousSelectionError:
    def test_inherits_base(self) -> None:
        assert issubclass(AmbiguousSelectionError, ToolSelectionError)

    def test_default_message(self) -> None:
        err = AmbiguousSelectionError()
        assert "tied" in str(err).lower()

    def test_candidates_and_score(self) -> None:
        err = AmbiguousSelectionError(
            candidates=["tool_a", "tool_b"],
            score=0.85,
        )
        assert err.candidates == ["tool_a", "tool_b"]
        assert err.score == 0.85

    def test_details_contain_candidates(self) -> None:
        err = AmbiguousSelectionError(
            candidates=["a", "b", "c"],
            score=0.9,
        )
        assert "a, b, c" in err.details["candidates"]
        assert err.details["score"] == "0.9"

    def test_empty_candidates(self) -> None:
        err = AmbiguousSelectionError()
        assert err.candidates == []
        assert err.score is None


# ======================================================================
# RankingError
# ======================================================================


class TestRankingError:
    def test_inherits_base(self) -> None:
        assert issubclass(RankingError, ToolSelectionError)

    def test_tool_name_stored(self) -> None:
        err = RankingError("bad score", tool_name="file")
        assert err.tool_name == "file"

    def test_details_contain_tool_name(self) -> None:
        err = RankingError("bad score", tool_name="terminal")
        assert err.details["tool_name"] == "terminal"

    def test_no_tool_name(self) -> None:
        err = RankingError("generic failure")
        assert err.tool_name is None
        assert err.details == {}


# ======================================================================
# FilterError
# ======================================================================


class TestFilterError:
    def test_inherits_base(self) -> None:
        assert issubclass(FilterError, ToolSelectionError)

    def test_filter_name_stored(self) -> None:
        err = FilterError("fail", filter_name="safety")
        assert err.filter_name == "safety"

    def test_details_contain_filter_name(self) -> None:
        err = FilterError("fail", filter_name="cost")
        assert err.details["filter_name"] == "cost"

    def test_no_filter_name(self) -> None:
        err = FilterError("generic")
        assert err.filter_name is None