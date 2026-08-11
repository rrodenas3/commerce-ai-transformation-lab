# Data boundary

Only fictional, generated data may be committed here.

Every future dataset must include:

- generator version and deterministic seed;
- schema and field descriptions;
- case-family distribution;
- provenance for any public statistical inspiration;
- separation between generator logic, development cases, and held-out cases;
- limitations and realism gaps;
- confirmation that no real person, company, order, payment, address, message, or identifier is present.

Large, private, real, scraped, or ambiguous datasets must not be committed.

## Stage 1 public discovery set

`data/stage1/` contains the fictional SCC-01 recovery policy and generated public foundation artifacts:

- 24 structured discovery cases;
- a frozen, co-designed oracle independent of future model output but not independent validation;
- transparent deterministic recommendations and a calibration summary;
- an empty manual baseline worksheet;
- a deterministic manual-run manifest template that pins the cases, oracle, policy, protocol, source manifest, versions, assignment order, tool policy, and truthful oracle-exposure status;
- a preparation command that creates a new case-only run directory with immutable provenance and no copied answer files.

The cases are not a held-out evaluation set and do not reproduce a real retailer's incident distribution.

Regenerate and verify them with:

```bash
python scripts/generate_stage1_cases.py
python scripts/stage1_deterministic_baseline.py
python -m unittest tests.test_stage1_case_system -v
```

Prepared manual runs live under `data/stage1/runs/<run-id>/`. A blank prepared pack remains an instrument, not a human result. The first public creator pack was exposed to answer-bearing oracle content during guided practice and is therefore familiarisation material, not a clean baseline. [D-015](../docs/DECISION_LOG.md#d-015--reclassify-the-exposed-creator-pack) records the adaptation.

## Stage 1 held-out evaluation pack

`data/stage1/heldout/v1/` is retained as an invalidated pre-run instrument. Its worksheet stayed blank and its oracle was never released, but its permitted files omitted route and message-fact vocabulary required by the worksheet. [D-021](../docs/DECISION_LOG.md#d-021--replace-the-incomplete-operator-instrument) records why it cannot produce baseline evidence.

`data/stage1/heldout/v2/` contains the replacement 32-case operator pack, a complete answer-free operator guide, and public hash commitments. Operator cases expose incident facts but omit evaluator-only titles, family labels, and generator metadata. V2 was subsequently invalidated before human handling when AI-assisted case exposure was requested. Its worksheet remains blank and its oracle remains unreleased; [D-022](../docs/DECISION_LOG.md#d-022--reclassify-v2-as-ai-assisted-simulation-material) records the transition.

The held-out workflow is implemented as a fail-closed evidence-state transition:

1. generate public cases and private oracle;
2. prepare and commit a case-only blank run;
3. complete and commit the human record outside an AI session;
4. release the oracle only after Git verifies that record freeze;
5. score only the byte-identical frozen record.

The [held-out protocol](../docs/STAGE1_HELDOUT_EVALUATION_PROTOCOL.md) is retained as the exact historical control contract. Do not complete V2 as a clean human result. A future blind human evaluation requires a new pack identity and private oracle. No held-out human result exists yet.

## Stage 1 AI-assisted multi-persona practice

`data/stage1/practice/multi-persona-v1/` contains five simulated operating lenses and a complete 32-case adversarial decision set. The original V2 worksheet is not overwritten. The practice CSVs expose first instincts, challenges, adaptations, governed final decisions, controls, and customer/economic trade-offs.

The dataset is AI-assisted synthetic practice, not a human result. Persona lenses are not independent reviewers; zero-second timestamps are not handling measurements; simulated routes are not observed handoffs; and oracle-checked construction is not model accuracy. See the [practice README](stage1/practice/multi-persona-v1/README.md) for the exact boundary.

The original public-discovery preparation command and scorer remain bound to the exposed discovery pack. Do not complete or score that pack as baseline evidence.
