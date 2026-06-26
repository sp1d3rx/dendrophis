"""Model Context Protocol (MCP) tool integration and connection management."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel

from dendrophis.config.schema import DendrophisConfig
from dendrophis.tools.base import BaseTool
from dendrophis.tools.registry import ToolRegistry


class MCPTool(BaseTool):
    """A Dendrophis tool that delegates execution to an MCP server."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        server_name: str,
        manager: MCPManager,
    ) -> None:
        self._name = name
        self._description = description
        self._parameters = parameters
        self._server_name = server_name
        self._manager = manager

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> Any:
        return await self._manager.execute_tool(self._server_name, self._name, kwargs)


class HTTPClientContextManager:
    """Async context manager to wrap httpx.AsyncClient and streamable_http_client."""

    def __init__(self, url: str, verify_ssl: bool) -> None:
        self.url = url
        self.verify_ssl = verify_ssl
        self.http_client: Any = None
        self.stream_ctx: Any = None

    async def __aenter__(self) -> tuple[Any, Any]:
        import httpx
        from mcp.client.streamable_http import streamable_http_client

        self.http_client = httpx.AsyncClient(verify=self.verify_ssl)
        await self.http_client.__aenter__()
        self.stream_ctx = streamable_http_client(url=self.url, http_client=self.http_client)
        read_stream, write_stream, _get_session_id = await self.stream_ctx.__aenter__()
        return read_stream, write_stream

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.stream_ctx:
            await self.stream_ctx.__aexit__(exc_type, exc_val, exc_tb)
        if self.http_client:
            await self.http_client.__aexit__(exc_type, exc_val, exc_tb)


