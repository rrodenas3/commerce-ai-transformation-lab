---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: evaluation design only; thresholds are not preregistered and no results exist
---

# Measurement and evaluation plan

## North-star measure

**Verified resolution rate within target time**: the proportion of eligible exception cases that reach a correct, policy-compliant, independently verified resolution within the preregistered time boundary.

The numerator requires every condition. A fast but incorrect, unverified, or unauthorised outcome does not count.

## Measurement chain

```text
case inputs
    -> workflow completion
    -> resolution correctness
    -> policy and authority compliance
    -> review and rework
    -> verified simulated action
    -> customer-outcome proxy
    -> hypothetical economic sensitivity
```

## Core measures

| Dimension | Measure | Evidence boundary |
| --- | --- | --- |
| Outcome | Verified resolution rate within target time | Synthetic-observed |
| Quality | Resolution correctness against an independent case oracle | Synthetic-observed |
| Governance | Correct escalation and dangerous under-escalation | Synthetic-observed |
| Communication | Unsupported customer-facing claim rate | Synthetic-observed and human-reviewed |
| Human work | Review minutes, edits, rejections, overrides, and abstentions | Human-reviewed when people participate |
| Reliability | Duplicate-action prevention, failure recovery, and postcondition verification | Synthetic-observed |
| Performance | End-to-end latency and model or infrastructure cost | Synthetic-observed |
| Enablement | First-use completion, help required, comprehension, and reported friction | Human-reviewed |
| Economics | Cost per correctly verified resolution and scenario sensitivity | Synthetic-observed plus hypothetical assumptions |

## Baselines

1. **Manual baseline:** a reviewer resolves the same representative cases using the synthetic source records and policy without AI assistance.
2. **Deterministic baseline:** rules handle the subset that requires no ambiguous interpretation.
3. **AI-assisted workflow:** the governed vertical slice operates with the same source evidence and case oracle.

Cases are split before tuning so the final evaluation is not merely a replay of development examples.

## Independent review design

Where reviewers are available, they should complete selected manual and assisted cases without creator coaching during first use. Capture corrections, confusion, requests for help, rejected recommendations, trust concerns, and the change each observation triggers.

Reviewer sessions demonstrate observed use of synthetic cases. They do not demonstrate organisational adoption.

## Preregistration rule

Success thresholds, critical control failures, case splits, and the scale-decision rule will be committed before the evaluated implementation run. Thresholds must not be moved after results are known without a recorded decision and a new evaluation cycle.

Critical authority violations, secret disclosure, duplicated consequential action, or falsely verified postconditions are exact-zero metrics for the evaluated release.

## Final decision

The recommendation considers outcome quality, human review burden, adoption friction, risk, reliability, and total operating cost. Model accuracy alone cannot justify scale.
