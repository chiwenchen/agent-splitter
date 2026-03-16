# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run tests
python3 -m pytest tests/ -v

# Build and deploy to AWS (API_KEY is optional; leave empty to disable auth)
PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy
# With API key:
PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy --parameter-overrides ApiKey=<your-key>

# Install MCP server dependencies
pip install -r mcp_server/requirements.txt

# Run MCP server (local stdio, for Claude Code / Claude Desktop)
python3 mcp_server/server.py

# Run MCP server (remote HTTP, for other agents)
SPLIT_SETTLE_API_KEY=<key> python3 mcp_server/server.py --transport http --port 8000
```

## Architecture

```
mcp_server/server.py        # MCP server: stdio (local) or HTTP/SSE (remote)
      │  HTTP POST
      ▼
API Gateway (HTTP API)       # AWS ap-northeast-1
      │
      ▼
Lambda: src/split_settle/handler.py   # Pure Python, no dependencies
  - API Key validation (x-api-key header, disabled when API_KEY env var is empty)
  - GET /openapi.json  — OpenAPI 3.1 schema
  - POST /split_settle — settlement calculation
```

**Lambda** (`src/split_settle/handler.py`) contains all business logic:
- API Key validation via `x-api-key` header (skipped when `API_KEY` env var is empty)
- Input validation
- Integer arithmetic (amounts × 100) to avoid floating point errors
- Greedy algorithm for minimum-transfer settlement
- OpenAPI 3.1 schema served at `GET /openapi.json`

**MCP server** (`mcp_server/server.py`) supports two transports:
- `stdio` (default) — for local Claude Code / Claude Desktop
- `http` — SSE-based remote server, for other agents to connect via URL

## API Key Setup

API key is managed by **AWS Secrets Manager** (`split-settle/api-key`).
- Auto-generated on first deploy (32-char random string)
- Lambda reads it at runtime via boto3 (cached in global scope)
- Not visible in Lambda config or CloudFormation outputs

Retrieve the key value after deploy:
```bash
aws secretsmanager get-secret-value \
  --secret-id split-settle/api-key \
  --query SecretString --output text \
  --region ap-northeast-1
```

For MCP server: set `SPLIT_SETTLE_API_KEY=<value>` env var.
For local dev/tests: set `API_KEY=<any-value>` env var (bypasses Secrets Manager).

## AWS Resources

- Stack: `agent-splitter` (ap-northeast-1)
- Endpoint: `https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle`
- OpenAPI: `https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/openapi.json`
- Budget: `monthly-10-usd-limit` — stops Lambda at $10/month (cwchen2000@gmail.com)
- IAM User: `ClaudeCLI` with `SplitSettleDeployPolicy` (Lambda + API Gateway + CloudFormation + S3 only)

## Claude Desktop Setup (local stdio)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "split-settle": {
      "command": "/Users/chiwenchen/documents/repos/agent-splitter/mcp_server/.venv/bin/python3",
      "args": ["/Users/chiwenchen/documents/repos/agent-splitter/mcp_server/server.py"],
      "env": {
        "SPLIT_SETTLE_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

## Remote Agent Setup (HTTP transport)

For other agents to connect via MCP protocol:

```bash
# Start the remote MCP server
SPLIT_SETTLE_API_KEY=<key> python3 mcp_server/server.py --transport http --port 8000
```

Other agents add via URL: `http://<your-server>:8000/sse`

Alternatively, agents can call the HTTP API directly (no MCP needed):
```bash
curl -X POST https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle \
  -H "x-api-key: <key>" \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

## Design Docs

- `SPEC.md` — MCP tool input/output schema
- `docs/mcp-server.md` — MCP server design details
- `docs/api-auth.md` — API Key auth design
