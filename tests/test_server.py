"""The server answers over the current MCP protocol with FastMCP 4.

These tests touch only what the server serves without a configured site:
no clone, no Astro build, no network.
"""

from fastmcp import Client

from mcp_astro_editor.server import mcp


async def test_instructions_reach_the_client():
    async with Client(mcp) as client:
        # client.instructions reads whichever negotiation result the protocol era
        # produced; client.initialize() raises on the modern (server/discover) era.
        assert client.instructions is not None
        assert "skill://astro-editor/usage" in client.instructions


async def test_tools_are_listed():
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert len(names) >= 20


async def test_usage_skill_is_served():
    async with Client(mcp) as client:
        contents = await client.read_resource("skill://astro-editor/usage")
    assert contents[0].text.strip()


async def test_main_ui_is_an_mcp_app():
    async with Client(mcp) as client:
        resources = {str(r.uri): r for r in await client.list_resources()}
        contents = await client.read_resource("ui://astro-editor/main")
    assert resources["ui://astro-editor/main"].mime_type == "text/html;profile=mcp-app"
    assert "<html" in contents[0].text.lower()
