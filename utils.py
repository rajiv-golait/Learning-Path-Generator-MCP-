from __future__ import annotations

import asyncio
import copy
import os
from typing import Any, Callable, Optional

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langchain_core.tools.base import BaseTool
from langchain_core.utils.pydantic import is_basemodel_subclass
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import ToolNode, create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

from prompt import user_goal_prompt

cfg = RunnableConfig(recursion_limit=100)


def format_mcp_error(exc: BaseException) -> str:
    """Unwrap ExceptionGroup / TaskGroup failures so Streamlit shows the real error."""
    lines: list[str] = [f"{type(exc).__name__}: {exc}"]
    grouped = getattr(exc, "exceptions", None)
    if grouped:
        for i, sub in enumerate(grouped):
            lines.append(f"  — sub [{i+1}]:")
            for subline in format_mcp_error(sub).splitlines():
                lines.append(f"    {subline}")
    elif exc.__cause__ is not None:
        lines.append("  Caused by:")
        for subline in format_mcp_error(exc.__cause__).splitlines():
            lines.append(f"    {subline}")
    return "\n".join(lines)


def _mcp_transports_to_try(mcp_urls: list[str]) -> list[str]:
    """
    Remote MCP hosts differ: some speak streamable HTTP, others SSE.
    Composio v3 (backend.composio.dev) is streamable HTTP only — SSE returns 405.
    Set MCP_TRANSPORT=sse or streamable_http to force one; otherwise try both (non-Composio).
    """
    override = (os.environ.get("MCP_TRANSPORT") or "").strip().lower()
    if override in ("sse", "streamable_http"):
        return [override]
    if any("composio.dev" in (u or "") for u in mcp_urls):
        return ["streamable_http"]
    return ["streamable_http", "sse"]


def _mcp_headers(composio_api_key: Optional[str] = None) -> Optional[dict]:
    """Composio requires x-api-key on MCP requests (401 without it for most projects)."""
    key = (composio_api_key or os.environ.get("COMPOSIO_API_KEY") or "").strip()
    org = (os.environ.get("COMPOSIO_ORG_API_KEY") or "").strip()
    if not key and not org:
        return None
    headers: dict[str, str] = {}
    if key:
        headers["x-api-key"] = key
    if org:
        headers["x-org-api-key"] = org
    return headers

def _patch_json_schema_for_gemini(obj: Any) -> Any:
    """Gemini requires JSON-schema arrays to declare `items`; many MCP tools omit it (e.g. `tags`)."""
    if isinstance(obj, list):
        return [_patch_json_schema_for_gemini(x) for x in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in ("$schema", "additionalProperties"):
                continue
            out[k] = _patch_json_schema_for_gemini(v)
        if out.get("type") == "array":
            items = out.get("items")
            if items is None or items == {}:
                out["items"] = {"type": "string"}
        return out
    return obj


def _patch_mcp_tools_for_gemini(tools: list[BaseTool]) -> list[BaseTool]:
    """Normalize MCP tool schemas so ChatGoogleGenerativeAI can bind them."""
    out: list[BaseTool] = []
    for t in tools:
        if not isinstance(t, StructuredTool):
            out.append(t)
            continue
        schema = t.args_schema
        if isinstance(schema, dict):
            new_schema = _patch_json_schema_for_gemini(copy.deepcopy(schema))
            out.append(t.model_copy(update={"args_schema": new_schema}))
        elif isinstance(schema, type) and is_basemodel_subclass(schema):
            new_schema = _patch_json_schema_for_gemini(schema.model_json_schema())
            out.append(t.model_copy(update={"args_schema": new_schema}))
        else:
            out.append(t)
    return out


def initialize_model(google_api_key: str) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=google_api_key
    )


def _recoverable_tool_error(exc: Exception) -> str:
    """Return tool errors to the model as messages so it can retry (e.g. new YouTube search)."""
    body = repr(exc)
    if len(body) > 4000:
        body = body[:4000] + "…"
    return (
        f"{body}\n\n"
        "Continue the task. If you see videoNotFound / 404 for YouTube: run search again "
        "and use only video IDs returned in those search results—never reuse a bad ID."
    )

