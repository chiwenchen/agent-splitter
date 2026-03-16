# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run tests
python3 -m pytest tests/ -v

# Build and deploy to AWS
PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy

# First-time IAM bootstrap (run once, then never again)
aws cloudformation deploy \
  --template-file iam/bootstrap.yaml \
  --stack-name agent-splitter-iam \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-northeast-1

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

API key auth is **disabled by default** (when `API_KEY` env var is empty on Lambda).

To enable:
1. Generate a key: `python3 -c "import secrets; print(secrets.token_hex(16))"`
2. Deploy: `sam deploy --parameter-overrides ApiKey=<generated-key>`
3. Pass in requests: `x-api-key: <key>` header
4. For MCP server: set `SPLIT_SETTLE_API_KEY=<key>` env var

## AWS Resources

- Stack: `agent-splitter` (ap-northeast-1)
- Endpoint: `https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle`
- OpenAPI: `https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/openapi.json`
- Budget: `monthly-10-usd-limit` — stops Lambda at $10/month (cwchen2000@gmail.com)
- IAM User: `ClaudeCLI` with minimal `SplitSettleDeployPolicy` (CloudFormation + PassRole + S3 only)
- IAM Role: `SplitSettleCFNRole` — assumed by CloudFormation service; holds all resource permissions

## IAM Architecture

Two-role pattern so ClaudeCLI permissions never need to change:

```
ClaudeCLI (IAM user)
  └── SplitSettleDeployPolicy
        ├── cloudformation:* on agent-splitter stack
        ├── iam:PassRole → SplitSettleCFNRole
        └── s3:* on SAM artifact bucket

SplitSettleCFNRole (assumed by cloudformation.amazonaws.com)
  └── SplitSettleCFNPolicy
        ├── lambda:* on agent-splitter-* functions
        ├── apigateway:* (HTTP API)
        ├── secretsmanager:* on split-settle/* secrets
        ├── iam:* on agent-splitter-* roles (Lambda execution roles)
        └── logs:* on /aws/lambda/agent-splitter-* groups
```

**When adding new AWS resource types to `template.yaml`**, only update `SplitSettleCFNRole` in `iam/bootstrap.yaml` — ClaudeCLI's policy stays fixed.

Bootstrap stack: `agent-splitter-iam` (deployed once via `iam/bootstrap.yaml`)

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
