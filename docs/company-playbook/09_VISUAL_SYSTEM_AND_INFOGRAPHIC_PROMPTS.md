---
evidence_status: research-grounded
public_safe: true
maturity: foundation
limitations: generated people, workplaces, interfaces, and diagrams are illustrative; they do not establish client adoption, operational deployment, compliance, certification, or realised value
source: Raul Rausell supplied V3 production prompt pack, cross-walked to docs/company-playbook/08_FRAMEWORK_CROSSWALK_AND_SOURCES.md
owner: Raul Rausell
version: 2026-08-10-v3
sensitivity: public
permitted_use: public-canonical-source-for-rausellos
review_state: accepted-public-source
replacement_or_expiry: superseded-by-reviewed-source-release
knowledge_type: raul-method
authority_scope: explanatory visual method only; observed organisational evidence requires authorised sources
conflict_policy: surface-and-block-dependent-claims
generated_content_authority: none
visual_evidence_boundary: explanatory-only
regression_trigger: material-change
outcome_evidence: none
research_as_of: 2026-08-10
source_freshness: review-on-import-or-material-change
intended_generator: ChatGPT image generation
---

# AI Transformation Visual System

## Professional ChatGPT image-generation prompt pack, version 3

This document replaces the earlier prompt pack. It is designed specifically for image generation inside ChatGPT and for a disciplined edit-review loop. The goal is not to make every plate look more complex. The goal is to make each plate more authoritative, easier to understand and visually credible enough for a board-quality AI transformation playbook.

The series contains:

- 12 core infographics;
- 4 independent documentary photographs;
- 1 coherent visual language;
- landscape and portrait production instructions;
- exact logic, evidence and quality gates;
- targeted correction prompts for ChatGPT image editing.

The twelve supplied landscape plates are published in the [Visual Atlas](VISUAL_ATLAS.md) and stored under [`assets/infographics/`](assets/infographics/). They are reference inputs, not automatically approved masters. The four independent documentary photographs remain production prompts only and are not represented as completed assets.

Repository review found three route-logic corrections that remain open in the supplied plates:

- **V06:** every Evidence gate needs a visible pass-through route into the next chapter, in addition to loopback and exit routes;
- **V09:** the incident chain needs an unambiguous connector from Manage or Incident response into Contain;
- **V12:** Expand, Revise, Pause, and Stop need four visibly distinct outcomes rather than two routes that collapse different decisions.

The corrected prompt contracts below are authoritative until replacement plates pass semantic review and their manifest hashes are updated. Keeping the supplied files and the open observations together preserves provenance without presenting visual polish as approval.

> **Mandatory interpretation boundary:** Every generated asset is a reference design or illustrative scene. It does not establish organisational adoption, deployed operation, legal compliance, certification or realised value.

## 1. Why the earlier prompts underperformed

The earlier pack contained strong transformation logic, but it asked the image model to solve too many different production problems in one render.

### 1.1 Prompt-level problems

1. Several prompts combined 30 to 50 exact labels, multiple photographs, small legends, arrows, decisions and footers in one image.
2. The master prompt was repeated after each content prompt, producing long instructions with competing priorities.
3. The specification requested exact vector geometry, exact hexadecimal colour reproduction, exact typography and exact 4K export as if the image model were a deterministic design application.
4. Almost every visual was described as a collection of cards, nodes and connectors. This produced a generic dashboard aesthetic rather than a distinctive editorial system.
5. Documentary photographs were inserted too often and too small. Instead of adding humanity, they became decorative thumbnails.
6. The prompts tried to create landscape and portrait assets from the same information architecture. Portrait versions need a genuine reflow.
7. Three composition variants were requested conceptually, but the prompts did not make each variant a separate generation call.
8. Exact semantic relationships were sometimes listed without mapping each verb to a specific source and target.

### 1.2 Content corrections

| Visual | Correction applied in this version |
| --- | --- |
| V2 | The ladder contains eight states, not seven. |
| V3 | One domain pod is expanded as the role pattern; four domain pods retain local outcome ownership. |
| V4 | Every ontology connector now has a defined source, target and verb. |
| V5 | Consequential action follows Tool selection -> Action boundary -> Human approval -> Business system. Direct Model -> Business system execution is prohibited. |
| V6 | The journey contains thirteen stages, not twelve. |
| V8 | The spectrum is an authority design, not a maturity ladder. L5 is not portrayed as the goal. |
| V11 | Escalation occurs during policy evaluation and before remedy selection or action preparation. |
| V12 | The 90-day horizon leads to a decision, not a guaranteed pilot or scale outcome. |

## 2. The new creative direction

### 2.1 Visual concept: Enterprise Evidence Atlas

The visual language combines four professional traditions:

- editorial information design;
- systems-engineering schematics;
- transit and wayfinding clarity;
- restrained documentary business photography.

It should feel like an independent, high-trust operating manual. It must not imitate a specific consulting firm or technology vendor.

### 2.2 Signature characteristics

- Warm mineral-white paper background with subtle grain, never sterile white.
- Deep ink-navy typography and rules.
- One dominant information topology per visual.
- Fine lines, disciplined alignment, large quiet margins and strong negative space.
- Flat 2D information design with very restrained depth.
- Small semantic colour accents, not full rainbow panels.
- Human scenes used as editorial evidence of work, judgement and collaboration.
- No decorative technology imagery.
- No repeated wall of rounded cards.
- No giant robot, brain, chip, hologram or glowing interface.

### 2.3 Semantic palette

| Meaning | Colour guide |
| --- | --- |
| Mandate, strategy, authority | ink navy `#0B1F33` |
| People, work, workflow | cobalt blue `#246BFD` |
| AI, information, connections | restrained teal `#14B8A6` |
| Decision gate, governance attention | amber `#F59E0B` |
| Prohibited route, incident, stop | coral `#F0645A` |
| Evidence, monitoring, neutral state | slate `#64748B` |
| Background | mineral white `#F5F3EE` |
| Main type | near-black navy `#172033` |

