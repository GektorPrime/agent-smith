import { afterEach, describe, test } from "node:test"
import assert from "node:assert/strict"
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { tmpdir } from "node:os"

import {
  ReasoningEnrichmentPlugin,
} from "../../wireframe/opencode/plugins/reasoning-enrichment.ts"
import * as pluginModule from "../../wireframe/opencode/plugins/reasoning-enrichment.ts"

const roots: string[] = []

function tempWorktree(): string {
  const root = join(
    tmpdir(),
    `agent-smith-plugin-${process.pid}-${Date.now()}-${roots.length}`,
  )
  mkdirSync(join(root, ".agent-smith", "knowledge_base"), { recursive: true })
  roots.push(root)
  return root
}

afterEach(() => {
  while (roots.length) rmSync(roots.pop()!, { recursive: true, force: true })
})

test("exports only one callable plugin factory", () => {
  const callableExports = Object.entries(pluginModule)
    .filter(([, value]) => typeof value === "function")
    .map(([name]) => name)
  assert.deepEqual(callableExports, ["ReasoningEnrichmentPlugin"])
})

test("prompt enrichment injects queried rules and writes session state", async () => {
  const worktree = tempWorktree()
  const runtime = {
    version: "1.0.0",
    package: "git+https://example.test/agent_smith.git@1.0.0",
    protocol: "knowledge" as const,
  }
  writeFileSync(
    join(worktree, ".agent-smith", "knowledge_base", "runtime.json"),
    JSON.stringify(runtime),
  )
  writeFileSync(
    join(worktree, ".agent-smith", "knowledge_base", "rules.db"),
    "fixture",
  )

  const scenario = {
    id: "testing-rule-001",
    severity: "hard",
    section: "Testing",
    source_file: "lore/rules_md/testing.md",
    tags: ["tests"],
    bdd: {
      given: "a code change",
      when: "verification runs",
      then: "the relevant tests pass",
    },
    explanation: "Verification prevents regressions.",
  }
  let command: string[] | undefined
  let environment: Record<string, string> | undefined
  const shell = (strings: TemplateStringsArray, value: string[]) => {
    assert.equal(strings.length, 2)
    command = value
    return {
      env(value: Record<string, string>) {
        environment = value
        return this
      },
      cwd() { return this },
      quiet() { return this },
      nothrow() {
        return Promise.resolve({
          exitCode: 0,
          stderr: Buffer.from(""),
          text() { return JSON.stringify([scenario]) },
        })
      },
    }
  }
  const logs: string[] = []
  const hooks = await ReasoningEnrichmentPlugin({
    $: shell,
    directory: worktree,
    worktree,
    client: {
      app: {
        log: async ({ body }: { body: { message: string } }) => {
          logs.push(body.message)
        },
      },
    },
  } as never)

  await hooks!["chat.message"]!(
    { sessionID: "session-1" },
    {
      message: {},
      parts: [{ type: "text", text: "Review the test implementation" }],
    },
  )

  assert.equal(command?.[0], "--from")
  assert.equal(command?.includes("agent-smith-kb-query"), true)
  assert.equal(environment?.PATH, process.env.PATH)
  assert.equal(environment?.AGENT_SMITH_PROJECT_ROOT, worktree)
  assert.equal(logs.some(message => message.startsWith("Rules loaded")), true)

  const output = { system: [] as string[] }
  await hooks!["experimental.chat.system.transform"]!(
    { sessionID: "session-1", model: {} },
    output,
  )
  assert.equal(output.system.length, 1)
  assert.match(output.system[0], /testing-rule-001/)
  assert.match(output.system[0], /THEN  the relevant tests pass/)

  const statePath = join(
    worktree,
    ".opencode",
    "plugins",
    "agent-smith-state",
    "session-1.json",
  )
  assert.equal(existsSync(statePath), true)
  const state = JSON.parse(readFileSync(statePath, "utf-8"))
  assert.equal(state.ruleCount, 1)
  assert.equal(state.rules[0].id, "testing-rule-001")
})

// ---------------------------------------------------------------------------
// Harness for the additional helper-behaviour cases (driven through hooks).
// ---------------------------------------------------------------------------

interface ShellCall {
  command?: string[]
  env?: Record<string, string>
}

/** A fake `$` that records the command and returns a canned KB response. */
function fakeShell(
  response: { exitCode: number; stdout?: string; stderr?: string },
  capture: ShellCall,
) {
  return (strings: TemplateStringsArray, value: string[]) => {
    capture.command = value
    return {
      env(value: Record<string, string>) {
        capture.env = value
        return this
      },
      cwd() { return this },
      quiet() { return this },
      nothrow() {
        return Promise.resolve({
          exitCode: response.exitCode,
          stderr: Buffer.from(response.stderr ?? ""),
          text() { return response.stdout ?? "" },
        })
      },
    }
  }
}

function writeRuntime(
  worktree: string,
  protocol: "rules" | "knowledge",
): void {
  writeFileSync(
    join(worktree, ".agent-smith", "knowledge_base", "runtime.json"),
    JSON.stringify({
      version: "1.0.0",
      package: "git+https://example.test/agent_smith.git@1.0.0",
      protocol,
    }),
  )
  writeFileSync(
    join(worktree, ".agent-smith", "knowledge_base", "rules.db"),
    "fixture",
  )
}

async function buildPlugin(
  worktree: string,
  shell: ReturnType<typeof fakeShell>,
  logs: string[] = [],
) {
  return ReasoningEnrichmentPlugin({
    $: shell,
    directory: worktree,
    worktree,
    client: {
      app: {
        log: async ({ body }: { body: { message: string } }) => {
          logs.push(body.message)
        },
      },
    },
  } as never)
}

