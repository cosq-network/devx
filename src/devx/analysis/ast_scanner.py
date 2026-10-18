"""AST-based codebase topology scanner and symbol extractor."""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SymbolInfo:
    name: str
    kind: str  # "class", "function", "async_function"
    line: int
    end_line: int
    params: list[tuple[str, str | None]] = field(default_factory=list)  # [(name, type_str)]
    return_type: str | None = None
    docstring: str | None = None
    exceptions_raised: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)


@dataclass
class ModuleInfo:
    path: Path
    relative_path: str
    imports: list[str] = field(default_factory=list)
    import_froms: dict[str, list[str]] = field(default_factory=dict)  # {module: [names]}
    symbols: list[SymbolInfo] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    dependencies: set[str] = field(default_factory=set)  # relative module names or paths


def _unparse_node(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def scan_module(path: Path, workspace: Path | None = None) -> ModuleInfo:
    """Scan a Python source file using the AST to extract imports, symbols, and dependencies."""
    rel_path = path.relative_to(workspace).as_posix() if workspace else path.name
    info = ModuleInfo(path=path, relative_path=rel_path)

    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content, filename=str(path))
    except Exception:
        return info

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                info.imports.append(alias.name)
                info.dependencies.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names = [alias.name for alias in node.names]
            info.import_froms[mod] = names
            if mod:
                info.dependencies.add(mod.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sym = _parse_function_node(node)
            info.symbols.append(sym)
            info.functions.append(sym.name)
        elif isinstance(node, ast.ClassDef):
            sym = _parse_class_node(node)
            info.symbols.append(sym)
            info.classes.append(sym.name)
            # Inspect methods inside class
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_sym = _parse_function_node(item, parent_class=sym.name)
                    info.symbols.append(method_sym)

    return info


def _parse_function_node(node: ast.FunctionDef | ast.AsyncFunctionDef, parent_class: str = "") -> SymbolInfo:
    full_name = f"{parent_class}.{node.name}" if parent_class else node.name
    params: list[tuple[str, str | None]] = []
    for arg in node.args.args:
        annotation_str = _unparse_node(arg.annotation)
        params.append((arg.arg, annotation_str))

    return_type = _unparse_node(node.returns)
    docstring = ast.get_docstring(node)

    exceptions_raised: list[str] = []
    calls: list[str] = []

    for sub in ast.walk(node):
        if isinstance(sub, ast.Raise):
            if sub.exc:
                if isinstance(sub.exc, ast.Call):
                    exc_name = _unparse_node(sub.exc.func)
                    if exc_name:
                        exceptions_raised.append(exc_name)
                elif isinstance(sub.exc, ast.Name):
                    exceptions_raised.append(sub.exc.id)
        elif isinstance(sub, ast.Call):
            call_name = _unparse_node(sub.func)
            if call_name and call_name not in calls:
                calls.append(call_name)

    return SymbolInfo(
        name=full_name,
        kind="async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
        line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno),
        params=params,
        return_type=return_type,
        docstring=docstring,
        exceptions_raised=exceptions_raised,
        calls=calls[:25],
    )


def _parse_class_node(node: ast.ClassDef) -> SymbolInfo:
    bases = [_unparse_node(b) for b in node.bases if _unparse_node(b)]
    docstring = ast.get_docstring(node)

    return SymbolInfo(
        name=node.name,
        kind="class",
        line=node.lineno,
        end_line=getattr(node, "end_lineno", node.lineno),
        params=[(b, "base") for b in bases],
        docstring=docstring,
    )


def scan_workspace(workspace: Path, max_files: int = 500) -> CodebaseTopology:
    """Scan workspace files and construct CodebaseTopology."""
    topology = CodebaseTopology(workspace=workspace)
    scanned_count = 0

    ignore_dirs = {
        ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
        ".pytest_cache", ".ruff_cache", "dist", "build", "site-packages",
    }

    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
        for file in files:
            if file.endswith(".py"):
                file_path = Path(root) / file
                mod_info = scan_module(file_path, workspace=workspace)
                topology.add_module(mod_info)
                scanned_count += 1
                if scanned_count >= max_files:
                    return topology
            elif file.endswith((".js", ".jsx", ".ts", ".tsx")):
                # Lightweight JS/TS import extraction
                rel = (Path(root) / file).relative_to(workspace).as_posix()
                info = _scan_js_ts_file(Path(root) / file, rel)
                topology.add_module(info)
                scanned_count += 1
                if scanned_count >= max_files:
                    return topology

    return topology


