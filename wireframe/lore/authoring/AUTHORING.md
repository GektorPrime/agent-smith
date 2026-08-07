# Agent Smith Knowledge Base — Scenario Authoring Guide

This guide is the instruction set for any agent converting rule files into scenario JSON.
Read it fully before processing any source file. Every decision below is mandatory.

---

## 1. What is one atomic scenario?

One scenario object represents **one constraint** — one indivisible rule that can be violated
or satisfied independently of every other rule.

- If a `SCENARIO` block contains five rules, it produces **five scenario objects**.
- If a sentence contains two constraints joined by "and", **split them into two objects**.
- A scenario is atomic when you cannot meaningfully violate half of it.

**Wrong (merged):**
> "The helper function must declare visibility and must be placed near related utilities."

**Right (split):**
> Scenario A: "The helper function must declare visibility explicitly."
> Scenario B: "The helper function must be placed near related utilities."

### When NOT to split

Not every AND clause in a source SCENARIO produces a separate scenario object. AND clauses
that are **dependent on or subordinate to** the THEN clause — meaning they only make sense
in the context of that THEN — stay together as `bdd.and[]` entries on the same scenario
object.

**Split** when the AND clause introduces a **new, independently violable constraint**:
> `THEN the helper function must declare visibility explicitly` ← one rule
> `AND helper functions must not be nested inside unrelated classes` ← different rule, split it

**Do NOT split** when the AND clause **elaborates, qualifies, or extends** the THEN:
> `THEN an item MUST be located by a known unique attribute` ← the rule
> `AND positional index access MUST NOT be used` ← same rule, negative form
> `AND the lookup value MUST be one the caller already knows` ← elaboration

The test: *"Can I violate this AND clause while fully satisfying the THEN clause?"*
- **Yes** → split into a separate scenario.
- **No** → keep as `bdd.and[]` on the same scenario.

