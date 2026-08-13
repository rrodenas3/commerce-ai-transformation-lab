---
title: Stage 2 Evaluation and Adaptation
evidence_class: synthetic development evidence and preregistered evaluation protocol
evidence_status: synthetic-observed
public_safe: true
maturity: foundation
claim_boundary: creator-evaluated synthetic development and confirmatory design only; no human, customer, pilot, production, adoption, independent-validation, or realised-value claim
limitations: the canonical 36-case run remains ineligible until separately recorded AI attempts, a clean source binding, real container attestation, sealed outputs, and oracle release all pass
---

# Stage 2 evaluation and adaptation

Stage 2 tests whether the redesigned recovery workflow can make bounded, evidence-linked decisions and preserve control under failure. It does not test customer satisfaction, human adoption, realised value, or production reliability.

## The development failure that changed the design

A 24-case development inventory was frozen before the U6 fault run. The deterministic ranking placed the absent outer evaluation boundary first because no lower-level policy, workflow, or scoring control can compensate if evaluated code can see the oracle or declare its own isolation.

The preserved base commit `9fb6665` had no `scripts/stage2_evaluation_release.py`. The executed Git-object probe therefore recorded `OUTER_RELEASE_BOUNDARY_ABSENT`, not a passing result. The adaptation introduced:

- an outer Docker launcher that alone can make a run canonical;
- a separate outer attestation path never mounted into evaluated code;
- a nonce-bound private oracle commitment;
- atomically installed file-per-state release records from `running` to `scored`, with terminal pre-oracle invalidation and stale-writer recovery;
- byte-length, digest, type, input, source, and final-ledger-head pins;
- a scorer that replays frozen U5 workspaces and Q1-Q8 comparator bytes after oracle release, ignoring runtime-authored pass claims;
- regression-only labelling for any same-pack change after release.

The linked regression executes the current attestation validator against a self-authored evaluated-process claim and confirms rejection. The original failure trace remains unchanged in `data/stage2/development/fault-results.jsonl`; the adaptation record links that trace, the changed source files, the rejected alternative, the regression, and the remaining limitation.

## Frozen comparison design

The confirmatory denominator is 36 newly identified synthetic cases: three cases in each of the 12 families preregistered in the Stage 2 contract. Stage 1 V1/V2, persona-practice, and 24 development identities are rejected. Public runtime inputs contain no case-family mapping, evaluator answer, oracle field, or scoring label.

Both variants receive byte-identical cases and event schedules:

- the comparator records the documented structural current-state path;
- the assisted path consumes a separately acquired, complete recorded-attempt ledger, while policy, authority, action, verification, communication, evaluation, and publication stay deterministic;
- refusal, rejection, timeout, malformed, unavailable, fallback, and success statuses remain in the preregistered denominator.

The generator cannot create canonical recorded AI candidates. It prepares an oracle-free acquisition bundle and validates bytes produced by a fresh restricted acquisition step. A generated all-unavailable fixture is allowed only in unit tests and is permanently marked `test-fixture-never-canonical`.

The first isolated V1 acquisition is preserved as a development failure, not repaired evidence. It recorded 36 terminal attempts (34 `SUCCESS`, one `REFUSAL`, and one `TIMEOUT`), but all 34 candidate-bearing attempts failed the real provider parser because the brief did not disclose the exact machine vocabulary and schema. No candidate was translated or curated. Only aggregate counts and the private failed-ledger digest are public in `data/stage2/development/acquisition-v1-failure.json`; candidate content remains withheld. V2 uses a new acquisition identity and directory and discloses the exact candidate/attempt fields, action, route and message-fact enumerations, canonical JSON rule, permitted `S2-SRC-` citation constraint, and the real parser's byte, depth, cardinality, string, control-text, bidirectional-text, and instruction-pattern restrictions without adding oracle or family metadata. Every V2 attempt also carries the exact acquisition ID and canonical acquisition-contract digest under the V2 attempt schema, preventing a syntactically valid record from being replayed from another brief.

