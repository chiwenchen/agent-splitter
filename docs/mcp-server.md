# MCP Server 設計

## 概述

將 `split_settle` 包裝成本地 MCP server，讓 Claude Desktop 可以直接呼叫，不需要使用者手動貼 curl 指令。

## 架構

```
Claude Desktop
     │  MCP protocol (stdio)
     ▼
Local MCP Server (Python)
     │  HTTP POST
     ▼
Lambda API Endpoint
```

MCP server 跑在本地，透過 stdio 與 Claude Desktop 溝通，收到呼叫請求後轉發給已部署的 Lambda endpoint。

## 實作

### 依賴

```
mcp>=1.0.0
httpx>=0.27.0
```

### 檔案結構

```
agent-splitter/
└── mcp_server/
    ├── server.py        # MCP server 主程式
    └── requirements.txt
```

### server.py 邏輯

```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import httpx
import json

API_URL = "https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle"

app = Server("split-settle")

@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="split_settle",
            description="Calculate the optimal settlement plan from a group of shared expenses. Returns the minimum number of transfers needed to settle all debts.",
            inputSchema={
                "type": "object",
                "required": ["currency", "participants", "expenses"],
                "properties": {
                    "currency": {"type": "string", "description": "ISO 4217 currency code, e.g. TWD, USD"},
                    "participants": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                    "expenses": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["paid_by", "amount", "split_among"],
                            "properties": {
                                "description": {"type": "string"},
                                "paid_by": {"type": "string"},
                                "amount": {"type": "number", "exclusiveMinimum": 0},
                                "split_among": {"type": "array", "items": {"type": "string"}, "minItems": 1}
                            }
                        }
                    }
                }
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name != "split_settle":
        raise ValueError(f"Unknown tool: {name}")

    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=arguments, timeout=10)
        result = response.json()

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

async def main():
    async with stdio_server() as streams:
        await app.run(*streams, app.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## 安裝到 Claude Desktop

### 1. 安裝依賴

```bash
cd mcp_server
pip install -r requirements.txt
```

### 2. 設定 Claude Desktop

編輯 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "split-settle": {
      "command": "python3",
      "args": ["/path/to/agent-splitter/mcp_server/server.py"]
    }
  }
}
```

### 3. 重啟 Claude Desktop

重啟後在對話中即可直接說「幫我算分帳」，Claude 會自動呼叫 `split_settle` tool。

## 注意事項

- 當 API 加上認證（API Key）後，需在 `server.py` 的 httpx request 加上 `headers={"x-api-key": API_KEY}`
- API_KEY 建議從環境變數讀取，不要 hardcode
