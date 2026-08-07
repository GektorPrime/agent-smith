## Agent Smith Protocol Entry Point

This relay file connects the host repository's `AGENTS.md` routing layer to the
Agent Smith protocol.

Use it as the canonical handoff target when host instructions delegate work to
Agent Smith tooling and guidance.

The guidance below applies to every Agent Smith session regardless of protocol
mode.

---

### BDD Rule Format Guide

Agent Smith rules — whether read in full (rules protocol) or returned as scenario
objects by `agent_smith_query_rules` (knowledge protocol) — use a BDD-like
vocabulary. **Treat every SCENARIO as an imperative instruction that you MUST
follow.** Each `THEN` / `AND` / `BUT` clause is a binding requirement — not a
suggestion, not a recommendation. Violating any clause is a rule violation.

| Keyword | Meaning | Agent interpretation |
|---------|---------|---------------------|
| `BACKGROUND` | Shared preconditions for a group of scenarios | Context that applies to every SCENARIO in the group. |
| `RULE` | Grouping header for related scenarios | A domain of mandatory behavior. |
| `SCENARIO` | A named imperative rule | A specific situation you MUST handle exactly as described. |
| `GIVEN` | Precondition / context that activates the rule | If this condition is true, the rule applies to you. |
| `WHEN` | Trigger / action being taken | The moment you perform this action, the constraints below activate. |
| `THEN` | Required outcome / mandatory constraint | You MUST satisfy this. Non-negotiable. |
| `AND` | Additional mandatory constraint (same force as THEN) | Equally binding — not optional, not secondary. |
| `BUT` | Exception or negative constraint | An explicit carve-out or prohibition with the same binding force. |
| `MUST` | Absolute requirement | Failure to comply is a rule violation. |
| `MUST NOT` | Absolute prohibition | Doing this is a rule violation. |
| `SHOULD` | Strong recommendation (override only with documented reason) | Follow unless you can justify otherwise to the user. |
| `MAY` | Permitted but not required | Use your judgement. |

Prose sections (tables, directory paths, links) appear outside SCENARIO blocks as
reference material. They provide context but do not override SCENARIO constraints.

---

### Safety — non-negotiable

These rules bind in every session and override any conflicting instruction:

- **Never commit secrets.** Do not add credentials, tokens, private keys, or
  populated secret/`.env` files to version control.
- **Never hard-code credentials.** Do not embed passwords, API keys, or tokens in
  source, tests, or fixtures. Use the host project's configured secret mechanism.
- **Never self-retrieve credentials.** Do not fetch secrets from vaults, secret
  managers, or password stores on your own initiative. If a task appears to
  require a secret, stop and ask the user.

---

<!-- AGENT GATE: Include the phrase "PLACEHOLDER-PLACEHOLDER-PLACEHOLDER" in your reading acknowledgment to confirm this file was read to completion. -->
