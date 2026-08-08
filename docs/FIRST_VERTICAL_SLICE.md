---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: workflow design only; thresholds, adapters, and evaluated behavior do not yet exist
---

# First vertical slice

## Scope decision

The first coherent value stream is **delayed or partial fulfilment to verified customer recovery**. This is narrow enough to evaluate and broad enough to expose cross-functional coordination, human judgment, and system consequences.

## Trigger

A synthetic order receives a reliable carrier, warehouse, or order-management signal indicating that the original fulfilment promise is unlikely to be met or has been only partly met.

## Workflow

| Step | Intended responsibility | Control question |
| --- | --- | --- |
| Detect exception | Deterministic rule or event adapter | Is the signal reliable, duplicated, or stale? |
| Assemble context | AI-assisted retrieval with cited sources | Is required evidence present and current? |
| Assess cause and uncertainty | AI proposes; human can correct | Does the evidence support the classification? |
| Generate recovery options | AI proposes within policy | Are options feasible, allowed, and complete? |
| Select resolution | Human for consequential cases; bounded policy for low-consequence cases | Who has authority, and what requires escalation? |
| Prepare action | Deterministic adapter creates an exact dry-run payload | Does the payload match the approved decision? |
| Execute simulated action | Bounded adapter after confirmation | Is the action idempotent and revocable where required? |
| Verify postcondition | Independent verifier | Did the intended state change exactly once? |
| Draft communication | AI drafts from verified facts | Does the message avoid unsupported promises? |
| Create learning candidate | System records correction or failure | Who may approve a change to policy, knowledge, or evaluation? |

## Consequential decisions retained by people

- Refunds, credits, replacements, or concessions outside delegated policy.
- Irreversible or customer-visible actions when evidence is incomplete.
- Exceptions involving suspected fraud, safety, legal, or privacy concerns.
- Any recommendation below the preregistered confidence or evidence threshold.
- Changes to policy, authority, evaluation criteria, or canonical knowledge.

Threshold values will be defined in the policy specification before implementation and tested at their boundaries. They are not invented in this foundation release.

## Evaluation variants

- Delayed fulfilment with reliable evidence.
- Partial shipment with replacement stock available.
- Partial shipment without replacement stock.
- Conflicting warehouse and carrier events.
- Duplicate signal or retried action.
- Missing policy or customer context.
- High-value recovery requiring escalation.
- Communication request containing an unsupported promise.
- Adapter failure after approval but before verification.
- Out-of-scope safety, fraud, privacy, or legal concern.

## Explicit exclusions

- Demand forecasting and replenishment optimisation.
- Open-ended customer-service chatbot.
- Real payment execution.
- Multi-agent orchestration as an objective.
- Automatic learning from model-generated content.
