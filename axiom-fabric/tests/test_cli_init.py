"""`af init` is clean by default; `af init --demo` seeds the three example layers."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from axiom_fabric.cli import app
from axiom_fabric.db import session_scope
from axiom_fabric.layers import list_layers


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _layer_count() -> int:
    with session_scope() as session:
        return len(list_layers(session))


def test_init_clean_creates_no_layers(runner):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.stderr
    assert _layer_count() == 0
    assert "Clean store" in result.stdout


def test_init_demo_seeds_three_layers(runner):
    result = runner.invoke(app, ["init", "--demo"])
    assert result.exit_code == 0, result.stderr
    assert _layer_count() == 3
    assert "canonical" in result.stdout


def test_init_skip_seed_alias_still_clean(runner):
    # --skip-seed is a hidden, deprecated no-op; behaves like the clean default.
    result = runner.invoke(app, ["init", "--skip-seed"])
    assert result.exit_code == 0
    assert _layer_count() == 0
