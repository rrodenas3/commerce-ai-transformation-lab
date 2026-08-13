---
evidence_status: synthetic-observed
public_safe: true
maturity: foundation
limitations: proposed synthetic process replay with hypothetical durations; no human work, customer outcome, adoption, or realised value was observed
---

# Stage 2 synthetic current-state comparator

## Decision boundary

This comparator is a reproducible **proposed current-state model** for the delayed or partial-fulfilment journey. It does not reconstruct a former employer's workflow and it does not measure human handling. Its purpose is to make the before-state topology, structural work, deterministic task decisions, and time assumptions explicit before the future workflow is evaluated.

The committed development denominator contains 24 generated cases: two variants across each of 12 disclosed scenario families. It is public development and regression material, not a held-out or independently reviewed evaluation set.

## What the comparator executes

Every case traverses the documented Q1-Q8 topology:

| Queue | Deterministic replay responsibility | Recorded evidence |
| --- | --- | --- |
| Q1 | Open the OMS exception | Intake event and one canonical case candidate |
| Q2 | Check CRM intake and deduplicate | Signal count and deduplication event |
| Q3 | Assemble the seven synthetic sources | Seven source opens and one policy lookup |
| Q4 | Resolve stale, conflicting, or unresolved-action evidence | Clarification, handoff, and dependency wait |
| Q5 | Apply the fictional €25/€100/order-value authority boundaries | Approval step, owner route, and dependency wait |
| Q6 | Determine whether customer choice is available | Choice step and dependency wait |
| Q7 | Record the proposed action/verification work or the legal no-action condition | Action attempt, verification step, wait, no-new-action, or stop |
| Q8 | Exercise closure/reopen control | Closure check and reopen count |

Q1 and Q2 remain separate structural touches. When both contain the same synthetic signal, replay records two intake signals, one deduplication event, and exactly one canonical case. The work does not disappear because it was successfully deduplicated.

## Source and revision model

Each case contains exactly one source record for OMS, WMS, carrier, inventory, payment, CRM, and policy. Every record carries:

- a fictional Stage 2 record and case ID;
- effective, observed, and ingestion instants with explicit UTC offsets;
- a source-specific provenance digest;
- the `synthetic-public` sensitivity label;
- the case revision and source name.

The batch becomes visible only after its commit marker. Its revision pins both the SHA-256 digest of the ordered source-event cut and the workflow-ledger genesis head. A changed record therefore invalidates its provenance, event-cut digest, and revision pin instead of silently changing the facts behind a governed object.

The inward facts module derives remaining quantity, affected value, source freshness, quantity conflicts, duplicate intake, active chargeback, prior-remedy coverage, and unresolved action state. Those truth labels are forbidden in input records. This prevents a generator, provider, or later caller from overriding authoritative derivation with a convenient boolean.

## Evidence classes

| Layer | Label | Meaning |
| --- | --- | --- |
| Case, queue, transition, source-open, policy-lookup, handoff, clarification, approval, action, verification, and reopen counts | `synthetic-observed` | Deterministically observed while replaying generated records |
| Deterministic route or stop result | `synthetic-observed` | Result of disclosed source facts and fictional policy thresholds |
| Active-work and dependency-wait milliseconds | `hypothetical-impact` | Versioned modelling inputs, not measured handling time |
| Trust, adoption, enablement friction, manual-review time, customer satisfaction, retained revenue, realised savings | `not_observed` | No human or real-world evidence exists |

Zero is never substituted for an unobserved human measure.

## Versioned time assumptions

`stage2-current-state-assumptions/v1` records active-work assumptions for Q1-Q8 and separate dependency-wait assumptions for clarification, approval, customer choice, and action recovery. For each case:

`total virtual elapsed = hypothetical active work + hypothetical dependency wait`

The output preserves all three values. It never converts modelled time into labour savings or realised value.

Times are parsed only when they carry an explicit timezone. The canonical generator uses UTC for source-cut timing and an explicit Europe/Paris summer offset for delivery promises and carrier estimates. Validation normalises instants to UTC, rejects timezone-free values, and rejects reversed or future source sequences and a policy that was not yet effective.

## Reproduce and inspect

From the repository root:

```powershell
python scripts/generate_stage2_cases.py --verify
python -m unittest tests.test_stage2_case_system tests.test_stage2_current_state -v
```

The verification command regenerates all six development artifacts in an isolated temporary directory and requires every path and byte to match the committed set. `manifest.json` covers every other artifact with SHA-256 and intentionally excludes itself.

The canonical files are:

- `data/stage2/development/cases.jsonl` — runtime-safe source batches;
- `data/stage2/development/evaluator-projections.jsonl` — disclosed development-only scenario expectations;
- `data/stage2/development/current-state-results.jsonl` — case-level Q1-Q8 replay;
- `data/stage2/development/current-state-summary.json` — conserved denominator and totals;
- `data/stage2/development/current-state-assumptions.json` — hypothetical time inputs;
- `data/stage2/development/manifest.json` — identity, claim boundary, balance, and hashes.

## Limitations

- The generator and development expectations share creator authorship. Agreement is regression evidence, not independent model accuracy.
- The queue topology is research-grounded and intentionally realistic enough to test the redesign, but it is not an observed company workflow.
- No person reviewed cases, operated queues, exercised authority, received a remedy, or assessed communication.
- No customer delivery, satisfaction, retention, savings, adoption, production reliability, or enterprise impact is established.
- Stage 1 discovery and invalidated held-out artifacts remain separate historical evidence and are not modified or promoted by Stage 2.