Hex values are art-direction anchors. Evaluate semantic consistency, not mathematical colour matching.

### 2.4 Typography and marks

- Use a modern humanist grotesk similar to Inter, IBM Plex Sans or Neue Haas Grotesk.
- Use sentence case.
- Use condensed numerals only for stage or level identifiers.
- Use large titles and short labels.
- Avoid paragraphs inside images.
- Use thin rules, brackets, rails, bands and swimlanes before defaulting to boxes.
- Use diamonds only for genuine decisions.
- Use solid arrows for work or action, dashed arrows for learning, and dotted lines for oversight.
- Show prohibited paths as a short coral line ending in a clear stop bar.

### 2.5 Text budget

ChatGPT image generation can render short text, but accuracy declines as density grows. Use this hierarchy:

- Primary labels: generated in the first pass.
- Secondary labels: added in one controlled edit pass.
- Interpretation footer: added in a final isolated edit.
- Long explanations: kept outside the image in the playbook body.

Target no more than 18 primary labels in the initial generation. A final plate may contain more after controlled edits, but no label may become presentation-distance microtext.

## 3. How to use these prompts in ChatGPT

### Step 1: Generate the series anchor

Generate Visual 1 first. Inspect it carefully. It becomes the style reference for Visuals 2 to 12.

### Step 2: Generate one image per call

Do not request a batch, contact sheet or multiple numbered visuals in one image. Each prompt below is one independent image-generation request.

### Step 3: Use the approved anchor as a style reference

For Visuals 2 to 12, attach the approved Visual 1 and add:

> Use the attached image only as the series style reference for palette, typography, line weight, paper texture, spacing and editorial restraint. Do not copy its layout or content.

### Step 4: Inspect before editing

Check reading order, missing objects, invented labels, arrow direction, evidence boundary and human authority. Do not ask for a general improvement.

### Step 5: Make one correction per edit

Use the targeted edit template in Section 9. Reassert what must remain unchanged.

### Step 6: Add the footer last

Add this exact footer only after the information design passes:

> Reference design | Illustrative, not operational evidence

### Step 7: Reflow portrait after landscape approval

Use the portrait prompt in Section 10. Do not crop or stretch the landscape master.

## 4. Shared art-direction block

Append this compact block to each production prompt. It is deliberately shorter than the previous master prompt.

```text
Use case: infographic-diagram
Asset: flagship landscape plate for an enterprise AI transformation playbook
Canvas: wide 16:9 landscape, generous margins, presentation-distance readability
Art direction: Enterprise Evidence Atlas; premium editorial information design; systems-engineering precision; transit-map clarity; warm mineral-white paper; ink-navy typography; fine rules; flat 2D geometry; disciplined negative space; small cobalt, teal, amber, coral and slate semantic accents
Typography: modern humanist sans serif, sentence case, large title, short labels, exact spelling
Image behaviour: one dominant topology, one obvious reading path, no decorative connectors, no repeated wall of cards
Constraints: render only supplied text; do not invent data, percentages, logos, certifications, software interfaces or claims; preserve the specified arrow directions and decision boundaries
Avoid: generic consulting-template collage, glossy 3D icons, isometric cityscape, glassmorphism, excessive gradients, neon cyberpunk, robots, glowing brains, circuitry faces, floating dashboards, tiny paragraphs, stock-photo handshakes, celebratory poses, visual clutter, watermark
```

## 5. Core infographic prompts

Each visual includes a composition prompt, a controlled detail edit and a pass gate. Send the composition prompt first. Use the detail edit only if the first image is structurally correct.

### Visual 1: AI Transformation Operating System

**Decision made visible:** AI transformation is a governed operating change, not a technology deployment.

**Topology:** Five-stage operating river with a large central workflow engine and a reversible investment gate.

#### Composition prompt

```text
Create a flagship editorial infographic titled "AI Transformation Operating System" with the subtitle "From ambition to governed, evidenced operating change".

Build one left-to-right operating river with five major stations: "Mandate", "Outcome", "Workflow", "Evidence", "Investment decision". Make "Workflow" the visual centre and largest station.

Inside Workflow, create one clean circular operating engine with six labelled segments: "People", "Authority", "Information", "AI behaviour", "Controls", "Support". The loop must look operational and controlled, not futuristic.

Above the river, add one narrow governance band containing exactly five labels: "Direction", "Delivery", "Enablement", "Trust", "Value". Use five short vertical rules to show that all five govern the central workflow.

Below Workflow, add a foundation rail with five contracts: "Context", "Decision", "Action", "Verification", "Learning".

At the right, place one amber decision diamond labelled "Next investment?" with four clearly separate exits: "Expand", "Revise", "Pause", "Stop". Stop is coral. Add a dashed learning route from the four exits back to Workflow.

Integrate one restrained documentary photograph as a vertical editorial window beside Workflow: a cross-functional operations team reconstructing a real process with paper notes and a process board, natural workplace light, candid posture, no readable confidential content. The photograph supports the system; it does not dominate it.

Reading order must be unmistakable in ten seconds. Keep all connectors outside text. Use no other text.

[Append the shared art-direction block]
```

#### Controlled detail edit

```text
Edit this image only to clarify the five governing connections and the reversible decision loop. Ensure every governance label has one visible rule reaching the Workflow engine. Ensure the dashed learning route returns from Expand, Revise, Pause and Stop to Workflow without crossing any label. Keep the title, all object positions, photograph, palette, typography and all other content unchanged. Add nothing else.
```

#### Pass gate

- All five governance rails connect to Workflow.
- The transformation unit is the workflow, not the model.
- Investment can expand, revise, pause or stop.
- The human photograph shows active process work.

