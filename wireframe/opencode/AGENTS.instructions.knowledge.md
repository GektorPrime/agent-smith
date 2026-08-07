# Agent Instructions — Agent Smith Knowledge Protocol

This file applies when `AGENT_SMITH_PROTOCOL=knowledge`.

At the start of the session:

1. Call `agent_smith_init`.
2. Call `agent_smith_handoff` and follow the directive.

During execution:

- Use `agent_smith_query_rules` on demand before writes/edits when rule context
  is needed.
- Apply returned scenarios as binding constraints according to their
  `given/when/then/and/but` structure and severity.
- Re-query whenever task scope changes materially.

Fallback behavior:

- If the knowledge base is unavailable, follow the protocol branch returned by
  `agent_smith_init`/`agent_smith_handoff`.
