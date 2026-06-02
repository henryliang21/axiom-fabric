# Agent integrations

Ready-to-use guidance that teaches an AI agent how to use Axiom Fabric as a
versioned, append-only truth store through the `af-mcp` MCP server. One file per
agent, in that agent's native format:

| Agent | File | How to use it |
| ----- | ---- | ------------- |
| Claude Code | [`claude/SKILL.md`](claude/SKILL.md) | Copy into `.claude/skills/axiom-fabric/` (project or `~/.claude/skills/`). |
| Gemini CLI | [`gemini/GEMINI.md`](gemini/GEMINI.md) | Add to your project `GEMINI.md` or `~/.gemini/`. |
| Codex CLI | [`codex/AGENTS.md`](codex/AGENTS.md) | Add to your project `AGENTS.md`. |

**Automated:** `af-mcp install --client <agent> --with-skill` drops the right file
in the right place for you (generated from the canonical guide; safe to re-run).
The files here are the same content, committed for reference and manual install.

## Canonical source

The authoritative guidance lives **in the package** at
`axiom-fabric/src/axiom_fabric/mcp/agent_guide.md` and is served live by the MCP
server as the prompt **`axiom_fabric_usage`** (any MCP client can fetch it). The
files here adapt that same content to each agent's format — if you change the
guidance, update `agent_guide.md` first and mirror it here.

## Wiring the server (all agents)

```bash
pipx install "axiom-fabric[mcp]"          # provides `af` and `af-mcp`
af init                                    # clean store in this directory
af-mcp install --client claude            # or: gemini | codex | claude-desktop
#   add --allow-writes to let the agent create/update facts
```

`af-mcp install` writes the correct MCP server block into that agent's config
(pinned to an absolute `AF_DATABASE_URL`). The skill/guidance file then teaches
the agent *how* to use the tools it now sees.