def _build_tools_config(
    transport: str,
    youtube_pipedream_url: str,
    drive_pipedream_url: Optional[str],
    notion_pipedream_url: Optional[str],
    composio_api_key: Optional[str] = None,
) -> dict:
    headers = _mcp_headers(composio_api_key)

    def server(url: str) -> dict:
        cfg: dict = {"url": url, "transport": transport}
        if headers:
            cfg["headers"] = headers
        return cfg

    tools_config: dict = {"youtube": server(youtube_pipedream_url)}
    if drive_pipedream_url:
        tools_config["drive"] = server(drive_pipedream_url)
    if notion_pipedream_url:
        tools_config["notion"] = server(notion_pipedream_url)
    return tools_config


async def setup_agent_with_tools(
    google_api_key: str,
    youtube_pipedream_url: str,
    drive_pipedream_url: Optional[str] = None,
    notion_pipedream_url: Optional[str] = None,
    composio_api_key: Optional[str] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Any:
    """
    Set up the agent with YouTube (mandatory) and optional Drive or Notion tools.
    """
    if progress_callback:
        progress_callback("Setting up agent with tools... ✅")
    if drive_pipedream_url and progress_callback:
        progress_callback("Added Google Drive integration... ✅")
    if notion_pipedream_url and progress_callback:
        progress_callback("Added Notion integration... ✅")

    url_list = [youtube_pipedream_url, drive_pipedream_url or "", notion_pipedream_url or ""]
    transports = _mcp_transports_to_try(url_list)
    last_exc: Optional[BaseException] = None
    for transport in transports:
        try:
            if progress_callback:
                progress_callback(f"Connecting to MCP ({transport})...")
            tools_config = _build_tools_config(
                transport,
                youtube_pipedream_url,
                drive_pipedream_url,
                notion_pipedream_url,
                composio_api_key=composio_api_key,
            )
            mcp_client = MultiServerMCPClient(tools_config)
            if progress_callback:
                progress_callback("Getting available tools... ✅")
            tools = await mcp_client.get_tools()
            tools = _patch_mcp_tools_for_gemini(tools)
            if progress_callback:
                progress_callback("Creating AI agent... ✅")
            mcp_orch_model = initialize_model(google_api_key)
            tool_node = ToolNode(tools, handle_tool_errors=_recoverable_tool_error)
            agent = create_react_agent(mcp_orch_model, tool_node)
            if progress_callback:
                progress_callback("Setup complete! Starting to generate learning path... ✅")
            return agent
        except BaseException as e:
            last_exc = e
            print(f"Error in setup_agent_with_tools ({transport}):\n{format_mcp_error(e)}")
            continue

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("MCP setup failed with no exception captured")

def run_agent_sync(
    google_api_key: str,
    youtube_pipedream_url: str,
    drive_pipedream_url: Optional[str] = None,
    notion_pipedream_url: Optional[str] = None,
    composio_api_key: Optional[str] = None,
    user_goal: str = "",
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Synchronous wrapper for running the agent.
    """
    async def _run():
        try:
            agent = await setup_agent_with_tools(
                google_api_key=google_api_key,
                youtube_pipedream_url=youtube_pipedream_url,
                drive_pipedream_url=drive_pipedream_url,
                notion_pipedream_url=notion_pipedream_url,
                composio_api_key=composio_api_key,
                progress_callback=progress_callback,
            )
            
            # Combine user goal with prompt template
            learning_path_prompt = "User Goal: " + user_goal + "\n" + user_goal_prompt
            
            if progress_callback:
                progress_callback("Generating your learning path...")
            
            # Run the agent
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=learning_path_prompt)]},
                config=cfg
            )
            
            if progress_callback:
                progress_callback("Learning path generation complete!")
            
            return result
        except BaseException as e:
            print(f"Error in _run:\n{format_mcp_error(e)}")
            raise

    # Run in new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()
