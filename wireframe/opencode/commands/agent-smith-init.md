---
description: Initialize Agent Smith protocol
---

1. Call the `agent_smith_init` MCP tool now.
2. Read the entry point, persona, and routing table, then call
   `agent_smith_select_rules` with the matching rule paths (empty list to read
   all).
3. Call `agent_smith_read_rules` repeatedly until it returns the `DONE` marker.
4. Post the acknowledgment in the same response as a call to
   `agent_smith_handoff`, passing every gate phrase in `acknowledged_tokens`.
