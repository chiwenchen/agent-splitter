# agent-splitter

AI Agent 分帳工具。輸入多筆代墊紀錄，回傳最少轉帳次數的結清方案。

部署於 AWS Lambda + API Gateway，使用整數運算避免浮點誤差。

## API

```
POST https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com/split_settle
Content-Type: application/json
```

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

# 部署
PATH="/opt/homebrew/bin:$PATH" sam build && sam deploy
```

## 架構

```
API Gateway (HTTP API)
      │
      ▼
Lambda (Python 3.13)
  src/split_settle/handler.py
```

# ruleset test
