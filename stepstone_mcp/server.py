"""STDIO MCP tools with structured success/error results and no truncation."""

import json
import os
import uuid

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from .client import StepStoneClient
from .errors import SourceError
from .parsers import parse_job_html, parse_listing_html
from .transport import PublicTransport

server = Server("stepstone-mcp-connector")
client = StepStoneClient(PublicTransport(backend=os.environ.get("STEPSTONE_HTTP_BACKEND", "httpx")))
sessions = {}


@server.list_tools()
async def list_tools():
    zip_schema = {"type": "string", "pattern": "^[0-9]{5}$"}
    return [
        Tool(name="get_capabilities", description="Check current provider robot policy, available routes and exact coverage limitations.",
             inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
        Tool(name="search_location_page", description="Read one geography-only page when current robot policy permits it. Current filtered/paginated routes are prohibited: returns a structured error, never false empty success.",
             inputSchema={"type": "object", "properties": {"zip_code": zip_schema,
                 "radius": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                 "page": {"type": "integer", "minimum": 1, "default": 1},
                 "age_days": {"type": "integer", "enum": [0, 1, 7], "default": 7}},
                 "required": ["zip_code"], "additionalProperties": False}),
        Tool(name="get_job_details", description="Read a public inline vacancy by exact URL or stored session/index. Return full untruncated job text, all structured locations, worker types, original dates, salary and sections.",
             inputSchema={"type": "object", "properties": {
                 "job_url": {"type": "string"}, "session_id": {"type": "string"},
                 "job_index": {"type": "integer", "minimum": 1}, "job_query": {"type": "string"}},
                 "additionalProperties": False}),
        Tool(name="parse_listing_html", description="Parse HTML supplied by the caller from an authorized source. Import parsing does not establish retrieval permission or complete coverage.",
             inputSchema={"type": "object", "properties": {"html": {"type": "string"}, "source_url": {"type": "string"},
                 "requested_page": {"type": "integer", "minimum": 1}}, "required": ["html", "source_url"], "additionalProperties": False}),
        Tool(name="parse_job_html", description="Parse supplied vacancy HTML offline; keep full source description and exact metadata without invented dates.",
             inputSchema={"type": "object", "properties": {"html": {"type": "string"}, "source_url": {"type": "string"}},
                 "required": ["html", "source_url"], "additionalProperties": False}),
    ]


def dispatch(name, arguments):
    if name == "get_capabilities":
        return client.capabilities()
    if name == "search_location_page":
        result = client.location_page(**arguments)
        session_id = str(uuid.uuid4())
        sessions[session_id] = result["jobs"]
        # Bound process-local sessions; no person preferences or account state.
        if len(sessions) > 100:
            sessions.pop(next(iter(sessions)))
        result["session_id"] = session_id
        return result
    if name == "get_job_details":
        url = arguments.get("job_url")
        if not url:
            session_id = arguments.get("session_id") or (next(reversed(sessions)) if sessions else None)
            jobs = sessions.get(session_id, [])
            index = arguments.get("job_index")
            query = arguments.get("job_query")
            if index is not None:
                if type(index) is not int or not 1 <= index <= len(jobs):
                    raise ValueError("job_index is outside the stored session")
                url = jobs[index - 1]["url"]
            elif query:
                matches = [job for job in jobs if query.casefold() in (job["title"] + " " + job["company"]).casefold()]
                if len(matches) != 1:
                    raise ValueError("job_query must match exactly one stored vacancy; prefer job_url or job_index")
                url = matches[0]["url"]
            else:
                raise ValueError("Provide job_url, or a stored session and job_index/job_query")
        return client.details(url)
    if name == "parse_listing_html":
        return parse_listing_html(**arguments)
    if name == "parse_job_html":
        return parse_job_html(**arguments)
    raise ValueError("Unknown tool")


@server.call_tool()
async def call_tool(name, arguments):
    # HTTP work is bounded in the transport; all tools return JSON with isError.
    import asyncio
    try:
        result = await asyncio.to_thread(dispatch, name, arguments)
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                              structuredContent=result, isError=False)
    except SourceError as exc:
        result = exc.to_dict()
    except (TypeError, ValueError) as exc:
        result = {"ok": False, "status": "invalid_request", "error": {"code": "invalid_request", "message": str(exc)},
                  "coverage_complete": False}
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
                          structuredContent=result, isError=True)


async def run():
    async with stdio_server() as (read, write):
        await server.run(read, write, InitializationOptions(
            server_name="stepstone-mcp-connector", server_version="0.1.0",
            capabilities=server.get_capabilities(notification_options=NotificationOptions(), experimental_capabilities={})))


def main():
    import asyncio
    try:
        asyncio.run(run())
    finally:
        client.transport.close()