### Visual 2: Activity-to-Transformation Evidence Ladder

**Decision made visible:** A claim cannot advance without a stronger type of evidence.

**Topology:** Eight ascending evidence terraces separated by a hard claim boundary.

#### Composition prompt

```text
Create an editorial evidence infographic titled "From AI Activity to Realised Value".

Use eight broad ascending terraces from lower left to upper right. Label them exactly, in this order: "Hypothesised", "Mapped", "Designed", "Tested", "Human-observed", "Pilot-observed", "Operational", "Value-realised".

Place a strong vertical amber boundary between Tested and Human-observed. Label the left field "Learning and design" and the right field "Authorised organisational evidence".

Under the first four terraces, add one coral rule labelled "Do not claim adoption or value here".

Before Value-realised, place an amber gate. Add one dashed route from the gate to a coral side destination labelled "Revise or stop". Make the upper-right outcome credible but not inevitable.

Use three small, aligned documentary contact-sheet frames beneath the relevant terraces: workflow observation, intended-user test and bounded pilot review. Natural workplace realism, no visible company identity or readable data.

Primary text only: the title, eight state labels, the two field labels, the warning and "Revise or stop".

[Append the shared art-direction block]
```

#### Controlled detail edit

```text
Add one small evidence tag beneath each terrace, in order: "Opportunity statement", "Observed workflow", "Future-state design", "Representative tests", "Intended-user sessions", "Bounded company use", "Monitoring and support", "Outcome with boundary". Keep each tag on one line and directly aligned with its terrace. Change nothing else and do not add explanatory text.
```

#### Pass gate

- There are exactly eight states.
- The organisational-evidence boundary occurs before Human-observed.
- Value-realised has a gate and an alternative exit.
- The image never implies that training, access or testing equals adoption.

### Visual 3: Federated AI Transformation Operating Model

**Decision made visible:** Domains own outcomes; the centre supplies method, trust and reusable capability.

**Topology:** Executive authority canopy, central enabling hub, four domain pods and shared foundation.

#### Composition prompt

```text
Create a role-and-authority topology titled "Federated AI Transformation Operating Model".

At the top, create a narrow authority canopy with three labels: "Executive sponsor", "Portfolio council", "Risk acceptance authority".

In the centre, create one compact enabling hub labelled "AI transformation hub". Inside it, place five functions: "Enablement", "Delivery", "Architecture", "Evidence", "Governance".

Around the hub, arrange four equal "Business domain" pods. Each domain pod must connect directly to its own two local objects: "Outcome" and "Pilot decision". The hub must not connect directly to Outcome.

Expand one domain pod as the role pattern and label four roles inside it: "Workflow owner", "Manager", "Workflow Activator", "Intended users". The other three pods use the same visual pattern without repeating all role labels.

Create one shared foundation band beneath the network labelled "Trust and platform functions" with six short labels: "Security", "Privacy", "Legal", "Risk", "Technology operations", "People and learning".

Use solid lines for local ownership, dotted lines from the executive canopy for oversight, and dashed two-way lines between the hub and domains for support and learning. Include a minimal three-item legend: "Owns", "Oversees", "Supports and learns".

Use no photography. Use no fabricated domain names. Use no other text.

[Append the shared art-direction block]
```

#### Controlled detail edit

```text
Edit only the relationship routing. Every Business domain must own its own Outcome and Pilot decision through solid local lines. The AI transformation hub may connect to domains only through dashed support-and-learning lines. The executive canopy may connect only through dotted oversight lines. Keep all nodes, labels, layout and styling unchanged.
```

#### Pass gate

- No central team owns business outcomes.
- Four business domains are visible.
- One domain clearly demonstrates the reusable role pattern.
- Ownership, oversight and support are visually different.

### Visual 4: Enterprise AI Transformation Ontology

**Decision made visible:** Every claim must be traceable through explicit transformation objects and evidence.

**Topology:** Six-object traceability spine with three ordered semantic clusters.

#### Composition prompt

```text
Create a precise enterprise relationship map titled "Enterprise AI Transformation Ontology".

Build one horizontal traceability spine with six large objects in this exact order: "Mandate" -> "Outcome" -> "Workflow" -> "AI behaviour" -> "Evidence" -> "Claim".

Use these exact spine relationships: Mandate "targets" Outcome; Outcome is "realised through" Workflow; Workflow "contains" AI behaviour; AI behaviour "produces" Evidence; Evidence reaches Claim only through an amber "Claim gate".

Around Workflow, create one blue semantic cluster with six satellites: "Task", "Decision", "Actor", "Authority", "Information", "Policy".

Around AI behaviour, create one teal semantic cluster with five satellites: "Capability", "Model", "Tool", "Connection", "Autonomy level".

Around Evidence, create one slate semantic cluster with six satellites: "Evaluation", "Control", "Monitoring", "Incident", "Change", "Support".

At the Claim gate, create three outcomes: "Supported" continues to Claim; "Qualified" continues to Claim through an amber qualifier; "Unsupported" ends at a coral stop bar before Claim.

No photography. Keep each cluster visually separate, use orthogonal connectors and eliminate line crossings. Do not add relationship verbs beyond those supplied.

[Append the shared art-direction block]
```

#### Controlled relationship edit

```text
Edit only the satellite relationships using these exact directions:
- Actor -> performs -> Task
- Workflow -> contains -> Task
- Workflow -> contains -> Decision
- Authority -> authorises -> Decision
- Information -> supports -> Task and Decision
- Policy -> constrains -> Decision and AI behaviour
- Capability -> enables -> AI behaviour
- AI behaviour -> uses -> Model, Tool and Connection
- Autonomy level -> constrains -> AI behaviour
- Evaluation -> tests -> AI behaviour
- Control -> constrains -> Workflow and AI behaviour
- Monitoring, Incident, Change and Support -> produce -> Evidence
Keep the traceability spine, claim gate, positions, colours and all other content unchanged. Use orthogonal connectors. Do not reverse any arrow.
```

