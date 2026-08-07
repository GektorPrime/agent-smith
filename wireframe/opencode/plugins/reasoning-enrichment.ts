/**
 * Agent Smith Reasoning-Enrichment Plugin
 *
 * HOST CONFIGURATION
 * Two optional settings can customise enrichment after install:
 *
 * 1. AGENT_SMITH_PROJECT_PATH_MARKER — set this environment variable to the
 *    path prefix that identifies relevant files (e.g. "src/", "app/",
 *    "lib/"). It defaults to the OpenCode worktree.
 *
 * 2. KEYWORDS — list of strings that trigger KB enrichment when found in
 *    agent reasoning or user prompts. Replace the generic defaults with
 *    terms specific to your project, framework, and conventions.
 *
 * Injects relevant rules from the vector knowledge base into the
 * agent's context by monitoring the agent's reasoning/thinking process.
 *
 * Architecture:
 * - event (message.part.updated + message.part.delta) — accumulates agent
 *   output into a sliding buffer. For models that emit reasoning parts
 *   (extended thinking), only reasoning text is captured. For models that
 *   don't, all text deltas are captured. Project relevance is detected via
 *   keyword matching, then a debounced KB query fires with the buffer
 *   content as a semantic search query.
 * - chat.message — pre-fetches rules when user prompt mentions project
 *   paths/keywords (supplementary, toggleable).
 * - tool.execute.after (read/glob/grep) — fallback: detects project file
 *   access and queries KB with path-derived tokens + inferred tags
 *   (toggleable).
 * - experimental.chat.system.transform — injects stored rules into the
 *   system prompt so the LLM sees them before generating responses.
 * - experimental.session.compacting — injects active rule context into
 *   the compaction summary so rules persist across context resets.
 *
 * Key design principles:
 * - ZERO blocking: no throws, no interrupted tool calls.
 * - Intent-aware: queries are derived from what the agent is thinking
 *   about, not just which files it touched.
 * - Non-adversarial: rules appear passively in the system prompt on
 *   the next LLM turn.
 */

import type { Plugin, PluginInput } from "@opencode-ai/plugin"
import { resolve } from "node:path"
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { createHash } from "node:crypto"

// ---------------------------------------------------------------------------
// Configurable Constants
// ---------------------------------------------------------------------------

/** Max chars of reasoning to keep in the sliding buffer. */
const REASONING_BUFFER_SIZE = 4000

/** Ms to wait after last reasoning update before firing KB query. */
const REASONING_QUERY_DEBOUNCE_MS = 5000

/** Enable file-read fallback enrichment (tool.execute.after). */
const READ_SIDE_ENABLED = true

/** Enable user-prompt pre-fetch enrichment (chat.message). */
const CHAT_MESSAGE_PREFETCH = true

/** Max scenarios per KB query. Increase for broader coverage, decrease to save tokens. */
const K = 15

/** Tools monitored for read-side fallback. */
const READ_TOOLS = new Set(["read", "glob", "grep"])

/** Keywords that trigger KB enrichment when found in agent reasoning or user prompts.
 *  Replace with terms specific to your project and conventions. */
const KEYWORDS: string[] = [
  'agent_smith',
  'rules',
  'convention',
  'best practice',
  'coding standard',
  'review',
  'refactor',
  'test',
  'implementation',
]

// ---------------------------------------------------------------------------
// State (per plugin lifetime = per opencode session)
// ---------------------------------------------------------------------------

/** Sliding buffer of recent reasoning text. */
let reasoningBuffer = ""

/** Formatted rules for system prompt injection. */
let ruleContext = ""

/** Hash of last query sent to KB (for deduplication). */
let lastQueryHash = ""

/** File sets already queried via read-side fallback. */
const queriedFileSets = new Set<string>()

/** Debounce timer for reasoning-based KB queries. */
let queryTimer: ReturnType<typeof setTimeout> | null = null

/** Last known active session ID (set by hooks that carry sessionID). */
let activeSessionID = ""

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Compute a short hash for deduplication.
 */
