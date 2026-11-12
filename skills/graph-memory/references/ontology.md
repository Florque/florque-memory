# Memory ontology: Work Graph Ontology (v0.2, minimal)

Minimal ontology for an agentic program-management graph. 6 node classes, 6 edge types. One root (Project), two typed trees under it, and a few links between them. Work tree: Project > initiative > epic (optional) > task / bug. Goal tree: Project > objective > key_result. Links: initiative serves goals, metric Signals are about key results.

## How writes work

- Nodes are identified by `id`. Omit it on create to get a generated id; pass it to update (upsert, never duplicates).
- One `remember` call sets properties and outgoing edges together. An edge slot given in the payload replaces that slot's edges; omitted edge slots are kept.
- Edge values are a target id, a list of ids, or `{"id": ..., <edge props>}` objects.
- Unknown slots, wrong enum values and broken rules are rejected with the reason.
- New nodes get `status: proposed` unless you set it. Set `null` to clear an optional property.
- Engine-managed: `created_at`, `created_by`, `updated_at`, `updated_by`.
- `Project` is the root. Most other nodes hang off it through `part_of`.

## Classes

### Project

WHAT: The root of everything: one product or effort. BRANCH: root.

- **Branch:** root
- **Use when:** Starting a new product or effort. Describe it in the summary. Create it first; every other node hangs off it, directly or through a parent.
- **Not for:** Teams, themes or labels. Use Actor for people and teams.

**Properties**

| name | type | required | description |
|---|---|---|---|
| `id` | string | auto if omitted | Stable unique id. Never reuse. |
| `title` | string | yes | Short name, under 80 chars. Must make sense without its parent. |
| `summary` | string | yes | 1-3 sentences stating what this node is and why it matters. This is what agents read first when building context, so keep it self-contained. |
| `status` | Status | defaults to `proposed` |  |

**Edges** (outgoing; pass target ids)

| slot | targets | cardinality | required | description |
|---|---|---|---|---|
| `owned_by` | Actor | one |  | Accountability. "This Actor answers for it." One owner per node. |

### Goal

WHAT: A desired outcome: an objective, or a key result that measures it. BRANCH: goals (why).

- **Branch:** goals
- **Use when:** Recording why work exists or what success looks like. Objective for the outcome, key_result for its measurable proof. This is the only place the "why" lives.
- **Not for:** Work that someone does (use WorkItem), or raw numbers (use Signal, then link with about).

**Properties**

| name | type | required | description |
|---|---|---|---|
| `id` | string | auto if omitted | Stable unique id. Never reuse. |
| `title` | string | yes | Short name, under 80 chars. Must make sense without its parent. |
| `summary` | string | yes | 1-3 sentences stating what this node is and why it matters. This is what agents read first when building context, so keep it self-contained. |
| `status` | Status | defaults to `proposed` |  |
| `target` | string |  | For key_result. Measurable success condition with number and date, e.g. "Activation rate >= 40% by 2027-03-31". |
| `due` | date |  |  |
| `level` | GoalLevel | yes |  |

**Edges** (outgoing; pass target ids)

| slot | targets | cardinality | required | description |
|---|---|---|---|---|
| `owned_by` | Actor | one |  | Accountability. "This Actor answers for it." One owner per node. |
| `part_of` | Goal \| Project | one | yes | Containment. "This lives inside that." The tree skeleton: task part_of epic or initiative, epic part_of initiative, key_result part_of objective, person part_of team. Initiatives, objectives, documents and signals are part_of the Project. Exactly one parent per node. Every node except the Project has one, so every chain ends at the root. |

**`level` values** (required)

- `objective`: Qualitative outcome the project wants, 1-2 quarters. Answers why the work exists. _(part_of → Project)_
- `key_result`: Measurable target that proves an objective is met. Must have a target. _(part_of → Goal:objective)_

**Rules**

- A key result must be measurable. (when level=key_result, target required)

### WorkItem

WHAT: Anything someone does, from initiative down to task or bug. BRANCH: execution (what and when).

- **Branch:** execution
- **Use when:** Creating or updating planned work. Start with an initiative that serves a goal; add epics only when an initiative needs structure. Default to kind=task.
- **Not for:** Outcomes or targets (Goal), written requirements (Document).