#### Pass gate

- All 23 ontology objects are present.
- Every relationship has a defined direction.
- Unsupported evidence cannot become a claim.
- No line crossing creates a false relationship.

### Visual 5: AI-Enabled Workflow Reference Architecture

**Decision made visible:** A model cannot act directly on a consequential business system.

**Topology:** Seven-layer architecture with one controlled action chute and one blocked bypass.

#### Composition prompt

```text
Create a rigorous architecture plate titled "AI-Enabled Workflow Reference Architecture".

Build seven wide horizontal layers, top to bottom:
1. "Experience and work"
2. "Workflow and orchestration"
3. "AI capability and model"
4. "Knowledge and information"
5. "Integration and action"
6. "Trust and control"
7. "Operations and evidence"

Place one continuous vertical rail on the left labelled "Human decision owner" spanning all seven layers.

Place these primary objects in the relevant layers:
- Experience and work: "Employee", "Customer", "Manager"
- Workflow and orchestration: "Case state", "Rules", "Task orchestration"
- AI capability and model: "Prompt and policy", "Retrieval", "Model", "Tool selection"
- Knowledge and information: "Authoritative sources", "Permissions", "Provenance"
- Integration and action: "APIs", "Business systems"
- Trust and control: "Identity", "Privacy", "Security"
- Operations and evidence: "Evaluation", "Monitoring", "Incident", "Change", "Cost"

Create one unmistakable blue action chute on the right in this exact order: "Tool selection" -> "Action boundary" -> "Human approval" -> "Business systems". Place the amber approval gate before Business systems.

Draw a separate short coral prohibited route from "Model" toward "Business systems" and terminate it at a stop bar before it reaches the system. Label it "No direct execution".

Show governed information moving upward in teal and verified action moving downward in blue. Add a narrow right-edge contract rail with "Context", "Decision", "Action", "Verification", "Learning".

Use no photography. Use an architectural drawing aesthetic, not a cloud-vendor diagram. Use no other text.

[Append the shared art-direction block]
```

#### Controlled detail edit

```text
Add this exact bottom rule: "The model is a component; the governed workflow is the transformation unit". Keep it large enough to read. Verify that Action boundary and Human approval both occur before Business systems. Keep all other content unchanged.
```

#### Pass gate

- The seven layers are complete.
- Human decision ownership spans the architecture.
- The approved action path does not intersect the blocked route.
- Direct model-to-system execution visibly fails.

### Visual 6: Transformation Delivery Journey

**Decision made visible:** Delivery advances through evidence gates and can loop back, pause or retire.

**Topology:** Four-row transit journey with thirteen stations and three chapter gates.

#### Composition prompt

```text
Create a stage-gated journey titled "From First Conversation to Evidence-Based Decision".

Use a top-down four-row transit-map composition, not one long horizontal timeline. Each row is a chapter with a clear start and end.

Chapter 1 "Orient": "Prepare" -> "Qualify" -> "Mandate"
Chapter 2 "Understand": "Observe current work" -> "Build portfolio" -> "Select lighthouse"
Chapter 3 "Design and test": "Design future workflow" -> "Define architecture and controls" -> "Freeze evaluation" -> "Build and test"
Chapter 4 "Operate and decide": "Prepare people and pilot" -> "Measure and decide" -> "Scale, revise, pause, or retire"

There are thirteen stage stations in total. Place one amber "Evidence gate" between each chapter, three gates total. Every gate must have three legible route types: one solid pass-through connector to the first station of the next chapter, one dashed learning loop to the immediately preceding chapter, and one visible exit route.

Below the transit route, add a seven-item artefact rail: "Mandate", "Current-state map", "Portfolio record", "Future workflow", "Evaluation contract", "Pilot charter", "Decision memo".

Integrate one documentary contact strip with four frames, aligned to the chapters: executive alignment, workflow observation, build and evaluation, intended-user pilot. Make the images observational, neutral and small enough that the route remains dominant.

Use no other text. Do not depict scale as inevitable.

[Append the shared art-direction block]
```

#### Controlled route edit

```text
Edit only the route logic. Confirm exactly thirteen labelled stations and exactly three Evidence gates. From every gate, show one solid forward connector into the next chapter, one dashed learning loop to the immediately preceding chapter, and one exit route. Ensure the final station contains all four legitimate outcomes: scale, revise, pause, retire. Keep the composition and photographs unchanged.
```

#### Pass gate

- Thirteen stages are visible.
- Four chapters and three gates are visible.
- Every gate has a forward pass route, a learning loop, and an exit route.
- Revision and retirement are legitimate outcomes.
- The artefact rail contains seven items.

### Visual 7: Use-Case Portfolio Decision Map

**Decision made visible:** Opportunity cannot cancel unacceptable consequence.

**Topology:** Qualification filter, three independent lenses and four delivery routes.

#### Composition prompt

```text
Create a portfolio decision infographic titled "Choose Work Worth Changing".

Use a strong left-to-right qualification and routing composition.

Left section: one vertical six-check filter with these exact questions: "Owned outcome?", "Recurring workflow?", "Observable baseline?", "Authoritative information?", "Safe boundary?", "Measurable decision?". Any failed check must lead to a coral "Stop or reframe" exit before assessment.

Centre section: three equal, independent assessment lenses arranged as overlapping but non-collapsing fields: "Opportunity", "Feasibility", "Consequence". Do not calculate one total score.

Right section: four large route destinations: "Enable", "Guide", "Co-build", "Stop". Give each a short subtitle: "Standard tools", "Templates and oversight", "Dedicated workflow change", "Weak value or unacceptable boundary".

Create one coral rule running beneath the three lenses: "Consequence is classified; it is not cancelled by opportunity". Connect unacceptable Consequence directly to Stop, even when Opportunity is high.

Use a small number of unlabeled candidate dots to show different routing possibilities. Do not invent scores, percentages or monetary values. No photography.

[Append the shared art-direction block]
```

