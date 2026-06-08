"""Write tools must be absent unless the server is built with allow_writes."""

from __future__ import annotations

import asyncio

from axiom_fabric.mcp import build_server

READ_TOOLS = {
    "list_layers",
    "list_facts",
    "get_fact",
    "get_fact_version",
    "get_fact_edges",
    "get_layer_history",
    "search_facts",
}
WRITE_TOOLS = {"create_layer", "create_fact", "update_fact", "retract_fact"}


def _tool_names(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def test_read_only_server_exposes_no_write_tools():
    names = _tool_names(build_server(allow_writes=False))
    assert names >= READ_TOOLS
    assert names.isdisjoint(WRITE_TOOLS)


def test_read_write_server_exposes_all_tools():
    names = _tool_names(build_server(allow_writes=True))
    assert names >= READ_TOOLS
    assert names >= WRITE_TOOLS


def test_usage_prompt_present_either_way():
    for allow in (False, True):
        server = build_server(allow_writes=allow)
        prompts = {p.name for p in asyncio.run(server.list_prompts())}
        assert "axiom_fabric_usage" in prompts
