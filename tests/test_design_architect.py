from __future__ import annotations

from pathlib import Path
from devx.config import Settings
from devx.design.architect import ArchitectureEngine


def test_architecture_engine_diagrams(tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="sk-test")
    engine = ArchitectureEngine(settings)

    # Setup sample classes and functions
    (tmp_path / "models.py").write_text("""
class BaseEntity:
    pass

class User(BaseEntity):
    def get_id(self): return 1
""", encoding="utf-8")

    (tmp_path / "service.py").write_text("""
from models import User

def handle_request():
    u = User()
    return u.get_id()
""", encoding="utf-8")

    # Flowchart
    flowchart = engine.generate_diagram(kind="flowchart")
    assert "flowchart TD" in flowchart
    assert "service" in flowchart

    # Class diagram
    class_diag = engine.generate_diagram(kind="class")
    assert "classDiagram" in class_diag
    assert "User" in class_diag

    # Sequence diagram
    seq_diag = engine.generate_diagram(kind="sequence")
    assert "sequenceDiagram" in seq_diag


def test_create_adr(tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="sk-test")
    engine = ArchitectureEngine(settings)

    rec1, path1 = engine.create_adr(
        title="Use SQLite for Local Caching",
        context="We need fast embedded zero-config persistence.",
        decision="Adopt SQLite WAL mode.",
        consequences="Fast reads and atomic writes; single-writer limitation.",
    )

    assert rec1.number == 1
    assert path1.exists()
    content = path1.read_text(encoding="utf-8")
    assert "ADR-0001: Use SQLite for Local Caching" in content
    assert "Adopt SQLite WAL mode." in content

    # Create second ADR
    rec2, path2 = engine.create_adr(
        title="Async Subprocess Execution",
        context="Non-blocking command execution.",
        decision="Use asyncio or ThreadPoolExecutor.",
        consequences="Scalable background jobs.",
    )
    assert rec2.number == 2
    assert path2.exists()


def test_deep_research_trace(tmp_path: Path):
    settings = Settings(workspace=tmp_path, api_key="sk-test")
    engine = ArchitectureEngine(settings)

    (tmp_path / "auth.py").write_text("""
def verify_token(token: str) -> bool:
    if not token:
        raise ValueError("empty token")
    return True
""", encoding="utf-8")

    (tmp_path / "api.py").write_text("""
from auth import verify_token

def login_handler(t):
    return verify_token(t)
""", encoding="utf-8")

    trace = engine.deep_research_trace("verify_token")
    assert trace["symbol"] == "verify_token"
    assert len(trace["definitions"]) >= 1
    assert trace["definitions"][0]["file"] == "auth.py"
    assert "ValueError" in trace["definitions"][0]["exceptions"]

    assert len(trace["callers"]) >= 1
    assert trace["callers"][0]["file"] == "api.py"
