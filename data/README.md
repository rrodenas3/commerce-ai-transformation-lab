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

Held-out preparation and scoring are **not implemented yet**. The current preparation command, scorer, and frozen protocol are bound to the public discovery artifacts and the exposed creator pack. Do not complete or score that pack as baseline evidence.

The next implementation unit will add a separately seeded case pack, a temporarily sealed oracle, explicit exposure states, and a release sequence that freezes the source record before scoring. Until that unit is verified, the commands above reproduce public contract-calibration evidence only; they do not create a valid held-out run.
