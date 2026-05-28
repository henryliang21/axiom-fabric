from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from axiom_fabric.cli import app
from axiom_fabric.db import session_scope
from axiom_fabric.facts import RETRACTION_NOTE, list_facts
from axiom_fabric.layers import get_layer_by_name, seed_default_layers


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


def test_fact_create_inserts_fact_and_version(runner, seeded):
    result = runner.invoke(
        app,
        [
            "fact", "create",
            "--layer", "canonical",
            "--content", '{"claim": "sky is blue"}',
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert "Created fact" in result.stdout

    with session_scope() as session:
        facts = list_facts(session, get_layer_by_name(session, "canonical"))
        assert len(facts) == 1
        fact = facts[0]
        assert len(fact.versions) == 1
        assert fact.versions[0].content == {"claim": "sky is blue"}
        # Default weight comes from the layer (canonical=90).
        assert fact.versions[0].weight == 90


def test_fact_create_rejects_non_object_content(runner, seeded):
    result = runner.invoke(
        app,
        ["fact", "create", "--layer", "canonical", "--content", '"just a string"'],
    )
    assert result.exit_code == 1
    assert "must be a JSON object" in result.stderr


def test_fact_create_rejects_bad_json(runner, seeded):
    result = runner.invoke(
        app,
        ["fact", "create", "--layer", "canonical", "--content", "{not json"],
    )
    assert result.exit_code == 1
    assert "not valid JSON" in result.stderr


def test_fact_create_unknown_layer(runner, seeded):
    result = runner.invoke(
        app,
        ["fact", "create", "--layer", "nope", "--content", "{}"],
    )
    assert result.exit_code == 1
    assert "No such layer" in result.stderr


def test_fact_update_appends_version_and_carries_weight(runner, seeded):
    runner.invoke(
        app,
        [
            "fact", "create",
            "--layer", "living",
            "--content", '{"claim": "v1"}',
            "--weight", "42",
        ],
    )
    fact_id = _only_fact_id("living")

    result = runner.invoke(
        app,
        [
            "fact", "update",
            "--fact-id", fact_id,
            "--content", '{"claim": "v2"}',
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert "Appended version" in result.stdout

    with session_scope() as session:
        fact = list_facts(session, get_layer_by_name(session, "living"))[0]
        assert [v.version for v in fact.versions] == [1, 2]
        assert fact.versions[1].content == {"claim": "v2"}
        # Weight is carried forward from v1 when --weight is omitted.
        assert fact.versions[1].weight == 42


def test_fact_update_with_edge_to_prior_version(runner, seeded):
    # Create a parent in canonical and a separate fact that we'll update to cite it.
    runner.invoke(
        app,
        ["fact", "create", "--layer", "canonical", "--content", '{"claim": "parent"}'],
    )
    parent_fact_id = _only_fact_id("canonical")
    with session_scope() as session:
        parent_fv_id = str(
            list_facts(session, get_layer_by_name(session, "canonical"))[0].versions[0].id
        )

    runner.invoke(
        app,
        ["fact", "create", "--layer", "living", "--content", '{"claim": "child v1"}'],
    )
    child_fact_id = _only_fact_id("living")

    result = runner.invoke(
        app,
        [
            "fact", "update",
            "--fact-id", child_fact_id,
            "--content", '{"claim": "child v2 with citation"}',
            "--edges-to", parent_fv_id,
        ],
    )
    assert result.exit_code == 0, result.stderr

    # Edge was created from child v2 -> parent v1.
    with session_scope() as session:
        from axiom_fabric.facts import edges_for
        child_v2 = list_facts(session, get_layer_by_name(session, "living"))[0].versions[-1]
        out, _ = edges_for(session, child_v2.id)
        assert len(out) == 1
        assert str(out[0].target_fv_id) == parent_fv_id
        assert out[0].edge_kind == "derived_from"

    # Sanity: still independent facts.
    assert parent_fact_id != child_fact_id


def test_fact_update_rejects_forward_edge_reference(runner, seeded):
    runner.invoke(
        app,
        ["fact", "create", "--layer", "canonical", "--content", '{"x": 1}'],
    )
    fact_id = _only_fact_id("canonical")
    bogus_uuid = "00000000-0000-0000-0000-000000000000"

    result = runner.invoke(
        app,
        [
            "fact", "update",
            "--fact-id", fact_id,
            "--content", '{"x": 2}',
            "--edges-to", bogus_uuid,
        ],
    )
    assert result.exit_code == 1
    assert "Edge target does not exist" in result.stderr


def test_fact_retract_appends_tombstone(runner, seeded):
    runner.invoke(
        app,
        ["fact", "create", "--layer", "episodic", "--content", '{"event": "noted"}'],
    )
    fact_id = _only_fact_id("episodic")

    result = runner.invoke(app, ["fact", "retract", "--fact-id", fact_id])
    assert result.exit_code == 0, result.stderr
    assert "Retracted fact" in result.stdout

    with session_scope() as session:
        fact = list_facts(session, get_layer_by_name(session, "episodic"))[0]
        # v1 still exists; v2 is the tombstone.
        assert [v.version for v in fact.versions] == [1, 2]
        tombstone = fact.versions[-1]
        assert tombstone.weight == 0
        assert tombstone.content == {}
        assert tombstone.note == RETRACTION_NOTE


def test_fact_retract_custom_note(runner, seeded):
    runner.invoke(
        app,
        ["fact", "create", "--layer", "living", "--content", '{"x": 1}'],
    )
    fact_id = _only_fact_id("living")

    runner.invoke(app, ["fact", "retract", "--fact-id", fact_id, "--note", "superseded by upstream"])

    with session_scope() as session:
        fact = list_facts(session, get_layer_by_name(session, "living"))[0]
        assert fact.versions[-1].note == "superseded by upstream"


def test_fact_list_latest_only_by_default(runner, seeded):
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"a": 1}'])
    fact_id = _only_fact_id("canonical")
    runner.invoke(app, ["fact", "update", "--fact-id", fact_id, "--content", '{"a": 2}'])

    result = runner.invoke(app, ["fact", "list"])
    assert result.exit_code == 0
    # Rich may truncate UUIDs in narrow terminals; match by first 8 chars.
    prefix = fact_id[:8]
    assert result.stdout.count(prefix) == 1
    assert '{"a": 2}' in result.stdout


def test_fact_list_all_versions_shows_each(runner, seeded):
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"a": 1}'])
    fact_id = _only_fact_id("canonical")
    runner.invoke(app, ["fact", "update", "--fact-id", fact_id, "--content", '{"a": 2}'])

    result = runner.invoke(app, ["fact", "list", "--all-versions"])
    assert result.exit_code == 0
    prefix = fact_id[:8]
    assert result.stdout.count(prefix) == 2


def test_fact_list_filter_by_layer(runner, seeded):
    runner.invoke(app, ["fact", "create", "--layer", "canonical", "--content", '{"a": 1}'])
    runner.invoke(app, ["fact", "create", "--layer", "living", "--content", '{"b": 2}'])

    result = runner.invoke(app, ["fact", "list", "--layer", "living"])
    assert result.exit_code == 0
    assert '{"b": 2}' in result.stdout
    assert '{"a": 1}' not in result.stdout


def test_fact_list_empty(runner, seeded):
    result = runner.invoke(app, ["fact", "list"])
    assert result.exit_code == 0
    assert "No facts" in result.stdout


def test_unknown_fact_id_returns_error(runner, seeded):
    bogus = "11111111-1111-1111-1111-111111111111"
    result = runner.invoke(app, ["fact", "update", "--fact-id", bogus, "--content", "{}"])
    assert result.exit_code == 1
    assert "No fact" in result.stderr