#### Controlled detail edit

```text
Add compact factor labels inside the three lenses only:
Opportunity: "Outcome", "Friction", "Frequency", "Learning"
Feasibility: "Data", "Integration", "Ownership", "Evaluation", "Support"
Consequence: "Rights", "Safety", "Financial", "Reputation", "Reversibility"
Keep the factors grouped within the correct lens. Change nothing else.
```

#### Pass gate

- Qualification occurs before prioritisation.
- The three lenses are independent.
- Consequence can force Stop.
- Four delivery modes are visually distinct.

### Visual 8: Human-AI Work and Autonomy Spectrum

**Decision made visible:** Autonomy is bounded authority, not a maturity race.

**Topology:** Six authority stations with persistent human ownership and increasing control density.

#### Composition prompt

```text
Create a human-centred authority infographic titled "Design Authority Before Autonomy".

Build six equal vertical stations from left to right:
"L0 Human only"
"L1 AI informs"
"L2 AI drafts"
"L3 AI recommends"
"L4 AI prepares action"
"L5 AI executes bounded action"

Above all six stations, place one continuous cobalt rail labelled "Human accountability retained".

Within each station, show three clearly separated bands: "AI contribution", "Human authority", "Control and evidence". Do not repeat the band labels six times; use one shared band legend on the left.

Run one concrete exception workflow along the bottom: "Receive exception" -> "Retrieve facts" -> "Draft options" -> "Recommend response" -> "Prepare action" -> "Execute within boundary".

Place an amber approval gate before L4. Place a stronger amber-and-coral boundary before L5 labelled "Approved scope + reversibility". Increase the visible density of controls and evidence from L3 onward.

Integrate one medium documentary image on the right: an experienced employee comparing an AI-prepared recommendation with an authoritative source, then recording a decision. The human is the active authority.

Add one bottom statement: "Higher autonomy requires stronger boundaries, verification, monitoring, reversibility, and authority".

Avoid an upward arrow, trophy, staircase or any suggestion that L5 is better. Use no other text.

[Append the shared art-direction block]
```

#### Controlled authority edit

```text
Edit only the authority and gate cues. Make the Human accountability retained rail equally strong over L0 through L5. Make the approval gate occur before L4 and the approved-scope plus reversibility boundary occur before L5. Do not portray L5 as the destination. Keep the person, workflow and all labels unchanged.
```

#### Pass gate

- Human accountability persists across every level.
- L4 and L5 have visibly stronger gates.
- The spectrum does not imply a maturity race.
- The photograph shows active human judgement.

### Visual 9: Governance, Control and Evidence Loop

**Decision made visible:** Governance, measurement and operations form one assurance loop, while claims remain bounded.

**Topology:** Four-quadrant control loop around the workflow, with an external claim path and incident-learning branch.

#### Composition prompt

```text
Create a rigorous assurance-system infographic titled "Govern, Evaluate, Operate, Learn".

Place "AI-enabled workflow" at the centre. Around it, create one continuous four-quadrant loop:
"Govern" -> "Map" -> "Measure" -> "Manage" -> back to "Govern".

Inside the quadrants, initially show only these primary objects:
Govern: "Mandate", "Roles", "Policy", "Risk acceptance"
Map: "Context", "People", "Impact", "Failure modes"
Measure: "Evaluation", "Human observation", "Monitoring", "Evidence quality"
Manage: "Controls", "Incident response", "Change", "Retire"

Create a thin control ring immediately around the central workflow with four labels: "Prevent", "Detect", "Respond", "Recover".

Outside the loop, create one evidence path in this order: "Observation" -> "Record" -> "Evaluation" -> "Claim boundary" -> "Investment decision". Claim boundary must be an amber gate, not a decorative label.

Add one coral incident branch from Manage: "Contain" -> "Investigate" -> "Correct" -> "Learn", then return through a dashed arrow to Govern.

No photography. Do not create a giant shield. Use a clean assurance and audit aesthetic.

[Append the shared art-direction block]
```

#### Controlled detail edit

```text
Add only these secondary objects inside their correct quadrants:
Govern: "Inventory"
Map: "Information"
Measure: "Cost"
Manage: "Vendor"
Then add this bottom note exactly: "Internal assurance supports decisions; it does not itself establish legal compliance or certification". Confirm one unambiguous coral connector runs from Manage's "Incident response" object into "Contain" before the existing Contain -> Investigate -> Correct -> Learn sequence. Keep all other relationships, object positions and styling unchanged.
```

#### Pass gate

- The four disciplines are one operating loop.
- The claim boundary sits before investment.
- Manage or Incident response visibly initiates the Contain -> Investigate -> Correct -> Learn chain.
- The incident branch returns learning to governance.
- Assurance is not represented as certification.

### Visual 10: Enablement-to-Adoption Capability Flywheel

**Decision made visible:** Training attendance is an event; adoption is sustained appropriate use in real work.

**Topology:** Capability progression feeding a repeated work-learning flywheel, supported by role ownership.

#### Composition prompt

```text
Create an enablement and adoption infographic titled "From AI Literacy to Operating Capability".

On the left, create a seven-stage capability progression: "Awareness" -> "Safe first use" -> "Task fluency" -> "Repeat appropriate use" -> "Workflow integration" -> "Operating capability" -> "Outcome".

Do not use a triumphant staircase. Use a restrained horizontal progression that feeds a large circular work-learning flywheel in the centre.

The flywheel contains six segments in this order: "Practice real work", "Observe use", "Support friction", "Improve workflow", "Share learning", "Measure again".

Below, create a role-support network with seven nodes: "Employee", "Manager", "Workflow Activator", "Enablement lead", "Builder", "Control partner", "Workflow owner". Connect each role to the part of the progression or flywheel it supports. Do not make all roles report to one centre.

On the right, create a compact five-row evidence panel: "Access", "Capability", "Appropriate use", "Integration", "Sustainability".

Integrate one documentary contact strip across the bottom with three scenes: role-based practice using real tasks, peer coaching over a failed output, and manager review of appropriate use. No classroom lecture.

Place one coral warning beside Awareness: "Attendance is not adoption". Use no other text.

[Append the shared art-direction block]
```

