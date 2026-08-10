---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: process-controlled synthetic evaluation; Git ordering and hash commitments do not independently prove the operator avoided prior knowledge or prohibited tools
---

# Stage 1 held-out human evaluation protocol

## Decision this protocol supports

Determine whether a person can diagnose and safely route the complete SCC-01 case pack without generative AI, answer files, creator coaching, or access to the public repository during handling. This is a workflow baseline, not a test of customer outcomes.

## Evidence boundary

- Every case, policy, identifier, and system record is fictional and generated.
- The operator case pack excludes evaluator-only titles, case-family labels, and generator metadata; the operator must work from incident facts and policy.
- The creator run is **not independent** because Raul owns the project and participated in the evaluation design.
- An independent run becomes human-reviewed evidence only when a consented reviewer completes a separately prepared pack without creator coaching.
- Answer-file withholding is a procedural and hash-bound control, not cryptographic proof of ignorance. The public code can derive policy answers from case facts.
- Git commits make exact bytes and ordering inspectable; their timestamps are supporting metadata, not trusted third-party time attestations.
- Results cannot establish adoption, realised savings, customer recovery, pilot status, production reliability, or business value.

## State machine

| State | Public artifacts | Private artifacts | Permitted next action |
| --- | --- | --- | --- |
| `answer-file-not-published` | Case pack, public manifest, seed and oracle hash commitments | Generation material and oracle file | Prepare one case-only run |
| `oracle-file-withheld-at-preparation` | Frozen run manifest, policy copy, case pack, blank worksheet | Oracle file remains private | Commit the blank instrument, then handle cases |
| `completed-records-frozen` | Completed worksheet committed without oracle | Oracle remains private | Verify Git ordering and release the oracle |
| `oracle-file-released-after-record-freeze` | Oracle, release manifest, released generator seed | No answer file needs to remain private | Score only the frozen worksheet |

No command may skip a state. If the operator sees the private oracle, oracle-generating code output, or a completed answer set before the records commit, the pack is contaminated. Stop and preserve the failure. This V2 toolchain does not auto-create V3: recovery requires a reviewed code change that increments the pack identity and paths before generating a new instrument.

## 1. Verify the committed pack

```bash
python scripts/generate_stage1_heldout.py --verify-public
python -m unittest tests.test_stage1_heldout -v
python scripts/verify_public_safety.py
```

The pack-author generation step is already complete. A fresh checkout cannot reproduce the withheld oracle before record freeze because it intentionally lacks the ignored generation material. `--verify-public` checks the committed cases, policy, and hash commitments without creating or opening private material. Do not run the generation mode or open `artifacts/private/` during handling.

## 2. Use the already prepared creator instrument

The creator instrument is already prepared at `data/stage1/heldout/runs/scc-01-heldout-v2-creator-001/`. Do not rerun preparation for that path: the command correctly refuses to overwrite it. The run directory contains only:

- `case-pack.jsonl`;
- `operator-guide.json`;
- `policy.json`;
- `manual-records.csv`;
- `run-manifest.json`.

Confirm that `manual-records.csv` is blank apart from assigned IDs, reviewer code, and run type. Use a clean commit containing the verified instrument as the preparation reference immediately before handling. The records commit must be its immediate child and change only this worksheet; the release gate rejects any intermediate commit or other tree change.

From the repository root in PowerShell, freeze the exact clean preparation SHA before opening a case:

```powershell
$worksheet = 'data/stage1/heldout/runs/scc-01-heldout-v2-creator-001/manual-records.csv'
if (@(git status --porcelain=v1).Count -ne 0) { throw 'Preparation tree is not clean.' }
$preparationSha = (git rev-parse HEAD).Trim()
if (-not $preparationSha) { throw 'Preparation commit could not be resolved.' }
$preparationShaPath = (git rev-parse --git-path heldout-v2-preparation-sha).Trim()
if (-not $preparationShaPath) { throw 'Git metadata path could not be resolved.' }
Set-Content -LiteralPath $preparationShaPath -Value $preparationSha -NoNewline
```

Keep the SHA file at the path returned by Git; it is local metadata and does not alter the committed tree. Resolving it with `git rev-parse --git-path` works in both a standard checkout and a linked worktree.

`scripts/prepare_stage1_heldout_run.py` remains available for a new reviewer or a future pack version with a new run ID and output path. It must never overwrite this creator instrument.

## 3. Handle the cases outside an AI session

Open only the prepared case pack, prepared policy, prepared operator guide, and worksheet. Allowed tools are a plain-text editor with AI features disabled, a calculator, and the system clock. The guide contains the complete decision priority, authority routing, evidence codes, message-fact codes, every worksheet-field definition, and the required UTF-8/LF serialization; it contains no per-case answers. Do not consult any other repository file, the private evidence directory, generative AI, deterministic decisions, or the discovery oracle.

