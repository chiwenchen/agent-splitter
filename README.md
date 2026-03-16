# agent-splitter

AI Agent 分帳工具。輸入多筆代墊紀錄，回傳最少轉帳次數的結清方案。

部署於 AWS Lambda + API Gateway，使用整數運算避免浮點誤差。

## API

```
POST https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle
Content-Type: application/json
x-api-key: <your-api-key>   # 啟用 API Key 後必填
```

OpenAPI schema: `GET /openapi.json`

### 範例

```bash
curl -X POST https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle \
  -H "Content-Type: application/json" \
  -d '{
    "currency": "TWD",
    "participants": ["Alice", "Bob", "Carol"],
    "expenses": [
      {"description": "晚餐", "paid_by": "Alice", "amount": 1200, "split_among": ["Alice", "Bob", "Carol"]},
      {"description": "計程車", "paid_by": "Bob", "amount": 300, "split_among": ["Alice", "Bob", "Carol"]}
    ]
  }'
```

### 回應

```json
{
  "currency": "TWD",
  "summary": [
    {"participant": "Alice", "total_paid": 1200.0, "total_owed": 500.0, "balance": 700.0},
    {"participant": "Bob",   "total_paid": 300.0,  "total_owed": 500.0, "balance": -200.0},
    {"participant": "Carol", "total_paid": 0.0,    "total_owed": 500.0, "balance": -500.0}
  ],
  "settlements": [
    {"from": "Carol", "to": "Alice", "amount": 500.0},
    {"from": "Bob",   "to": "Alice", "amount": 200.0}
  ],
  "total_expenses": 1500.0,
  "num_settlements": 2
}
```

完整的 input/output schema 與設計說明見 [SPEC.md](./SPEC.md)。

## 開發

```bash
# 測試
python3 -m pytest tests/ -v

# 部署（無 API Key）
PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy

# 部署（啟用 API Key）
API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")
PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy --parameter-overrides ApiKey=$API_KEY
echo "Your API key: $API_KEY"
```

## 架構

```
mcp_server/server.py   # MCP server: stdio (本地) 或 HTTP/SSE (遠端)
      │  HTTP POST
      ▼
API Gateway (HTTP API)
      │
      ▼
Lambda (Python 3.13)
  src/split_settle/handler.py
  - POST /split_settle  — 分帳計算
  - GET  /openapi.json  — API schema
```

## 給其他 Agent 使用

**方式 1 — 直接呼叫 HTTP API**（最簡單）

```bash
curl -X POST https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle \
  -H "x-api-key: <key>" \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

**方式 2 — MCP（本地 stdio）**

```json
{
  "mcpServers": {
    "split-settle": {
      "command": "python3",
      "args": ["/path/to/mcp_server/server.py"],
      "env": { "SPLIT_SETTLE_API_KEY": "<key>" }
    }
  }
}
```

**方式 3 — MCP（遠端 HTTP/SSE）**

```bash
SPLIT_SETTLE_API_KEY=<key> python3 mcp_server/server.py --transport http --port 8000
# 其他 agent 連線: http://<your-server>:8000/sse
```

