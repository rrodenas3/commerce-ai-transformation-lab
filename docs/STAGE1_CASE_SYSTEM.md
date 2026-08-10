---
evidence_status: synthetic-observed
public_safe: true
maturity: foundation
limitations: author-designed public discovery cases and oracle calibrate internal contracts but are not held-out evaluation or independent validation
---

# Stage 1 synthetic case and oracle system

## Purpose

The case system turns the operating-model assumptions into reproducible evidence. It is designed to answer three questions before any AI is built:

1. Which routine cases can transparent rules recommend safely?
2. Which cases require human clarification, approval, or specialist control?
3. Can the project measure decisions without confusing a requested action with a verified customer recovery?

## Dataset contract

| Property | Value |
| --- | --- |
| Fictional organisation | SCC-01 |
| Dataset role | Public foundation discovery |
| Case count | 24 |
| Generator | `scc-01-foundation-case-generator` version 1.0.0 |
| Deterministic seed | `20260809` |
| Policy | `SCC-01-RECOVERY-POLICY` version 1.0.0 |
| Real data | None |
| Held-out evaluation set | No |

Every case contains only structured synthetic facts. It omits names, addresses, email, telephone, payment instruments, and free-text customer messages.

## Case families

| Family | Cases | What it tests |
| --- | ---: | --- |
| Reliable delay | 3 | Wait, replacement, and refund options under consistent evidence |
| Partial with stock | 3 | Quantity reconciliation, customer preference, and authority |
| Partial without stock | 3 | Exact missing-quantity refund and exposure routing |
| Conflicting evidence | 3 | Contradiction or missing-source escalation |
| Duplicate or stale | 3 | Prior recovery, duplicate signals, and freshness |
| Authority boundary | 3 | Exact threshold and one-cent-above behavior |
| Retry and verification | 3 | Pending, failed-safe, and unverified prior actions |
| Out-of-scope risk | 3 | Safety, privacy, and suspected-fraud control stops |

The balanced distribution improves boundary coverage. It is not intended to reproduce a real retailer's incident mix.

## Oracle design

The oracle is computed from frozen case facts and the versioned policy. It records:

- eligibility for the recovery denominator;
- allowed and preferred action;
- required route and decision owner;
- required evidence;
- message facts that may be stated safely;
- rationale and control codes;
- applicable exact-zero controls and whether a decision violates one.

Freshness is derived from timezone-aware source age against the policy threshold; a caller-supplied `fresh` flag cannot override the timestamp. Active chargeback is derived from the payment record, and a prior action marked `VERIFIED` counts as recovered only when its action-specific authoritative postcondition agrees.

The oracle ignores any `model_output` field and is not called by the deterministic decision function. This makes it independent of future model output, but not independently authored: the same project author designed the cases, policy, rules baseline, and oracle. A separately generated 32-case held-out instrument now exists, but its human record is blank; independent reviewers and completed observations are still required before stronger claims.

## Data separation

```text
policy.json
    -> case generator -> public discovery cases
    -> oracle builder -> public discovery oracle

public discovery cases -> transparent rules baseline -> recommendations
recommendations + oracle -> scorer -> calibration summary

current: committed held-out cases -> withheld oracle file -> human record pending -> record freeze -> oracle release -> score
```

Model development may use the public discovery cases. It must not use the held-out cases or oracle before the human baseline is frozen. Invalidated V1 can never enter baseline evidence. After release, V2 becomes evaluation evidence rather than a reusable blind set; later confirmatory evaluation requires a new pack version.

## Deterministic calibration result

The transparent rules baseline was executed on all 24 public discovery cases.

| Measure | Observed result |
| --- | ---: |
| Cases scored | 24 |
| Oracle-eligible recovery cases | 19 |
| Control-stop or no-new-action cases | 5 |
| Non-abstaining decision coverage | 14/24 — 58.3% |
| Controlled abstentions | 10/24 — 41.7% |
| Correct recommendation or safe escalation on this calibration set | 24/24 |
| Critical control violations | 0 |
| Unsupported message facts | 0 |
| Consequential actions executed | 0 |
| Verified customer recoveries | 0 |

The 24/24 result is **contract calibration**, not a performance headline. The author-designed rules and oracle encode the same frozen policy, the cases are public, and no manual or AI comparison exists. The useful signal is the explicit trade-off: rules cover 58.3% of the discovery set and route the remaining 41.7% to a controlled stop.

## Reproduce

```bash
python scripts/generate_stage1_cases.py
python scripts/stage1_deterministic_baseline.py
python -m unittest tests.test_stage1_case_system -v
```

Committed artifacts:

- [`policy.json`](../data/stage1/policy.json)
- [`manifest.json`](../data/stage1/generated/manifest.json)
- [`cases.jsonl`](../data/stage1/generated/cases.jsonl)
- [`oracle.jsonl`](../data/stage1/generated/oracle.jsonl)
- [`deterministic-decisions.jsonl`](../data/stage1/generated/deterministic-decisions.jsonl)
- [`deterministic-summary.json`](../data/stage1/generated/deterministic-summary.json)
- [`manual-baseline-template.csv`](../data/stage1/generated/manual-baseline-template.csv)
- [`manual-run-manifest-template.json`](../data/stage1/generated/manual-run-manifest-template.json)

## Claims this evidence supports

- A reproducible public synthetic case system exists.
- Policy boundaries, time-derived freshness, authoritative postconditions, safe escalation, exact-zero controls, and public-data invariants are executable and tested.
- A transparent rules baseline generated the disclosed calibration result.
- Every committed generated artifact can be reproduced byte-for-byte from the public source and policy.

It does not support claims of AI performance, human adoption, faster resolution, verified action, retained revenue, customer satisfaction, production reliability, or enterprise value.