For every assigned row record:

- timezone-aware UTC start and end;
- active handling seconds no greater than elapsed time;
- one policy action and route;
- evidence codes separated with `|`;
- safe message-fact codes separated with `|`;
- confidence from 1 to 5;
- help, handoff, and policy-lookup counts;
- optional notes without personal data.

Do not fabricate speed. Pauses remain visible in wall-clock time; active handling records focused work. Do not change the assignment, policy, manifest, case order, reviewer code, or run type.

## 4. Freeze the completed source record

Before opening or releasing any answer-bearing artifact:

1. Confirm every assigned row is complete and in the original order.
2. Commit the completed `manual-records.csv` without the oracle or a score.
3. Record the full commit SHA as the records reference.
4. Do not edit the worksheet after this commit. A correction requires a new run and disclosure of the invalidated attempt.

Run this exact PowerShell sequence. It refuses a broad or intermediate commit and proves that the records commit is the immediate child of preparation:

```powershell
$worksheet = 'data/stage1/heldout/runs/scc-01-heldout-v2-creator-001/manual-records.csv'
$status = @(git status --porcelain=v1)
if ($status.Count -ne 1 -or $status[0] -ne " M $worksheet") { throw 'Only the worksheet may be modified.' }
git diff --check -- $worksheet
if ($LASTEXITCODE -ne 0) { throw 'Worksheet diff is not clean.' }
git add -- $worksheet
$staged = @(git diff --cached --name-only)
if ($staged.Count -ne 1 -or $staged[0] -ne $worksheet) { throw 'Only the worksheet may be staged.' }
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged worksheet diff is not clean.' }
git commit -m 'evidence(stage1): freeze creator held-out V2 record'
if ($LASTEXITCODE -ne 0) { throw 'Records commit failed.' }
$recordsSha = (git rev-parse HEAD).Trim()
$preparationShaPath = (git rev-parse --git-path heldout-v2-preparation-sha).Trim()
$preparationSha = (Get-Content -Raw -LiteralPath $preparationShaPath).Trim()
$parentSha = (git rev-parse "$recordsSha^").Trim()
if ($parentSha -ne $preparationSha) { throw 'Records commit is not the immediate child of preparation.' }
if (@(git status --porcelain=v1).Count -ne 0) { throw 'Records tree is not clean.' }
```

## 5. Release the oracle after the record freeze

```powershell
python scripts/release_stage1_heldout_oracle.py `
  --run-manifest data/stage1/heldout/runs/scc-01-heldout-v2-creator-001/run-manifest.json `
  --preparation-ref $preparationSha `
  --records-ref $recordsSha
```

The command fails unless it can prove that:

- both references resolve to commits;
- the preparation commit is an ancestor of the records commit;
- the records commit is the immediate child of the preparation commit, leaving no intermediate history where an answer artifact could appear and disappear;
- the manifest is byte-identical at both commits;
- the preparation commit contains the pinned blank worksheet;
- the records commit contains every completed assigned row;
- handling starts after preparation and ends before the records commit timestamp;
- the private oracle and generator seed reproduce the committed cases and frozen oracle commitment;
- the release occurs after the records commit.

On success it writes `oracle.released.jsonl` and `oracle-release-manifest.json`. The release manifest records the exact state transition, both Git anchors, hashes, release time, and the now-public deterministic seed.

## 6. Score only the frozen record

```powershell
python scripts/score_stage1_heldout_manual.py `
  --input data/stage1/heldout/runs/scc-01-heldout-v2-creator-001/manual-records.csv `
  --output data/stage1/heldout/runs/scc-01-heldout-v2-creator-001/manual-summary.json `
  --cases data/stage1/heldout/runs/scc-01-heldout-v2-creator-001/case-pack.jsonl `
  --oracle data/stage1/heldout/v2/oracle.released.jsonl `
  --run-manifest data/stage1/heldout/runs/scc-01-heldout-v2-creator-001/run-manifest.json `
  --release-manifest data/stage1/heldout/v2/oracle-release-manifest.json
```

The scorer rejects substituted paths, modified inputs, a stale release manifest, a worksheet that differs from the records commit, an oracle that differs from its pre-run commitment, missing assignments, unsafe CSV encoding, or incompatible policy and protocol versions.

## 7. Interpret the result

Report the complete denominator, active handling distribution, handoffs, policy lookups, help requests, action and routing quality, abstention, unsupported facts, and named critical-control violations. Show failures and adaptations. A creator result is `synthetic-observed`; a consented independent reviewer result is `human-reviewed`. Neither is organisational adoption.
