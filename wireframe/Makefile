# Agent Smith — command shortcuts
#
# This Makefile wraps the bare `agent-smith-*` commands so you don't have to
# memorize them. It is seeded into <repo-root>/.agent-smith/ on install.
#
# Run it from the repo root with:
#     make -C .agent-smith <target>
#
# Or, to run from the repo root without the -C, add a shell alias
# (see `make alias`):
#     alias asm='make -C .agent-smith'
# then: asm sync   /   asm kb-query ARGS='"query text" --k 5'
#
# The bare commands auto-discover the project root and .mode from the current
# directory, so targets work whether invoked from the repo root or .agent-smith/.
#
# Pass extra flags to any target with ARGS, e.g.:
#     make -C .agent-smith sync ARGS='--protocol rules'
#     make -C .agent-smith kb-query ARGS='"query text" --k 5'

.DEFAULT_GOAL := help
ARGS ?=

# --- Activation -------------------------------------------------------------

.PHONY: sync
sync: ## Activate Agent Smith in this project (ARGS='--protocol rules|knowledge')
	agent-smith-sync $(ARGS)

.PHONY: desync
desync: ## Deactivate Agent Smith (removes .opencode wiring; .agent-smith kept)
	agent-smith-sync --desync $(ARGS)

# --- Rules docs -------------------------------------------------------------

.PHONY: symlink-rules
symlink-rules: ## Symlink a rules folder or .md into lore/rules_md (ARGS='<path> [--name x]')
	agent-smith-symlink-rules $(ARGS)

# --- Knowledge base ---------------------------------------------------------

.PHONY: kb-sync
kb-sync: ## Build/sync the knowledge base from scenario JSON (ARGS='--force')
	agent-smith-kb-sync $(ARGS)

.PHONY: kb-query
kb-query: ## Query the knowledge base (ARGS='"query text" --k 5')
	agent-smith-kb-query $(ARGS)

.PHONY: kb-audit
kb-audit: ## Audit scenario JSON against the schema and content checks
	agent-smith-kb-audit $(ARGS)

.PHONY: kb-visualize
kb-visualize: ## Visualize the knowledge base (requires the `viz` extra)
	agent-smith-kb-visualize $(ARGS)

.PHONY: kb-export-tensorboard
kb-export-tensorboard: ## Export embeddings for the TensorBoard Embedding Projector
	agent-smith-kb-export-tensorboard $(ARGS)

# --- Gates & maintenance ----------------------------------------------------

.PHONY: rollback-gates
rollback-gates: ## Revert gate-token-only changes in gate files
	agent-smith-rollback-gates $(ARGS)

.PHONY: mcp
mcp: ## Run the MCP server (stdio) for debugging
	agent-smith-mcp $(ARGS)

# --- Release management -----------------------------------------------------

.PHONY: install-release
install-release: ## Install/upgrade a release (ARGS='--version <tag>')
	agent-smith-install-release $(ARGS)

.PHONY: uninstall-release
uninstall-release: ## Remove a release, latest or ARGS='--version <tag>' (add --purge-host-data to drop .agent-smith)
	agent-smith-install-release --uninstall $(ARGS)

.PHONY: nuke
nuke: ## Remove a current release and drop .agent-smith (latest, or pin with ARGS='--version <tag>')
	agent-smith-install-release --uninstall --purge-host-data $(ARGS)

# --- Convenience ------------------------------------------------------------

.PHONY: alias
alias: ## Print a shell alias so you can run `asm <target>` from the repo root
	@echo "alias asm='make -C .agent-smith'"
	@echo
	@echo '# Add the line above to your ~/.zshrc or ~/.bashrc, then run e.g.:'
	@echo "#   asm sync"
	@echo "#   asm kb-query ARGS='\"query text\" --k 5'"

.PHONY: help
help: ## Show this help
	@echo 'Agent Smith commands (run: make -C .agent-smith <target>)'
	@echo
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
