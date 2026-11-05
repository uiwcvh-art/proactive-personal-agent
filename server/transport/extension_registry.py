"""
Extension Registry (`server/transport/extension_registry.py`).

Allows internal modules and third-party contributors on GitHub to register:
- `ToolPlugin` (custom agent tools exposed to the LLM)
- `SearchPlugin` (custom search providers)
- `MemoryHook` (custom pre/post memory consolidation hooks)

Supports automatic discovery from `server/plugins/*.py` if present.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, List

from core.interfaces import MemoryHook, SearchPlugin, ToolPlugin


class ExtensionRegistry:
    def __init__(self) -> None:
        self.tool_plugins: Dict[str, ToolPlugin] = {}
        self.search_plugins: Dict[str, SearchPlugin] = {}
        self.memory_hooks: List[MemoryHook] = []

    def register_tool(self, plugin: ToolPlugin) -> None:
        name = getattr(plugin, 'name', '').strip()
        if name:
            self.tool_plugins[name] = plugin

    def register_search(self, plugin: SearchPlugin) -> None:
        name = getattr(plugin, 'name', '').strip()
        if name:
            self.search_plugins[name] = plugin

    def register_memory_hook(self, hook: MemoryHook) -> None:
        self.memory_hooks.append(hook)

    def tool_schemas(self) -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        for name, plugin in self.tool_plugins.items():
            schemas.append({
                'type': 'function',
                'function': {
                    'name': name,
                    'description': getattr(plugin, 'description', f'Plugin tool {name}'),
                    'parameters': getattr(plugin, 'parameters', {'type': 'object', 'properties': {}}),
                },
            })
        return schemas

    async def execute_tool(self, name: str, args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        plugin = self.tool_plugins.get(name)
        if not plugin:
            return {'error': f'Unknown tool plugin: {name}'}
        return await plugin.execute(args, context)

    def discover_plugins_dir(self, plugins_dir: Path) -> List[str]:
        """Auto-load any `.py` plugin modules inside `server/plugins/` that define `register(registry)`."""
        loaded: List[str] = []
        if not plugins_dir.is_dir():
            return loaded
        for py_file in sorted(plugins_dir.glob('*.py')):
            if py_file.name.startswith('_'):
                continue
            try:
                spec = importlib.util.spec_from_file_location(f'plugins.{py_file.stem}', py_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    reg_fn = getattr(module, 'register', None)
                    if callable(reg_fn):
                        reg_fn(self)
                        loaded.append(py_file.stem)
            except Exception:
                continue
        return loaded

    def summary(self) -> Dict[str, Any]:
        return {
            'tool_plugins': list(self.tool_plugins.keys()),
            'search_plugins': list(self.search_plugins.keys()),
            'memory_hooks_count': len(self.memory_hooks),
        }
