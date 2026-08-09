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
- a policy-derived oracle independent of future model output;
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

Prepared manual runs live under `data/stage1/runs/<run-id>/`. A blank prepared pack remains an instrument, not a human result. Commit and push the complete blank pack before handling starts; retain its public commit URL and full SHA as the required scoring `--preparation-ref`. Completed records must use the frozen pseudonymous reviewer code, canonical UTF-8/LF CSV bytes, and no personal data. Score only the exact prepared files with `scripts/score_stage1_manual.py`; never overwrite the generated source template or frozen manifest.
