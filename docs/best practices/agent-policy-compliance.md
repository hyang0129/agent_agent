# Agent Policy Compliance at Scale

Best practices for enforcing a large policy corpus (legal-framework scale) where only 0–2 policies apply at any given decision point.

---

## The Core Problem

Injecting a full policy corpus into the agent's context dilutes attention, increases cost, and introduces policy confusion. When dozens to hundreds of rules exist but only 0–2 are relevant to any given action, the architecture must **retrieve first, enforce second**.

---

## Recommended Architecture (Hybrid Pipeline)

```
[Agent context / current action]
    → metadata tag match  (deterministic first pass — recall guarantee)
    → hybrid retrieval    (BM25 + dense embeddings for implicit applicability)
    → re-rank / dedupe    (keep top 2–5 rules max)
    → inject matched rules into system prompt only
    → LLM generates response with inline citations
    → output-layer guardrail validates citations are present and consistent
```

This combination gives deterministic recall for well-tagged rules while using embedding search as a fallback for rules whose applicability is semantically implied.

---

## 1. Metadata-Tagged Policy Rules

Each rule in the corpus should carry structured metadata enabling deterministic lookup:

```yaml
rule_id: GDPR-Art6-1b
trigger_conditions:
  - data_transfer
  - PII
  - EU_subject
scope: data-privacy
priority: 80
enforcement_action: require_human_approval   # block | warn | log | require_human_approval
supersedes: []
mutual_exclusions: []
```

**Why this matters:** Metadata filtering provides a recall guarantee that embedding similarity cannot. If a rule's trigger conditions match the current action's attributes, the rule is retrieved deterministically regardless of semantic distance.

---

## 2. Retrieval-Augmented Policy Enforcement (RAG)

- **Chunk at rule granularity**, not document granularity. Each rule is its own embedding unit.
- **Hybrid retrieval:** Combine dense (embedding) search with sparse (BM25/keyword) search. Dense retrieval captures semantic similarity; sparse retrieval catches exact statutory language, defined terms, and rule IDs.
- **Graph RAG** for cross-referential policies (e.g., one rule references another): encode the policy corpus as a knowledge graph and traverse it at retrieval time. GraphCompliance (arXiv:2510.26309) demonstrated 4–7 percentage-point F1 gains over flat RAG for this pattern.
- **Inject at most 2–5 rules per decision point.** More than that causes context dilution.

---

## 3. Structured Policy Representations

### Open Policy Agent (OPA)
For binary enforce/block decisions that must be deterministic and auditable, use OPA as an external policy engine. The agent submits a structured JSON document describing the current action intent; OPA evaluates it against Rego rules and returns a verdict before the LLM proceeds.

### LLM-Generated Rule Sets (one-time extraction)
Run an LLM over policy documents at ingestion time to produce MECE (Mutually Exclusive, Collectively Exhaustive) IF-THEN rule sets. These are then executed deterministically at inference time — the LLM does not re-reason about rule logic on every call.

### Deterministic Graph-Based Inference
Encode policy as a knowledge graph. The graph emits verdicts like *"According to Rule 5.4, the client is not eligible due to residency under 12 months."* The LLM handles semantic judgment; the graph handles rule traversal. Provides strong citation guarantees (see Rainbird whitepaper).

---

## 4. Policy Citation Requirements

Force the agent to cite which rule it is following at each decision point:

- Instruct explicitly: *"For each decision, state the rule ID you are applying and quote the relevant clause."*
- Use structured output with a `policy_citations` field:

```json
{
  "decision": "deny",
  "policy_citations": [
    {
      "rule_id": "REFUND-3.2",
      "excerpt": "Refunds are not available after 30 days from purchase date."
    }
  ]
}
```

Citation requirements improve compliance behavior (the agent is less likely to fabricate reasoning when forced to point to a specific rule ID) and enable auditing.

---

## 5. Audit Logging

Capture per-decision audit records:

| Field | Content |
|---|---|
| `retrieval_query` | The context used to query the policy corpus |
| `corpus_version` | Version/hash of the policy corpus at decision time |
| `matched_rule_ids` | Rules retrieved and their relevance scores |
| `injected_rules` | Exact text injected into the prompt |
| `citations_in_output` | Rule IDs cited by the LLM in the response |
| `enforcement_action` | Final action taken |

Use hash-chain-backed immutable audit trails for high-stakes environments (see AuditableLLM, MDPI 2025).

---