The first Stage 2 confirmatory pack identity, `S2-EVALUATION-20260811-V1`, was invalidated before any container run because its runtime-mounted public manifest disclosed the named coverage taxonomy. Its public manifest hash and the exact absence of a run, oracle release, and score are preserved in `data/stage2/development/evaluation-v1-pre-run-invalidation.json`. Its private oracle and nonce remained unopened.

The successor `S2-EVALUATION-20260812-V2` corrected that disclosure but failed closed during release preparation, still at `not-started`: its minimal runtime pin had been derived from mutable checkout bytes, so clean LF and CRLF representations of the same commit and tree could not reproduce it. No container ran, no release state advanced, no oracle or nonce was opened, and no score exists. The public governance record is `data/stage2/development/evaluation-v2-pre-run-invalidation.json`.

`S2-EVALUATION-20260812-V3` then failed closed at the same pre-run boundary for a different reason. The release controller acquired its persistent run lock and verified runtime pins against the now-dirty module checkout instead of the explicit clean source checkout. State remained `not-started`; only the lock marker existed, no container ran, no state advanced, no oracle or nonce was opened, and no score exists. This is preserved in `data/stage2/development/evaluation-v3-pre-run-invalidation.json`. The controller now safely reads the public manifest only to identify canonical status, requires an explicit source root, and performs pack, Git-export, and runtime-pin verification against that clean source.

`S2-EVALUATION-20260812-V4` passed canonical preparation and advanced to `running`, but Docker Desktop returned the exact applied seccomp profile in `docker inspect` as inline JSON rather than the launch-time Windows path. The path-only inspect validator failed closed before container start, output materialization, attestation, or evaluated execution. No oracle or nonce was opened and no score exists. The public governance record is `data/stage2/development/evaluation-v4-pre-run-invalidation.json`. Subsequent pre-run review also found that the launcher had treated mutable module-checkout seccomp bytes as authority. V5 therefore included the committed profile blob in the minimal runtime inventory, pinned its digest explicitly, and bound that digest through image labels, the image receipt, release preparation, held outer materialization, inspect validation, and attestation. The outer launcher holds and identity-checks its exclusive materialized profile before and after `docker create`; container inspection must return inline applied-profile JSON that strictly parses to the same frozen object. Exact-path echo is rejected even when the intended-source bytes are correct, because a path does not prove the profile Docker applied. Altered, swapped, malformed, duplicate-key, additional, and unconfined profiles fail. Every release continuation also revalidates the current pack ID and schema across the state chain, preparation, manifest, pins, and output seal before mutation, so an archived running chain cannot advance under newer code.

`S2-EVALUATION-20260812-V5` passed canonical preparation, advanced to `running`, created the container, and passed the inline seccomp proof. The next pre-start mount-identity check failed closed because `subprocess.run(text=True)` used the Windows default codec for Docker's UTF-8 JSON: the real `Imágenes` path became `ImÃ¡genes`. The container never started, no evaluated execution occurred, and no output, attestation, oracle release, or score exists. This is preserved in `data/stage2/development/evaluation-v5-pre-run-invalidation.json`. V6 pins UTF-8 with strict error handling for every Docker command whose textual output supplies JSON, image IDs, container IDs, or build output; binary framed evaluation output remains byte-preserving. The current identity is `S2-EVALUATION-20260812-V6`; V1 through V5 identities and schemas independently fail the current verifier, release controller, and continuation gate.

## Canonical release sequence

