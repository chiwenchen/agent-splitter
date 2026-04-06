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
  - POST /v1/split_settle — settlement calculation
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

Retrieve the key after deploy:
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
- Endpoint: `https://split.redarch.dev/v1/split_settle`
- OpenAPI: `https://split.redarch.dev/openapi.json`
- Budget: `monthly-10-usd-limit` — stops Lambda at $10/month (cwchen2000@gmail.com)
- IAM User: `ClaudeCLI` with `SplitSettleDeployPolicy` (`iam/claudecli-policy.json`)

## IAM Setup

`ClaudeCLI` needs a single inline policy. Paste `iam/claudecli-policy.json` into AWS Console:

> IAM → Users → ClaudeCLI → Add permissions → Create inline policy → JSON tab

Covers: CloudFormation, Lambda, API Gateway, Secrets Manager, IAM (Lambda execution roles), CloudWatch Logs, S3 (SAM bucket). All scoped to `agent-splitter-*` resources.

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
curl -X POST https://split.redarch.dev/v1/split_settle \
  -H "x-api-key: <key>" \
  -H "Content-Type: application/json" \
  -d '{ ... }'
```

## Development Workflow

When you finish implementing a feature or fix, follow these steps automatically:

1. **Run tests** — `python3 -m pytest tests/ -v`. Fix any failures before proceeding.
2. **Commit** — stage relevant files and create a descriptive commit.
3. **Push & open PR** — push the branch and create a PR via `gh pr create`.
4. **Monitor CI** — poll with `gh run list --branch <branch>` until all checks complete.
5. **Fix CI failures** — if any check fails, inspect logs with `gh run view <run-id> --log-failed`, fix the issue, commit, and push. Repeat until all checks pass.

Do this without waiting for explicit instruction — it is the expected end-to-end flow for every development task in this repo.

## Design Docs

- `SPEC.md` — MCP tool input/output schema
- `docs/mcp-server.md` — MCP server design details
- `docs/api-auth.md` — API Key auth design

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
