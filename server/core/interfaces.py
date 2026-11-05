"""Standard Extension Protocols & Plugin Interfaces for Pulse Agent OS.

Architecture is organized into 3 top-level domains:
1. `core/`      — Kernel & Cognition (LLM client, silent memory engine, scheduler, multimodal vision, search)
2. `cloud_os/`  — 1C 1G 10GB Isolated Linux Subsystem & Agent-Computer Interface (ACI: tmux PTY, X11 Z-order WM, modern JS browser)
3. `transport/` — Encrypted API Gateway, WebSocket Realtime Bus & Dynamic Extension Registry

Any developer on GitHub can extend the agent by registering a `ToolPlugin`, `SearchPlugin`,
`MemoryHook`, or `WorkspacePanelPlugin` without modifying core engine files.
"""
from __future__ import annotations
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@runtime_checkable
class ToolPlugin(Protocol):
    """Protocol for pluggable agent tools (e.g. custom MCP bridges, IoT, GitHub skills)."""
    name: str
    description: str
    parameters: dict[str, Any]
    required: list[str]

    async def execute(self, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        ...


@runtime_checkable
class SearchPlugin(Protocol):
    """Protocol for pluggable web or private-knowledge search providers."""
    name: str

    async def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class MemoryHook(Protocol):
    """Lifecycle hook for custom memory backends (Vector DB, Obsidian sync, GraphRAG)."""
    async def on_message_saved(self, message: dict[str, Any]) -> None:
        ...

    async def on_sleep_consolidate(self, recent_messages: list[dict[str, Any]]) -> None:
        ...
