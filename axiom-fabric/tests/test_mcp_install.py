"""The install helper merges config non-destructively and backs up existing files."""

from __future__ import annotations

import json

import pytest

from axiom_fabric.mcp import install as I


@pytest.fixture
def spec():
    return I.build_spec(allow_writes=True, db="/tmp/example/af.db")


def test_build_spec_absolute_db_url(spec):
    # resolve() may canonicalize symlinks (e.g. /tmp -> /private/tmp on macOS),
    # so assert shape rather than an exact path.
    url = spec.env["AF_DATABASE_URL"]
    assert url.startswith("sqlite:////")  # four slashes = absolute sqlite path
    assert url.endswith("af.db")
    assert "serve" in spec.args
    assert "--allow-writes" in spec.args


def test_merge_json_creates_entry(tmp_path, spec):
    path = tmp_path / ".mcp.json"
    content = I.merge_json(path, spec)
    data = json.loads(content)
    entry = data["mcpServers"]["axiom-fabric"]
    assert entry["env"]["AF_DATABASE_URL"].endswith("af.db")
    assert entry["args"][-1] == "--allow-writes"


def test_merge_json_preserves_other_servers(tmp_path, spec):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}),
        encoding="utf-8",
    )
    data = json.loads(I.merge_json(path, spec))
    assert data["theme"] == "dark"  # unrelated keys preserved
    assert data["mcpServers"]["other"] == {"command": "x"}  # other server preserved
    assert "axiom-fabric" in data["mcpServers"]


def test_merge_toml_appends_and_replaces(tmp_path, spec):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "y"\n', encoding="utf-8")

    merged = I.merge_toml(path, spec)
    assert 'model = "gpt-5"' in merged  # top-level key preserved
    assert "[mcp_servers.other]" in merged  # other server preserved
    assert "[mcp_servers.axiom-fabric]" in merged

    # Replacing (idempotent): writing again must not duplicate our table.
    path.write_text(merged, encoding="utf-8")
    merged2 = I.merge_toml(path, spec)
    assert merged2.count("[mcp_servers.axiom-fabric]") == 1


def test_install_writes_and_backs_up(tmp_path, monkeypatch):
    target = tmp_path / ".gemini" / "settings.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"mcpServers": {"keep": {"command": "z"}}}), encoding="utf-8")
    monkeypatch.setattr(I, "target_path", lambda client, *, scope: target)

    summary = I.install("gemini", allow_writes=False, db="/tmp/x/af.db")
    assert str(target) in summary
    assert (tmp_path / ".gemini" / "settings.json.bak").exists()  # backup made
    data = json.loads(target.read_text())
    assert "keep" in data["mcpServers"]  # not clobbered
    assert "axiom-fabric" in data["mcpServers"]


def test_install_print_only_writes_nothing(tmp_path, monkeypatch, capsys):
    target = tmp_path / ".mcp.json"
    monkeypatch.setattr(I, "target_path", lambda client, *, scope: target)
    summary = I.install("claude", print_only=True)
    assert "mcpServers" in summary
    assert not target.exists()  # nothing written


def test_unknown_client_raises():
    with pytest.raises(ValueError):
        I.install("emacs")


# ---- --with-skill ----------------------------------------------------------


def test_render_skill_claude_has_frontmatter():
    text = I.render_skill("claude")
    assert text.startswith("---\n")
    assert "name: axiom-fabric" in text
    assert "description:" in text
    assert "truth" in text.lower()  # body from the canonical guide


def test_render_skill_gemini_is_marker_wrapped():
    text = I.render_skill("gemini")
    assert text.startswith(I._SKILL_BEGIN)
    assert text.rstrip().endswith(I._SKILL_END)


def test_write_skill_claude_standalone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # project scope -> ./.claude/skills/...
    summary = I.write_skill("claude", scope="project")
    skill = tmp_path / ".claude/skills/axiom-fabric/SKILL.md"
    assert skill.exists()
    assert skill.read_text().startswith("---\nname: axiom-fabric")
    assert "claude" in summary


def test_write_skill_gemini_marker_idempotent_and_preserves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # project scope -> ./GEMINI.md
    gem = tmp_path / "GEMINI.md"
    gem.write_text("# My project\n\nExisting notes.\n")

    I.write_skill("gemini", scope="project")
    once = gem.read_text()
    assert once.count(I._SKILL_BEGIN) == 1
    assert "Existing notes." in once  # user content preserved

    # Re-running replaces the block rather than duplicating it.
    I.write_skill("gemini", scope="project")
    twice = gem.read_text()
    assert twice.count(I._SKILL_BEGIN) == 1
    assert twice.count(I._SKILL_END) == 1


def test_write_skill_unsupported_client_returns_none():
    assert I.write_skill("claude-desktop", scope="project") is None


def test_install_with_skill_writes_config_and_skill(tmp_path, monkeypatch):
    cfg = tmp_path / "settings.json"
    skill = tmp_path / "GEMINI.md"
    monkeypatch.setattr(I, "target_path", lambda client, *, scope: cfg)
    monkeypatch.setattr(I, "skill_target_path", lambda client, *, scope: skill)

    summary = I.install("gemini", with_skill=True, db="/tmp/x/af.db")
    assert cfg.exists() and skill.exists()
    assert "Installed guidance" in summary
    assert skill.read_text().count(I._SKILL_BEGIN) == 1


def test_install_with_skill_print_only_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = I.install("claude", with_skill=True, print_only=True)
    assert "Skill ->" in out
    assert "name: axiom-fabric" in out
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".mcp.json").exists()
