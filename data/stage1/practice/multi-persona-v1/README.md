# Stage 1 multi-persona adversarial practice

This directory contains a **fully synthetic, AI-assisted practice dataset** built after V2 was invalidated before human handling. It does not overwrite the blank creator worksheet.

Five simulated operating lenses challenge all 32 cases: Customer Recovery, Fulfilment Operations, Workflow Ownership, Technical Reliability, and Policy and Risk. Each case records a first instinct, an adversarial challenge, the governed final decision, and a first-person transformation-leader response.

## Files

- `personas.csv`: role, objective, privileged signal, decision rights, and predictable failure tendency for each simulated lens.
- `multi-persona-decisions.csv`: all 32 case deliberations, adaptations, controls, trade-offs, and final decisions.
- `manual-records.ai-assisted.csv`: the same final decisions in the 15-column worksheet shape for practice and interface development only.
- `summary.json`: case, persona, action, route, scenario, and adaptation distribution.
- `manifest.json`: provenance, claim boundary, and SHA-256 pins.

## Evidence boundary

The personas are generated lenses, not people or independent reviewers. Timestamps mark batch generation, not observed handling time. Handoffs are proposed routes, not observed transfers. Final decisions were constructed from the frozen policy and checked against the committed private oracle after invalidation; therefore their agreement is **not** model performance.

This dataset can demonstrate policy interpretation, adversarial operating-model design, and failure-to-adaptation reasoning. It cannot demonstrate human performance, blind evaluation, adoption, executed recovery, customer impact, realised economics, production reliability, or independent validation.

Regenerate or verify with:

```bash
python scripts/generate_stage1_multi_persona_practice.py
python scripts/generate_stage1_multi_persona_practice.py --verify-public
```

This public command checks deterministic bytes only; it does not repeat the
private-oracle agreement check. Maintainers with the ignored private oracle use
`--verify` to repeat both checks. Canonical publication without that oracle is
not supported.
