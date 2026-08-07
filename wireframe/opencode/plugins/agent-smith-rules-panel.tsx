/** @jsxImportSource @opentui/solid */
import type { TuiPlugin, TuiPluginApi } from "@opencode-ai/plugin/tui"
import { createSignal, createEffect, onMount, onCleanup, Show, For } from "solid-js"
import { TextAttributes } from "@opentui/core"
import { join } from "node:path"
import { readFileSync, existsSync } from "node:fs"

const id = "agent-smith-rules"

interface RuleEntry {
  id: string
  severity: string
  section: string
  tags: string[]
}

interface RuleState {
  updatedAt: string
  queryHash: string
  ruleCount: number
  totalChars: number
  rules: RuleEntry[]
}

function loadState(sessionId: string): RuleState | null {
  try {
    if (!sessionId) return null
    const path = join(process.env.AGENT_SMITH_PROJECT_ROOT || process.cwd(), ".opencode", "plugins", "agent-smith-state", `${sessionId}.json`)
    if (!existsSync(path)) return null
    return JSON.parse(readFileSync(path, "utf-8")) as RuleState
  } catch {
    return null
  }
}

function timeAgo(isoDate: string): string {
  const ts = new Date(isoDate).getTime()
  if (!Number.isFinite(ts)) return "—"

  const diffMs = Date.now() - ts
  if (diffMs < 5_000) return "just now"

  const seconds = Math.floor(diffMs / 1000)
  if (seconds < 60) return `${seconds}s ago`

  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`

  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function severityBadge(severity: string): string {
  if (severity === "hard") return "🔴"
  if (severity === "soft") return "🟡"
  return "⚪"
}

function cleanSection(section: string): string {
  return section.replace(/^RULE:\s*/i, "")
}

const KV_KEY = "agent-smith-rules.expanded"

function View(props: { api: TuiPluginApi }) {
  const theme = () => props.api.theme.current
  const [state, setState] = createSignal<RuleState | null>(null)
  const [expanded, setExpanded] = createSignal(props.api.kv.get(KV_KEY, false))

  // Derive session ID from the active route — session_id slot prop is empty
  // when the sidebar renders outside an active session context.
  const sessionId = () => {
    const route = props.api.route.current
    return route.name === "session" ? route.params.sessionID : ""
  }

  function toggle() {
    const next = !expanded()
    setExpanded(next)
    props.api.kv.set(KV_KEY, next)
  }

  function refresh() {
    setState(loadState(sessionId()))
  }

  createEffect(() => {
    // Re-read state whenever the active session changes
    sessionId()
    refresh()
  })

  onMount(() => {
    refresh()
    const unsubscribe = props.api.event.on("session.idle", refresh)
    const timer = setInterval(refresh, 3000)
    onCleanup(() => {
      unsubscribe()
      clearInterval(timer)
    })
  })

  const title = () => {
    const s = state()
    return s ? `Agent Smith Rules (${s.ruleCount})` : "Agent Smith Rules"
  }

  const updatedLabel = () => {
    const s = state()
    return s ? timeAgo(s.updatedAt) : "—"
  }

  return (
    <box flexDirection="column">
      <box flexDirection="row" justifyContent="space-between" onMouseDown={toggle}>
        <box flexDirection="row" gap={1}>
          <text fg={theme().textMuted}>{expanded() ? "▼" : "▶"}</text>
          <text fg={theme().text} attributes={TextAttributes.BOLD}>{title()}</text>
        </box>
        <text fg={theme().textMuted}>{updatedLabel()}</text>
      </box>

      <Show when={expanded() && state()}>
        {(s) => (
          <For each={s().rules}>
            {(rule) => (
              <box flexDirection="column" paddingLeft={2}>
                <box flexDirection="row">
                  <text>{severityBadge(rule.severity)} </text>
                  <text fg={theme().accent}>{rule.id}</text>
                </box>
                <text fg={theme().textMuted} paddingLeft={3}>{cleanSection(rule.section)}</text>
              </box>
            )}
          </For>
        )}
      </Show>
    </box>
  )
}

const tui: TuiPlugin = async (api) => {
  api.slots.register({
    order: 200,
    slots: {
      sidebar_content: () => <View api={api} />,
    },
  })
}

export default { id, tui }
