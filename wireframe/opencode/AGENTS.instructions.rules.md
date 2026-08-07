# Agent Instructions — Agent Smith Rules Protocol

This file applies when `AGENT_SMITH_PROTOCOL=rules`.

At the start of the session:

1. Call `agent_smith_init`.
2. Call `agent_smith_read_entry_point` and `agent_smith_read_persona`.
3. Call `agent_smith_read_routing_table`, then call `agent_smith_select_rules`
   once with the relative paths from every routing-table row that matches the
   task (empty list if nothing matches or you are unsure — this reads all rules).
4. Call `agent_smith_read_rules` repeatedly. It serves one rule file per call
   and tells you when to call again. Keep going until it returns the `DONE`
   marker.
5. Post the required acknowledgment (`<filename>: <gate-phrase>` per file read).
6. In the same response, call `agent_smith_handoff` and pass every gate phrase
   in `acknowledged_tokens`.
7. Follow the handoff message exactly. It is the authoritative directive for
   your next action.

Operational notes:

- Do not skip mandatory reads. The server drives the read chain; you MUST call
  `agent_smith_read_rules` until it returns `DONE`.
- Handoff is refused until every mandated file was served and every gate phrase
  is echoed back in `acknowledged_tokens`.
- If a required file is unavailable, stop and report the failure clearly.
- If handoff instructions conflict with background assumptions, handoff wins.