1. Commit the reviewed U6 source and development fault evidence.
2. Prepare the ignored oracle-free V2 acquisition bundle; preserve V1 unchanged.
3. Acquire and preserve all 36 recorded attempt outcomes without oracle access.
4. From a clean Git tree, freeze cases, schedules, attempts, policy/workflow/adapter/provider/clock/threshold pins, source commit/tree/export digest, and the nonce-bound oracle commitment.
5. Load the Dockerfile, seccomp profile, and allow-listed runtime modules as exact committed `HEAD` blobs, materialize those bytes into the minimal Docker context, and preserve the immutable-ID build receipt with the explicit seccomp digest. Checkout line endings never enter the pin.
6. Prepare the release against that receipt and the explicit clean source checkout—not the lock-owning module checkout—then resolve the image before creation and create the container by immutable ID, never by mutable tag. Decode Docker's textual JSON, identifiers, and build output explicitly as strict UTF-8; retain the framed evaluation bundle as bytes.
7. Run the actual Q1-Q8 comparator and the real U5 guarded assisted workflow inside the outer-enforced container.
8. Freeze output bytes and final ledger heads atomically.
9. Validate the launcher-written capability attestation, exact image ID/build labels, held seccomp intended-source identity through container creation, inline Docker Desktop applied-profile JSON semantically identical to the frozen committed bytes, the frozen seccomp digest, exact read-only input identity, isolated workspace, and outer-materialized output inventory. The attested materialized path records intended-source provenance; it is not proof of the applied profile.
10. Release the oracle and nonce, score the preserved bytes, and retain every negative, zero, pending, failed, escalated, and excluded result.

The container runs as `65532:65532`, with network disabled, all capabilities dropped, `no-new-privileges`, a read-only root filesystem, PID/memory/CPU/file limits, one exact read-only host input bind, one isolated read/write `tmpfs` workspace, and the exact pinned seccomp profile denying fork/clone/socket routes. There is no host output mount: the inner process emits a bounded, digest-framed allow-listed bundle on stdout, and the outer launcher materializes it with exclusive-create writes after container exit. The image attestation binds the pinned base digest, pack-pinned minimal build-context digest, source commit/tree, pre-create immutable image ID, verified labels, and build-receipt digest. Negative probes cover absolute, parent, home, Git-object, socket, and subprocess access. This is creator-built isolation evidence, not an independent security assessment.

## Layered metrics and stop precedence

The scorer reports sealed recorded-recommendation agreement with the released oracle action, route, and governed outcome; safe routing; approval; execution; authoritative verification; supported communication; closure; recovery; raw workflow-event counts; comparator Q1-Q8 structural work; and provider cost/latency availability separately. Agreement with deterministic runtime policy alone cannot count as oracle correctness. Every eligibility denominator comes from the frozen schedule or released oracle; raw workspaces supply numerators only. Safe escalation never enters the verified-remedy numerator.

The mutually exclusive outcome buckets must sum to all 36 cases. An unauthorised action, duplicate action, false verification, personal/secret disclosure, oracle contamination, or evidence-chain tamper forces `stop` before any aggregate can pass. Missing evidence or pre-run exposure cannot be relabelled as a quality result.

## Current truthful status

The development fault inventory, failure trace, adaptation, release code, container policy, and focused tests exist. The confirmatory result is not yet claimed. Until the separately recorded attempts, clean source commit, real Docker attestation, output seal, and oracle release complete, the repository remains at `foundation` maturity.

Focused verification:

```powershell
python -m unittest tests.test_stage2_evaluation tests.test_stage2_evaluation_release
python scripts/generate_stage2_evaluation.py --prepare-acquisition
python scripts/generate_stage2_evaluation.py --verify
python scripts/stage2_evaluation_release.py verify-materialized --pack-root data/stage2/evaluation/v6 --private-root artifacts/private/stage2-evaluation/v6 --run-root data/stage2/runs/<run-id>
python scripts/evaluate_recovery_workflow.py --verify --run-root data/stage2/runs/<run-id>
```

The generator's `--verify` command is deliberately public-only: it verifies the frozen pack without opening the private oracle or nonce. The release controller first validates those private files as sole regular files and opens their commitment only after `eligibility-verified`, during the irreversible `release-oracle` transition.
