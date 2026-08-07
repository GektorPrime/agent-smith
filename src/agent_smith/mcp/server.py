from __future__ import annotations

from fastmcp import FastMCP

from agent_smith.ast_tools.search import (
    agent_smith_ast_find_definitions,
    agent_smith_ast_search,
)
from agent_smith.ast_tools.structure import (
    agent_smith_ast_analyze_structure,
    agent_smith_ast_class_outline,
    agent_smith_ast_list_imports,
)
from agent_smith.config import get_config
from agent_smith.mcp.gates.readers import (
    handoff,
    init,
    read_entry_point,
    read_persona,
    read_routing_table,
    read_rules,
    select_rules,
)
from agent_smith.mcp.kb.query import db_available, query_rules

mcp = FastMCP('agent_smith')

_KNOWLEDGE_INIT_MESSAGE = (
    '=== AGENT SMITH ===\n'
    'Knowledge base is available.\n'
    'Use agent_smith_query_rules on demand when you need rule context.\n'
    'Call agent_smith_handoff to begin the task.'
)

_KNOWLEDGE_HANDOFF_MESSAGE = (
    'Initialization complete. '
    "Your sole remaining obligation is the user's stated task. "
    'Begin executing that task immediately using the tools available in your current role. '
    'Use agent_smith_query_rules on demand when you need rule context. '
    'Do not emit another text-only response until the task is complete or you genuinely need '
    'clarification from the user. '
    'If no task has been stated yet, ask the user what they want to do.'
)


async def agent_smith_check_db() -> dict[str, str | bool]:
    config = get_config()
    db_path = config.rules_db_path()
    return {'available': db_available(), 'path': str(db_path)}


async def agent_smith_init() -> str:
    """
    Initialise the Agent Smith session.
    Always regenerates gate phrases to keep the fallback machinery live.
    Behaviour depends on the configured protocol:
    - 'rules': the full rule-reading protocol activates.
    - 'knowledge': branches on knowledge base availability.
      If the KB is available, gate phrases are regenerated but no mandatory
      file reads are required. If the KB is unavailable, falls back to the
      full rule-reading protocol.
    """
    config = get_config()

    if config.protocol() == 'knowledge':
        db_status = await agent_smith_check_db()
        if bool(db_status['available']):
            init()  # regenerate gate phrases; result intentionally discarded
            return _KNOWLEDGE_INIT_MESSAGE

    return init()


async def agent_smith_handoff(acknowledged_tokens: list[str] | None = None) -> str:
    config = get_config()

    if config.protocol() == 'knowledge':
        db_status = await agent_smith_check_db()
        if bool(db_status['available']):
            return _KNOWLEDGE_HANDOFF_MESSAGE

    return handoff(acknowledged_tokens=acknowledged_tokens)


async def agent_smith_query_rules(
    query: str, tags: list[str] | None = None, k: int = 5
) -> str:
    return query_rules(query=query, tags=tags, k=k)


async def agent_smith_read_entry_point() -> str:
    return read_entry_point()


async def agent_smith_read_rules() -> str:
    return read_rules()


async def agent_smith_read_routing_table() -> str:
    return read_routing_table()


async def agent_smith_select_rules(selected_paths: list[str] | None = None) -> str:
    return select_rules(selected_paths=selected_paths)


async def agent_smith_read_persona() -> str:
    return read_persona()


def main() -> None:
    mcp.run()


@mcp.tool(name='check_db')
async def _agent_smith_check_db_tool() -> dict[str, str | bool]:
    return await agent_smith_check_db()


@mcp.tool(name='init')
async def _agent_smith_init_tool() -> str:
    return await agent_smith_init()


@mcp.tool(name='handoff')
async def _agent_smith_handoff_tool(acknowledged_tokens: list[str] | None = None) -> str:
    return await agent_smith_handoff(acknowledged_tokens=acknowledged_tokens)


@mcp.tool(name='query_rules')
async def _agent_smith_query_rules_tool(
    query: str, tags: list[str] | None = None, k: int = 5
) -> str:
    return await agent_smith_query_rules(query=query, tags=tags, k=k)


@mcp.tool(name='read_entry_point')
async def _agent_smith_read_entry_point_tool() -> str:
    return await agent_smith_read_entry_point()


@mcp.tool(name='read_rules')
async def _agent_smith_read_rules_tool() -> str:
    return await agent_smith_read_rules()


@mcp.tool(name='read_routing_table')
async def _agent_smith_read_routing_table_tool() -> str:
    return await agent_smith_read_routing_table()


@mcp.tool(name='select_rules')
async def _agent_smith_select_rules_tool(
    selected_paths: list[str] | None = None,
) -> str:
    return await agent_smith_select_rules(selected_paths=selected_paths)


@mcp.tool(name='read_persona')
async def _agent_smith_read_persona_tool() -> str:
    return await agent_smith_read_persona()


@mcp.tool(name='ast_analyze_structure')
async def _agent_smith_ast_analyze_structure_tool(
    path: str, language: str | None = None
) -> str:
    return await agent_smith_ast_analyze_structure(path=path, language=language)


@mcp.tool(name='ast_class_outline')
async def _agent_smith_ast_class_outline_tool(
    path: str, class_name: str | None = None, language: str | None = None
) -> str:
    return await agent_smith_ast_class_outline(
        path=path, class_name=class_name, language=language
    )


@mcp.tool(name='ast_list_imports')
async def _agent_smith_ast_list_imports_tool(
    path: str, language: str | None = None
) -> str:
    return await agent_smith_ast_list_imports(path=path, language=language)


@mcp.tool(name='ast_find_definitions')
async def _agent_smith_ast_find_definitions_tool(
    path: str,
    name: str,
    symbol_type: str | None = None,
    language: str | None = None,
) -> str:
    return await agent_smith_ast_find_definitions(
        path=path, name=name, symbol_type=symbol_type, language=language
    )


@mcp.tool(name='ast_search')
async def _agent_smith_ast_search_tool(
    path: str, pattern: str, language: str, k: int = 50
) -> str:
    return await agent_smith_ast_search(path=path, pattern=pattern, language=language, k=k)