#### Controlled role edit

```text
Edit only the role-support connections. Employee participates in practice; Manager removes local friction and reinforces appropriate use; Workflow Activator supports peers; Enablement lead designs capability; Builder improves the workflow; Control partner protects boundaries; Workflow owner owns the outcome. Use short clean lines and no new labels. Keep all nodes and images unchanged.
```

#### Pass gate

- Capability progression and adoption repetition are visibly different.
- Attendance is explicitly rejected as adoption evidence.
- Roles support different accountabilities.
- The photographs show practice, failure learning and management support.

### Visual 11: Commerce Lighthouse, Exception to Recovery

**Decision made visible:** AI prepares evidence and action, while accountable people approve remedies and consequential execution.

**Topology:** Five-lane service blueprint with gates positioned before consequential action.

#### Composition prompt

```text
Create an end-to-end service blueprint titled "Commerce Lighthouse: Exception to Verified Customer Recovery".

Use five horizontal swimlanes:
1. "Customer and outcome"
2. "Operations workflow"
3. "AI support"
4. "Policy and human authority"
5. "Information, systems and evidence"

Run nine stages from left to right across the blueprint:
"Exception detected" -> "Case assembled" -> "Facts verified" -> "Policy options evaluated" -> "Human decision" -> "Action prepared" -> "Customer response approved" -> "System updated" -> "Outcome monitored".

In the AI support lane, align six contributions to the appropriate stages: "Retrieve", "Structure", "Check", "Draft", "Recommend", "Prepare". None may bypass Human decision or Customer response approved.

In the authority lane, place four clear human actions: "Confirm facts", "Escalate exception", "Select remedy", "Approve communication". Escalate exception must occur during Policy options evaluated and before Select remedy.

In the bottom lane, show six source chips: "Order", "Shipment", "Inventory", "Customer history", "Policy", "Entitlement". Add six controls aligned to the route: "Source citation", "Permission", "Policy boundary", "Approval", "Action verification", "Audit record".

Integrate two documentary windows above the swimlanes: fulfilment operations examining an exception and a service employee deciding from verified facts. No brand identity or readable customer data.

At the far right, show a six-item outcome panel: "Resolution quality", "Customer effort", "Review burden", "Reliability", "Operating cost", "Incidents".

Add this exact boundary statement: "Synthetic worked example until authorised organisational validation occurs".

Use no other text. Keep the blueprint operational, realistic and non-futuristic.

[Append the shared art-direction block]
```

#### Controlled gate edit

```text
Edit only the sequence and gates. Place Escalate exception under Policy options evaluated, before Select remedy and before Action prepared. Ensure AI Prepare cannot connect directly to System updated. Ensure Human decision, Customer response approved and Action verification all occur before System updated. Keep all lanes, photographs, labels and styling unchanged.
```

#### Pass gate

- Nine stages are present and ordered.
- Escalation occurs early enough to prevent inappropriate action.
- AI support never owns remedy or approval.
- System update occurs only after human gates and action verification.
- The synthetic-evidence boundary is visible.

### Visual 12: Company Engagement and 90-Day Decision Roadmap

**Decision made visible:** Ninety days buys evidence for an investment decision, not guaranteed scale.

**Topology:** Four-phase evidence horizon terminating in a decision gate and optional next branch.

#### Composition prompt

```text
Create a client-facing roadmap titled "Start Small Enough to Learn, Structured Enough to Decide".

Use a wide 90-day evidence horizon divided into four unequal but balanced phases:
"Days 0-15 | Mandate and readiness"
"Days 16-35 | Workflow and portfolio discovery"
"Days 36-60 | Future workflow and evaluation design"
"Days 61-90 | Bounded test or pilot readiness"

Place one output stack beneath each phase:
Phase 1: "Decision", "Owner", "Boundary", "Success measure"
Phase 2: "Current-state evidence", "Baseline", "Lighthouse choice"
Phase 3: "Authority", "Architecture", "Controls", "Evaluation contract"
Phase 4: "Test evidence", "User observation", "Pilot charter", "Decision memo"

At day 90, place a large amber decision diamond: "Expand, revise, pause, or stop?". From it, create four visually distinct and equally legitimate routes. "Expand" goes to the optional state "Operate and scale only with evidence". "Revise" goes to "Additional discovery or control work" and loops back to the relevant phase. "Pause" goes to "Hold with review condition". "Stop" goes to "Terminate or retire". Do not merge Pause or Stop into the Revise loop.

Below the horizon, create one restrained artefact rail with eight workshop ticks labelled "W1" through "W8" and twelve canvas ticks labelled "C1" through "C12". These are markers only, not a wall of cards.

Integrate a four-frame documentary contact strip: executive alignment, current-work mapping, future-workflow design, evidence review.

Do not imply that every organisation reaches a live pilot by day 90. Use no other text.

[Append the shared art-direction block]
```

#### Controlled route edit

```text
Edit only the day-90 decision. Show four separate labelled routes: Expand -> "Operate and scale only with evidence"; Revise -> "Additional discovery or control work" with a loop to the relevant phase; Pause -> "Hold with review condition"; Stop -> "Terminate or retire". Make all four decisions equally legitimate and keep expansion visibly optional. Keep the entire timeline, outputs, artefact rail and photographs unchanged.
```

