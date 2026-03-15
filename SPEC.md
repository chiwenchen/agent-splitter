# SplitSettle — MCP Tool Schema (MVP)

> AI Agent 分帳工具：輸入多筆代墊紀錄，精確計算最少轉帳次數的結清方案。

---

## Tool Definition

```json
{
  "name": "split_settle",
  "description": "Calculate the optimal settlement plan from a group of shared expenses. Given a list of participants and who-paid-what, returns the minimum number of transfers needed to settle all debts. Uses precise arithmetic (no floating point errors).",
  "inputSchema": {
    "type": "object",
    "required": ["currency", "participants", "expenses"],
    "properties": {
      "currency": {
        "type": "string",
        "description": "ISO 4217 currency code, e.g. TWD, USD, JPY",
        "examples": ["TWD", "USD"]
      },
      "participants": {
        "type": "array",
        "items": { "type": "string" },
        "minItems": 2,
        "description": "List of participant names",
        "examples": [["Alice", "Bob", "Carol"]]
      },
      "expenses": {
        "type": "array",
        "minItems": 1,
        "description": "List of expense records",
        "items": {
          "type": "object",
          "required": ["paid_by", "amount", "split_among"],
          "properties": {
            "description": {
              "type": "string",
              "description": "What this expense was for",
              "examples": ["晚餐", "計程車", "飯店住宿"]
            },
            "paid_by": {
              "type": "string",
              "description": "Name of the person who paid (must be in participants)"
            },
            "amount": {
              "type": "number",
              "exclusiveMinimum": 0,
              "description": "Total amount paid. Integer or up to 2 decimal places."
            },
            "split_among": {
              "type": "array",
              "items": { "type": "string" },
              "minItems": 1,
              "description": "Who shares this expense. Each person splits equally. Must all be in participants."
            }
          }
        }
      }
    }
  }
}
```

---

## Response Schema

```json
{
  "type": "object",
  "properties": {
    "currency": {
      "type": "string",
      "description": "Same currency as input"
    },
    "summary": {
      "type": "array",
      "description": "Per-person breakdown",
      "items": {
        "type": "object",
        "properties": {
          "participant": { "type": "string" },
          "total_paid": { "type": "number", "description": "Total amount this person paid" },
          "total_owed": { "type": "number", "description": "Total amount this person should pay" },
          "balance": { "type": "number", "description": "Positive = is owed money, Negative = owes money" }
        }
      }
    },
    "settlements": {
      "type": "array",
      "description": "Minimum transfers to settle all debts",
      "items": {
        "type": "object",
        "properties": {
          "from": { "type": "string", "description": "Person who pays" },
          "to": { "type": "string", "description": "Person who receives" },
          "amount": { "type": "number", "description": "Amount to transfer" }
        }
      }
    },
    "total_expenses": { "type": "number" },
    "num_settlements": { "type": "integer", "description": "Number of transfers needed" }
  }
}
```

---

## Example

### Request

```json
{
  "currency": "TWD",
  "participants": ["Alice", "Bob", "Carol"],
  "expenses": [
    {
      "description": "晚餐",
      "paid_by": "Alice",
      "amount": 1200,
      "split_among": ["Alice", "Bob", "Carol"]
    },
    {
      "description": "計程車",
      "paid_by": "Bob",
      "amount": 300,
      "split_among": ["Alice", "Bob", "Carol"]
    }
  ]
}
```

### Response

```json
{
  "currency": "TWD",
  "summary": [
    {
      "participant": "Alice",
      "total_paid": 1200,
      "total_owed": 500,
      "balance": 700
    },
    {
      "participant": "Bob",
      "total_paid": 300,
      "total_owed": 500,
      "balance": -200
    },
    {
      "participant": "Carol",
      "total_paid": 0,
      "total_owed": 500,
      "balance": -500
    }
  ],
  "settlements": [
    { "from": "Carol", "to": "Alice", "amount": 500 },
    { "from": "Bob", "to": "Alice", "amount": 200 }
  ],
  "total_expenses": 1500,
  "num_settlements": 2
}
```

---

## Design Notes

### 精度處理
- 所有計算使用整數運算（金額 × 100 轉為分/cent），避免浮點誤差
- 除不盡的尾差分配給代墊最多的人（或第一位），確保總和精確

### 最小化轉帳演算法
- 計算每人 balance（paid - owed）
- 使用 greedy algorithm：最大債務人付給最大債權人，直到全部結清
- 對於 N ≤ 20 人的場景，greedy 已足夠最佳化

### 驗證規則
- `paid_by` 和 `split_among` 中所有名字必須在 `participants` 內
- `amount` 必須 > 0
- `split_among` 不可為空
- 所有 balance 加總必須為 0（內部 checksum）

### 未來擴展（非 MVP）
- 自訂比例分帳（weighted split）
- 多幣種 + 匯率
- 排除特定人不分攤特定項目
- 群組歷史記錄（stateful）
