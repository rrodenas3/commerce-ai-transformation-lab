---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: fictional operating assumptions and decision rights that have not been exercised by a real organisation or operator
---

# Stage 1 operating model

## SCC-01 — Synthetic Home & Living Retailer

SCC-01 is a deliberately clinical identifier for a fictional direct-to-consumer retailer. It does not represent, resemble, or imply endorsement by any real company.

The laboratory boundary is intentionally small:

- one domestic European market, EUR, and one business-hours calendar;
- one online channel, one warehouse, and one parcel carrier;
- non-perishable, non-regulated home and lifestyle goods;
- one customer-recovery team, one fulfilment-operations contact, and one finance approver;
- orders with one to five lines and no more than two parcels;
- generated orders, customers, events, policies, actions, and timestamps only.

Marketplaces, stores, B2B, cross-border customs, pre-orders, subscriptions, perishables, hazardous goods, personalised products, chargebacks, fraud investigation, and returns after successful delivery are excluded. Safety, privacy, fraud, legal, and active-chargeback signals appear only as control-stop cases.

## Business problem to test

> Delayed and partial-fulfilment recovery may be constrained less by message drafting than by fragmented evidence, divided authority, queue waiting, repeated investigation, manual rekeying, and closure without proving that the intended recovery occurred exactly once.

This is a testable hypothesis, not an observed diagnosis. The manual baseline must be able to disprove it.

## Systems of record

| Synthetic system | Authoritative for | Not authoritative for |
| --- | --- | --- |
| OMS | Ordered quantity, paid line value, promise version, cancellation and replacement relationship | Physical pick, pack, delivery, or refund settlement |
| WMS | Picked, packed, shorted, and shipped quantity; warehouse exception | Customer promise, delivery, or financial settlement |
| Carrier source | Parcel scans, estimated arrival, and delivery event | Which order lines were physically inside a parcel |
| Inventory service | Time-stamped reservable replacement stock | Historical shipment or delivery |
| Payment ledger | Capture, refund, prior adjustment, and transaction status | Physical fulfilment |
| CRM | Case owner, contacts, customer choice, message, and closure reason | Order, warehouse, carrier, or payment truth |
| Policy register | Recovery rules, authority limits, version, and effective time | Whether an action occurred |

No one source establishes a verified recovery. The relevant authoritative records must jointly establish the postcondition.

## Role topology

| Role | Accountable contribution |
| --- | --- |
| Executive outcome owner | Sets priority and risk appetite; decides scale, revise, pause, or stop. |
| Workflow owner / local Activator | Owns the recurring queue, support route, operating review, and feedback loop. |
| Customer-recovery specialist | Investigates the case, exercises delegated judgment, requests approval, performs the simulated manual action, communicates, and closes. |
| Fulfilment-operations coordinator | Resolves WMS/carrier ambiguity and confirms operational feasibility. |
| Finance duty approver | Authorises high-exposure recovery. |
| Policy and risk owner | Owns policy, authority, red-flag treatment, incidents, and canonical changes. |
| Technical owner | Owns future adapters, observability, recovery, and postcondition mechanisms. |
| Independent lab evaluator | Holds the oracle, scores case outputs, and does not coach the reviewer. |
| Transformation leader — Raul Rausell | Defines the outcome and scope, designs the study and operating model, makes the human/AI allocation, coordinates delivery, evaluates evidence, records adaptations, and recommends the next investment decision. |

The roles are simulated. Raul does not impersonate a sponsor, finance approver, risk owner, or organisational adopter.

## Recovery exposure and authority

These thresholds are fictional, versioned design assumptions—not legal entitlements, industry norms, or a former employer's policy. They are frozen for Stage 1 comparison; a future evaluated release requires a separately timestamped preregistration.

| Band | Generated exposure | Authority |
| --- | ---: | --- |
| A0 | €0 informational recovery | Customer-recovery specialist |
| A1 | More than €0 and up to €25 | Customer-recovery specialist, once per order, with complete evidence |
| A2 | €25.01 to €100 | Workflow-owner approval |
| A3 | More than €100, order value above €500, or repeat recovery | Finance approval with workflow-owner visibility |

Exposure is the greater of the cash adjustment and affected quantity's generated net-paid value. Boundary cases cover €25.00, €25.01, €100.01, and an order above €500.

Incomplete or contradictory evidence blocks execution regardless of value. Any control-stop flag overrides every financial threshold.

## Allowed recovery decisions

| Decision | Minimum conditions |
| --- | --- |
| Wait with verified revised ETA | Current operational source, customer preference to wait, and communication that an estimate is not a promise. |
| Reship exact missing quantity | Confirmed shortfall, reservable stock, no verified prior recovery for that quantity, and appropriate authority. |
| Refund exact missing quantity | Generated net-paid value, confirmed payment capture, no verified prior recovery or chargeback, and appropriate authority. |
| No new action | Duplicate signal or verified prior recovery; canonical state must be checked. |
| Controlled escalation | Red flag, evidence conflict, stale or missing source, failed/unverified action, policy gap, or exceeded specialist authority. |

Refund and replacement for the same quantity are mutually exclusive in this policy version.

## Compliance and public-data boundary

- The repository contains no real personal data; this is stronger than attempting to anonymise an operational extract.
- Data fields omit names, addresses, email, telephone, payment instruments, free-text customer messages, and real identifiers.
- NIST AI RMF informs governance, scope, measurement, and risk treatment; this is not certification.
- No AI Act or GDPR compliance claim is made. Any future direct natural-person interaction, real data processing, or organisational deployment requires an authorised legal and privacy assessment.
- All future consequential adapters remain read-only or dry-run until a later approved maturity change.

The European Commission states that relevant Article 50 transparency obligations apply from 2 August 2026. The present lab does not interact with real customers; a future customer-facing design must assess disclosure obligations before any introduction.

## Raul's Stage 1 decisions

1. Use one end-to-end value stream instead of several agent demonstrations.
2. Keep the fictional company narrow enough for a falsifiable baseline.
3. Separate authoritative sources instead of inventing a unified current-state view.
4. Freeze policy and authority before any future model recommendation exists.
5. Treat controlled abstention as an acceptable outcome.
6. Keep oracle ownership independent from future model output.
7. Publish the limitations and zero-action result alongside the positive control results.
