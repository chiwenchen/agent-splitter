# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run tests
python3 -m pytest tests/ -v

# Build and deploy to AWS
PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy

# Install MCP server dependencies
pip install -r mcp_server/requirements.txt
```

## Architecture

```
mcp_server/server.py        # Local MCP server for Claude Desktop
      │  HTTP POST
      ▼
API Gateway (HTTP API)       # AWS ap-northeast-1
      │
      ▼
Lambda: src/split_settle/handler.py   # Pure Python, no dependencies
```

**Lambda** (`src/split_settle/handler.py`) contains all business logic:
- Input validation
- Integer arithmetic (amounts × 100) to avoid floating point errors
- Greedy algorithm for minimum-transfer settlement

**MCP server** (`mcp_server/server.py`) is a thin wrapper that calls the Lambda endpoint via HTTP. Reads `SPLIT_SETTLE_API_URL` and `SPLIT_SETTLE_API_KEY` from environment.

## AWS Resources

- Stack: `agent-splitter` (ap-northeast-1)
- Endpoint: `https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle`
- Budget: `monthly-10-usd-limit` — stops Lambda at $10/month (cwchen2000@gmail.com)
- IAM User: `ClaudeCLI` with `SplitSettleDeployPolicy` (Lambda + API Gateway + CloudFormation + S3 only)

## Claude Desktop Setup

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "split-settle": {
      "command": "python3",
      "args": ["/Users/chiwenchen/documents/repos/agent-splitter/mcp_server/server.py"]
    }
  }
}
```

## Design Docs

- `SPEC.md` — MCP tool input/output schema
- `docs/mcp-server.md` — MCP server design details
- `docs/api-auth.md` — API Key auth design (REST API migration plan)
