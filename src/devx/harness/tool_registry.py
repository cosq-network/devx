from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any


class ToolRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any] | None = None
    langchain_tool: Any = None
    permission_action: str | None = None
    approval_target: str | Callable[[dict[str, Any]], str] | None = None
    args_schema: Any = None


class ToolRegistry:
    """Stable registration point for built-in, plugin, and MCP tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._resources: list[Any] = []

    def register(self, name: str, handler: Callable[..., Any], *, description: str = "",
                 permission_action: str | None = "plugin",
                 approval_target: str | Callable[[dict[str, Any]], str] | None = None,
                 args_schema: Any = None, replace: bool = False) -> ToolSpec:
        clean_name = name.strip()
        if not clean_name or not clean_name.replace("_", "").replace("-", "").isalnum():
            raise ToolRegistryError(f"Invalid tool name: {name!r}")
        if clean_name in self._tools and not replace:
            raise ToolRegistryError(f"Tool already registered: {clean_name}")
        spec = ToolSpec(clean_name, description or clean_name, handler=handler,
                        permission_action=permission_action, approval_target=approval_target,
                        args_schema=args_schema)
        self._tools[clean_name] = spec
        return spec

    def register_object(self, name: str, tool: Any, *, description: str = "",
                        permission_action: str | None = "plugin",
                        approval_target: str | Callable[[dict[str, Any]], str] | None = None) -> ToolSpec:
        clean_name = name.strip()
        if clean_name in self._tools:
            raise ToolRegistryError(f"Tool already registered: {clean_name}")
        spec = ToolSpec(clean_name, description or getattr(tool, "description", clean_name),
                        langchain_tool=tool, permission_action=permission_action,
                        approval_target=approval_target)
        self._tools[clean_name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolRegistryError(f"Unknown tool: {name}") from exc

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(self._tools.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def add_resource(self, resource: Any) -> None:
        self._resources.append(resource)

    def langchain_tools(self, before_call: Callable[[ToolSpec, dict[str, Any]], str | None] | None = None) -> list[Any]:
        """Convert registered Python handlers to LangChain tools lazily."""
        try:
            from langchain_core.tools import StructuredTool
        except ImportError as exc:
            raise ToolRegistryError("LangChain is required to build agent tools") from exc

        result: list[Any] = []
        for spec in self._tools.values():
            if spec.langchain_tool is not None:
                if before_call is None or spec.permission_action is None:
                    result.append(spec.langchain_tool)
                    continue

                original = spec.langchain_tool

                @wraps(getattr(original, "invoke", original))
                def guarded_object(*args: Any, __tool: Any = original,
                                   __spec: ToolSpec = spec, **kwargs: Any) -> Any:
                    denial = before_call(__spec, kwargs) if before_call else None
                    if denial:
                        return denial
                    payload: Any = kwargs
                    if args:
                        payload = args[0] if len(args) == 1 and not kwargs else (args, kwargs)
                    invoke = getattr(__tool, "invoke", None)
                    if not callable(invoke):
                        raise ToolRegistryError(f"Registered tool {__spec.name!r} has no invoke method")
                    return invoke(payload)

                result.append(StructuredTool.from_function(
                    func=guarded_object,
                    name=spec.name,
                    description=spec.description,
                    args_schema=getattr(original, "args_schema", None),
                ))
                continue
            if spec.handler is None:
                continue
            handler = spec.handler

            @wraps(handler)
            def guarded_handler(*args: Any, __handler: Callable[..., Any] = handler,
                                __spec: ToolSpec = spec, **kwargs: Any) -> Any:
                if before_call:
                    denial = before_call(__spec, kwargs)
                    if denial:
                        return denial
                return __handler(*args, **kwargs)

            result.append(StructuredTool.from_function(
                func=guarded_handler,
                name=spec.name,
                description=spec.description,
                args_schema=spec.args_schema,
            ))
        return result

    def close(self) -> None:
        for resource in reversed(self._resources):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except (OSError, RuntimeError):
                    pass
        self._resources.clear()

    def __del__(self) -> None:
        self.close()


def _plugin_files(workspace: Path, configured: list[str] | tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for raw in configured:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(workspace.resolve())
        except ValueError as exc:
            raise ToolRegistryError(f"Plugin must be inside the workspace: {candidate}") from exc
        if candidate.is_dir():
            files.extend(sorted(path for path in candidate.glob("*.py") if not path.name.startswith("_")))
        elif candidate.is_file() and candidate.suffix == ".py":
            files.append(candidate)
        else:
            raise ToolRegistryError(f"Plugin path does not exist or is not a Python file: {candidate}")
    return list(dict.fromkeys(files))


def load_plugins(workspace: Path, configured: list[str] | tuple[str, ...], registry: ToolRegistry) -> list[str]:
    """Load explicitly configured, workspace-contained Python plugins.

    A plugin exports ``register(registry)`` or ``setup(registry)``. Plugin
    imports are intentionally opt-in because importing Python is executable
    code, even when the plugin only registers read-only tools.
    """
    loaded: list[str] = []
    for path in _plugin_files(workspace.resolve(), configured):
        module_name = f"devx_plugin_{path.stem}_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ToolRegistryError(f"Unable to load plugin: {path}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            hook = getattr(module, "register", None) or getattr(module, "setup", None)
            if not callable(hook):
                raise ToolRegistryError(f"Plugin must define register(registry): {path}")
            hook(registry)
        except ToolRegistryError:
            raise
        except Exception as exc:  # plugin errors should identify the configured file
            raise ToolRegistryError(f"Plugin failed to load {path}: {exc}") from exc
        loaded.append(str(path))
    return loaded
