"""Smoke test: import + ONNX matcher only (no network, no browser).

Requires: pytest, and a captured (grid.jpg, prompt.png) pair (any Geetest v4
nine sample). Otherwise skips.
"""
import os
from pathlib import Path

import pytest

SAMPLE = Path(__file__).parent / "fixtures"


def test_imports():
    from geetest_nine import (
        BrowserVT,
        GeetestNineClient,
        OnnxMatcher,
        build_w,
    )
    assert callable(build_w)
    assert callable(OnnxMatcher.solve)


def test_onnx_sessions_lazy():
    from geetest_nine.onnx_matcher import sessions

    bb, head = sessions()
    assert bb.get_inputs()[0].name == "image"
    assert set(x.name for x in head.get_inputs()) == {"prompt_features", "cell_features"}


@pytest.mark.skipif(not (SAMPLE / "grid.jpg").exists(), reason="no fixture")
def test_onnx_solve_shape():
    from geetest_nine import OnnxMatcher

    grid = (SAMPLE / "grid.jpg").read_bytes()
    prompt = (SAMPLE / "prompt.png").read_bytes()
    result = OnnxMatcher.solve(grid, prompt)
    assert result is not None
    indices, margin = result
    assert len(indices) == 3
    assert 0 <= min(indices) and max(indices) <= 8
    assert isinstance(margin, float)
