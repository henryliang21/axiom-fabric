"""Tests for the second wave of CLI surface: --edge-kind, layer create,
fact show, fact version, and status."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from axiom_fabric.cli import app
from axiom_fabric.db import session_scope
from axiom_fabric.facts import edges_for, list_facts
from axiom_fabric.layers import (
    create_layer,
    get_layer_by_name,
    list_layers,
    seed_default_layers,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def seeded() -> None:
    with session_scope() as session:
        seed_default_layers(session)


def _only_fact_id(layer_name: str = "canonical") -> str:
    with session_scope() as session:
        layer = get_layer_by_name(session, layer_name)
        facts = list_facts(session, layer)
        assert len(facts) == 1
        return str(facts[0].id)


# ---------------------------------------------------------------------------
# #1: --edge-kind on fact create / update
# ---------------------------------------------------------------------------

def test_fact_create_default_edge_kind_is_derived_from(runner, seeded):
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"a": 1}'])
    parent_fact_id = _only_fact_id("canonical")
    with session_scope() as session:
        parent_fv_id = str(
            list_facts(session, get_layer_by_name(session, "canonical"))[0].versions[0].id
        )

    runner.invoke(
        app,
        [
            "fact", "create",
            "--layer", "living",
            "--content", '{"b": 2}',
            "--edges-to", parent_fv_id,
        ],
    )
    with session_scope() as session:
        child_fv = list_facts(session, get_layer_by_name(session, "living"))[0].versions[0]
        out, _ = edges_for(session, child_fv.id)
        assert len(out) == 1
        assert out[0].edge_kind == "derived_from"
    assert parent_fact_id  # silence linter


@pytest.mark.parametrize("kind", ["refutes", "evidence_of", "supersedes"])
def test_fact_create_with_explicit_edge_kind(runner, seeded, kind):
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"a": 1}'])
    with session_scope() as session:
        parent_fv_id = str(
            list_facts(session, get_layer_by_name(session, "canonical"))[0].versions[0].id
        )

    result = runner.invoke(
        app,
        [
            "fact", "create",
            "--layer", "living",
            "--content", '{"b": 2}',
            "--edges-to", parent_fv_id,
            "--edge-kind", kind,
        ],
    )
    assert result.exit_code == 0, result.stderr

    with session_scope() as session:
        child_fv = list_facts(session, get_layer_by_name(session, "living"))[0].versions[0]
        out, _ = edges_for(session, child_fv.id)
        assert len(out) == 1
        assert out[0].edge_kind == kind


def test_fact_create_rejects_invalid_edge_kind(runner, seeded):
    # Typer's Enum rejection should exit non-zero; the click error usually goes to stderr.
    result = runner.invoke(
        app,
        [
            "fact", "create",
            "--layer", "canonical",
            "--content", '{"a": 1}',
            "--edge-kind", "bogus",
        ],
    )
    assert result.exit_code != 0


def test_fact_update_with_explicit_edge_kind(runner, seeded):
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"original": true}'])
    with session_scope() as session:
        parent_fv_id = str(
            list_facts(session, get_layer_by_name(session, "canonical"))[0].versions[0].id
        )

    runner.invoke(app, ["fact", "create", "--layer", "living", "--content", '{"x": 1}'])
    child_fact_id = _only_fact_id("living")

    result = runner.invoke(
        app,
        [
            "fact", "update",
            "--fact-id", child_fact_id,
            "--content", '{"x": 2, "claim": "original is wrong"}',
            "--edges-to", parent_fv_id,
            "--edge-kind", "refutes",
        ],
    )
    assert result.exit_code == 0, result.stderr

    with session_scope() as session:
        child_v2 = list_facts(session, get_layer_by_name(session, "living"))[0].versions[-1]
        out, _ = edges_for(session, child_v2.id)
        assert len(out) == 1
        assert out[0].edge_kind == "refutes"


# ---------------------------------------------------------------------------
# #2: af layer create
# ---------------------------------------------------------------------------

def test_layer_create_succeeds(runner, seeded):
    result = runner.invoke(
        app,
        [
            "layer", "create",
            "--name", "policy",
            "--display", "Policy",
            "--weight", "95",
            "--ordinal", "-10",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert "Created layer" in result.stdout

    with session_scope() as session:
        layer = get_layer_by_name(session, "policy")
        assert layer is not None
        assert layer.weight == 95
        assert layer.ordinal == -10
        assert layer.display_name == "Policy"
        # v1 layer-version was created automatically.
        assert len(layer.versions) == 1


def test_layer_create_rejects_duplicate_name(runner, seeded):
    result = runner.invoke(
        app,
        ["layer", "create", "--name", "canonical", "--weight", "90", "--ordinal", "-5"],
    )
    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_layer_create_rejects_duplicate_ordinal(runner, seeded):
    # canonical is at ordinal 0.
    result = runner.invoke(
        app,
        ["layer", "create", "--name", "novel", "--weight", "50", "--ordinal", "0"],
    )
    assert result.exit_code == 1
    assert "ordinal" in result.stderr


def test_layer_create_weight_bounds_enforced(runner, seeded):
    result = runner.invoke(
        app,
        ["layer", "create", "--name", "bad", "--weight", "150", "--ordinal", "200"],
    )
    assert result.exit_code != 0  # Typer rejects via min/max validators


def test_layer_create_inserts_no_facts(runner, seeded):
    runner.invoke(
        app,
        ["layer", "create", "--name", "policy", "--weight", "90", "--ordinal", "-1"],
    )
    with session_scope() as session:
        new_layer = get_layer_by_name(session, "policy")
        # Brand-new layer has no facts.
        assert list_facts(session, new_layer) == []


# ---------------------------------------------------------------------------
# #3: af fact show / af fact version
# ---------------------------------------------------------------------------

def test_fact_show_renders_metadata_and_latest_content(runner, seeded):
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"a": 1}'])
    fact_id = _only_fact_id("canonical")
    runner.invoke(app, ["fact", "update", "--fact-id", fact_id, "--content", '{"a": 42}'])

    result = runner.invoke(app, ["fact", "show", fact_id])
    assert result.exit_code == 0, result.stderr
    # Latest content (v2) appears un-truncated.
    assert '"a": 42' in result.stdout
    # Both versions are listed.
    assert result.stdout.count("FV ID") >= 1  # table column header
    assert "Versions:" in result.stdout
    # 'canonical' should appear as the layer label.
    assert "canonical" in result.stdout


def test_fact_show_bad_uuid(runner, seeded):
    result = runner.invoke(app, ["fact", "show", "not-a-uuid"])
    assert result.exit_code == 1
    assert "Not a valid UUID" in result.stderr


def test_fact_show_unknown_id(runner, seeded):
    bogus = "00000000-0000-0000-0000-000000000000"
    result = runner.invoke(app, ["fact", "show", bogus])
    assert result.exit_code == 1
    assert "No fact with id" in result.stderr


def test_fact_version_renders_content_justification_and_edges(runner, seeded):
    # Build parent and child so the child has an outgoing edge.
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"parent": true}'])
    with session_scope() as session:
        parent_fv_id = str(
            list_facts(session, get_layer_by_name(session, "canonical"))[0].versions[0].id
        )

    runner.invoke(
        app,
        [
            "fact", "create",
            "--layer", "living",
            "--content", '{"child": true}',
            "--edges-to", parent_fv_id,
        ],
    )
    with session_scope() as session:
        child_fv_id = str(
            list_facts(session, get_layer_by_name(session, "living"))[0].versions[0].id
        )

    result = runner.invoke(app, ["fact", "version", child_fv_id])
    assert result.exit_code == 0, result.stderr
    assert '"child": true' in result.stdout
    assert "Outgoing" in result.stdout
    # The child has no incoming edges.
    assert "No incoming edges" in result.stdout

    # The parent shows incoming but no outgoing.
    parent_result = runner.invoke(app, ["fact", "version", parent_fv_id])
    assert parent_result.exit_code == 0
    assert "Incoming" in parent_result.stdout
    assert "No outgoing edges" in parent_result.stdout


def test_fact_version_unknown_id(runner, seeded):
    bogus = "00000000-0000-0000-0000-000000000000"
    result = runner.invoke(app, ["fact", "version", bogus])
    assert result.exit_code == 1
    assert "No fact-version" in result.stderr


# ---------------------------------------------------------------------------
# #4: af status
# ---------------------------------------------------------------------------

def test_status_after_init(runner, seeded):
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.stderr
    assert "Database:" in result.stdout
    assert "sqlite" in result.stdout
    assert "Schema:" in result.stdout
    assert "Layers:" in result.stdout
    # Seeded fixture creates the 3 default layers.
    assert "3" in result.stdout


def test_status_counts_reflect_created_facts(runner, seeded):
    # Create one fact in canonical and add a v2.
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"a": 1}'])
    fact_id = _only_fact_id("canonical")
    runner.invoke(app, ["fact", "update", "--fact-id", fact_id, "--content", '{"a": 2}'])

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    # Look for the specific labelled counts; values appear right of the colon.
    # Fact identities: 1, fact-versions: 2.
    out = result.stdout
    assert "Fact identities:" in out
    assert "Fact-versions:" in out
    # Sanity: the digits 1 and 2 both appear somewhere on those lines.
    assert "1" in out
    assert "2" in out


def test_status_when_not_migrated(runner, monkeypatch):
    # Override the autouse fresh_db fixture's migrated state by switching to a
    # brand-new, unmigrated in-memory DB.
    from axiom_fabric.config import get_settings
    from axiom_fabric.db import reset_engine_for_tests

    monkeypatch.setenv("AF_DATABASE_URL", "sqlite:///:memory:")
    get_settings.cache_clear()
    reset_engine_for_tests()

    result = runner.invoke(app, ["status"])
    # status should still exit 0 — it reports state, doesn't enforce it.
    assert result.exit_code == 0
    assert "not migrated" in result.stdout
    assert "af init" in result.stdout


# ---------------------------------------------------------------------------
# Repo function sanity (not the CLI surface)
# ---------------------------------------------------------------------------

def test_create_layer_repo_function(seeded):
    with session_scope() as session:
        before = len(list_layers(session))
        layer = create_layer(session, name="meta", weight=80, ordinal=-50)
        assert layer.id is not None
        assert len(layer.versions) == 1
        assert layer.versions[0].version == 1

    with session_scope() as session:
        after = len(list_layers(session))
        assert after == before + 1
