from __future__ import annotations

from pathlib import Path
from devx.harness.session import SessionStore


def test_session_fork_and_tree(tmp_path: Path):
    db_path = tmp_path / "test_sessions.db"
    store = SessionStore(db_path)

    # 1. Create root session
    root_id = store.save(
        None,
        "root-session",
        {"messages": [{"role": "user", "content": "hello from root"}]},
        {"author": "test"},
    )
    assert root_id is not None
    root_session = store.load(root_id)
    assert root_session is not None
    assert root_session["name"] == "root-session"
    assert root_session.get("parent_id") is None

    # Add event to root
    store.append_event(root_id, "run-1", {"type": "thought", "content": "planning"})

    # 2. Fork into branch 1
    branch1_id = store.fork_session(root_id, new_name="auth-feature")
    assert branch1_id != root_id
    b1 = store.load(branch1_id)
    assert b1 is not None
    assert b1["name"] == "auth-feature"
    assert b1["parent_id"] == root_id
    assert b1["metadata"]["forked_from"] == root_id
    assert b1["state"]["messages"] == [{"role": "user", "content": "hello from root"}]

    # Verify events were copied
    b1_events = store.events(branch1_id)
    assert len(b1_events) == 1
    assert b1_events[0]["event"]["content"] == "planning"

    # 3. Fork into sub-branch of branch 1
    sub_branch_id = store.fork_session(branch1_id, new_name="auth-oauth")
    sub = store.load(sub_branch_id)
    assert sub is not None
    assert sub["parent_id"] == branch1_id

    # 4. Check session tree
    tree = store.get_session_tree()
    assert len(tree) == 1  # 1 root
    root_node = tree[0]
    assert root_node["id"] == root_id
    assert len(root_node["children"]) == 1
    b1_node = root_node["children"][0]
    assert b1_node["id"] == branch1_id
    assert len(b1_node["children"]) == 1
    assert b1_node["children"][0]["id"] == sub_branch_id


def test_session_compact(tmp_path: Path):
    db_path = tmp_path / "test_sessions.db"
    store = SessionStore(db_path)

    # Create session with many messages
    messages = [
        {"role": "user", "content": "fix bug in module"},
        {"role": "assistant", "content": "looking at files... " * 50},
        {"role": "tool", "content": "result output line 1... " * 100},
        {"role": "assistant", "content": "trying another fix... " * 50},
        {"role": "tool", "content": "another tool output... " * 100},
        {"role": "assistant", "content": "tests failed, retrying... " * 50},
        {"role": "user", "content": "keep working on it"},
        {"role": "assistant", "content": "fixed tests!"},
    ]
    sid = store.save(None, "long-session", {"messages": messages})

    # Compact session
    res = store.compact_session(sid, max_messages=4, max_chars=1000, summary_chars=300)
    assert res["compacted"] is True
    assert res["removed_messages"] > 0

    # Reload session and check compacted state
    compacted = store.load(sid)
    assert compacted is not None
    compacted_msgs = compacted["state"]["messages"]
    assert len(compacted_msgs) <= 4
    assert compacted_msgs[0]["role"] == "summary"
    assert "Compacted session history" in compacted_msgs[0]["content"]


def test_cli_session_commands(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner
    from devx.cli.app import app

    db_path = tmp_path / "cli_sessions.db"
    monkeypatch.setenv("DEVX_SESSION_DB", str(db_path))

    runner = CliRunner()

    # 1. Initially empty list
    res = runner.invoke(app, ["session", "list"])
    assert res.exit_code == 0
    assert "No saved sessions" in res.output

    # 2. Seed a session
    store = SessionStore(db_path)
    sid = store.save(None, "main-flow", {"messages": [{"role": "user", "content": "hi"}]})

    # 3. List
    res = runner.invoke(app, ["session", "list"])
    assert res.exit_code == 0
    assert "main-flow" in res.output

    # 4. Fork
    res = runner.invoke(app, ["session", "fork", sid, "--name", "forked-flow"])
    assert res.exit_code == 0
    assert "Forked session" in res.output

    # 5. Tree
    res = runner.invoke(app, ["session", "tree"])
    assert res.exit_code == 0
    assert "main-flow" in res.output
    assert "forked-flow" in res.output

    # 6. Compact
    res = runner.invoke(app, ["session", "compact", sid])
    assert res.exit_code == 0
    assert "already compact" in res.output
