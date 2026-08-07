#!/usr/bin/env python3
"""Lightweight CLI wrapper for KB semantic search.

Called by the opencode plugin (reasoning-enrichment.ts) through the installed
console script. Outputs JSON to stdout. Designed to be invoked via:

    agent-smith-kb-query "query text" [--tags tag1,tag2] [--k 5]

Exit codes:
    0 — success (JSON array on stdout)
    1 — DB unavailable or error (JSON error object on stdout)
"""

from __future__ import annotations

import argparse
import json
import sys

from agent_smith.mcp.kb.query import (
    db_available,
    query_rules,
)


def main() -> None:
    from agent_smith.config import configure_cli_environment, ensure_sqlite_cli_runtime

    configure_cli_environment()
    ensure_sqlite_cli_runtime('agent-smith-kb-query')
    parser = argparse.ArgumentParser(
        description='Query the Agent Smith knowledge base.'
    )
    parser.add_argument('query', help='Natural-language query string')
    parser.add_argument(
        '--tags', default=None, help='Comma-separated tag filter (matches ANY)'
    )
    parser.add_argument('--k', type=int, default=5, help='Number of results (1–20)')
    args = parser.parse_args()

    tags = [t.strip() for t in args.tags.split(',') if t.strip()] if args.tags else None

    if not db_available():
        print(json.dumps({'error': 'rules.db unavailable', 'db_available': False}))
        sys.exit(1)

    result = query_rules(query=args.query, tags=tags, k=args.k)
    print(result)


if __name__ == '__main__':
    main()
