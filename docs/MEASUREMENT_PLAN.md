---
evidence_status: synthetic-observed
public_safe: true
maturity: local-mvp
limitations: measured results are creator-evaluated synthetic workflow evidence; human performance, live reliability, provider cost and latency, customer outcomes, and realised value are not observed
---

# Measurement and evaluation record

## Outcome contract

The intended business outcome is verified customer recovery. The local MVP can measure only a narrower operational milestone on generated records:

> An eligible remedy enters the synthetic numerator only when the exact action is policy-compliant, bound to valid simulated authority, committed once, and confirmed by a structurally separate read-only verifier from generated authoritative source effects.

The verifier is architecturally separate from mutation code but is not independent human validation. Refund requires the exact action-linked payment entry. Reship requires replacement creation, exact reservation, and WMS acceptance. Neither establishes delivery, satisfaction, or realised customer value. Safe escalation, evidence blocking, wait, and no-new-action do not enter the verified-remedy numerator.

## Frozen evidence identity

| Field | Value |
| --- | --- |
| Evaluation pack | `S2-EVALUATION-20260812-V6` |
| Run | `S2-CF-RUN-0005` |
| Scheduled cases | 36 |
| Provider attempts | 36 |
| Evidence class | Creator-evaluated synthetic observed |
| Maturity ceiling | `local-mvp` |
| Human evidence | `not_observed` |
| Decision | `pause` |

The V6 pack, thresholds, schedules, provider attempts, runtime pins, isolation profile, and oracle commitment were frozen before execution. Outputs were sealed before oracle release; score generation followed release. The claim is process-controlled and creator-evaluated, not cryptographically blind or independent.

## Layered measures and observed values

| Dimension | Measure | Result | Denominator | Interpretation |
| --- | --- | ---: | ---: | --- |
| Recommendation | Correct action, route, and governed outcome | 10,000 bp | 36 | 100% inside the creator-designed synthetic oracle |
| Safe routing | Control-stop or safe route where required | 10,000 bp | 3 | Safe behaviour, never recovery |
| Authority | Exact simulated approval validity | 10,000 bp | 6 | Synthetic roles, not human approval |
| Execution | Eligible action committed | **8,333 bp** | 18 | 15 committed; 3 pending |
| Remedy | Required operational postcondition verified | 10,000 bp | 15 | Non-independent system milestone, not delivery |
| Recovery | Injected lost-receipt cases reconciled | 10,000 bp | 3 | Synthetic fault recovery |
| Closure | Final state and linked artifacts legal | 10,000 bp | 36 | Workflow closure, not customer outcome |
| Communication | Unsupported facts | 0 | 36 | Unsent artifacts only |
| Exact zero | Critical failures | 0 | 36 | Passed in V6; any future one forces stop |
| Provider cost | Known attempts | 0 | 36 | Unknown, not zero cost |
| Provider latency | Known attempts | 0 | 36 | Unknown, not zero latency |

Basis-point values and denominators come from [the sealed score](../data/stage2/runs/S2-CF-RUN-0005/score.json); display values and evidence gaps come from [the evaluation summary](../data/stage2/decision-pack/evaluation-summary.json).

## Denominator conservation

| Outcome | Count | Numerator treatment |
| --- | ---: | --- |
| Verified remedy | 15 | Enters only the eligible remedy denominator |
| Verified wait | 3 | Separate verified condition |
| Verified no-new-action | 3 | Separate verified condition |
| Evidence blocked | 9 | Preserved, not recovery |
| Control stopped | 3 | Safe behaviour, not recovery |
| Pending | 3 | Preserved incomplete evidence |
| Failed | 0 | Visible zero |
| Escalated | 0 | Visible zero |
| Excluded | 0 | Visible zero |

The counts sum to 36. A safe stop cannot inflate recovery and an incomplete postcondition cannot become success.

## Structural comparator—not a productivity baseline

The synthetic Q1–Q8 comparator recorded 288 queue transitions, 252 source opens, 36 policy lookups, 15 handoffs, 9 clarification requests, 9 reopens, 18 action attempts, 24 verification steps, and 20,160,000 milliseconds of virtual active work across 36 cases. The assisted path recorded 303 workflow events.

These fields describe two structures. They do not measure a real person's handling time, review burden, labour saving, throughput, or productivity change. Human review burden remains a set of structural proxies: evidence items, conflicts, missing-evidence blocks, governed overrides, approval steps, and recovery transitions.

## Exact-zero precedence

Any observed unauthorised action, duplicate action, false verification, personal/secret disclosure, oracle contamination, or evidence-chain tampering forces `stop`, regardless of average quality. Pre-run exposure or incomplete evidence forces `pause`; subthreshold quality, reliability, or cost forces `revise`; only complete passing gates may return `scale_next_experiment`, never a pilot authorisation.

## Economics and enablement

Economics uses integer euro cents, conservative/base/upside assumptions, total operating cost, a non-AI alternative, and a separate capacity-realisation assumption. Because the scenario recommendation changes across the envelope, economics is `inconclusive` and does not support scale. No value is realised. [Economics summary](../data/stage2/decision-pack/economics-summary.json)

Enablement has five designed role packages. Human first use, comprehension, confidence, help, review time, friction, trust, repeated use, adoption, and outcome contribution are all `not_observed`. [Readiness matrix](../data/stage2/decision-pack/enablement-readiness.json)

## Current decision and next measurement

Decision: **PAUSE**.

The next evidence question is whether a new capped synthetic pack can achieve 18 of 18 eligible execution commitments and complete cost/latency evidence while preserving exact-zero controls. The action is owned by Raul and capped at 36 cases, 36 attempts, 7 calendar days, and EUR 50. [Decision output](../data/stage2/decision-pack/decision-output.json)

Independent human review, live reliability, customer outcomes, adoption, satisfaction, retained revenue, and realised savings remain future measures. They are not inferred from synthetic results or assigned zero.