#### Pass gate

- Four phases and their outputs are complete.
- Day 90 ends in a decision, not a promise.
- Expand, Revise, Pause, and Stop have four distinct destinations.
- The optional Expand path is evidence-dependent.
- Revise loops to additional discovery or control work.
- Pause and Stop remain legitimate terminal or held states.

## 6. Documentary photography prompts

Generate each photograph independently. Do not add titles, captions, diagrams or readable screen content during generation. Add the interpretation caption later in layout.

### P1: Executive mandate and investment boundary

```text
Use case: photorealistic-natural
Asset: full-width editorial photograph for an enterprise AI transformation playbook
Primary request: a candid executive decision session focused on a real business workflow and investment boundary
Scene: ordinary contemporary company meeting room; one executive sponsor, one operations leader and one transformation lead studying a large physical workflow map, handwritten notes and a short decision record
Human action: the operations leader traces a process dependency; the sponsor checks the decision record; the transformation lead listens and annotates risk and evidence questions
Composition: wide 16:9 environmental photograph, eye level, people grouped naturally around the table, hands and work materials visible, meaningful negative space for a title outside the photograph
Lighting: soft northern window light, neutral colour temperature, realistic shadow falloff
Realism: natural skin texture, minor fabric creases, ordinary work materials, believable posture, serious but constructive mood
Constraints: no one looks at the camera; no handshake; no celebration; no logos; no readable confidential text; no AI imagery; no glowing interface; no watermark
Avoid: glossy corporate stock photography, staged pointing, perfect showroom office, exaggerated smiles, tokenistic lineup, cinematic teal-orange grading
```

### P2: Current-work observation

```text
Use case: photorealistic-natural
Asset: editorial documentary photograph for workflow discovery
Primary request: a transformation practitioner quietly observing an operations team reconstructing how a real exception moves through current work
Scene: believable fulfilment or service-operations workspace with a standard business monitor, printed case materials, sticky notes and a partially completed process board; all sensitive content unreadable
Human action: two operators explain a handoff using physical evidence while the practitioner records sequence, delay and exception points; everyone focuses on the work
Composition: wide 16:9, over-the-shoulder documentary viewpoint, layered foreground materials, practical room depth, no posed group arrangement
Lighting: mixed natural daylight and normal office light, slightly imperfect and credible
Realism: natural faces and hands, ordinary clothing, worn paper edges, functional workplace texture
Constraints: no logos, no readable personal data, no floating UI, no staged pointing at the camera, no watermark
Avoid: generic workshop smiles, sterile innovation lab, futuristic screens, dramatic cinematic lighting
```

### P3: Human authority in an AI-supported workflow

```text
Use case: photorealistic-natural
Asset: editorial photograph for human-AI authority and governance
Primary request: an experienced service employee comparing an AI-prepared recommendation with an authoritative policy source and recording a consequential decision, while a colleague remains available for escalation
Scene: realistic commerce customer-operations desk, standard monitor, printed policy reference, case notes and approval record; interface content abstract and unreadable
Human action: the employee actively checks evidence line by line, marks one discrepancy and records the final decision; the colleague is attentive but does not take over
Composition: medium-wide 16:9, three-quarter side view, employee in visual focus, evidence source and decision record both visible, natural hand interaction
Lighting: calm daylight with soft task lighting, no blue glow
Realism: natural skin, believable concentration, imperfect desk texture, practical clothing
Constraints: AI is a support tool, never a character; no logos; no readable customer data; no hologram; no watermark
Avoid: passive person watching a magical dashboard, humanoid assistant, neon lighting, generic smiling call-centre stock photo
```

### P4: Role-based practice and peer support

```text
Use case: photorealistic-natural
Asset: editorial photograph for enablement and adoption
Primary request: a small role-based practice session in which employees test realistic job scenarios, compare an imperfect AI output with policy, discuss the failure and record a workflow improvement
Scene: practical team workspace with four to six people, laptops, printed scenario cards, policy references, failure note and simple process board
Human action: one employee demonstrates the failed output; a Workflow Activator asks a question; the manager removes a local process blocker; another participant records the agreed learning
Composition: wide 16:9, candid grouping around real work, hands and materials visible, nobody facing the camera, no presenter-at-front arrangement
Lighting: warm natural light with ordinary office fill, psychologically safe and analytical mood
Realism: natural expressions, mixed seniority, believable posture, practical workspace texture
Constraints: not a lecture, not a celebration, no logos, no readable confidential content, no robots, no holograms, no watermark
Avoid: classroom training cliché, applause, staged diversity poster, glossy innovation laboratory
```

## 7. Photography integration rules

- V1 uses one vertical documentary window.
- V2 uses a three-frame contact strip.
- V3, V4, V5, V7 and V9 use no photography.
- V6 uses a four-frame journey strip.
- V8 uses one authority photograph.
- V10 uses a three-frame practice strip.
- V11 uses two operational photographs.
- V12 uses a four-frame engagement strip.
- P1 to P4 remain independent section-opening plates.
- Never reuse the same generated person as if they were an employee across unrelated organisations.
- Generated people may illustrate roles, but never prove participation or adoption.

## 8. Exact footer edit

Apply only after the image has passed its semantic review.

```text
Edit only the lower footer area. Add one quiet full-width footer rule and this exact text in small but readable ink-navy type: "Reference design | Illustrative, not operational evidence". Keep every other pixel, label, connector, person, colour and object unchanged. Do not add logos, page numbers or any other text.
```

## 9. Targeted correction prompts

Never combine these corrections. Make one edit, inspect, then decide whether another is required.

### Correct a misspelled label

```text
Change only the misspelled label "<current text>" to "<correct text>". Preserve its position, type size, weight, colour, alignment and surrounding geometry exactly. Change nothing else. Add no text.
```

### Remove invented copy