class MCPManager:
    """Manages connections to multiple MCP servers and registers their tools."""

    def __init__(
        self,
        config: DendrophisConfig,
        tool_registry: ToolRegistry,
        debug_logger: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.debug_logger = debug_logger
        self._sessions: dict[str, ClientSession] = {}
        self._contexts: dict[str, Any] = {}  # Keeps the stdio_client context managers alive
        self._tasks: list[asyncio.Task] = []
        self._launch_configs: dict[str, tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]] = {}

    def log(self, msg: str) -> None:
        if self.debug_logger:
            self.debug_logger(f"[MCP] {msg}")
        else:
            print(f"[MCP] {msg}")

    async def initialize_servers(self) -> None:
        """Start and connect to all configured MCP servers."""
        if not self.config.mcp_servers:
            self.log("No MCP servers configured.")
            return

        for name, cfg in self.config.mcp_servers.items():
            if not cfg.enabled:
                self.log(f"MCP server '{name}' is disabled in configuration.")
                continue
            self._tasks.append(asyncio.create_task(self._connect_server(name, cfg)))

    async def _connect_server(self, name: str, cfg: Any) -> None:
        # Merge current environment with configured env
        env = os.environ.copy()
        if cfg.env:
            env.update(cfg.env)

        if cfg.url:
            self.log(f"Connecting to MCP server '{name}' via HTTP: {cfg.url}")
            verify_ssl = env.get("NODE_TLS_REJECT_UNAUTHORIZED") != "0"
            ctx = HTTPClientContextManager(cfg.url, verify_ssl)
        else:
            self.log(f"Connecting to MCP server '{name}' via command: {cfg.command} {' '.join(cfg.args)}")
            server_params = StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=env,
            )

        log_file = None
        if not cfg.url and self.config.debug_log:
            try:
                from pathlib import Path

                log_path = Path(self.config.debug_log).expanduser()
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
            except Exception as exc:
                self.log(f"Failed to open debug log for MCP redirection: {exc}")

        try:
            # Enter the async context manager
            if cfg.url:
                read, write = await ctx.__aenter__()
                self._contexts[name] = ctx
            else:
                try:
                    ctx = stdio_client(server_params, errlog=log_file)
                    read, write = await ctx.__aenter__()
                    self._contexts[name] = ctx
                finally:
                    if log_file:
                        log_file.close()

            session = ClientSession(read, write)
            await session.__aenter__()
            self._sessions[name] = session

            # Initialize MCP session
            await session.initialize()
            self.log(f"Initialized MCP session with '{name}'")

            # Store launch configuration parameters for change-detection on reload
            env_tuple = tuple(sorted(cfg.env.items())) if cfg.env else ()
            self._launch_configs[name] = (cfg.command, tuple(cfg.args) if cfg.args else (), env_tuple, cfg.url)

            # Query tools
            tools_result = await session.list_tools()
            self.log(f"Discovered tools from '{name}': {[tool_item.name for tool_item in tools_result.tools]}")

            # Register each tool
            for mcp_tool in tools_result.tools:
                schema = mcp_tool.inputSchema
                if isinstance(schema, BaseModel):
                    schema = schema.model_dump()
                elif hasattr(schema, "dict"):
                    schema = schema.dict()

                tool = MCPTool(
                    name=mcp_tool.name,
                    description=mcp_tool.description or "",
                    parameters=schema,
                    server_name=name,
                    manager=self,
                )
                self.tool_registry.add(tool)
                self.log(f"Registered MCP tool '{mcp_tool.name}' from '{name}'")

        except Exception as error:
            self.log(f"Failed to connect to MCP server '{name}': {error}")
            await self._cleanup_server(name)

    async def execute_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        session = self._sessions.get(server_name)
        if not session:
            raise ValueError(f"MCP server '{server_name}' is not connected.")

        self.log(f"Calling tool '{tool_name}' on server '{server_name}' with args: {arguments}")
        result = await session.call_tool(tool_name, arguments=arguments)

        # Parse text content from CallToolResult
        text_parts = []
        for content in result.content:
            if hasattr(content, "text"):
                text_parts.append(content.text)
            elif isinstance(content, dict) and "text" in content:
                text_parts.append(content["text"])
            else:
                text_parts.append(str(content))
        return "\n".join(text_parts)

    async def _cleanup_server(self, name: str) -> None:
        session = self._sessions.pop(name, None)
        if session:
            try:
                await session.__aexit__(None, None, None)
            except Exception as error:
                self.log(f"Error closing session for '{name}': {error}")

        ctx = self._contexts.pop(name, None)
        if ctx:
            try:
                await ctx.__aexit__(None, None, None)
            except Exception as error:
                self.log(f"Error exiting context for '{name}': {error}")

        self._launch_configs.pop(name, None)

        # Remove tools associated with this server from the registry
        for tool_name in list(self.tool_registry._tools.keys()):
            tool = self.tool_registry.get(tool_name)
            if isinstance(tool, MCPTool) and tool._server_name == name:
                self.tool_registry.remove(tool_name)
                self.log(f"Removed MCP tool '{tool_name}' associated with server '{name}'")

    async def sync_servers(self) -> None:
        """Synchronize running servers with current configuration."""
        active_names = set(self._sessions.keys())
        config_names = set(self.config.mcp_servers.keys())

        # 1. Detect servers that need to be stopped
        to_stop = []
        for name in active_names:
            if name not in config_names:
                to_stop.append(name)
            else:
                cfg = self.config.mcp_servers[name]
                if not cfg.enabled:
                    to_stop.append(name)
                else:
                    env_tuple = tuple(sorted(cfg.env.items())) if cfg.env else ()
                    current_launch = (cfg.command, tuple(cfg.args) if cfg.args else (), env_tuple, cfg.url)
                    if self._launch_configs.get(name) != current_launch:
                        to_stop.append(name)

        for name in to_stop:
            self.log(f"Stopping MCP server '{name}' due to config reload.")
            await self._cleanup_server(name)

        # 2. Detect servers that need to be started
        for name, cfg in self.config.mcp_servers.items():
            if cfg.enabled and name not in self._sessions:
                task = asyncio.create_task(self._connect_server(name, cfg))
                self._tasks.append(task)

    async def aclose(self) -> None:
        self.log("Closing all MCP servers...")
        # Cancel any pending connection tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Close all active connections
        servers = list(self._sessions.keys()) + list(self._contexts.keys())
        for name in set(servers):
            await self._cleanup_server(name)
        self.log("All MCP servers closed.")