**Properties**

| name | type | required | description |
|---|---|---|---|
| `id` | string | auto if omitted | Stable unique id. Never reuse. |
| `title` | string | yes | Short name, under 80 chars. Must make sense without its parent. |
| `summary` | string | yes | 1-3 sentences stating what this node is and why it matters. This is what agents read first when building context, so keep it self-contained. |
| `status` | Status | defaults to `proposed` |  |
| `acceptance_criteria` | string |  | For WorkItem. Checkable conditions for "done". |
| `due` | date |  |  |
| `kind` | WorkKind | yes |  |

**Edges** (outgoing; pass target ids)

| slot | targets | cardinality | required | description |
|---|---|---|---|---|
| `owned_by` | Actor | one |  | Accountability. "This Actor answers for it." One owner per node. |
| `part_of` | WorkItem \| Project | one | yes | Containment. "This lives inside that." The tree skeleton: task part_of epic or initiative, epic part_of initiative, key_result part_of objective, person part_of team. Initiatives, objectives, documents and signals are part_of the Project. Exactly one parent per node. Every node except the Project has one, so every chain ends at the root. |
| `serves` | Goal | many |  | Purpose link. "This exists to achieve that goal." An initiative serves one or more objectives or key results; epics and tasks inherit it through part_of. |
| `depends_on` | WorkItem | many |  | Hard prerequisite. "Cannot finish or start until that is done." Use only for real blocking. If it is just related, use `about`. |

**`kind` values** (required)

- `initiative`: Bundle of work pursuing goals. Weeks to months. Must serve at least one goal. _(part_of → Project; requires serves)_
- `epic`: Optional chunk of an initiative, shippable on its own. Skip it for small initiatives. _(part_of → WorkItem:initiative)_
- `task`: Smallest unit one actor can finish. The default. _(part_of → WorkItem:epic or WorkItem:initiative)_
- `bug`: Something that was supposed to work and does not. _(part_of → WorkItem:epic or WorkItem:initiative)_

**Rules**

- An initiative must serve at least one goal. (when kind=initiative, serves required)

### Document

WHAT: A pointer plus summary for long-form content: specs, designs, guidelines, references. BRANCH: knowledge (what is true).

- **Branch:** knowledge
- **Use when:** Content must be consulted while working. Store the summary here and the body at `uri`. Link to the thing it covers with about.
- **Not for:** Observations or numbers (Signal).

**Properties**

| name | type | required | description |
|---|---|---|---|
| `id` | string | auto if omitted | Stable unique id. Never reuse. |
| `title` | string | yes | Short name, under 80 chars. Must make sense without its parent. |
| `summary` | string | yes | 1-3 sentences stating what this node is and why it matters. This is what agents read first when building context, so keep it self-contained. |
| `status` | Status | defaults to `proposed` |  |
| `uri` | string |  | For Document. Where the full content lives (path, URL, file id). The graph stores the summary, not the body. |
| `kind` | DocKind | yes |  |

**Edges** (outgoing; pass target ids)

| slot | targets | cardinality | required | description |
|---|---|---|---|---|
| `owned_by` | Actor | one |  | Accountability. "This Actor answers for it." One owner per node. |
| `part_of` | Project | one | yes | Containment. "This lives inside that." The tree skeleton: task part_of epic or initiative, epic part_of initiative, key_result part_of objective, person part_of team. Initiatives, objectives, documents and signals are part_of the Project. Exactly one parent per node. Every node except the Project has one, so every chain ends at the root. |
| `about` | WorkItem \| Goal \| Project | many |  | Subject link. "This talks about that." A spec Document about an initiative, a metric Signal about a key result, feedback about a task or the Project. |

**`kind` values** (required)

- `spec`: Defines what to build and how it must behave.
- `design`: Visual or structural design artifact (mockup, flow, architecture).
- `guideline`: Standing rule or convention (brand, code style, process).
- `reference`: Background material that does not prescribe anything.

### Signal

WHAT: An observation from the world: metric, feedback, research finding or experiment result. BRANCH: evidence (what we learned).

