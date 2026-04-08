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
Client (browser / curl / MCP agent)
      │
      ▼
Cloudflare custom domain         split.redarch.dev   |   split-admin.redarch.dev
      │                                                            │
      ▼                                                            ▼
Cloudflare Worker                split-senpai-proxy        split-admin-proxy
  (sets x-forwarded-host)          │                              │
                                   └──────────┬───────────────────┘
                                              ▼
API Gateway (HTTP API, ap-northeast-1)
  - Default execute-api endpoint reachable but Lambda rejects
    any request whose host is not in ALLOWED_HOSTS
      │
      ▼
Lambda: src/split_settle/handler.py
  - Host allow-list gate (ALLOWED_HOSTS, reads X-Forwarded-Host first)
  - Fail-closed API-key check (x-api-key vs Secrets Manager)
  - Routes: GET  /                              -> web UI
            GET  /health                        -> health check
            GET  /openapi.json                  -> OpenAPI 3.1 schema
            GET  /docs                          -> Swagger UI
            POST /v1/split_settle               -> settlement calc (+ group_id calldata)
            POST /v1/share                      -> create share link (public, size-capped)
            GET  /s/{id}                        -> shared result HTML
            GET  /v1/share/{id}                 -> shared result JSON
            POST /v1/groups                     -> create wallet group (API key)
            GET  /admin, /admin/{proxy+}        -> admin SPA + JSON APIs
                                                   (CF Access JWT + email allow-list)
            GET  /.well-known/apple-app-site-association, /assetlinks.json

MCP server: mcp_server/server.py           (stdio for Claude Desktop, or HTTP/SSE for remote agents)
      │ HTTP POST with x-api-key
      ▼
      points at https://split.redarch.dev/v1/split_settle (not execute-api directly)
```

**Lambda** (`src/split_settle/handler.py`) contains all business logic:
- Host allow-list: `ALLOWED_HOSTS` env var (comma-separated). Lambda prefers
  `X-Forwarded-Host` over `Host` so the Cloudflare Worker chain can forward the
  original client hostname. Requests whose effective host is not in the list
  return 403 — this is how we block direct hits on the raw
  `*.execute-api.amazonaws.com` endpoint (HTTP API has no
  `disableExecuteApiEndpoint` flag).
- API key validation via `x-api-key` header. **Fail-closed**: if the secret
  resolves to an empty string and we are running inside a Lambda runtime
  (`AWS_LAMBDA_FUNCTION_NAME` is set), `_get_secret` raises rather than
  silently allowing unauthenticated access. Local tests bypass this because
  they don't set that env var.
- Integer arithmetic (amounts × 100) to avoid floating point errors
- Greedy algorithm for minimum-transfer settlement
- Admin dashboard gated behind Cloudflare Access JWT — see "Admin Dashboard" below

**MCP server** (`mcp_server/server.py`) supports two transports:
- `stdio` (default) — for local Claude Code / Claude Desktop
- `http` — SSE-based remote server, for other agents to connect via URL

The MCP server talks to `https://split.redarch.dev/v1/split_settle`, not the
raw execute-api URL — that's important because the raw URL is blocked by the
host allow-list.

## API Key Setup

API key lives in **AWS Secrets Manager** as `split-settle/api-key-v2` and is
read at runtime via boto3 (cached in module scope).

**Why it's not auto-generated:** `GenerateSecretString` requires the
`secretsmanager:GetRandomPassword` IAM action, which cannot be scoped to a
resource ARN. We intentionally don't grant it to `ClaudeCLI` to keep the blast
radius small. So the template creates an empty secret and you populate it
once after the first deploy:

```bash
# First deploy only (or key rotation): generate + store a random key
aws secretsmanager put-secret-value \
  --secret-id split-settle/api-key-v2 \
  --secret-string "$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')" \
  --region ap-northeast-1
```

Retrieve the current value:
```bash
aws secretsmanager get-secret-value \
  --secret-id split-settle/api-key-v2 \
  --query SecretString --output text \
  --region ap-northeast-1
```

For MCP server / external agents: set `SPLIT_SETTLE_API_KEY=<value>` env var.
For local dev / pytest: set `API_KEY=<any-value>` env var — this takes
precedence over Secrets Manager. The fail-closed check only fires inside a
real Lambda runtime, so `pytest` works without AWS creds.

## AWS Resources

- Stack: `agent-splitter` (ap-northeast-1)
- Endpoint: `https://split.redarch.dev/v1/split_settle`
- OpenAPI: `https://split.redarch.dev/openapi.json`
- Budget: `monthly-10-usd-limit` — stops Lambda at $10/month (cwchen2000@gmail.com)
- IAM User: `ClaudeCLI` with `SplitSettleDeployPolicy` (`iam/claudecli-policy.json`)

## IAM Setup

`ClaudeCLI` needs a single inline policy. Paste `iam/claudecli-policy.json` into AWS Console:

> IAM → Users → ClaudeCLI → Add permissions → Create inline policy → JSON tab

Covers: CloudFormation (including `ContinueUpdateRollback`), Lambda,
API Gateway, Secrets Manager (scoped `split-settle/*` plus the unscopable
`GetRandomPassword` is **deliberately excluded**), IAM (Lambda execution
roles), CloudWatch Logs, DynamoDB (including `UpdateTable` /
`DescribeContinuousBackups` / `UpdateContinuousBackups` — CloudFormation
refreshes the table on every stack update), and S3 (SAM bucket). All
resource-scoped to `agent-splitter-*` / `split-settle/*` where possible.

Notable omissions by design:
- `secretsmanager:GetRandomPassword` — cannot be scoped to a resource; we
  populate the API key manually via `put-secret-value` instead.
- `iam:PutUserPolicy` / `iam:GetUserPolicy` — ClaudeCLI cannot modify its
  own policy. Any policy update must be done by you in the AWS Console.
- `lambda:ListFunctions` / `logs:DescribeLogStreams` — log reading is not
  granted. Debug via temporary code paths that return errors in the response
  body, not by grepping CloudWatch.

## Cloudflare Setup

Two Cloudflare Workers sit in front of the Lambda. Both repos live outside
this one:

- `~/Documents/repos/split-senpai-proxy` — proxies `split.redarch.dev/*` to
  the execute-api origin
- `~/Documents/repos/split-admin-proxy` — proxies `split-admin.redarch.dev/*`
  to `split.redarch.dev/admin/*` (chains through the first Worker)

**Both Workers must set `x-forwarded-host` to the incoming client hostname
before calling `fetch()`.** Without it, the Lambda host allow-list sees only
the execute-api hostname and rejects everything. `split-senpai-proxy` only
sets `x-forwarded-host` when it is missing, so the admin hostname set by
`split-admin-proxy` survives the two-hop proxy chain.

Deploy either Worker:
```bash
cd ~/Documents/repos/split-senpai-proxy   # or split-admin-proxy
wrangler deploy
```

wrangler is authenticated via OAuth (`wrangler login`). **Do not** set
`CLOUDFLARE_API_TOKEN` — it overrides OAuth and the token in Secrets Manager
only has `Zone Analytics: Read` scope, which cannot deploy Workers.

## Admin Dashboard

- Public URL: `https://split-admin.redarch.dev`
- Front door: Cloudflare Access application `split-senpai-admin`, policy
  `Allow owner` (include email `cwchen2000@gmail.com`, one-time PIN identity
  provider). Unauthenticated requests get 302'd to the Access login page.
- Back door: Lambda re-verifies the Cloudflare Access JWT via
  `_verify_access_jwt` (RS256, signatures checked with pycryptodome, JWKS
  cached for 1 hour). This is defence in depth — even if Cloudflare Access
  is misconfigured, the Lambda still requires a valid signed JWT.

**Required Lambda env vars (all set via template.yaml, do NOT edit in the
Lambda console):**

```
CF_ACCESS_TEAM_DOMAIN = redarch.cloudflareaccess.com
CF_ACCESS_AUD         = 92097101075a4b784478b1f54092148c842ccdd2f42724cb838a1bac7dc11d66
CF_ALLOWED_EMAIL      = cwchen2000@gmail.com
CF_API_TOKEN_ARN      = arn:aws:secretsmanager:ap-northeast-1:274571492950:secret:split-settle/cloudflare-api-token
CF_ZONE_ID            = 2fdc064b300cf497b17c10d7e0bd9ab1
```

Editing these in the Lambda console creates drift that gets wiped by the
next `sam deploy`. If you need to rotate any of them, edit `template.yaml`
and re-deploy.

Admin routes (all require a valid Access JWT):
```
GET    /admin                         -> inline Preact SPA
GET    /admin/api/stats               -> aggregate stats (currency / day)
GET    /admin/api/shares              -> list all shares
GET    /admin/api/shares/{id}         -> single share detail
DELETE /admin/api/shares/{id}         -> delete share (Origin header checked)
GET    /admin/api/cloudflare/analytics-> 24h Cloudflare Analytics summary
```

## Deploy Recovery

If `sam deploy` fails mid-flight and the stack ends up in
`UPDATE_ROLLBACK_FAILED`, CloudFormation usually cannot auto-recover because
the same permission that blocked the update also blocks the rollback. Skip
the problem resource and continue:

```bash
aws cloudformation continue-update-rollback \
  --stack-name agent-splitter \
  --region ap-northeast-1 \
  --resources-to-skip GroupsTable
```

`GroupsTable` is the usual culprit — CloudFormation re-issues `UpdateTable`
on every stack change for drift detection, and any missing DynamoDB
permission surfaces here. After `--resources-to-skip`, fix the IAM gap in
`iam/claudecli-policy.json`, paste it in the console, then re-run
`sam deploy`.

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