```text
Remove only the unsupplied text "<invented text>" and restore the underlying background cleanly. Preserve every supplied label, connector, object, photograph and all spacing. Do not replace it with new copy.
```

### Correct one arrow

```text
Edit only the connector between "<source>" and "<target>". It must point from "<source>" to "<target>" and follow the shortest clean route without crossing a label or node. Keep all other connectors and content unchanged.
```

### Clarify one human decision gate

```text
Edit only the decision gate named "<gate>". Make it occur before "<consequential action>" and visibly block any bypass route. Keep every label, object position, photograph and all unrelated connections unchanged.
```

### Improve photograph realism

```text
Edit only the documentary photograph. Preserve the people, action, framing and surrounding infographic. Make skin texture, hands, clothing, posture, workplace materials, lens behaviour and natural light more believable. Remove glossy stock-photo polish. Do not alter any diagram, label or connector.
```

### Reduce visual clutter

```text
Edit only spacing and line routing. Increase negative space around the main reading path, shorten connectors, remove decorative lines and prevent all line-label collisions. Preserve every required object, label, semantic colour, photograph and relationship. Do not simplify the meaning or add content.
```

### Reassert a failed invariant

```text
Correct only this invariant: <state one exact rule>. Keep unchanged: canvas ratio, title, all approved object positions, supplied labels, semantic colours, photographs, footer and every unrelated connector. Do not add objects, metrics, logos or explanatory copy.
```

## 10. Portrait adaptation prompt

Use the approved landscape master as the edit/reference image.

```text
Use case: infographic-diagram
Asset: portrait adaptation for a professional playbook and editorial post
Primary request: recompose the approved landscape infographic into a tall 9:16 portrait information design
Input image: the attached landscape master is the sole content and style reference
Composition: rebuild the information architecture as two to four vertically stacked semantic sections; preserve the original reading order; keep the title once at the top; move the evidence boundary and decision gate into clearly separated vertical zones; maintain generous side margins
Invariants: preserve every required object, relationship, arrow direction, human-authority point, semantic colour and exact label; preserve documentary people and their work; keep the footer once at the bottom
Constraints: do not crop a meaningful object; do not stretch the landscape image; do not shrink the full landscape into a small panel; do not repeat the title; do not invent text, data, icons or connectors
Style: match the approved landscape master exactly in palette, typography, line weight, paper texture, photographic treatment and editorial restraint
```

## 11. Quality-control protocol

### 11.1 Ten-second test

The target audience must be able to state the main message after ten seconds. If they describe the topic but not the relationship or decision, the image has failed.

### 11.2 Semantic inspection

- Is the reading path obvious?
- Is every arrow directional and meaningful?
- Are human authority and business ownership visible?
- Does a consequential action encounter the required gate?
- Can a negative or incomplete evidence state legitimately stop progress?
- Does the image distinguish a design, an observation and a claim?
- Could any element accidentally imply deployment, compliance or realised value?

### 11.3 Visual inspection

- One dominant topology only.
- No more than seven primary visual groups.
- No paragraphs inside the plate.
- No illegible microtext at presentation scale.
- No line crossing through a label.
- No repeated icon-card wallpaper.
- Semantic colours remain consistent.
- Photography supports the argument and never competes with it.

### 11.4 Documentary inspection

- People perform meaningful work.
- The scene is observational rather than staged.
- Hands, faces, screens and materials look plausible.
- No confidential or identifying information is readable.
- No generated scene is presented as actual organisational evidence.

### 11.5 Exact-copy inspection

- Compare every label with this prompt pack.
- Remove all invented words.
- Correct one text error per edit.
- Confirm that the footer appears once.
- Confirm that titles and subtitles use consistent punctuation and sentence case.

## 12. Production sequence

1. Generate V1 information-dominant composition.
2. Inspect and correct V1 one issue at a time.
3. Add the footer and approve V1 as the style anchor.
4. Generate P1 to P4 independently.
5. Generate V2 to V12 one at a time using V1 as the style reference.
6. Apply each visual's controlled detail edit only after its composition passes.
7. Apply targeted corrections one at a time.
8. Add the footer only after semantic approval.
9. Create portrait reflows only after all landscape masters pass.
10. Review the complete series as a contact sheet for style drift and duplicated composition.
11. Archive prompts, approved images, rejected variants, edit history and evidence status.

## 13. Recommended generation order

For the strongest narrative and fastest learning, produce:

1. V1 Operating System
2. V11 Commerce Lighthouse
3. V5 Reference Architecture
4. V2 Evidence Ladder
5. V3 Operating Model
6. V9 Governance Loop
7. V8 Authority Spectrum
8. V10 Adoption Flywheel
9. V7 Portfolio Decision Map
10. V6 Delivery Journey
11. V12 90-Day Roadmap
12. V4 Ontology

V4 is intentionally last because it has the greatest relationship density and should inherit the fully stabilised visual language.

## 14. File and production record convention

```text
V01-ai-transformation-operating-system-v03-landscape.png
V01-ai-transformation-operating-system-v03-portrait.png
V01-ai-transformation-operating-system-v03-prompt.md
V01-ai-transformation-operating-system-v03-record.md
P01-executive-mandate-v03-landscape.png
```

Record for every asset:

- visual number and title;
- purpose and audience;
- playbook section;
- exact prompt;
- reference images and their roles;
- generation date;
- edit history;
- semantic-review status;
- exact-copy status;
- documentary-image status;
- evidence boundary;
- approval status.

## 15. Final approval standard

Approve a visual only when all five statements are true:

1. The intended decision or relationship is understood in ten seconds.
2. Every object, connection and gate is semantically correct.
3. Every visible word is supplied, correctly spelled and readable.
4. Generated imagery cannot be mistaken for organisational evidence.
5. The plate looks like part of one professional editorial system, while retaining a topology suited to its own argument.
