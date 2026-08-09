---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: a creator-run pack can be frozen and scored, but no completed human case run or independent reviewer evidence has been recorded
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

1. Run the preparation command once for a new run ID and output directory.
2. Confirm the frozen manifest has `status: prepared` and the intended pseudonymous reviewer code, operator role, UTC preparation time, and run type.
3. Confirm its SHA-256 pins for the cases, oracle, policy, source artifact manifest, protocol, case-only pack, policy copy, and blank records template.
4. Confirm the policy and oracle versions and the complete assigned case IDs and order.
5. Confirm the allowed and prohibited tool policy.
6. Commit and push the complete blank run directory to the public repository before case handling starts. Record the full preparation commit SHA and public commit URL. Do not amend, rebase, or otherwise rewrite that commit after handling begins.
7. Do not edit `run-manifest.json` after preparation. A correction requires a new run ID and directory.

Do not change eligibility, policy, thresholds, or the oracle after observing results. Any correction starts a new version and evaluation cycle.

Prepare a creator-run pack with:

```bash
python scripts/prepare_stage1_manual_run.py \
  --output data/stage1/runs/scc-01-creator-manual-001 \
  --run-id scc-01-creator-manual-001 \
  --reviewer-code CREATOR-01 \
  --operator-role creator
```

Preparation records the current UTC time automatically and refuses unsafe identifiers or an existing output directory. The resulting folder contains only `case-pack.jsonl`, `policy.json`, `manual-records.csv`, and `run-manifest.json`. It does not copy the oracle or deterministic decisions into the working pack.

Before starting the timer, commit and publish that blank pack, then record its content-addressed Git anchor:

```bash
git add data/stage1/runs/scc-01-creator-manual-001
git commit -m "evidence: freeze creator manual baseline pack"
git push origin HEAD
PREPARATION_REF=$(git rev-parse HEAD)
```

The commit must contain both `run-manifest.json` and the still-blank `manual-records.csv`. Confirm that the full SHA opens on the public GitHub repository before handling, and keep that commit URL outside the measured worksheet. The commit makes the exact bytes tamper-evident; it does not independently prove who performed the work or whether prohibited tools were avoided. The scorer also rejects a recorded commit timestamp later than the earliest handling start, but that timestamp is supporting metadata rather than a trusted third-party clock.

## Operator instructions

Complete the measured run outside a generative-AI session. Use only the files and tools allowed by the frozen manifest. Do not open the repository oracle or deterministic decisions while handling cases. Because the oracle remains publicly available elsewhere in this repository, the run is still declared `public-oracle-available` and must not be described as blinded.

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

Timestamps must be timezone-aware UTC instants. The end must not precede the start, and active handling time cannot exceed elapsed wall-clock time. Save `manual-records.csv` as UTF-8 without a byte-order mark, with LF-only line endings and a final LF; the scorer rejects other byte representations so the evidence digest is unambiguous.

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

Stage 1 manual evidence remains incomplete until at least one recorded creator-run baseline exists. A prepared but blank run is an evidence instrument, not a human observation. Independent-human evidence remains absent until a consented reviewer completes cases without creator coaching.

Running `scripts/stage1_deterministic_baseline.py` regenerates the source template. Use `scripts/prepare_stage1_manual_run.py` to create the actual frozen pack. After personally completing every CSV row, score the bound files without modifying the source record or frozen manifest:

```bash
python scripts/score_stage1_manual.py \
  --input data/stage1/runs/scc-01-creator-manual-001/manual-records.csv \
  --output data/stage1/runs/scc-01-creator-manual-001/manual-summary.json \
  --cases data/stage1/runs/scc-01-creator-manual-001/case-pack.jsonl \
  --oracle data/stage1/generated/oracle.jsonl \
  --run-manifest data/stage1/runs/scc-01-creator-manual-001/run-manifest.json \
  --preparation-ref "$PREPARATION_REF"
```

The scorer requires the exact prepared case, policy, records, and manifest paths. It resolves `--preparation-ref` to a Git commit, requires the committed manifest bytes to equal the current frozen manifest, verifies the committed blank worksheet against that manifest, and rejects a commit timestamp later than the earliest handling start. It reads each current input into one immutable byte snapshot, verifies hashes and canonical CSV encoding against those snapshots, checks the protocol pin, policy/oracle versions, reviewer identity, role, run type, complete denominator, and preparation-before-handling order, and binds final records, manifest, and preparation-commit provenance into the summary. It also rejects any output path that aliases an input artifact.
