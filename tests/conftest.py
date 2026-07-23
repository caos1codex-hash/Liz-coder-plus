"""Pytest configuration shared by all tests.

Sprint 1 - Prompt 2.

The Liz Coder Plus monorepo has multiple `src/` directories (one per
package and one in the backend). To prevent Python's import system from
mixing them up, we:

  1. Do NOT add any path to sys.path globally here.
  2. Each test module is responsible for inserting the path it needs
     via `sys.path.insert(0, ...)` before importing.
  3. The `src` namespace is shared; whichever path appears first on
     sys.path "wins". To keep tests deterministic, each test module
     clears the cached `src` modules from sys.modules before importing.

The helper `clear_src_cache()` below standardizes step 3.
"""

from __future__ import annotations

import sys


def clear_src_cache() -> None:
    """Drop any cached `src` or `src.*` modules from sys.modules.

    Call this at the top of a test module after inserting the desired
    path into sys.path, but before importing anything from `src.*`.
    """
    for key in [k for k in list(sys.modules) if k == "src" or k.startswith("src.")]:
        sys.modules.pop(key, None)
