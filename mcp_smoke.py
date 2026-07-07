"""Smoke test del MCP de Vitals: handshake stdio real (initialize + tools/list + tools/call)."""
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(
        command=r"C:\vitals-app\.venv\Scripts\python.exe",
        args=["vitals_mcp.py"],
        cwd=r"C:\vitals-app",
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS(" + str(len(tools.tools)) + "):", ", ".join(t.name for t in tools.tools))
            res = await session.call_tool("vitals_morning_brief", {})
            txt = "".join(getattr(c, "text", "") for c in res.content)
            print("BRIEF_SAMPLE:", txt[:300].replace("\n", " | "))
            res2 = await session.call_tool("vitals_insights", {})
            txt2 = "".join(getattr(c, "text", "") for c in res2.content)
            print("INSIGHTS_SAMPLE:", txt2[:200])


asyncio.run(main())