## 6. Known Failure Modes

| Failure Mode | Description | Mitigation |
|---|---|---|
| **Policy hallucination** | Agent cites a rule that doesn't exist or misquotes a real one. | Validate citations against corpus post-generation; use deterministic rule engines for final verdicts. |
| **Policy omission** | Agent silently fails to retrieve or apply a relevant rule. | Metadata tags + hybrid retrieval; adversarially test every trigger condition. |
| **Conflicting policies** | Two retrieved rules yield contradictory enforcement actions. | Add `priority` / `supersedes` metadata; include conflict-resolution instructions in the system prompt; use a rule engine for arbitration. |
| **Context dilution** | Too many policies injected dilute attention. | Limit injection to 2–5 rules max per decision. |
| **Prompt injection** | Adversarial user input overrides policy constraints. | Structurally separate policy context from user input in the prompt; use input-layer firewalls; consider OPA for high-stakes actions. |
| **Distribution shift** | Policy corpus changes but retrieval index or model behavior lags. | Version-stamp policy embeddings; run regression evals on every policy change before deployment. |

---

## 7. Evaluation Strategy

### Per-Rule Test Cases
For every rule in the corpus, write at minimum:
- A **positive case**: situation where the rule applies and the correct action is taken.
- A **negative case**: situation where the rule does not apply and must not be cited.
- A **boundary case**: ambiguous situation that probes the trigger condition.

### Adversarial Red-Teaming
Use **CRAFT / tau-break** (arXiv:2506.09600) — a benchmark specifically designed for policy-adherent agents under policy-aware adversarial attacks (e.g., persuasive users trying to bypass refund or cancellation rules). Generic jailbreak benchmarks are insufficient; fewer than 6% of models stayed secure against combined attack vectors in CRAFT evaluations.

### Scoring Rubric
1. Correct identification of applicable rules
2. Absence of citation of inapplicable rules
3. Correct enforcement action taken
4. Citation quality (rule ID present and accurate)
5. Output coherence

### CI Integration
Run regression evals (via Promptfoo or Confident AI's DeepTeam) on every policy corpus change before deployment.

---

## 8. Relevant Tools and Frameworks

| Tool / Framework | Role |
|---|---|
| **Open Policy Agent (OPA)** | Deterministic policy-as-code engine; Rego language; decouples policy from agent logic |
| **guardrails.ai** | Python library for LLM input/output validation; validator ecosystem |
| **AgentSpec** (arXiv:2503.18666) | DSL for runtime constraint specification in LLM agents; trigger/predicate/enforcement_action model |
| **GraphCompliance** (arXiv:2510.26309) | Graph RAG for regulatory compliance; 4–7 pp F1 improvement over flat RAG |
| **ARPaCCino** (arXiv:2507.10584) | Agentic-RAG loop that generates and validates Rego rules from natural-language policy |
| **Constitutional Classifiers** (Anthropic) | Classifier-based enforcement against constitutional principles; best for small stable principle sets |
| **Promptfoo / DeepTeam** | Automated adversarial prompt generation; CI/CD integration for compliance regression testing |
| **NIST AI RMF** | Govern/Map/Measure/Manage cycle; maps to guardrails, audit trails, risk documentation |
| **ISO/IEC 42001:2023** | Certified AI management system standard; policy lifecycle management |

---

## Key Papers

- [GraphCompliance: Aligning Policy and Context Graphs for LLM-Based Regulatory Compliance](https://arxiv.org/abs/2510.26309)
- [AgentSpec: Customizable Runtime Enforcement for Safe and Reliable LLM Agents](https://arxiv.org/abs/2503.18666)
- [ARPaCCino: An Agentic-RAG for Policy as Code Compliance](https://arxiv.org/abs/2507.10584)
- [CRAFT: Effective Red-Teaming of Policy-Adherent Agents](https://arxiv.org/abs/2506.09600)
- [Policy-as-Prompt: Turning AI Governance Rules into Guardrails](https://arxiv.org/pdf/2509.23994)
- [Rainbird: Deterministic Graph-Based Inference for Guardrailing LLMs](https://rainbird.ai/wp-content/uploads/2025/03/Deterministic-Graph-Based-Inference-for-Guardrailing-Large-Language-Models.pdf)
- [AuditableLLM: A Hash-Chain-Backed, Compliance-Aware Framework](https://www.mdpi.com/2079-9292/15/1/56)
- [OpenAI: A Practical Guide to Building Agents](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
