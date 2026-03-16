import json
import os

import boto3

_cached_api_key = None


def _get_api_key() -> str:
    """Read API key from Secrets Manager (cached) or API_KEY env var (local dev/tests)."""
    global _cached_api_key

    # Local dev / tests: use API_KEY env var directly
    env_key = os.environ.get("API_KEY", "")
    if env_key:
        return env_key

    # Production: read from Secrets Manager and cache
    secret_arn = os.environ.get("SECRET_ARN", "")
    if not secret_arn:
        return ""  # no auth configured

    if _cached_api_key is None:
        client = boto3.client("secretsmanager")
        response = client.get_secret_value(SecretId=secret_arn)
        _cached_api_key = response["SecretString"]

    return _cached_api_key


OPENAPI_SCHEMA = {
    "openapi": "3.1.0",
    "info": {
        "title": "SplitSettle API",
        "description": "Calculate the minimum number of transfers to settle shared expenses.",
        "version": "1.0.0",
    },
    "servers": [
        {
            "url": "https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com",
            "description": "Production",
        }
    ],
    "paths": {
        "/split_settle": {
            "post": {
                "summary": "Calculate optimal settlement plan",
                "description": "Given a list of participants and shared expenses, returns the minimum transfers needed to settle all debts.",
                "security": [{"ApiKeyAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SplitSettleRequest"},
                            "example": {
                                "currency": "TWD",
                                "participants": ["Alice", "Bob", "Carol"],
                                "expenses": [
                                    {
                                        "description": "Dinner",
                                        "paid_by": "Alice",
                                        "amount": 1200,
                                        "split_among": ["Alice", "Bob", "Carol"],
                                    },
                                    {
                                        "description": "Taxi",
                                        "paid_by": "Bob",
                                        "amount": 300,
                                        "split_among": ["Alice", "Bob", "Carol"],
                                    },
                                ],
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Settlement plan",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SplitSettleResponse"}
                            }
                        },
                    },
                    "400": {"description": "Invalid request"},
                    "403": {"description": "Invalid or missing API key"},
                },
            }
        }
    },
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-api-key"}
        },
        "schemas": {
            "Expense": {
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
            "SplitSettleRequest": {
                "type": "object",
                "required": ["currency", "participants", "expenses"],
                "properties": {
                    "currency": {"type": "string", "description": "ISO 4217 currency code"},
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                    },
                    "expenses": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Expense"},
                        "minItems": 1,
                    },
                },
            },
            "ParticipantSummary": {
                "type": "object",
                "properties": {
                    "participant": {"type": "string"},
                    "total_paid": {"type": "number"},
                    "total_owed": {"type": "number"},
                    "balance": {"type": "number"},
                },
            },
            "Settlement": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "amount": {"type": "number"},
                },
            },
            "SplitSettleResponse": {
                "type": "object",
                "properties": {
                    "currency": {"type": "string"},
                    "summary": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ParticipantSummary"},
                    },
                    "settlements": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Settlement"},
                    },
                    "total_expenses": {"type": "number"},
                    "num_settlements": {"type": "integer"},
                },
            },
        },
    },
}


def lambda_handler(event, context):
    path = event.get("rawPath", "")

    if path == "/openapi.json":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(OPENAPI_SCHEMA),
        }

    # API Key validation
    api_key = _get_api_key()
    if api_key:
        provided = (event.get("headers") or {}).get("x-api-key", "")
        if provided != api_key:
            return {
                "statusCode": 403,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Forbidden: invalid or missing x-api-key"}),
            }

    try:
        body = json.loads(event.get("body") or "{}")
        result = split_settle(body)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }
    except ValueError as e:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e)}),
        }
    except Exception:
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def split_settle(data: dict) -> dict:
    currency = data.get("currency")
    participants = data.get("participants", [])
    expenses = data.get("expenses", [])

    if not currency:
        raise ValueError("currency is required")
    if len(participants) < 2:
        raise ValueError("at least 2 participants required")
    if len(expenses) < 1:
        raise ValueError("at least 1 expense required")

    participant_set = set(participants)
    total_paid_cents = {p: 0 for p in participants}
    total_owed_cents = {p: 0 for p in participants}
    total_cents = 0

    for expense in expenses:
        paid_by = expense.get("paid_by")
        amount = expense.get("amount")
        split_among = expense.get("split_among", [])

        if not paid_by or paid_by not in participant_set:
            raise ValueError(f"Invalid paid_by: '{paid_by}'")
        if amount is None or amount <= 0:
            raise ValueError("amount must be > 0")
        if not split_among:
            raise ValueError("split_among cannot be empty")
        for p in split_among:
            if p not in participant_set:
                raise ValueError(f"'{p}' in split_among is not in participants")

        amount_cents = round(amount * 100)
        total_cents += amount_cents
        total_paid_cents[paid_by] += amount_cents

        n = len(split_among)
        share = amount_cents // n
        remainder = amount_cents % n

        for i, person in enumerate(split_among):
            total_owed_cents[person] += share + (1 if i < remainder else 0)

    balances = {p: total_paid_cents[p] - total_owed_cents[p] for p in participants}

    assert sum(balances.values()) == 0, "Balance checksum failed"

    summary = [
        {
            "participant": p,
            "total_paid": total_paid_cents[p] / 100,
            "total_owed": total_owed_cents[p] / 100,
            "balance": balances[p] / 100,
        }
        for p in participants
    ]

    settlements = _calculate_settlements(balances)

    return {
        "currency": currency,
        "summary": summary,
        "settlements": [
            {"from": s["from"], "to": s["to"], "amount": s["amount"] / 100}
            for s in settlements
        ],
        "total_expenses": total_cents / 100,
        "num_settlements": len(settlements),
    }


def _calculate_settlements(balances: dict) -> list:
    """Greedy: match largest debtor with largest creditor until all settled."""
    creditors = sorted(
        [[v, k] for k, v in balances.items() if v > 0], reverse=True
    )
    debtors = sorted(
        [[-v, k] for k, v in balances.items() if v < 0], reverse=True
    )

    settlements = []
    i = j = 0

    while i < len(creditors) and j < len(debtors):
        credit, creditor = creditors[i]
        debt, debtor = debtors[j]

        transfer = min(credit, debt)
        settlements.append({"from": debtor, "to": creditor, "amount": transfer})

        creditors[i][0] -= transfer
        debtors[j][0] -= transfer

        if creditors[i][0] == 0:
            i += 1
        if debtors[j][0] == 0:
            j += 1

    return settlements
