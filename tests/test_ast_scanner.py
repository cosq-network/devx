from __future__ import annotations

from pathlib import Path
from devx.analysis.ast_scanner import CodebaseTopology, scan_module, scan_workspace


def test_scan_module_extracts_symbols_and_metadata(tmp_path: Path):
    source_code = """
import os
from pathlib import Path

class DataProcessor:
    def process(self, value: int) -> str:
        if value < 0:
            raise ValueError("Negative value not allowed")
        return str(value)

async def fetch_async(url: str) -> dict:
    return {"status": "ok"}
"""
    file_path = tmp_path / "processor.py"
    file_path.write_text(source_code, encoding="utf-8")

    mod_info = scan_module(file_path, workspace=tmp_path)
    assert mod_info.relative_path == "processor.py"
    assert "os" in mod_info.imports
    assert "pathlib" in mod_info.import_froms

    assert "DataProcessor" in mod_info.classes
    assert "fetch_async" in mod_info.functions

    # Check method details
    process_sym = next(s for s in mod_info.symbols if s.name == "DataProcessor.process")
    assert process_sym.kind == "function"
    assert ("value", "int") in process_sym.params
    assert process_sym.return_type == "str"
    assert "ValueError" in process_sym.exceptions_raised

    # Check async function
    async_sym = next(s for s in mod_info.symbols if s.name == "fetch_async")
    assert async_sym.kind == "async_function"
    assert async_sym.return_type == "dict"


def test_topology_graph_and_cycles(tmp_path: Path):
    topology = CodebaseTopology(workspace=tmp_path)

    # Setup 3 modules: a -> b -> c -> a (cycle)
    (tmp_path / "a.py").write_text("import b", encoding="utf-8")
    (tmp_path / "b.py").write_text("import c", encoding="utf-8")
    (tmp_path / "c.py").write_text("import a", encoding="utf-8")

    topology.add_module(scan_module(tmp_path / "a.py", workspace=tmp_path))
    topology.add_module(scan_module(tmp_path / "b.py", workspace=tmp_path))
    topology.add_module(scan_module(tmp_path / "c.py", workspace=tmp_path))

    topology.build_internal_graph()

    assert "b.py" in topology.get_dependencies("a.py")
    assert "c.py" in topology.get_dependencies("b.py")
    assert "a.py" in topology.get_dependencies("c.py")

    assert "c.py" in topology.get_dependents("a.py")

    cycles = topology.detect_circular_dependencies()
    assert len(cycles) > 0
    # verify cycle contains a.py, b.py, c.py
    flattened = [item for cycle in cycles for item in cycle]
    assert "a.py" in flattened


def test_source_to_test_mapping(tmp_path: Path):
    src_dir = tmp_path / "src" / "pkg"
    src_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)

    auth_file = src_dir / "auth.py"
    auth_file.write_text("def login(): pass", encoding="utf-8")

    test_file = tests_dir / "test_auth.py"
    test_file.write_text("from pkg.auth import login\ndef test_login(): pass", encoding="utf-8")

    top = scan_workspace(tmp_path)
    top.build_internal_graph()

    mapped = top.map_source_to_test("src/pkg/auth.py")
    assert len(mapped) >= 1
    assert any("test_auth.py" in m for m in mapped)


def test_topology_to_mermaid(tmp_path: Path):
    topology = CodebaseTopology(workspace=tmp_path)
    (tmp_path / "service.py").write_text("import db", encoding="utf-8")
    (tmp_path / "db.py").write_text("pass", encoding="utf-8")

    topology.add_module(scan_module(tmp_path / "service.py", workspace=tmp_path))
    topology.add_module(scan_module(tmp_path / "db.py", workspace=tmp_path))

    mermaid = topology.to_mermaid()
    assert "flowchart TD" in mermaid
    assert "service" in mermaid
    assert "db" in mermaid