test("irrelevant user prompt does not trigger a KB query", async () => {
  const worktree = tempWorktree()
  writeRuntime(worktree, "knowledge")
  const capture: ShellCall = {}
  const hooks = await buildPlugin(worktree, fakeShell({ exitCode: 0 }, capture))

  await hooks!["chat.message"]!(
    { sessionID: "session-irrelevant" },
    {
      message: {},
      parts: [{ type: "text", text: "what is the weather like today" }],
    },
  )

  // No KEYWORDS present -> no shell invocation at all.
  assert.equal(capture.command, undefined)
})

test("rules protocol short-circuits before invoking the query subprocess", async () => {
  const worktree = tempWorktree()
  writeRuntime(worktree, "rules")
  const capture: ShellCall = {}
  const logs: string[] = []
  const hooks = await buildPlugin(worktree, fakeShell({ exitCode: 0 }, capture), logs)

  await hooks!["chat.message"]!(
    { sessionID: "session-rules" },
    {
      message: {},
      parts: [{ type: "text", text: "please review this implementation for convention issues" }],
    },
  )

  // Relevant prompt, but protocol is not knowledge -> no subprocess, no error log.
  assert.equal(capture.command, undefined)
  assert.equal(logs.some(m => m.startsWith("KB query failed")), false)
})

test("KB subprocess failure is logged and injects nothing new", async () => {
  const worktree = tempWorktree()
  writeRuntime(worktree, "knowledge")
  const capture: ShellCall = {}
  const logs: string[] = []
  const hooks = await buildPlugin(
    worktree,
    fakeShell({ exitCode: 1, stderr: "boom" }, capture),
    logs,
  )

  await hooks!["chat.message"]!(
    { sessionID: "session-fail" },
    {
      message: {},
      parts: [{ type: "text", text: "unique-fail-probe review refactor implementation" }],
    },
  )

  assert.equal(capture.command?.includes("agent-smith-kb-query"), true)
  assert.equal(logs.some(m => m === "KB query failed: boom"), true)
})

test("empty KB result set produces no rules", async () => {
  const worktree = tempWorktree()
  writeRuntime(worktree, "knowledge")
  const capture: ShellCall = {}
  const logs: string[] = []
  const hooks = await buildPlugin(
    worktree,
    fakeShell({ exitCode: 0, stdout: "[]" }, capture),
    logs,
  )

  await hooks!["chat.message"]!(
    { sessionID: "session-empty" },
    {
      message: {},
      parts: [{ type: "text", text: "unique-empty-probe review refactor best practice" }],
    },
  )

  assert.equal(capture.command?.includes("agent-smith-kb-query"), true)
  assert.equal(logs.some(m => m === "KB query returned no rules"), true)
})

test("formatRules renders AND/BUT clauses and deduplicates by id", async () => {
  const worktree = tempWorktree()
  writeRuntime(worktree, "knowledge")

  const scenario = {
    id: "andbut-rule-777",
    severity: "hard",
    section: "Clauses",
    source_file: "lore/rules_md/clauses.md",
    tags: ["clauses"],
    bdd: {
      given: "a scenario with multiple clauses",
      when: "it is formatted",
      then: "the THEN clause renders",
      and: ["the first AND renders", "the second AND renders"],
      but: "the BUT renders as a single string",
    },
    explanation: "Clause coverage matters.",
  }
  // Duplicate id in the payload must collapse to a single block.
  const payload = JSON.stringify([scenario, scenario])
  const capture: ShellCall = {}
  const hooks = await buildPlugin(worktree, fakeShell({ exitCode: 0, stdout: payload }, capture))

  await hooks!["chat.message"]!(
    { sessionID: "session-andbut" },
    {
      message: {},
      parts: [{ type: "text", text: "unique-andbut-probe review convention implementation" }],
    },
  )

  const output = { system: [] as string[] }
  await hooks!["experimental.chat.system.transform"]!(
    { sessionID: "session-andbut", model: {} },
    output,
  )

  assert.equal(output.system.length, 1)
  const block = output.system[0]
  assert.match(block, /AND   the first AND renders/)
  assert.match(block, /AND   the second AND renders/)
  assert.match(block, /BUT   the BUT renders as a single string/)
  // Deduplicated: the id appears exactly once.
  const occurrences = block.split("andbut-rule-777").length - 1
  assert.equal(occurrences, 1)
})

test("state file is written per active session id from the prompt", async () => {
  const worktree = tempWorktree()
  writeRuntime(worktree, "knowledge")

  const scenario = {
    id: "session-rule-888",
    severity: "soft",
    section: "Session",
    source_file: "lore/rules_md/session.md",
    tags: ["session"],
    bdd: { given: "g", when: "w", then: "t" },
    explanation: "Per-session state coverage.",
  }
  const capture: ShellCall = {}
  const hooks = await buildPlugin(
    worktree,
    fakeShell({ exitCode: 0, stdout: JSON.stringify([scenario]) }, capture),
  )

  const sessionId = "session-statefile-42"
  await hooks!["chat.message"]!(
    { sessionID: sessionId },
    {
      message: {},
      parts: [
        { type: "text", text: "unique-statefile-probe review convention best practice" },
      ],
    },
  )

  // chat.message set activeSessionID to sessionId; writeStateFile keys on it.
  const statePath = join(
    worktree,
    ".opencode",
    "plugins",
    "agent-smith-state",
    `${sessionId}.json`,
  )
  assert.equal(existsSync(statePath), true)
  const state = JSON.parse(readFileSync(statePath, "utf-8"))
  assert.equal(state.ruleCount, 1)
  assert.equal(state.rules[0].id, "session-rule-888")
})