function hashQuery(text: string): string {
  return createHash("md5").update(text).digest("hex").slice(0, 12)
}

/**
 * Check if text contains project-relevant keywords.
 */
function isProjectRelevant(text: string): boolean {
  const lower = text.toLowerCase()
  return KEYWORDS.some(kw => lower.includes(kw.toLowerCase()))
}

/**
 * Extract a semantic query from the reasoning buffer.
 * Takes the most recent portion and extracts meaningful content.
 */
function extractQueryFromReasoning(buffer: string): string {
  // Use the last ~1500 chars (fits within embedding model token limit)
  const recent = buffer.slice(-1500).trim()
  if (!recent) return ""

  // Remove common filler phrases that don't help vector search
  const cleaned = recent
    .replace(/\b(let me|I need to|I should|I will|I'll|let's|okay|alright)\b/gi, "")
    .replace(/\s{2,}/g, " ")
    .trim()

  return cleaned || recent
}

/**
 * Build a query from file path(s) for read-side fallback.
 */
function buildPathQuery(paths: string[], projectPathMarker: string): string {
  const tokens: string[] = []
  for (const p of paths.slice(0, 5)) {
    const idx = p.indexOf(projectPathMarker)
    if (idx >= 0) {
      const rel = p.slice(idx + projectPathMarker.length)
      const parts = rel.split(/[/_.]/).filter(t =>
        t.length > 2 && !["json", "test", "src", "lib", "app"].includes(t)
      )
      tokens.push(...parts)
    }
  }
  if (tokens.length === 0) return "writing project code"
  return [...new Set(tokens)].slice(0, 10).join(" ")
}

/**
 * Infer tags from path(s).
 */
function inferTags(_paths: string[]): string[] | undefined {
  // Host-specific tag inference. Populate based on your project structure.
  // e.g.
  // const tags = new Set<string>()
  // for (const p of paths) {
  //   if (p.includes("/public_api/") || p.includes("public_api")) tags.add("test_type:api-public")
  //   if (p.includes("/internal_api/") || p.includes("internal_api")) tags.add("test_type:api-admin")
  //   if (p.includes("/ui_") || p.includes("uilocal")) tags.add("test_type:ui")
  //   if (p.includes("/conftest") || p.includes("/fixture")) tags.add("fixture")
  //   if (p.includes("/infrastructure/")) tags.add("infrastructure")
  // }
  // return tags.size > 0 ? [...tags] : undefined
  return undefined
}

/**
 * Extract project file paths from read tool args and output.
 */
function extractReadProjectPaths(
  tool: string,
  args: Record<string, unknown>,
  output: string,
  projectPathMarker: string,
  agentSmithHome: string,
): string[] {
  const paths: string[] = []

  if (args?.filePath && typeof args.filePath === "string") paths.push(args.filePath)
  if (args?.path && typeof args.path === "string") paths.push(args.path)
  if (args?.pattern && typeof args.pattern === "string") paths.push(args.pattern)

  if (tool === "glob" || tool === "grep") {
    for (const line of output.split("\n")) {
      const match = line.match(/^(\/[^\s:]+)/)
      if (match) paths.push(match[1])
    }
  }

  return [...new Set(paths)]
    .filter(p => p.includes(projectPathMarker) && !p.includes(`${agentSmithHome}/`))
}

interface RuntimeConfig {
  version: string
  package: string
  protocol: "rules" | "knowledge"
}

function loadRuntimeConfig(worktree: string): RuntimeConfig | null {
  try {
    const runtimePath = resolve(
      worktree,
      ".agent-smith",
      "knowledge_base",
      "runtime.json",
    )
    const runtime = JSON.parse(readFileSync(runtimePath, "utf-8")) as Partial<RuntimeConfig>
    if (
      typeof runtime.version !== "string" ||
      typeof runtime.package !== "string" ||
      (runtime.protocol !== "rules" && runtime.protocol !== "knowledge")
    ) return null
    return runtime as RuntimeConfig
  } catch {
    return null
  }
}