- **Branch:** evidence
- **Use when:** Capturing something observed rather than planned. Metrics track key results (link with about); record the number in value and the origin in source.
- **Not for:** Plans, requirements, or targets (use Goal).

**Properties**

| name | type | required | description |
|---|---|---|---|
| `id` | string | auto if omitted | Stable unique id. Never reuse. |
| `title` | string | yes | Short name, under 80 chars. Must make sense without its parent. |
| `summary` | string | yes | 1-3 sentences stating what this node is and why it matters. This is what agents read first when building context, so keep it self-contained. |
| `status` | Status | defaults to `proposed` |  |
| `observed_at` | datetime |  |  |
| `value` | string |  | For Signal. The measured value or the key quote or finding. |
| `source` | string |  | For Signal. Where it came from (tool, person, channel, link). |
| `kind` | SignalKind | yes |  |

**Edges** (outgoing; pass target ids)

| slot | targets | cardinality | required | description |
|---|---|---|---|---|
| `owned_by` | Actor | one |  | Accountability. "This Actor answers for it." One owner per node. |
| `part_of` | Project | one | yes | Containment. "This lives inside that." The tree skeleton: task part_of epic or initiative, epic part_of initiative, key_result part_of objective, person part_of team. Initiatives, objectives, documents and signals are part_of the Project. Exactly one parent per node. Every node except the Project has one, so every chain ends at the root. |
| `about` | Goal \| WorkItem \| Project | many |  | Subject link. "This talks about that." A spec Document about an initiative, a metric Signal about a key result, feedback about a task or the Project. |

**`kind` values** (required)

- `metric`: A measured number at a point in time. Link it to a key result with about.
- `feedback`: What a user, customer or stakeholder said.
- `research`: Synthesized finding from interviews, analysis or market study.
- `experiment`: Result of a test with a hypothesis and outcome.

### Actor

WHAT: Someone who can own or do work: person, team or agent. BRANCH: organization (who).

- **Branch:** organization
- **Use when:** Resolving ownership or access, routing a question, or attributing a change. Agents get their own Actor so their writes are traceable.
- **Not for:** Roles or skills as abstractions. Access levels live on the member_of edge.

**Properties**

| name | type | required | description |
|---|---|---|---|
| `id` | string | auto if omitted | Stable unique id. Never reuse. |
| `title` | string | yes | Short name, under 80 chars. Must make sense without its parent. |
| `summary` | string | yes | 1-3 sentences stating what this node is and why it matters. This is what agents read first when building context, so keep it self-contained. |
| `status` | Status | defaults to `proposed` |  |
| `handle` | string |  | For Actor. Email, username or agent id. |
| `kind` | ActorKind | yes |  |

**Edges** (outgoing; pass target ids)

| slot | targets | cardinality | required | description |
|---|---|---|---|---|
| `owned_by` | Actor | one |  | Accountability. "This Actor answers for it." One owner per node. |
| `part_of` | Actor | one |  | Containment. "This lives inside that." The tree skeleton: task part_of epic or initiative, epic part_of initiative, key_result part_of objective, person part_of team. Initiatives, objectives, documents and signals are part_of the Project. Exactly one parent per node. Every node except the Project has one, so every chain ends at the root. |
| `member_of` | Project | many |  | Access link. "This Actor works on that Project, with this role." Carries a required `role` property (admin, contributor or viewer). A team can be a member, so its people inherit the role through part_of. Different from owned_by: membership grants access, ownership assigns accountability. Edge properties: `role`: Role (required). |

**`kind` values** (required)

- `person`: A human.
- `team`: A group of actors. Members link to it with part_of.
- `agent`: An AI agent with its own identity, so its changes are attributable.

## Shared enums

### Role

Access level of an Actor on a Project. Stored as the `role` property of the member_of edge.

- `admin`: Manage members and structure, accept agent proposals, archive anything.
- `contributor`: Create and edit nodes. Default for agents, whose new nodes start as proposed.
- `viewer`: Read only.

### Status

Shared lifecycle for every node.

- `proposed`: Suggested (often by an agent), not yet accepted by a human.
- `active`: Currently valid or in progress.
- `done`: Completed or achieved.
- `archived`: No longer relevant. Kept for history, not used as context.
