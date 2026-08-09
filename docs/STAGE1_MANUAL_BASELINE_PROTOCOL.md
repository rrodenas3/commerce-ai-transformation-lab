---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: manual baseline protocol and instruments exist but no human case run or reviewer evidence has been recorded
---

# Manual baseline protocol

## Research question

How does a person resolve the SCC-01 discovery cases without AI assistance, and where do handling effort, evidence gaps, handoffs, authority boundaries, and unsafe closure appear?

## Study boundary

- Synthetic cases only.
- No real actions, integrations, communications, customers, or financial consequences.
- Ordinary search, calculator, and provided templates are allowed.
- Generative AI, direct oracle consultation during case handling, deterministic decisions, and creator coaching are prohibited during a measured run.
- A creator-run session is labelled creator-run, not independent review.
- A future independent participant is represented only by a consented pseudonymous reviewer code.

The committed oracle is public, so every Stage 1 discovery run declares `oracle_exposure_status: public-oracle-available`. This is an exposure limitation, not a blinded study. A reviewer may receive a case-only package to reduce immediate priming, but the run must not be described as blinded. A future held-out evaluation requires a separately frozen and temporarily sealed oracle.

## Pre-run freeze

Record before starting:

1. Copy and freeze `manual-run-manifest-template.json` for the run.
2. Confirm its SHA-256 pins for the cases, oracle, policy, and source artifact manifest.
3. Confirm the policy and oracle versions.
4. Confirm the complete assigned case IDs and order.
5. Record a pseudonymous reviewer code and choose the matching run type in both the manifest and CSV.
6. Record allowed tools, available instructions, start and stop rules, and exact score definitions.

Do not change eligibility, policy, thresholds, or the oracle after observing results. Any correction starts a new version and evaluation cycle.

## Operator instructions

For each assigned case:

1. Start the active-handling timer.
2. Check the signal ID, timestamp, duplication, and promise version.
3. Reconcile ordered, shipped, delivered, remaining, refunded, and replaced quantity.
4. Inspect each available source and note missing, stale, or conflicting evidence.
5. Decide whether the case is eligible, a control stop, or a false positive.
6. Select one recommended action or a controlled escalation.
7. Select the decision route and owner.
8. List the facts that a customer message may safely state.
9. Stop the timer after the worksheet is complete.
10. Record help, confusion, policy lookups, handoffs, and uncertainty.

Timestamps must be timezone-aware UTC instants. The end must not precede the start, and active handling time cannot exceed elapsed wall-clock time.

Do not infer a completed refund, replacement, or delivery from a request, draft, pending status, or estimate.

## Recorded fields

The generated CSV captures:

- case and pseudonymous reviewer code;
- run type and timestamps;
- active handling seconds;
- recommended action and route;
- evidence used and message facts;
- confidence and help requested;
- handoffs and policy lookups;
- notes without personal data.

Queue waiting time is not fabricated. If later modelled, it remains a separate hypothetical-impact artifact.

## Scoring

The evaluator—not the operator—compares the completed record with the frozen oracle.

| Score | Condition |
| --- | --- |
| Triage correct | Eligible, control, or no-new-action route matches the oracle |
| Action allowed | Recommendation appears in the allowed action set |
| Authority correct | Decision route matches required authority |
| Evidence complete | Required evidence was inspected or absence triggered the correct stop |
| Communication supported | Every message fact appears in the allowed fact set |
| Critical control | No prohibited or duplicate consequential action and no false verification |

The frozen run manifest is the denominator. Scoring requires one completed, unique record for every assigned case and rejects unresolved or unassigned IDs. The summary reports assigned, completed, and unresolved counts and IDs; difficult cases cannot be silently removed.

## Fair-comparison rules

- Later manual, deterministic, and AI-assisted runs receive the same source evidence and policy version.
- Case ordering is counterbalanced when more than one reviewer participates.
- A participant does not resolve the same measured case manually and with assistance unless a washout/crossover limitation is explicit.
- Active work, queue delay, review burden, and total elapsed time remain separate.
- Every failed, abstained, overridden, or unresolved case remains in the evidence pack.

## Exit gate

Stage 1 manual evidence remains incomplete until at least one recorded creator-run baseline exists. Independent-human evidence remains absent until a consented reviewer completes cases without creator coaching.

Running `scripts/stage1_deterministic_baseline.py` generates both the blank CSV and the compact run-manifest template. Copy both for a measured run, freeze the run manifest before handling cases, and keep `oracle_exposure_status` truthful. After completing the CSV, score explicit pinned artifacts without modifying the source record:

```bash
python scripts/score_stage1_manual.py \
  --input completed-manual-run.csv \
  --output manual-run-summary.json \
  --cases data/stage1/generated/cases.jsonl \
  --oracle data/stage1/generated/oracle.jsonl \
  --run-manifest frozen-manual-run-manifest.json
```

The scorer verifies the pinned cases, oracle, policy, and source-manifest hashes and their policy/oracle versions before evaluating records. It also rejects any output path that aliases an input artifact.