function buildQueryCommand(
  runtime: RuntimeConfig,
  query: string,
  tags?: string[],
): string[] {
  const command = [
    "--from",
    runtime.package,
    "agent-smith-kb-query",
    query,
    "--k",
    String(K),
  ]
  if (tags && tags.length > 0) command.push("--tags", tags.join(","))
  return command
}

/**
 * Query the KB via subprocess.
 */
async function queryKB(
  $: PluginInput["$"],
  worktree: string,
  query: string,
  tags?: string[],
): Promise<{ output: string; error?: string }> {
  const runtime = loadRuntimeConfig(worktree)
  if (!runtime) return { output: "", error: "runtime.json is missing or invalid" }
  if (runtime.protocol !== "knowledge") return { output: "" }

  const db = resolve(worktree, ".agent-smith", "knowledge_base", "rules.db")
  if (!existsSync(db)) return { output: "", error: `rules.db not found at ${db}` }

  try {
    const args = buildQueryCommand(runtime, query, tags)
    const inheritedEnvironment = Object.fromEntries(
      Object.entries(process.env).filter(
        (entry): entry is [string, string] => typeof entry[1] === "string",
      ),
    )
    const result = await $`uvx ${args}`
      .env({
        ...inheritedEnvironment,
        AGENT_SMITH_HOME: resolve(worktree, ".agent-smith"),
        AGENT_SMITH_PROJECT_ROOT: worktree,
        AGENT_SMITH_PROTOCOL: runtime.protocol,
      })
      .cwd(worktree)
      .quiet()
      .nothrow()
    const output = result.text().trim()
    if (result.exitCode !== 0) {
      return {
        output: "",
        error: result.stderr.toString().trim() || output || `exit ${result.exitCode}`,
      }
    }
    return { output }
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : String(error),
    }
  }
}

/**
 * Format KB results into a rules block for system prompt injection.
 */
function formatRules(jsonResult: string): string {
  if (!jsonResult) return ""

  try {
    const scenarios = JSON.parse(jsonResult)
    if (!Array.isArray(scenarios) || scenarios.length === 0) return ""

    const seen = new Set<string>()
    const blocks: string[] = []

    for (const s of scenarios) {
      const key = s.id || s.verbatim_rule
      if (seen.has(key)) continue
      seen.add(key)

      const lines: string[] = []
      lines.push(`• [${s.severity}] ${s.section}`)
      lines.push(`  id: ${s.id} | source: ${s.source_file}`)
      if (s.tags?.length) lines.push(`  tags: ${s.tags.join(", ")}`)
      if (s.bdd?.given) lines.push(`  GIVEN ${s.bdd.given}`)
      if (s.bdd?.when)  lines.push(`  WHEN  ${s.bdd.when}`)
      if (s.bdd?.then)  lines.push(`  THEN  ${s.bdd.then}`)
      if (s.bdd?.and) {
        for (const a of s.bdd.and) {
          lines.push(`  AND   ${a}`)
        }
      }
      if (s.bdd?.but) {
        const buts = Array.isArray(s.bdd.but) ? s.bdd.but : [s.bdd.but]
        for (const b of buts) {
          lines.push(`  BUT   ${b}`)
        }
      }
      if (s.explanation) lines.push(`  Why: ${s.explanation}`)
      if (s.examples?.correct) {
        lines.push(`  correct:`)
        for (const l of s.examples.correct.split("\n")) {
          lines.push(`      ${l}`)
        }
      }
      if (s.examples?.incorrect) {
        lines.push(`  incorrect:`)
        for (const l of s.examples.incorrect.split("\n")) {
          lines.push(`      ${l}`)
        }
      }
      if (s.mcp_tool_hint) lines.push(`  verify with: ${s.mcp_tool_hint}`)

      blocks.push(lines.join("\n"))
    }

    return blocks.join("\n\n")
  } catch {
    return ""
  }
}

