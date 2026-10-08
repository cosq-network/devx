from pathlib import Path
from types import SimpleNamespace

import pytest

from devx.agents.graph import DevState, DevxState, build_dev_graph, build_devx_graph
from devx.agents.supervisor import SupervisorRunner
from devx.completion.file_path import FilePathCompleter
from devx.completion.parser import parse_file_mentions
from devx.config import Settings
from devx.harness.approval import ApprovalDecision, ApprovalManager, ApprovalRequest
from devx.harness.changes import ChangeJournal
from devx.harness.permissions import PermissionError, PermissionPolicy
from devx.harness.session import SessionStore
from devx.harness.tools import WorkspaceTools
from devx.token_optim.cache import SemanticCache, fingerprint_files
from devx.token_optim.context import read_context, select_relevant_files


def test_mentions_resolve_and_report_missing(tmp_path: Path):
    (tmp_path / "a.py").write_text("print(1)")
    cleaned, files, missing = parse_file_mentions("fix @a.py and @missing.py", tmp_path)
    assert files == [(tmp_path / "a.py").resolve()]
    assert missing == ["missing.py"]
    assert "[file:a.py]" in cleaned


def test_workspace_escape_is_rejected(tmp_path: Path):
    policy = PermissionPolicy(tmp_path, approval_required=False)
    with pytest.raises(PermissionError):
        policy.path(Path("..") / "secret.txt")


def test_writes_are_approval_gated(tmp_path: Path):
    with pytest.raises(PermissionError):
        PermissionPolicy(tmp_path).path(Path("x"), write=True)


def test_approved_write_is_atomic_and_readable(tmp_path: Path):
    tools = WorkspaceTools(PermissionPolicy(tmp_path), timeout=2)
    result = tools.write("x.txt", "hello", approved=True)
    assert result.ok
    assert tools.read("x.txt").output == "hello"


def test_replace_requires_unique_match_and_expected_hash(tmp_path: Path):
    path = tmp_path / "x.txt"
    path.write_text("one\none\n")
    tools = WorkspaceTools(PermissionPolicy(tmp_path), timeout=2)
    assert not tools.replace(path, "one", "two", approved=True).ok
    path.write_text("one\n")
    from devx.harness.changes import file_hash
    assert tools.replace(path, "one", "two", expected_hash="stale", approved=True).ok is False
    assert tools.replace(path, "one", "two", expected_hash=file_hash(path), approved=True).ok


def test_fingerprint_changes_when_file_changes(tmp_path: Path):
    path = tmp_path / "x.txt"
    path.write_text("one")
    first = fingerprint_files([path])
    path.write_text("two")
    assert fingerprint_files([path]) != first


