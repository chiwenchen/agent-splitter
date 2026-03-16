# Issue #27: test: add tests for unified participant splits model

> Claimed by: dev-2
> Source: https://github.com/chiwenchen/agent-splitter/issues/27

---

## Context

Issue #26 introduces a new `splits` field on each expense entry, enabling weighted splits, fixed amounts, exclusions, and free riders. This issue covers writing the tests **before or alongside** the implementation (TDD).

Read issue #26 first for the full schema design.

## Test Cases to Add

### 1. Weighted split
```python
# A:B:C = 2:1:1 on a $1000 expense paid by A
# Expected: A net +500, B owes 250, C owes 250
```

### 2. Fixed amount
```python
# A pays $1000, B has fixed amount=400, C gets remainder
# B owes 400, C owes 600
```

### 3. Free rider (treat someone)
```python
# A pays $900, B has amount=0 (treated), A and C split remainder equally
# B owes 0, A and C each owe 450
```

### 4. Exclude participant
```python
# 3 participants, but splits only lists 2 (or share=0 for third)
# Third person owes 0
```

### 5. Combination: weighted + fixed + exclusion
```python
# 4 people, one fixed, one excluded, two weighted differently
```

### 6. Advance payment (先墊後收)
```python
# A pays full $900, equal split among all 3
# Verify B→A and C→A settlements via existing paid_by mechanism
# (no splits needed — this is a regression test)
```

### 7. Backward compatibility
```python
# Request using split_among (old format) still works
# Request omitting splits defaults to equal split
```

### 8. Validation errors
```python
# splits participant not in participants list → 400
# negative share → 400
# negative amount in splits → 400
# sum of fixed amounts exceeds expense amount → 400
# all shares are 0 with remaining > 0 → 400
# both split_among and splits provided → 400
```

### 9. Remainder distribution precision
```python
# $10 split with share=2:1:1 → 5.00, 2.50, 2.50
# $10 split with share=1:1:1 → remainders distributed to first participant
```

## Notes

- Add tests to `tests/test_handler.py` alongside existing tests
- Follow existing test style (using `make_event()` helper and `lambda_handler`)
- All amounts in cents internally — test edge cases around rounding
- Tests should fail until issue #26 implementation lands (TDD red phase)

## Depends on

Issue #26 (schema definition must be agreed before writing tests)

---

## Discussion



---

## Implementation Checklist

- [ ] Write failing tests first (TDD)
- [ ] Implement to make tests pass
- [ ] All acceptance criteria met
- [ ] Run full test suite — no regressions
- [ ] Code reviewed (no CRITICAL issues)
- [ ] Commit with message: `feat: test: add tests for unified participant splits model (closes #27)`
