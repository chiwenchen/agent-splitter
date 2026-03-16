# Issue #26: feat: unified participant model for flexible expense splitting

> Claimed by: dev-1
> Source: https://github.com/chiwenchen/agent-splitter/issues/26

---

## Background

Currently `POST /v1/split_settle` only supports equal splitting via `split_among`. This issue designs a unified participant model that covers weighted splits, fixed amounts, exclusions, advance payments, and combinations — all in one flexible schema.

## Proposed API Change

### New `expenses[].splits` field

Replace `split_among: [string]` with an optional `splits` array on each expense. When omitted, falls back to current equal-split behavior for backward compatibility.

```json
{
  "currency": "TWD",
  "participants": ["A", "B", "C"],
  "expenses": [
    {
      "description": "dinner",
      "paid_by": "A",
      "amount": 900,
      "splits": [
        { "participant": "A", "share": 1 },
        { "participant": "B", "amount": 0 },
        { "participant": "C", "share": 1 }
      ]
    }
  ]
}
```

### Split entry fields

| Field | Type | Description |
|-------|------|-------------|
| `participant` | string | Must be in `participants` |
| `amount` | number | Fixed amount this person owes (optional) |
| `share` | number | Relative weight for remaining amount (optional, default 1) |

**Algorithm:**
1. Sum all fixed `amount` entries
2. `remaining = expense.amount - sum(fixed amounts)`
3. Distribute `remaining` proportionally by `share` among non-fixed participants
4. Each person's `net = paid - owed`; run greedy settlement on nets

### Scenarios covered by this model

| Scenario | How to express |
|----------|---------------|
| Equal split (current) | omit `splits` or all `share=1` |
| Weighted split | different `share` values |
| Custom fixed amount | set `amount` directly |
| Exclude someone | `share=0` or omit from splits |
| Treat someone (free rider) | `amount=0` |
| Advance payment (先墊後收) | `paid_by` already handles this |
| Combinations | mix `amount` and `share` freely |

## Validation Rules

- `splits` participants must be a subset of `participants`
- `share` must be ≥ 0
- `amount` must be ≥ 0
- Sum of fixed `amount`s must not exceed expense `amount`
- At least one participant must have `share > 0` if there is remaining amount after fixed deductions
- If `splits` is omitted → treat all `participants` as equal share (backward compat)

## Backward Compatibility

`split_among` remains supported. If both `split_among` and `splits` are provided, return 400.

## Deliverables

- [ ] Update `split_settle()` in `src/split_settle/handler.py` to handle new `splits` field
- [ ] Update OpenAPI schema in `handler.py` (`GET /openapi.json`)
- [ ] Update `SPEC.md` with new schema and examples
- [ ] All existing tests must still pass

---

## Discussion



---

## Implementation Checklist

- [ ] Write failing tests first (TDD)
- [ ] Implement to make tests pass
- [ ] All acceptance criteria met
- [ ] Run full test suite — no regressions
- [ ] Code reviewed (no CRITICAL issues)
- [ ] Commit with message: `feat: feat: unified participant model for flexible expense splitting (closes #26)`
