---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: external sources justify design coverage but do not establish this laboratory's baseline or expected impact
---

# Stage 1 research notes

## How research is used

Sources shape the workflow, case taxonomy, controls, and evidence plan. No source result is copied into SCC-01 as a baseline, target, adoption claim, or benefit forecast.

## Operating-model and enablement evidence

| Source | Design signal | Boundary |
| --- | --- | --- |
| [McKinsey — operating-model advantage, 7 July 2026](https://www.mckinsey.com/industries/industrials/our-insights/the-operating-model-advantage-why-ai-winners-are-rewiring-their-organizations) | Redesign decisions and end-to-end coordination before selecting tools; concentrate on a small number of high-value domains. | Consulting research; associations and reported company outcomes are not SCC-01 forecasts. |
| [OpenAI Academy — Agent Activator, updated 17 July 2026](https://academy.openai.com/en/public/clubs/champions-ecqup/resources/getting-started-as-an-ai-activator-2026-06-08) | Start with today's workflow, handoffs, exceptions, human decisions, access, reliability, support, maintenance, and outcomes. | First-party operating guidance; no adoption is demonstrated here. |
| [NIST AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/) | Govern, Map, Measure, and Manage; define human oversight, test sets, independent review, feedback, and proceed/stop decisions. | Voluntary framework, not certification. |

## Commerce and systems evidence

| Source | Design signal | Cases or controls added |
| --- | --- | --- |
| [SAP Order Management Assistant, 11 May 2026](https://www.sap.com/use-cases/joule-assistant/order-management-ai) | Order issue and fulfilment-gap identification, guided resolution, sourcing context, and end-to-end coordination are a real product direction. | Supports the domain choice, not performance claims. |
| [Oracle Cloud Order Management 26B — partial shipment](https://docs.oracle.com/en/cloud/saas/supply-chain-and-manufacturing/26b/fauom/specify-shipping-details-for-sales-orders.html) | Partial delivery depends on customer preference and may produce multiple arrival dates and extra cost. | Planned split, partial-allowed, customer preference, and remaining-quantity cases. |
| [Oracle Cloud Order Management 25D — shipment-notice recovery](https://docs.oracle.com/en/cloud/saas/supply-chain-and-manufacturing/25d/faiom/recover-an-advance-shipment-notice.html) | Asynchronous events can arrive rapidly, be rejected during order revision, or leave systems unsynchronised; recovery and retry are separate work. | Duplicate, stale, conflicting, pending, failed-safe, and unverified-action cases. |
| [Shopify order-management fulfilment model](https://shopify.dev/docs/apps/build/orders-fulfillment/order-management-apps) | Order, line, fulfilment order, quantity, and location are distinct records; partial fulfilment is not one simple status. | Structured quantity reconciliation and separate systems of record. |
| [Stripe webhook guidance](https://docs.stripe.com/webhooks) | Asynchronous events require source verification and retrieval of canonical current state. | Event identity, freshness, duplicate handling, and canonical verification requirements. |
| [Stripe refund guidance](https://docs.stripe.com/refunds) | A refund may be requested, pending, updated, or failed before settlement. | Action requested is not outcome achieved; postcondition required before closure. |

## External context—not a baseline

Ofcom's [2025 UK parcel-delivery study](https://www.ofcom.org.uk/siteassets/resources/documents/postal-services/monitoring-reports/2024-25/measuring-user-experience-of-parcel-delivery-to-residential-addresses-2025.pdf?v=406643) surveyed 4,058 UK residents in two 2025 waves. It reported delay as the most common named issue and weak satisfaction with contact and resolution. This supports investigating proactive evidence and recovery, but the sample, market, providers, and measurement are external. None of its percentages are used as SCC-01 prevalence, a target, or expected benefit.

## Privacy, transparency, and AI risk

- The [European Commission's Article 50 guidance](https://digital-strategy.ec.europa.eu/en/library/guidelines-transparency-obligations-providers-and-deployers-ai-systems), published 20 July 2026, states that relevant transparency obligations apply from 2 August 2026. Future direct customer interaction requires legal assessment and appropriate disclosure; this synthetic lab claims neither applicability nor compliance.
- The [NIST Generative AI Profile](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence) and [OWASP GenAI risk register](https://genai.owasp.org/llm-top-10/) inform future tests for confabulation, prompt injection, sensitive-data disclosure, improper output handling, and excessive agency.
- The public laboratory avoids real personal data altogether. A real pilot would need approved purpose, access, retention, security, privacy, and legal controls.

## Design conclusion

The value stream deserves investigation because it combines fragmented evidence, customer judgment, policy, financial consequence, asynchronous execution, and measurable closure. The evidence supports building a transformation experiment—not predicting that AI will improve it.

