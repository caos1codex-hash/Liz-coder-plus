"""Unit tests for planner.utils (Sprint 2.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLANNER_SRC = ROOT / "packages" / "planner" / "src"
sys.path.insert(0, str(PLANNER_SRC))

for _k in [k for k in list(sys.modules) if k == "planner" or k.startswith("planner.")]:
    sys.modules.pop(_k, None)

from planner.utils import (  # noqa: E402
    clamp,
    coerce_float,
    coerce_int,
    merge_dicts,
    new_task_id,
    now_epoch,
    now_iso,
    parse_bool,
    safe_get,
    update_inplace,
)

# ----------------------------------------------------------------------
# new_task_id
# ----------------------------------------------------------------------


def test_new_task_id_has_prefix():
    tid = new_task_id("plan")
    assert tid.startswith("plan-")


def test_new_task_id_unique():
    ids = {new_task_id("t") for _ in range(100)}
    assert len(ids) == 100


def test_new_task_id_default_prefix():
    tid = new_task_id()
    assert tid.startswith("task-")


# ----------------------------------------------------------------------
# now_iso / now_epoch
# ----------------------------------------------------------------------


def test_now_iso_format():
    s = now_iso()
    assert "T" in s
    assert s.endswith("+00:00") or s.endswith("Z")


def test_now_epoch_positive():
    assert now_epoch() > 0


def test_now_epoch_increasing():
    a = now_epoch()
    b = now_epoch()
    assert b >= a


# ----------------------------------------------------------------------
# clamp
# ----------------------------------------------------------------------


def test_clamp_in_range():
    assert clamp(5, 0, 10) == 5


def test_clamp_below():
    assert clamp(-1, 0, 10) == 0


def test_clamp_above():
    assert clamp(11, 0, 10) == 10


def test_clamp_swapped_bounds():
    assert clamp(5, 10, 0) == 5


# ----------------------------------------------------------------------
# coerce_int / coerce_float
# ----------------------------------------------------------------------


def test_coerce_int_from_int():
    assert coerce_int(5) == 5


def test_coerce_int_from_float():
    assert coerce_int(3.7) == 3


def test_coerce_int_from_str():
    assert coerce_int("42") == 42


def test_coerce_int_from_bad_str():
    assert coerce_int("abc", default=-1) == -1


def test_coerce_int_from_bool():
    assert coerce_int(True) == 1


def test_coerce_float_from_str():
    assert coerce_float("3.14") == pytest.approx(3.14)


def test_coerce_float_default():
    assert coerce_float("bad", default=9.9) == 9.9


# ----------------------------------------------------------------------
# merge_dicts
# ----------------------------------------------------------------------


def test_merge_dicts_simple():
    a = {"x": 1, "y": 2}
    b = {"y": 3, "z": 4}
    merged = merge_dicts(a, b)
    assert merged == {"x": 1, "y": 3, "z": 4}


def test_merge_dicts_nested():
    a = {"nested": {"a": 1, "b": 2}}
    b = {"nested": {"b": 3, "c": 4}}
    merged = merge_dicts(a, b)
    assert merged == {"nested": {"a": 1, "b": 3, "c": 4}}


def test_merge_dicts_does_not_mutate():
    a = {"x": 1}
    b = {"y": 2}
    merge_dicts(a, b)
    assert a == {"x": 1}
    assert b == {"y": 2}


def test_merge_dicts_overlay_wins():
    a = {"k": [1, 2]}
    b = {"k": [3]}
    assert merge_dicts(a, b) == {"k": [3]}


# ----------------------------------------------------------------------
# parse_bool
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "val,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("1", True),
        ("on", True),
        ("false", False),
        ("no", False),
        ("0", False),
        ("off", False),
        (1, True),
        (0, False),
    ],
)
def test_parse_bool(val, expected):
    assert parse_bool(val) is expected


def test_parse_bool_default():
    assert parse_bool("garbage", default=True) is True


# ----------------------------------------------------------------------
# safe_get
# ----------------------------------------------------------------------


def test_safe_get_nested():
    d = {"a": {"b": {"c": 1}}}
    assert safe_get(d, "a.b.c") == 1


def test_safe_get_missing():
    assert safe_get({}, "a.b.c", default="x") == "x"


def test_safe_get_partial():
    d = {"a": {"b": 1}}
    assert safe_get(d, "a.b.c", default=None) is None


# ----------------------------------------------------------------------
# update_inplace
# ----------------------------------------------------------------------


def test_update_inplace_sets_keys():
    target = {"a": 1}
    update_inplace(target, b=2, c=3)
    assert target["a"] == 1
    assert target["b"] == 2
    assert target["c"] == 3
    assert "updated_at" in target