Cross-reference scenarios (e.g. "Following coding conventions — assertion order
MUST be followed") that merely point back to rules already covered in another source file
do NOT produce separate scenario objects. The referenced rules already have their own
scenarios in the core corpus.

---

## 2. How to write `explanation`

The explanation answers: *why does this rule exist?* It is **rationale prose**, not a
restatement of `verbatim_rule`.

- State the **consequence of violation** — what breaks, what misleads, what risks arise.
- State the **design intent** — what property the rule protects or enforces.
- Minimum **two sentences**.
- Do not begin with "This rule..." or copy words from `verbatim_rule`.

**Wrong:**
> "The actual value must be on the left and the expected value on the right in assert statements."

**Right:**
> "Keeping the expression under test on the left and the expected literal on the right
> aligns with Python readability norms and Ruff SIM300 enforcement.
> Reversing the order introduces inconsistent style and noisy lint failures."

---

## 3. How to write `examples`

Both `examples.correct` and `examples.incorrect` must be:

- **Minimal** — one to three lines that isolate the constraint. No full class. No imports.
  No helper definitions.
- **Self-contained** — the snippet stands alone. Variable names are self-evident.
- **Syntactically valid** — the snippet must parse without errors.
- **Focused** — the only difference between correct and incorrect is the rule being tested.

Do not add comments inside snippets explaining what they demonstrate — the `explanation`
field does that.

**Good (correct):**
```python
assert category.name == 'Pants'
```

**Good (incorrect):**
```python
assert 'Pants' == category.name
```

---

## 4. `severity: hard` vs `soft`

| Value | Meaning                                                                                              | 
|-------|------------------------------------------------------------------------------------------------------|
| `hard` | Zero tolerance. Failure is always wrong.                                                             |
| `soft` | Judgment call, style preference, or context-dependent. Cannot be mechanically verified in all cases. |

When uncertain, prefer `hard` if the rule says MUST and a programmatic check could catch it;
prefer `soft` if the rule says SHOULD or the check requires semantic context.

---

## 5. Tag vocabulary

Every scenario **must** include at least one source tag and at least one topic tag.

### Source tags (pick exactly one per scenario)

| Tag | Use when |
|-----|----------|
| `core` | Rule applies broadly across project types (general style, assertions, structure, isolation). |
| `test_type:<domain>` | Rule is specific to one host-defined test domain (e.g. `test_type:api-public`). |

Test-type or domain tags are host-specific. Hosts define their own vocabulary in
`lore/authoring/tags.md`.

### Topic tags (include all that apply)

[Topic tags](./tags.md)

---

## 6. `mcp_tool_hint` values

Assign the read-only AST tool that best helps **inspect the structure this rule
constrains**. Use `null` when no tool is applicable. Valid values:

| Value | Use when the rule concerns |
|-------|-----------------------------|
| `agent_smith_ast_analyze_structure` | a file's overall shape — classes, functions, methods, imports |
| `agent_smith_ast_class_outline` | the members of a specific class |
| `agent_smith_ast_list_imports` | imports or dependency usage |
| `agent_smith_ast_find_definitions` | the existence or location of a named symbol |
| `agent_smith_ast_search` | a structural code pattern expressible as an ast-grep pattern |
| `null` | no programmatic structural check is possible |

These tools report structure; they do not by themselves pass or fail a rule. The
hint tells a reviewer which tool surfaces the evidence for judging compliance.

---

## 7. Exemplar scenarios vs rule scenarios

Exemplars use `"type": "exemplar"`. They demonstrate correct patterns holistically, not
individual constraints.

- `bdd.given` — the situation in which the exemplar is needed (e.g. "A new public API CRUD test class is being written").
- `bdd.when` — when the agent would consult this (e.g. "The agent needs a complete correct reference implementation").
- `bdd.then` — the instruction (e.g. "Use this class as the structural template").
- `verbatim_rule` — always `"Reference exemplar — not a rule constraint"`.
- `examples.correct` — full class definition with annotations and one representative method.
- `examples.incorrect` — always `null`.
- `severity` — always `"soft"`.
- Tags — always include `"exemplar"` plus the applicable `test_type:*` tag and all topic tags demonstrated.

**Never include** in exemplar `examples.correct`: real runtime data values, account credentials,
helper internals, or implementation details beyond class structure.

---

## 7b. Reference scenarios

References use `"type": "reference"`. They encode **factual knowledge** — directory layouts,
file locations, architectural decisions, configuration structures — not constraints.
They exist so the retrieval system can answer "where does X go?" and "what is the structure
of Y?" without needing the original markdown file.

- `bdd.given` — the information need (e.g. "An agent needs to place a new test file").
- `bdd.when` — when this knowledge is consulted (e.g. "The agent determines the file location").
- `bdd.then` — the factual answer (e.g. "Public API tests live in `tests/<suite>/public_api/`").
- `bdd.and` — additional facts in the same knowledge cluster.
- `verbatim_rule` — a factual statement summarizing the reference content. Not a constraint.
  For the general map reference, this is `"Repository directory structure — full annotated tree"`.
- `examples.correct` — a representative path or structure snippet. For the general map,
  this is the full annotated directory tree.
- `examples.incorrect` — always `null`.
- `severity` — always `"soft"`.
- Tags — always include `"reference"` plus `"core"` and relevant topic tags.
- `mcp_tool_hint` — always `null`.

### General vs granular references

A source file like repository map produces:
1. **One general reference** — contains the full map in `examples.correct`. This is the
   "return everything" object for broad orientation queries.
2. **Granular references** — one per major directory or architectural boundary. These are
   the objects retrieved for specific "where does X go?" queries.

The general reference has a higher token cost but is the correct retrieval target when
the agent needs the full picture. Granular references are cheaper and more targeted.

---

## 8. BDD structure — full clause mapping

The `bdd` object in each scenario must mirror the BDD structure of the source SCENARIO block.
Every clause type maps to a specific JSON field:

| Source keyword | JSON field | Type | Required | Description |
|---|---|---|---|---|
| `GIVEN` | `bdd.given` | string | yes | Precondition that activates the scenario. |
| `WHEN` | `bdd.when` | string | yes | Trigger or action that causes the constraint to apply. |
| `THEN` | `bdd.then` | string | yes | Primary mandatory constraint (the most central rule). |
| `AND` | `bdd.and` | string[] | no | Additional mandatory constraints with the same binding force as THEN. |
| `BUT` | `bdd.but` | string[] | no | Exceptions or negative constraints — explicit carve-outs or prohibitions. |

**How to extract:**

1. `bdd.given` — the GIVEN clause text, stripped of the keyword prefix.
2. `bdd.when` — the WHEN clause text, stripped of the keyword prefix.
3. `bdd.then` — the FIRST THEN clause text, stripped of the keyword prefix.
4. `bdd.and` — an array of ALL AND clause texts (in source order), each stripped of the prefix.
   Omit the field entirely if there are no AND clauses.
5. `bdd.but` — an array of ALL BUT clause texts (in source order), each stripped of the prefix.
   Omit the field entirely if there are no BUT clauses.

**Example source:**
```
GIVEN a source file in the repository
WHEN the agent writes or modifies code in that file
THEN a public function MUST declare an explicit return type
AND helpers that are not part of the public surface MUST instead be:
    a) a method on the appropriate object
    b) a module-level function in a shared utilities module
    c) a local function within the calling scope
```

**Resulting JSON:**
```json
{
  "bdd": {
    "given": "a source file in the repository",
    "when": "the agent writes or modifies code in that file",
    "then": "a public function MUST declare an explicit return type",
    "and": [
      "helpers that are not part of the public surface MUST instead be: a) a method on the appropriate object, b) a module-level function in a shared utilities module, c) a local function within the calling scope"
    ]
  }
}
```

Multi-line AND/BUT clauses (those that continue with indentation on the next line) must be
joined into a single string with their continuation text.

---

## 9. `id` naming convention

Format: `<source-slug>-<section-slug>-<NNN>`

**Example:**
- `<source-slug>` — abbreviated identifier for the source file, following the
  pattern `<rule-topic>.md → <short-slug>`:
  - `coding-convention.md` → `conv`
  - `repo-map.md` → `map`
  - `tech-stack.md` → `stack`
  - `environments.md` → `env`
  - `requirements.md` → `req`

  Hosts define slugs for their own rule files; keep them short, unique, and stable.
- `<section-slug>` — kebab-case of the `##` section heading (abbreviated if long).
- `<NNN>` — zero-padded three-digit sequence number within the file, starting at `001`.

IDs must be unique across all scenario files in the corpus.

---

## 9. Output file naming convention

One file per `##` section in the source rule file.

Filename: `<section-slug>.json` — kebab-case of the `##` heading, lowercased, spaces replaced
with hyphens, special characters stripped.

Example: Section `## RULE: Assertions` → filename `rule-assertions.json`.

---

## 10. Output structure

Each output file is a **JSON array** of scenario objects, all conforming to `schema.json`.

```json
[
  {
    "id": "conv-assert-001",
    "type": "rule",
    ...
  },
  {
    "id": "conv-assert-002",
    "type": "rule",
    ...
  }
]
```

Validate each file against `schema.json` before committing.

---

## Gate markers (automatic)

Rule markdown files placed in `.agent-smith/lore/rules_md/` receive an `AGENT GATE:`
marker automatically on next sync. The gate token is rotated on every
`agent_smith_init` call and is git-tracked. Running `agent-smith-rollback-gates`
reverts token-only changes.
