# Branches & PRs — host-supplied integration docs

The Librarian agent reads this folder to learn **how your repository expects
branches and pull requests to be created**: base branch, branch/title naming,
the Jira (or other tracker) ticket-key convention, reviewer assignment, and the
PR body template.

Agent Smith ships **no** branch/PR policy of its own. This folder is the seam
where the host repository plugs in its own conventions.

## What to put here

Symlink (or copy) the relevant documents from your repository into this folder.
The Librarian treats every file it finds here as authoritative. Typical contents:

- **Branch & PR rules** — base/integration branch, branch-name and PR-title
  format, ticket-key requirement, reviewer/CODEOWNERS policy.
- **PR template** — the exact section structure a PR body must follow
  (e.g. your `PULL_REQUEST_TEMPLATE.md`).

Use `agent-smith-symlink-rules` or a plain symlink, for example:

```bash
ln -s ../../../../docs/branches-and-prs.md \
  .agent-smith/lore/branches_and_prs/branches-and-prs.md
ln -s ../../../../docs/PULL_REQUEST_TEMPLATE.md \
  .agent-smith/lore/branches_and_prs/PULL_REQUEST_TEMPLATE.md
```

## When this folder is empty

If the host provides no documents here, the Librarian MUST NOT invent a policy.
It is required to **discover** the conventions from the hosting repository itself
— inspecting existing branches, recent merged PRs, `.github/` (CODEOWNERS,
`PULL_REQUEST_TEMPLATE.md`, workflow files), and the tracker key pattern used by
existing tickets/branches — before opening or updating a PR. If it cannot
determine a required value (such as the ticket key), it blocks and asks the
caller rather than guessing.
