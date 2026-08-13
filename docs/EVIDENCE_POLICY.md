---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: policy controls reduce accidental disclosure but do not replace human review or authorise a higher maturity
---

# Public evidence and claim policy

## Purpose

This policy keeps a public transformation portfolio useful without converting research, synthetic execution, or reviewer feedback into unsupported business claims.

## Evidence classes

| Class | Meaning | May establish | Must not imply |
| --- | --- | --- | --- |
| Research-grounded | A public source supports the workflow pattern or proposition. | Relevance, precedent, and an informed hypothesis. | That this project reproduced the source's results. |
| Synthetic-observed | A disclosed generated case was executed and measured. | Behaviour, control performance, latency, cost, or evaluation results within that test boundary. | Real customers, adoption, enterprise scale, or realised value. |
| Human-reviewed | A documented person reviewed or used a synthetic case. | Observed comprehension, corrections, friction, review time, or feedback. | Workforce adoption, organisational change, or production use. |
| Hypothetical impact | A scenario uses explicit assumptions. | Sensitivity analysis and an investment hypothesis. | Forecast certainty, booked value, or realised financial impact. |

## Creator-evaluated and simulated-role boundary

A creator-evaluated result means Raul accepted the declared synthetic evidence boundary and evaluation result. It is not independent review. Simulated-role records—including recommendations, approvals, handoffs, actions, verification records, and persona decisions—are generated workflow evidence and are **not human-reviewed evidence**. They may support the `synthetic-observed` class only after the disclosed workflow executes and the result is measured.

When no documented person has performed a review or use session, manual-review time, trust, adoption, enablement friction, and human performance remain `not observed`. They are not recorded as zero and are not inferred from synthetic actors. The same rule applies to customer satisfaction, retained revenue, and realised savings.

The creator-evaluated Stage 2 track can reach at most `local-mvp` after all implementation, evaluation, integrity, and publication gates pass. It cannot reach independently reviewed, pilot, production, adoption, customer-impact, or realised-value status without the corresponding authorised external evidence. A verified refund or reship is an operational synthetic postcondition; a safe route, adapter receipt, estimate, replacement milestone, or local message artifact does not establish customer delivery, satisfaction, or realised value.

## Maturity ladder

1. Research-grounded concept.
2. Specification and test design.
3. Working local MVP using realistic synthetic cases.
4. Independently reviewed local MVP using realistic synthetic cases.
5. Candidate for an authorised real-user pilot.

This repository cannot advance to a pilot or production label through additional documentation, test count, or visual polish. That requires an authorised organisation, accountable owners, approved data and tools, real intended users, operating support, monitoring, and observed outcomes.

## Required public wording

Every journey artifact must state:

- its evidence class;
- whether it is safe for public review;
- the maturity it supports;
- material limitations or unresolved assumptions.

## Public-safe material

- Fictional organisation and role descriptions.
- Generated datasets with documented generators and limitations.
- Research citations and clearly attributed enterprise precedents.
- Current- and future-state workflow maps.
- Policy, authority, evaluation, and enablement designs.
- Local UI images using synthetic records.
- Reconstructable traces with fictional identifiers.
- Aggregate synthetic metrics.
- Anonymised reviewer feedback with consent.

## Material that stays out

- Real customer, employee, supplier, payment, or former-company records.
- Credentials, private endpoints, local machine paths, or infrastructure secrets.
- Private correspondence, recruiter messages, or interview materials.
- Unverified historical business metrics.
- Reviewer identity without explicit consent.
- Claims that imply endorsement by a cited company or technology provider.

## Publication gate

Before each public update:

1. Run the automated verifier and tests.
2. Inspect the actual diff for sensitive context and unsupported implications.
3. Confirm evidence labels and maturity wording.
4. Confirm that screenshots contain synthetic identifiers only.
5. Record the decision or learning the update contributes.
6. Stop publication when any material fact is uncertain.

Passing automation never replaces human review.
