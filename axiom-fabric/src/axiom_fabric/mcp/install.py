"""Wire the Axiom Fabric MCP server into an agent's config (non-destructively).

Each supported client launches a local stdio MCP server. We compute the launch
spec (command + args + env, with an absolute DB path so it doesn't depend on the
client's working directory) and merge a single `axiom-fabric` entry into the
client's config file, backing the file up first. `--print` renders the block
without touching anything.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from axiom_fabric.mcp.guide import read_agent_guide

SERVER_KEY = "axiom-fabric"

# client -> (kind, description). "json" clients share the mcpServers shape.
CLIENTS = {
    "claude": "json",  # Claude Code: project ./.mcp.json (or ~/.claude.json with --scope user)
    "claude-desktop": "json",
    "gemini": "json",
    "codex": "toml",
}

# Clients that support a persistent guidance file (--with-skill).
SKILL_CLIENTS = {"claude", "gemini", "codex"}

# Frontmatter description for the Claude skill (drives when Claude loads it).
_CLAUDE_SKILL_DESCRIPTION = (
    "Use Axiom Fabric (the `af` MCP server) as a versioned, append-only truth "
    "store during a task — read the facts that constrain your work before "
    "acting, and record decisions/findings as facts as you go. Trigger when the "
    "axiom-fabric MCP tools (list_layers, list_facts, create_fact, ...) are "
    "available, when the user refers to the truth store / fact store / truth "
    "ledger, or when work should be grounded in or persisted to Axiom Fabric."
)

# Markers wrapping the guidance appended into shared files (GEMINI.md / AGENTS.md),
# so re-running --with-skill replaces the block instead of duplicating it.
_SKILL_BEGIN = "<!-- BEGIN axiom-fabric skill (managed by af-mcp install) -->"
_SKILL_END = "<!-- END axiom-fabric skill -->"


@dataclass(frozen=True)
class ServerSpec:
    command: str
    args: list[str]
    env: dict[str, str]

    def as_dict(self) -> dict:
        return {"command": self.command, "args": self.args, "env": self.env}


def build_spec(*, allow_writes: bool, db: str | None) -> ServerSpec:
    """Compute the stdio launch spec for the MCP server."""
    db_path = Path(db).expanduser().resolve() if db else (Path.cwd() / "af.db").resolve()
    env = {"AF_DATABASE_URL": f"sqlite:///{db_path}"}

    exe = shutil.which("af-mcp")
    if exe:
        command, args = exe, ["serve"]
    else:
        # Fall back to invoking the module with the current interpreter.
        command, args = sys.executable, ["-m", "axiom_fabric.mcp", "serve"]
    if allow_writes:
        args = [*args, "--allow-writes"]
    return ServerSpec(command=command, args=args, env=env)


def target_path(client: str, *, scope: str) -> Path:
    if client == "claude":
        if scope == "user":
            return Path.home() / ".claude.json"
        return Path.cwd() / ".mcp.json"
    if client == "claude-desktop":
        # macOS path; other platforms differ.
        if sys.platform == "darwin":
            return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
        if sys.platform.startswith("win"):
            import os

            base = os.environ.get("APPDATA", str(Path.home()))
            return Path(base) / "Claude/claude_desktop_config.json"
        return Path.home() / ".config/Claude/claude_desktop_config.json"
    if client == "gemini":
        return Path.home() / ".gemini/settings.json"
    if client == "codex":
        return Path.home() / ".codex/config.toml"
    raise ValueError(f"unknown client: {client!r}; choose one of {', '.join(CLIENTS)}")


# ---- JSON (mcpServers) clients --------------------------------------------


def merge_json(path: Path, spec: ServerSpec) -> str:
    data: dict = {}
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError(f"{path} is not a JSON object; refusing to edit")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path} has a non-object 'mcpServers'; refusing to edit")
    servers[SERVER_KEY] = spec.as_dict()
    return json.dumps(data, indent=2) + "\n"


def render_json(spec: ServerSpec) -> str:
    return json.dumps({"mcpServers": {SERVER_KEY: spec.as_dict()}}, indent=2) + "\n"


# ---- TOML (codex) ----------------------------------------------------------


def _toml_str(value: str) -> str:
    # JSON string literals are valid TOML basic strings for our values.
    return json.dumps(value)


def render_toml_block(spec: ServerSpec) -> str:
    args = ", ".join(_toml_str(a) for a in spec.args)
    env = ", ".join(f"{k} = {_toml_str(v)}" for k, v in spec.env.items())
    return (
        f"[mcp_servers.{SERVER_KEY}]\n"
        f"command = {_toml_str(spec.command)}\n"
        f"args = [{args}]\n"
        f"env = {{ {env} }}\n"
    )


def merge_toml(path: Path, spec: ServerSpec) -> str:
    block = render_toml_block(spec)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    header = re.escape(f"[mcp_servers.{SERVER_KEY}]")
    # Match the existing table from its header to the next top-level [header] or EOF.
    pattern = re.compile(rf"(?ms)^{header}\s*\n.*?(?=^\[|\Z)")
    if pattern.search(existing):
        return pattern.sub(block, existing, count=1)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    sep = "\n" if existing else ""
    return existing + sep + block


# ---- skill / guidance files (--with-skill) --------------------------------


def skill_target_path(client: str, *, scope: str) -> Path | None:
    """Where the guidance file goes for `client`, or None if it has no skill file.

    `scope="user"` installs globally; "project" installs into the current dir.
    """
    if client == "claude":
        base = Path.home() if scope == "user" else Path.cwd()
        return base / ".claude/skills/axiom-fabric/SKILL.md"
    if client == "gemini":
        return (Path.home() / ".gemini/GEMINI.md") if scope == "user" else (Path.cwd() / "GEMINI.md")
    if client == "codex":
        return (Path.home() / ".codex/AGENTS.md") if scope == "user" else (Path.cwd() / "AGENTS.md")
    return None  # claude-desktop and any other client: use the MCP prompt instead


def render_skill(client: str) -> str:
    """Render the guidance for `client` from the canonical packaged guide.

    Claude gets a standalone SKILL.md (YAML frontmatter + body); Gemini/Codex get
    a marker-wrapped block suitable for appending into GEMINI.md / AGENTS.md.
    """
    body = read_agent_guide().rstrip() + "\n"
    if client == "claude":
        return (
            f"---\nname: {SERVER_KEY}\ndescription: {_CLAUDE_SKILL_DESCRIPTION}\n---\n\n{body}"
        )
    return f"{_SKILL_BEGIN}\n{body}{_SKILL_END}\n"


def _merge_marked(existing: str, block: str) -> str:
    """Replace the existing axiom-fabric block (between markers) or append it."""
    pattern = re.compile(rf"(?ms){re.escape(_SKILL_BEGIN)}.*?{re.escape(_SKILL_END)}\n?")
    if pattern.search(existing):
        return pattern.sub(lambda _m: block, existing, count=1)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    sep = "\n" if existing else ""
    return existing + sep + block


def _backup_and_write(path: Path, content: str) -> str:
    """Write `content` to `path`, backing up any existing file. Returns a note."""
    note = ""
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        note = f" (backed up old file to {backup})"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return note


def write_skill(client: str, *, scope: str) -> str | None:
    """Install the guidance file for `client`. Returns a summary, or None if the
    client has no skill file."""
    path = skill_target_path(client, scope=scope)
    if path is None:
        return None
    content = render_skill(client)
    if client == "claude":
        new_content = content  # standalone file — overwrite
    else:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        new_content = _merge_marked(existing, content)
    note = _backup_and_write(path, new_content)
    return f"Installed guidance for {client} at {path}{note}."


# ---- entry point -----------------------------------------------------------


def install(
    client: str,
    *,
    allow_writes: bool = False,
    db: str | None = None,
    scope: str = "project",
    print_only: bool = False,
    with_skill: bool = False,
) -> str:
    """Install or print the MCP config for `client`. Returns a human-readable summary.

    With `with_skill=True`, also installs the per-agent guidance file (Claude
    SKILL.md / Gemini GEMINI.md / Codex AGENTS.md), generated from the packaged
    canonical guide.
    """
    kind = CLIENTS.get(client)
    if kind is None:
        raise ValueError(f"unknown client: {client!r}; choose one of {', '.join(CLIENTS)}")

    spec = build_spec(allow_writes=allow_writes, db=db)
    path = target_path(client, scope=scope)

    if print_only:
        block = render_toml_block(spec) if kind == "toml" else render_json(spec)
        out = f"# Add to {path}\n\n{block}"
        if with_skill:
            sp = skill_target_path(client, scope=scope)
            if sp is None:
                out += f"\n# No skill file for client {client!r}; use the axiom_fabric_usage MCP prompt.\n"
            else:
                out += f"\n# Skill -> {sp}\n\n{render_skill(client)}"
        return out

    new_content = merge_toml(path, spec) if kind == "toml" else merge_json(path, spec)
    backup_note = _backup_and_write(path, new_content)
    writes = "read+write" if allow_writes else "read-only"
    summary = f"Configured '{SERVER_KEY}' ({writes}) for {client} at {path}{backup_note}."

    if with_skill:
        skill_summary = write_skill(client, scope=scope)
        if skill_summary is None:
            summary += (
                f"\nNo skill file applies to client {client!r}; "
                "use the axiom_fabric_usage MCP prompt instead."
            )
        else:
            summary += "\n" + skill_summary

    return summary
