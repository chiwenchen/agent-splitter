import asyncio
import json
import os

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

API_URL = os.environ.get(
    "SPLIT_SETTLE_API_URL",
    "https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle",
)
API_KEY = os.environ.get("SPLIT_SETTLE_API_KEY", "")

app = Server("split-settle")


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="split_settle",
            description=(
                "Calculate the optimal settlement plan from a group of shared expenses. "
                "Given a list of participants and who paid what, returns the minimum number "
                "of transfers needed to settle all debts."
            ),
            inputSchema={
                "type": "object",
                "required": ["currency", "participants", "expenses"],
                "properties": {
                    "currency": {
                        "type": "string",
                        "description": "ISO 4217 currency code, e.g. TWD, USD, JPY",
                    },
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "description": "List of participant names",
                    },
                    "expenses": {
                        "type": "array",
                        "minItems": 1,
                        "description": "List of expense records",
                        "items": {
                            "type": "object",
                            "required": ["paid_by", "amount", "split_among"],
                            "properties": {
                                "description": {"type": "string"},
                                "paid_by": {"type": "string"},
                                "amount": {"type": "number", "exclusiveMinimum": 0},
                                "split_among": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                },
                            },
                        },
                    },
                },
            },
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name != "split_settle":
        raise ValueError(f"Unknown tool: {name}")

    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["x-api-key"] = API_KEY

    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=arguments, headers=headers, timeout=10)
        result = response.json()

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def main():
    async with stdio_server() as streams:
        await app.run(*streams, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