def _scan_js_ts_file(path: Path, rel_path: str) -> ModuleInfo:
    info = ModuleInfo(path=path, relative_path=rel_path)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return info

    # match: import ... from '...'; or require('...')
    import_matches = re.findall(r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|require\(['"]([^'"]+)['"]\))""", content)
    for m in import_matches:
        mod = m[0] or m[1]
        info.imports.append(mod)
        info.dependencies.add(mod)

    # match export function/class
    symbols = re.findall(r"""(?:export\s+(?:async\s+)?function\s+([A-Za-z0-9_]+)|export\s+class\s+([A-Za-z0-9_]+))""", content)
    for sym in symbols:
        name = sym[0] or sym[1]
        info.symbols.append(SymbolInfo(name=name, kind="function" if sym[0] else "class", line=1, end_line=1))
        if sym[0]:
            info.functions.append(name)
        else:
            info.classes.append(name)

    return info


class CodebaseTopology:
    """Manages workspace module dependencies, symbol lookup, and test mappings."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.modules: dict[str, ModuleInfo] = {}  # rel_path -> ModuleInfo
        self._dep_graph: dict[str, set[str]] = {}  # rel_path -> set of internal rel_paths
        self._reverse_dep_graph: dict[str, set[str]] = {}

    def add_module(self, info: ModuleInfo) -> None:
        self.modules[info.relative_path] = info

    def build_internal_graph(self) -> None:
        """Resolve import names to internal relative paths in the workspace."""
        self._dep_graph.clear()
        self._reverse_dep_graph.clear()

        # Build lookup table: module dotted name or stem -> rel_path
        lookup: dict[str, str] = {}
        for rel_path in self.modules:
            stem = Path(rel_path).stem
            lookup[stem] = rel_path
            # dotted representation for python
            if rel_path.startswith("src/"):
                dotted = rel_path[4:].replace("/", ".").removesuffix(".py")
                lookup[dotted] = rel_path
            dotted_full = rel_path.replace("/", ".").removesuffix(".py")
            lookup[dotted_full] = rel_path

        for rel_path, mod in self.modules.items():
            self._dep_graph.setdefault(rel_path, set())
            self._reverse_dep_graph.setdefault(rel_path, set())

            all_imported = list(mod.imports) + list(mod.import_froms.keys())
            for imp in all_imported:
                # Match exact or prefix
                target_rel = lookup.get(imp) or lookup.get(imp.split(".")[0])
                if not target_rel and imp.startswith("."):
                    # Relative import resolution
                    base_dir = Path(rel_path).parent
                    candidate = (base_dir / imp).resolve()
                    cand_rel = str(candidate).replace("\\", "/")
                    target_rel = lookup.get(cand_rel)

                if target_rel and target_rel != rel_path:
                    self._dep_graph[rel_path].add(target_rel)
                    self._reverse_dep_graph.setdefault(target_rel, set()).add(rel_path)

    def get_dependencies(self, rel_path: str) -> list[str]:
        if not self._dep_graph:
            self.build_internal_graph()
        return sorted(self._dep_graph.get(rel_path, set()))

    def get_dependents(self, rel_path: str) -> list[str]:
        if not self._reverse_dep_graph:
            self.build_internal_graph()
        return sorted(self._reverse_dep_graph.get(rel_path, set()))

    def detect_circular_dependencies(self) -> list[list[str]]:
        """Find circular dependency cycles using DFS."""
        if not self._dep_graph:
            self.build_internal_graph()

        cycles: list[list[str]] = []
        visited: set[str] = set()
        rec_stack: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.append(node)
            for neighbor in self._dep_graph.get(node, set()):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_stack:
                    # Found cycle
                    idx = rec_stack.index(neighbor)
                    cycle = rec_stack[idx:] + [neighbor]
                    cycles.append(cycle)
            rec_stack.pop()

        for node in list(self._dep_graph.keys()):
            if node not in visited:
                dfs(node)

        return cycles

    def map_source_to_test(self, source_path: str | Path) -> list[str]:
        """Map a source file to relevant unit test files in the workspace."""
        path_str = str(source_path).replace("\\", "/")
        name = Path(path_str).stem  # e.g., 'rules' from 'src/devx/rules.py'

        test_candidates: list[str] = []
        direct_test_name = f"test_{name}.py"

        for rel_path in self.modules:
            if not rel_path.startswith("tests/") and not rel_path.startswith("test/"):
                continue
            if Path(rel_path).name == direct_test_name:
                test_candidates.insert(0, rel_path)
            elif name in rel_path:
                test_candidates.append(rel_path)

        # Also check dependents: if a test file imports this module
        dependents = self.get_dependents(path_str)
        for dep in dependents:
            if (dep.startswith("tests/") or dep.startswith("test/")) and dep not in test_candidates:
                test_candidates.append(dep)

        return test_candidates

    def to_mermaid(self, max_nodes: int = 25) -> str:
        """Render a Mermaid flowchart of the module topology."""
        if not self._dep_graph:
            self.build_internal_graph()

        lines = ["flowchart TD"]
        edges_rendered = 0
        nodes_seen: set[str] = set()

        # Sort by connectivity
        sorted_nodes = sorted(
            self._dep_graph.keys(),
            key=lambda n: len(self._dep_graph.get(n, set())) + len(self._reverse_dep_graph.get(n, set())),
            reverse=True,
        )

        for src in sorted_nodes[:max_nodes]:
            src_clean = Path(src).stem
            for dst in sorted(self._dep_graph.get(src, set())):
                dst_clean = Path(dst).stem
                lines.append(f'    {src_clean}["{src_clean}"] --> {dst_clean}["{dst_clean}"]')
                nodes_seen.add(src_clean)
                nodes_seen.add(dst_clean)
                edges_rendered += 1
                if edges_rendered >= max_nodes * 2:
                    break

        if edges_rendered == 0:
            lines.append('    Empty["No internal cross-module dependencies detected"]')

        return "\n".join(lines)
