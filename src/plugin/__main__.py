"""Typed MCP entry points; benchmark reports are produced only by the evaluator."""

import os
from pathlib import Path

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from slimconfig import build_output, output_schema, plain_output

from contract import TOOLS, Tool
from plugin.benchmark import describe_problem, evaluate_code

server = Server("code-optimize")
IMPLEMENTATIONS = {Tool.DESCRIBE_PROBLEM: describe_problem, Tool.EVALUATE_CODE: evaluate_code}


@server.list_tools()
async def list_tools():
    return [
        types.Tool(name=tool.value, inputSchema=output_schema(given), outputSchema=output_schema(made))
        for tool, (given, made) in TOOLS.items()
    ]


@server.call_tool()
async def call_tool(name, arguments):
    tool = Tool(name)
    given, made = TOOLS[tool]
    output = await IMPLEMENTATIONS[tool](build_output(arguments, given), Path(os.environ["NANOAGENT_WORKSPACE"]))
    if not isinstance(output, made):
        raise TypeError(f"{name} returned the wrong output type")
    return plain_output(output)


def main():
    if "NANOAGENT_WORKSPACE" not in os.environ:
        raise SystemExit("NANOAGENT_WORKSPACE must identify the plugin workspace")

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