/**
 * Persist lightweight rule metadata for UI sidebar consumption.
 */
function writeStateFile(worktree: string, jsonResult: string): void {
  try {
    if (!activeSessionID) return
    const scenarios = JSON.parse(jsonResult)
    if (!Array.isArray(scenarios)) return

    const stateDir = resolve(process.env.AGENT_SMITH_PROJECT_ROOT || worktree, ".opencode", "plugins", "agent-smith-state")
    if (!existsSync(stateDir)) {
      mkdirSync(stateDir, { recursive: true })
    }

    const statePath = resolve(stateDir, `${activeSessionID}.json`)
    const rules = scenarios.map(s => ({
      id: s?.id,
      severity: s?.severity,
      section: s?.section,
      tags: Array.isArray(s?.tags) ? s.tags : [],
    }))

    const payload = {
      updatedAt: new Date().toISOString(),
      queryHash: lastQueryHash,
      ruleCount: rules.length,
      totalChars: ruleContext.length,
      rules,
    }

    writeFileSync(statePath, `${JSON.stringify(payload, null, 2)}\n`, "utf-8")
  } catch {
    // Fire-and-forget: never block plugin flow on state write failures.
  }
}

/**
 * Core query logic: query KB, deduplicate, update ruleContext.
 * Returns true if new rules were loaded.
 */
async function performQuery(
  $: PluginInput["$"],
  worktree: string,
  query: string,
  tags?: string[],
  logFn?: (msg: string) => Promise<void>,
): Promise<boolean> {
  const qHash = hashQuery(query + (tags?.join(",") || ""))
  if (qHash === lastQueryHash) return false

  const result = await queryKB($, worktree, query, tags)
  if (result.error) {
    if (logFn) await logFn(`KB query failed: ${result.error}`)
    return false
  }
  const rawResult = result.output
  const rules = formatRules(rawResult)

  if (rules) {
    ruleContext = rules
    lastQueryHash = qHash
    writeStateFile(worktree, rawResult)
    if (logFn) await logFn(`Rules loaded (${rules.length} chars), query hash: ${qHash}`)
    return true
  }
  if (logFn) await logFn("KB query returned no rules")
  return false
}

// ---------------------------------------------------------------------------
// Plugin export
// ---------------------------------------------------------------------------

