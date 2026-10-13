from pathlib import Path

from typer.testing import CliRunner

from devx.cli.app import app
from devx.config import Settings
from devx.harness.session import SessionStore
from devx.pricing import estimate_cost, format_cost, price_for

runner = CliRunner()


# ── pricing table ──────────────────────────────────────────────────────────


def test_price_for_exact_and_versioned_models():
    assert price_for("gpt-4o-mini") is not None
    assert price_for("gpt-4o-mini-2024-07-18") is not None
    # Longest match wins: gpt-4o-mini must not resolve to gpt-4o.
    assert price_for("gpt-4o-mini") == price_for("gpt-4o-mini-2024-07-18")
    assert price_for("gpt-4o") != price_for("gpt-4o-mini")


def test_price_for_free_markers_are_zero_cost():
    for model in ("openrouter/free", "meta-llama/llama-3.1-8b:free"):
        price = price_for(model)
        assert price is not None
        assert price.input_per_million == 0.0
        assert price.output_per_million == 0.0


def test_price_for_dash_free_suffix_is_not_free():
    # ``-free`` is deliberately NOT a free marker: it is far too loose and can
    # hit unrelated model names (only ``:free`` and ``openrouter/free`` count).
    assert price_for("some-model-free") is None


def test_price_for_unknown_model_returns_none():
    assert price_for("totally-made-up-model") is None
    assert price_for("") is None


def test_estimate_cost_math_and_unknown():
    # 1,000,000 input tokens of gpt-4o-mini costs $0.15.
    assert estimate_cost("gpt-4o-mini", 1_000_000, 0) == 0.15
    # 1,000,000 output tokens costs $0.60.
    assert estimate_cost("gpt-4o-mini", 0, 1_000_000) == 0.60
    assert estimate_cost("unknown-model", 1000, 1000) is None
    assert estimate_cost("openrouter/free", 10_000, 10_000) == 0.0


def test_format_cost():
    assert format_cost(None) == "n/a"
    assert format_cost(0.0) == "$0.00"
    assert format_cost(0.0012) == "$0.0012"
    assert format_cost(1.5) == "$1.50"


# ── usage persistence + aggregation ───────────────────────────────────────


