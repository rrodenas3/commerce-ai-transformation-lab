---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: synthetic Stage 1 calibration and a prepared creator-run pack only, with no completed human baseline, AI-assisted workflow, executed recovery, pilot, production deployment, or realised business outcome
---

# Commerce AI Transformation Lab

## Delayed or Partial Fulfilment to Verified Customer Recovery

This public laboratory documents, step by step, how a synthetic mid-market commerce company can redesign a cross-functional order-exception workflow around business outcomes, human accountability, governed AI, evaluation, enablement, and an explicit investment decision.

The repository is a transformation case, not an autonomous-agent showcase.

> **Current maturity:** Foundation stage. A proposed current state, versioned policy, 24-case synthetic discovery system, oracle, deterministic calibration, and frozen creator-run evidence pack now exist. The pack is prepared but blank. No completed human baseline, AI-assisted workflow, executed recovery, real-user pilot, production deployment, or realised business outcome is claimed.

## Current evidence snapshot

| Evidence | Result | Meaning |
| --- | ---: | --- |
| Public discovery cases | 24 across 8 families | Balanced coverage for design and contract calibration, not a real incident distribution |
| Transparent rules coverage | 14/24 — 58.3% | Cases receiving a non-abstaining recommendation |
| Controlled abstention | 10/24 — 41.7% | Conflicting, stale, risky, or unresolved-action cases routed to a stop |
| Critical control violations | 0 | On the author-designed public calibration set only |
| Consequential actions and verified recoveries | 0 | The project has not yet built or executed the governed workflow |
| Creator manual run | Prepared, not started | Frozen case-only pack; no human-performance result exists |

The co-designed rules and oracle aligned on all 24 cases. This is an internal contract check, not AI performance, independent validation, or business value.

## Transformation question

Can an AI-assisted workflow increase the proportion of delayed or partially fulfilled orders that reach a correct, policy-compliant, and verified customer recovery within the target time—without weakening human authority over financial, customer-facing, or irreversible consequences?

## Transformation loop

```text
Business outcome
    -> current workflow and manual baseline
    -> workflow redesign
    -> human and AI task allocation
    -> decision rights and controls
    -> governed local implementation
    -> evaluation and independent review
    -> enablement and observed first use
    -> scale, revise, pause, or stop
```

## Journey

| Stage | Decision to demonstrate | Public status |
| --- | --- | --- |
| 0. Foundation | Why this value stream, which outcome, and which evidence boundaries? | Complete |
| 1. Baseline | What happens without AI, and where does coordination fail? | **Active — creator-run pack frozen; completion pending** |
| 2. Redesign | Which tasks move, which decisions remain human, and why? | Not started |
| 3. Governed build | Can the minimum vertical slice operate within its authority? | Not started |
| 4. Evaluation | Does it improve verified resolution without hiding risk or review cost? | Not started |
| 5. Enablement | Can someone other than the creator use and challenge it? | Not started |
| 6. Benefits decision | Should the fictional organisation scale, revise, pause, or stop? | Not started |

The [journey index](journey/README.md) links every decision artifact as it becomes evidence-bearing.

## Evidence model

Every material statement is classified as one of:

- **Research-grounded:** supported by a cited public source.
- **Synthetic-observed:** produced by executing a disclosed synthetic case.
- **Human-reviewed:** observed during a documented independent review session.
- **Hypothetical impact:** an assumption used for a scenario or benefits model.

Synthetic results may demonstrate task performance, policy adherence, escalation, action control, latency, cost, and recovery behaviour. They do not demonstrate customer satisfaction, adoption, retained revenue, realised savings, enterprise deployment, or production reliability.

See the [public evidence policy](docs/EVIDENCE_POLICY.md) and [security policy](SECURITY.md).

## First vertical slice

The first slice is deliberately narrow:

```text
late or partial fulfilment signal
    -> evidence and policy assembly
    -> cause and uncertainty assessment
    -> recovery options
    -> human confirmation when consequential
    -> simulated action
    -> postcondition verification
    -> customer communication draft
    -> reviewed learning candidate
```

Nearby exception types are evaluation variants, not parallel products. See [FIRST_VERTICAL_SLICE.md](docs/FIRST_VERTICAL_SLICE.md).

## Repository map

- [`docs/PROJECT_CHARTER.md`](docs/PROJECT_CHARTER.md) — outcome, scope, roles, and non-goals.
- [`docs/EVIDENCE_POLICY.md`](docs/EVIDENCE_POLICY.md) — claim, maturity, privacy, and publication rules.
- [`docs/RESEARCH_BASE.md`](docs/RESEARCH_BASE.md) — primary research and enterprise precedents.
- [`docs/FIRST_VERTICAL_SLICE.md`](docs/FIRST_VERTICAL_SLICE.md) — bounded workflow and human decision rights.
- [`docs/MEASUREMENT_PLAN.md`](docs/MEASUREMENT_PLAN.md) — baseline, evaluation, and scale-decision logic.
- [`docs/DELIVERY_ROADMAP.md`](docs/DELIVERY_ROADMAP.md) — stage gates and immediate next steps.
- [`docs/DECISION_LOG.md`](docs/DECISION_LOG.md) — durable decisions and their evidence.
- [`docs/STAGE1_OPERATING_MODEL.md`](docs/STAGE1_OPERATING_MODEL.md) — fictional company boundary, roles, systems, policy, and Raul's decisions.
- [`docs/STAGE1_CURRENT_STATE.md`](docs/STAGE1_CURRENT_STATE.md) — proposed manual queues, workflow, failure hypotheses, and measures.
- [`docs/STAGE1_CASE_SYSTEM.md`](docs/STAGE1_CASE_SYSTEM.md) — generated cases, independent oracle contract, and deterministic calibration.
- [`docs/STAGE1_MANUAL_BASELINE_PROTOCOL.md`](docs/STAGE1_MANUAL_BASELINE_PROTOCOL.md) — fair manual-run protocol and evidence instrument.
- [`journey/01-stage-1-baseline.md`](journey/01-stage-1-baseline.md) — decisions, observations, failures, and adaptations.
- [`policy/publication-policy.json`](policy/publication-policy.json) — machine-readable public-safety rules.

## Public-safety verification

The repository includes a zero-dependency verifier and CI check for common secret patterns, private machine paths, risky filenames, and missing evidence metadata.

```bash
python -m unittest discover -s tests -v
python scripts/verify_public_safety.py
```

Reproduce the Stage 1 artifacts with:

```bash
python scripts/generate_stage1_cases.py
python scripts/stage1_deterministic_baseline.py
```

Passing these checks reduces accidental disclosure risk; it is not a security certification.

## Author

Raul Rausell — founder-operator and AI transformation practitioner focused on business outcomes, workflow redesign, adoption, governed autonomy, and measurable value.

## Rights

Copyright © 2026 Raul Rausell. See [LICENSE.md](LICENSE.md).