def test_settings_loads_workspace_dotenv_with_environment_precedence(tmp_path: Path, monkeypatch):
    (tmp_path / ".env").write_text(
        "DEV_BASE_URL=https://workspace.example/v1\nDEV_MODEL=workspace-model\nDEV_APPROVAL_REQUIRED=false\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("DEV_BASE_URL", raising=False)
    monkeypatch.setenv("DEV_MODEL", "environment-model")
    monkeypatch.delenv("DEV_APPROVAL_REQUIRED", raising=False)

    settings = Settings.load(workspace=tmp_path)

    assert settings.base_url == "https://workspace.example/v1"
    assert settings.model == "environment-model"
    assert settings.approval_required is False


def test_invalid_boolean_configuration_is_rejected(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DEVX_APPROVAL_REQUIRED", "definitely-not-a-boolean")
    with pytest.raises(ValueError, match="DEVX_APPROVAL_REQUIRED"):
        Settings.load(workspace=tmp_path)


def test_settings_load_uses_runtime_default_paths():
    settings = Settings.load(workspace=Path.cwd())
    assert settings.session_db.name == "sessions.db"
    assert settings.cache_db.name == "cache.db"
    assert settings.session_db.parent.name == ".devx"
    assert settings.cache_db.parent.name == ".devx"


def test_workspace_can_be_selected_by_environment(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEVX_WORKSPACE", str(Path.cwd().parent))

    settings = Settings.load()

    assert settings.workspace == tmp_path.parent.resolve()


def test_commands_are_gated_and_dangerous_commands_rejected(tmp_path: Path):
    tools = WorkspaceTools(PermissionPolicy(tmp_path), timeout=2)
    assert not tools.run(["python", "-c", "print(1)"]).ok
    with pytest.raises(PermissionError):
        tools.run(["rm", "-rf", "."], approved=True)
    with pytest.raises(PermissionError):
        tools.run(["python", "-c", "from pathlib import Path; Path('x').unlink()"], approved=True)


def test_approval_manager_defaults_to_deny_and_records_request():
    manager = ApprovalManager()
    assert not manager.allows("r1", "write", "x.py")
    assert manager.requests == [ApprovalRequest("r1", "write", "x.py", "")]


def test_approval_manager_accepts_typed_decisions():
    manager = ApprovalManager(lambda request: ApprovalDecision.APPROVE)
    assert manager.allows("r1", "write", "x.py")


def test_sessions_round_trip(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.save(None, "demo", {"task": "hello"})
    assert store.load(sid)["state"] == {"task": "hello"}
    assert store.list_sessions()[0]["name"] == "demo"


def test_sessions_can_rename_export_and_import(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.save(None, "demo", {"task": "hello"})
    store.append_event(sid, "run-1", {"type": "completed"})
    assert store.rename(sid, "renamed")
    export_path = store.export_session("renamed", tmp_path / "session.json")

    imported = SessionStore(tmp_path / "imported.db").import_session(export_path, "copy")
    imported_store = SessionStore(tmp_path / "imported.db")
    assert imported_store.load(imported)["name"] == "copy"
    assert imported_store.events(imported)[0]["event"] == {"type": "completed"}


def test_deleting_session_also_deletes_event_history(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.save(None, "demo", {"task": "hello"})
    store.append_event(sid, "run-1", {"type": "completed"})

    assert store.delete(sid)
    assert store.events(sid) == []


def test_graph_has_bounded_result():
    result = build_devx_graph(max_iterations=1).invoke({"task": "test"})
    assert result["status"] == "completed"
    assert result["messages"][-1]["role"] == "assistant"
    compat = build_dev_graph(max_iterations=1).invoke(DevState(task="test"))
    assert compat["status"] == "completed"
    assert DevState is DevxState


def test_context_is_bounded_and_deterministic(tmp_path: Path):
    paths = []
    for name in ("one.py", "two.py", "three.py"):
        path = tmp_path / name
        path.write_text(name)
        paths.append(path)
    assert len(select_relevant_files("one", paths, max_files=2)) == 2
    context = read_context(paths, max_total_bytes=10)
    assert len(context.encode("utf-8")) <= 10


def test_context_selection_with_embedder_list_scores(tmp_path: Path):
    f1 = tmp_path / "apple.py"
    f1.write_text("apple fruit")
    f2 = tmp_path / "orange.py"
    f2.write_text("orange citrus")

    class FakeEmbedder:
        def embed(self, texts: list[str]) -> list[list[float]]:
            results = []
            for t in texts:
                results.append([1.0 if "apple" in t else 0.0, 1.0 if "orange" in t else 0.0])
            return results

    selected = select_relevant_files("apple", [f1, f2], max_files=1, embedder=FakeEmbedder())
    assert selected == [f1]


def test_rollback_preserves_backup_file(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")
    target = tmp_path / "file.txt"
    target.write_text("v1")
    tools = WorkspaceTools(PermissionPolicy(tmp_path), journal=journal, run_id="run1", timeout=2)
    tools.write("file.txt", "v2", approved=True)
    assert target.read_text() == "v2"

    backup_dir = tmp_path / "backups" / "run1"
    backups = list(backup_dir.glob("*.v1*")) or list(backup_dir.iterdir())
    assert len(backups) == 1
    backup_file = backups[0]
    assert backup_file.exists()

    restored = journal.rollback("run1")
    assert str(target) in restored
    assert target.read_text() == "v1"
    assert backup_file.exists()


def test_rollback_keeps_backups_distinct_for_same_named_files(tmp_path: Path):
    journal = ChangeJournal(tmp_path / "changes.db")
    tools = WorkspaceTools(PermissionPolicy(tmp_path), journal=journal, run_id="run1", timeout=2)
    for relative in ("a/file.txt", "b/file.txt"):
        path = tmp_path / relative
        path.parent.mkdir()
        path.write_text("same")
        assert tools.write(relative, relative, approved=True).ok

    restored = journal.rollback("run1")

    assert sorted(Path(path).relative_to(tmp_path).as_posix() for path in restored) == [
        "a/file.txt", "b/file.txt"
    ]
    assert (tmp_path / "a/file.txt").read_text() == "same"
    assert (tmp_path / "b/file.txt").read_text() == "same"

def test_sqlite_wal_and_concurrency(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    with store._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert mode.lower() == "wal"
        assert timeout == 5000

    journal = ChangeJournal(tmp_path / "changes.db")
    with journal._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert mode.lower() == "wal"
        assert timeout == 5000

    cache = SemanticCache(tmp_path / "cache.db")
    with cache._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        assert mode.lower() == "wal"
        assert timeout == 5000


def test_write_cleans_up_tmp_file_on_failure(tmp_path: Path, monkeypatch):
    tools = WorkspaceTools(PermissionPolicy(tmp_path), timeout=2)
    target = tmp_path / "target.txt"

    def fail_replace(self, dest):
        raise OSError("Simulated replace disk failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    res = tools.write(target, "fail content", approved=True)
    assert not res.ok
    assert not list(tmp_path.glob(".target.txt.devx-tmp-*"))


def test_file_path_completion_filters_clutter(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / "main.py").write_text("print(1)")

    class DummyDoc:
        def __init__(self, text):
            self.text_before_cursor = text

    completer = FilePathCompleter(workspace=tmp_path, limit=10)
    completions = [c.text for c in completer.get_completions(DummyDoc("@"), None)]
    assert "@main.py" in completions
    assert "@.git/" not in completions
    assert "@node_modules/" not in completions
    assert "@__pycache__/" not in completions
    assert "@.env" not in completions

    dot_completions = [c.text for c in completer.get_completions(DummyDoc("@."), None)]
    assert "@.env" in dot_completions
    assert "@.git/" not in dot_completions


def test_read_and_command_output_are_bounded(tmp_path: Path):
    target = tmp_path / "large.txt"
    target.write_text("x" * 100)
    tools = WorkspaceTools(PermissionPolicy(tmp_path, approval_required=False), max_bytes=10, timeout=2)

    assert len(tools.read(target).output.encode()) <= 10
    result = tools.run(["python", "-c", "print('x' * 100)"], approved=True)
    assert len(result.output.encode()) <= 10


def test_write_and_replace_previews_do_not_mutate_files(tmp_path: Path):
    target = tmp_path / "preview.txt"
    target.write_text("before\n", encoding="utf-8")
    tools = WorkspaceTools(PermissionPolicy(tmp_path), max_bytes=100, timeout=2)

    write_preview = tools.preview_write(target, "after\n")
    replace_preview = tools.preview_replace(target, "before", "after")

    assert "-before" in write_preview and "+after" in write_preview
    assert "-before" in replace_preview and "+after" in replace_preview
    assert target.read_text(encoding="utf-8") == "before\n"


def test_semantic_cache_isolated_by_namespace(tmp_path: Path):
    database = tmp_path / "cache.db"
    first = SemanticCache(database, namespace="provider:model:embed-a")
    second = SemanticCache(database, namespace="provider:model:embed-b")

    first.set("same query", "first response")

    assert first.get("same query") == "first response"
    assert second.get("same query") is None


def test_settings_loads_context_limit_and_lint_command(tmp_path: Path, monkeypatch):
    (tmp_path / ".env").write_text(
        "DEVX_MAX_CONTEXT_FILES=3\nDEVX_LINT_COMMAND=ruff check src\n", encoding="utf-8"
    )

    settings = Settings.load(workspace=tmp_path)

    assert settings.max_context_files == 3
    assert settings.lint_command == "ruff check src"


def test_fallback_supervisor_supports_plan_only(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "devx.llm.create_llm", lambda _settings: object()
    )
    monkeypatch.setattr(
        "devx.agents.supervisor.invoke_with_retry",
        lambda _model, _prompt, **_kwargs: SimpleNamespace(content="a safe plan"),
    )
    settings = Settings(
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
        approval_required=False,
    )
    state = DevxState(task="inspect the project", files_in_context=[])

    result = SupervisorRunner(
        settings, ApprovalManager(lambda _request: ApprovalDecision.APPROVE), plan_only=True
    )(state)

    assert result.status == "plan_only"
    assert result.result == "a safe plan"
    assert any(event["type"] == "plan_ready" for event in result.events)


def test_supervisor_applies_context_file_cap_without_embeddings(monkeypatch, tmp_path: Path):
    apple = tmp_path / "apple.py"
    orange = tmp_path / "orange.py"
    apple.write_text("apple implementation", encoding="utf-8")
    orange.write_text("orange implementation", encoding="utf-8")
    prompts: list[str] = []

    monkeypatch.setattr("devx.llm.create_llm", lambda _settings: object())
    monkeypatch.setattr(
        "devx.agents.supervisor.invoke_with_retry",
        lambda _model, prompt, **_kwargs: prompts.append(prompt) or SimpleNamespace(content="plan"),
    )
    settings = Settings(
        workspace=tmp_path,
        session_db=tmp_path / "sessions.db",
        max_context_files=1,
        approval_required=False,
    )
    state = DevxState(
        task="fix apple", files_in_context=[str(apple), str(orange)]
    )

    SupervisorRunner(
        settings, ApprovalManager(lambda _request: ApprovalDecision.APPROVE), plan_only=True
    )(state)

    assert len(state.files_in_context) == 1
    assert state.files_in_context[0] == str(apple)
    assert "orange implementation" not in prompts[0]


def test_search_output_is_bounded(tmp_path: Path):
    (tmp_path / "large.txt").write_text("needle " * 100)
    tools = WorkspaceTools(PermissionPolicy(tmp_path, approval_required=False), max_bytes=25, timeout=2)

    assert len(tools.search("needle").output.encode()) <= 25


def test_permission_policy_allows_safe_subcommand_flags(tmp_path: Path):
    policy = PermissionPolicy(tmp_path, approval_required=False)
    assert policy.command(["pytest", "-k", "rm"]) == ["pytest", "-k", "rm"]
    assert policy.command(["ruff", "format"]) == ["ruff", "format"]
    assert policy.command("pytest -k rm") == ["pytest", "-k", "rm"]

    with pytest.raises(PermissionError, match="Destructive command"):
        policy.command(["rm", "-rf", "."])

    with pytest.raises(PermissionError, match="Destructive command"):
        policy.command("rm -rf .")

    with pytest.raises(PermissionError, match="Destructive command"):
        policy.command(["sh", "-c", "rm -rf ."])

    with pytest.raises(PermissionError, match="System-level command"):
        policy.command(["format", "C:"])

    with pytest.raises(PermissionError, match="Destructive command"):
        policy.command(["env", "rm", "-rf", "."])

    with pytest.raises(PermissionError, match="Destructive command"):
        policy.command(["sudo", "-u", "user", "rm", "-rf", "."])

    with pytest.raises(PermissionError, match="Destructive command"):
        policy.command(["python", "-c", "import os; os.remove('x')"])