def test_session_store_records_and_aggregates_usage(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.save(None, "usage-session", {"task": "t"})

    recorded = store.record_usage(
        sid,
        "run-1",
        [
            {"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            {"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 0, "completion_tokens": 1000, "total_tokens": 1000},
        ],
    )
    assert recorded == 2

    report = store.aggregate_usage(sid)
    assert report["calls"] == 2
    assert report["prompt_tokens"] == 1000
    assert report["completion_tokens"] == 1500
    assert report["total_tokens"] == 2500
    assert report["unpriced_models"] == []
    # 1000/1e6*0.15 + 1500/1e6*0.60
    assert round(report["estimated_cost_usd"], 6) == round(0.00015 + 0.0009, 6)
    assert len(report["by_model"]) == 1
    assert report["by_model"][0]["model"] == "gpt-4o-mini"


def test_session_store_aggregate_usage_reports_unpriced_models(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions.db")
    store.record_usage(
        "s1", "r",
        [{"model": "mystery-model", "provider": "custom", "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}],
    )
    report = store.aggregate_usage("s1")
    assert report["unpriced_models"] == ["mystery-model"]
    assert report["estimated_cost_usd"] == 0.0


def test_session_store_record_usage_accepts_objects(tmp_path: Path):
    from devx.llm import TokenUsage

    store = SessionStore(tmp_path / "sessions.db")
    store.record_usage("s2", "r", [TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10, model="gpt-4o", provider="openai")])
    report = store.aggregate_usage("s2")
    assert report["calls"] == 1
    assert report["total_tokens"] == 10


def test_token_tracker_drain_reports_only_new_entries():
    from devx.llm import TokenUsage, TokenUsageTracker

    tracker = TokenUsageTracker()
    tracker.record(TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2, model="gpt-4o-mini", provider="openai"))
    first = tracker.drain()
    assert len(first) == 1
    assert tracker.drain() == []
    tracker.record(TokenUsage(prompt_tokens=3, completion_tokens=3, total_tokens=6, model="gpt-4o-mini", provider="openai"))
    second = tracker.drain()
    assert len(second) == 1
    # History is preserved for get_stats.
    assert tracker.get_stats().call_count == 2


# ── usage recording from LLM responses ────────────────────────────────────


def test_record_response_usage_records_usage_metadata():
    from types import SimpleNamespace

    from devx.llm import get_token_tracker, record_response_usage

    tracker = get_token_tracker()
    tracker.clear()
    try:
        response = SimpleNamespace(
            usage_metadata={"input_tokens": 120, "output_tokens": 40, "total_tokens": 160},
            content="ok",
        )
        record_response_usage(response, model="gpt-4o-mini", provider="openai")
        stats = tracker.get_stats()
        assert stats.call_count == 1
        assert stats.total_tokens == 160
        assert stats.by_model["gpt-4o-mini"]["total"] == 160
    finally:
        tracker.clear()


def test_record_response_usage_skips_metadata_free_responses():
    from types import SimpleNamespace

    from devx.llm import get_token_tracker, record_response_usage

    tracker = get_token_tracker()
    tracker.clear()
    try:
        # Mocks and streaming fallbacks expose no usage metadata: skipped.
        record_response_usage(SimpleNamespace(content="no usage"), model="gpt-4o-mini", provider="openai")
        assert tracker.get_stats().call_count == 0
    finally:
        tracker.clear()


def test_invoke_with_retry_records_usage():
    from types import SimpleNamespace

    from devx.llm import get_token_tracker, invoke_with_retry

    class FakeModel:
        model_name = "gpt-4o-mini"

        def invoke(self, prompt):
            return SimpleNamespace(
                usage_metadata={"input_tokens": 300, "output_tokens": 100, "total_tokens": 400},
                content="plan",
            )

    tracker = get_token_tracker()
    tracker.clear()
    try:
        response = invoke_with_retry(FakeModel(), "plan a task", provider="openai")
        assert response.content == "plan"
        stats = tracker.get_stats()
        assert stats.call_count == 1
        assert stats.total_tokens == 400
        assert stats.by_model["gpt-4o-mini"]["total"] == 400
    finally:
        tracker.clear()


def test_stream_agent_text_records_usage():
    from types import SimpleNamespace

    from devx.agents.runtime import stream_agent_text
    from devx.llm import get_token_tracker

    class FakeAgent:
        model_name = "gpt-4o-mini"

        def stream(self, messages, stream_mode="messages"):
            yield (
                SimpleNamespace(
                    type="AIMessageChunk",
                    content="hello",
                    usage_metadata={"input_tokens": 25, "output_tokens": 5, "total_tokens": 30},
                ),
            )

    tracker = get_token_tracker()
    tracker.clear()
    try:
        text = stream_agent_text(FakeAgent(), [{"role": "user", "content": "hi"}])
        assert text == "hello"
        stats = tracker.get_stats()
        assert stats.call_count == 1
        assert stats.total_tokens == 30
        assert stats.by_model["gpt-4o-mini"]["total"] == 30
    finally:
        tracker.clear()


def test_record_usage_resolves_session_name_to_id(tmp_path: Path):
    from devx.llm import TokenUsage

    store = SessionStore(tmp_path / "sessions.db")
    sid = store.save(None, "session-mist-42", {"task": "t"})

    # Keying by the session *name* must resolve to the session id so lookups
    # by the resolved id and by run id agree on the stored rows.
    store.record_usage(
        "session-mist-42",
        "run-9",
        [TokenUsage(model="gpt-4o-mini", provider="openai", prompt_tokens=10, completion_tokens=5, total_tokens=15)],
    )
    assert store.aggregate_usage(sid)["calls"] == 1
    assert store.aggregate_usage(sid)["total_tokens"] == 15
    assert store.aggregate_usage(None, "run-9")["calls"] == 1


# ── CLI ────────────────────────────────────────────────────────────────────


def test_cli_usage_empty(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "sessions.db"
    SessionStore(db_file)
    settings = Settings(session_db=db_file)
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))

    result = runner.invoke(app, ["usage"])
    assert result.exit_code == 0
    assert "calls: 0" in result.output
    assert "estimated cost: $0.00" in result.output


def test_cli_usage_json_reports_cost(tmp_path: Path, monkeypatch):
    import json

    db_file = tmp_path / "sessions.db"
    store = SessionStore(db_file)
    store.record_usage(
        "sess", "run",
        [{"model": "gpt-4o-mini", "provider": "openai", "prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000}],
    )
    settings = Settings(session_db=db_file)
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))

    result = runner.invoke(app, ["usage", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["calls"] == 1
    assert payload["total_tokens"] == 1_000_000
    assert payload["estimated_cost_usd"] == 0.15


def test_cli_usage_human_output_lists_models(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "sessions.db"
    store = SessionStore(db_file)
    store.record_usage(
        "sess", "run",
        [{"model": "gpt-4o", "provider": "openai", "prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000}],
    )
    settings = Settings(session_db=db_file)
    monkeypatch.setattr("devx.cli.app.Settings.load", staticmethod(lambda: settings))

    result = runner.invoke(app, ["usage"])
    assert result.exit_code == 0
    assert "gpt-4o" in result.output
    assert "estimated cost:" in result.output