export const ReasoningEnrichmentPlugin: Plugin = async ({ $, directory, worktree, client }) => {
  const projectRoot = directory || worktree
  const agentSmithHome = resolve(projectRoot, ".agent-smith")
  const projectPathMarker = process.env.AGENT_SMITH_PROJECT_PATH_MARKER || projectRoot
  const log = async (msg: string) => {
    await client.app.log({
      body: { service: "reasoning-enrichment", level: "info", message: msg },
    })
  }

  await log(`Plugin initialized for ${projectRoot}`)

  return {
    // ----- REASONING MONITORING -----

    event: async ({ event }) => {
      const eventType = event.type as string

      // Capture session ID from events that carry it
      if (eventType === "session.idle" || eventType === "session.status") {
        const props = (event as Record<string, unknown>).properties as Record<string, unknown> | undefined
        const sid = props?.sessionID as string | undefined
        if (sid) activeSessionID = sid
        return
      }

      if (eventType === "message.part.updated") {
        // Full part snapshot — check for reasoning type
        const props = (event as Record<string, unknown>).properties as Record<string, unknown> | undefined
        const part = props?.part as Record<string, unknown> | undefined
        if (!part) return

        if (part.type === "reasoning") {
          const text = (part.text as string) || ""
          if (!text) return
          reasoningBuffer = (reasoningBuffer + text).slice(-REASONING_BUFFER_SIZE)
        } else {
          // Not a reasoning part — skip buffer accumulation
          return
        }
      } else if (eventType === "message.part.delta") {
        // Streaming text delta — accumulate for relevance detection.
        // Delta events don't carry part type, so we accumulate all text
        // deltas and rely on keyword detection to filter.
        const props = (event as Record<string, unknown>).properties as Record<string, unknown> | undefined
        const field = props?.field as string | undefined
        const delta = props?.delta as string | undefined
        if (field !== "text" || !delta) return
        reasoningBuffer = (reasoningBuffer + delta).slice(-REASONING_BUFFER_SIZE)
      } else {
        return
      }

      // Check if the accumulated buffer is project-relevant
      if (!isProjectRelevant(reasoningBuffer)) return

      // Debounce: reset timer on each update, fire after pause
      if (queryTimer) clearTimeout(queryTimer)
      queryTimer = setTimeout(async () => {
        queryTimer = null
        const query = extractQueryFromReasoning(reasoningBuffer)
        if (!query) return

        // Infer tags from any file paths mentioned in reasoning
        const pathMatches = reasoningBuffer.match(
          new RegExp(projectPathMarker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[^\\s\'\"`,)}\\]]+", "g")
        )
        const tags = pathMatches ? inferTags(pathMatches) : undefined

        await performQuery($, projectRoot, query, tags, log)
      }, REASONING_QUERY_DEBOUNCE_MS)
    },

    // ----- USER PROMPT PRE-FETCH -----

    "chat.message": async (
      input: { sessionID: string; agent?: string },
      output: { message: unknown; parts: Array<{ type: string; text?: string }> },
    ) => {
      if (!CHAT_MESSAGE_PREFETCH) return
      if (input.sessionID) activeSessionID = input.sessionID

      // Extract text from user message parts
      const userText = output.parts
        .filter(p => p.type === "text" && p.text)
        .map(p => p.text!)
        .join(" ")

      if (!userText || !isProjectRelevant(userText)) return

      // Extract file paths from user message for tag inference
      const pathMatches = userText.match(
        new RegExp(projectPathMarker.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[^\\s\'\"`,)}\\]]+", "g")
      )
      const tags = pathMatches ? inferTags(pathMatches) : undefined

      // Use user text directly as query (truncate for embedding)
      const query = userText.slice(0, 1500)
      await performQuery($, projectRoot, query, tags, log)
    },

    // ----- READ-SIDE FALLBACK -----

    "tool.execute.after": async (
      input: { tool: string; sessionID: string; callID: string; args: Record<string, unknown> },
      output: { title: string; output: string; metadata: unknown },
    ) => {
      if (!READ_SIDE_ENABLED) return
      if (!READ_TOOLS.has(input.tool)) return
      if (input.sessionID) activeSessionID = input.sessionID
      const projectPaths = extractReadProjectPaths(
        input.tool,
        input.args,
        output.output,
        projectPathMarker,
        agentSmithHome,
      )
      if (projectPaths.length === 0) return

      // Dedup: skip if this exact file set was already queried
      const fileSetKey = projectPaths.sort().join("|")
      if (queriedFileSets.has(fileSetKey)) return
      queriedFileSets.add(fileSetKey)

      const query = buildPathQuery(projectPaths, projectPathMarker)
      const tags = inferTags(projectPaths)
      await performQuery($, projectRoot, query, tags, log)
    },

    // ----- SYSTEM PROMPT INJECTION -----

    "experimental.chat.system.transform": async (
      _input: { sessionID?: string; model: unknown },
      output: { system: string[] },
    ) => {
      if (!ruleContext) return
      output.system.push(
        `## Agent Smith: Active Rules (from Knowledge Base)\n\n` +
        `The following rules are relevant to the current session.\n` +
        `Apply them when generating, reviewing, or planning code.\n\n` +
        `${ruleContext}`
      )
    },

    // ----- COMPACTION PERSISTENCE -----

    "experimental.session.compacting": async (
      _input: { sessionID: string },
      output: { context: string[]; prompt?: string },
    ) => {
      if (!ruleContext) return
      output.context.push(
        `## Agent Smith: Active Rules\n\n${ruleContext}\n\n` +
        `These rules apply to all operations in this session.`
      )
    },
  }
}
