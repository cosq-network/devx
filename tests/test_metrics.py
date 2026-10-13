"""Tests for the Prometheus/JSON metrics collection layer (``devx metrics``)."""

from __future__ import annotations

from devx.observability import metrics as metrics_mod
from devx.observability.metrics import Metrics, MetricsCollector


def flatten(collector: MetricsCollector) -> list:
    return [metric for group in collector.get_metrics() for metric in group]


class TestMetricsCollector:
    def test_increment_accumulates_per_labels(self):
        collector = MetricsCollector()
        collector.increment("calls", labels={"tool": "read"})
        collector.increment("calls", labels={"tool": "read"})
        collector.increment("calls", labels={"tool": "write"})
        read = [m.value for m in flatten(collector) if m.labels.get("tool") == "read"]
        assert read == [1.0, 2.0]
        write = [m.value for m in flatten(collector) if m.labels.get("tool") == "write"]
        assert write == [1.0]

    def test_set_gauge_replaces_value(self):
        collector = MetricsCollector()
        collector.set_gauge("workers", 1)
        collector.set_gauge("workers", 5)
        gauge = flatten(collector)
        assert [m.value for m in gauge] == [1.0, 5.0]
        assert collector._gauges["workers"] == 5.0

    def test_observe_appends_histogram_values(self):
        collector = MetricsCollector()
        collector.observe("latency_ms", 1.5)
        collector.observe("latency_ms", 2.5)
        assert collector._histograms["latency_ms"] == [1.5, 2.5]
        assert all(m.metric_type == "histogram" for m in flatten(collector))

    def test_timing_context_manager_observes_duration(self):
        collector = MetricsCollector()
        with collector.timing("op", labels={"x": "y"}):
            pass
        observed = [m for m in flatten(collector) if m.name == "op"]
        assert len(observed) == 1
        assert observed[0].value >= 0.0
        assert observed[0].labels == {"x": "y"}
        assert observed[0].metric_type == "histogram"

    def test_timing_never_entered_records_nothing(self):
        collector = MetricsCollector()
        collector.timing("op")
        assert flatten(collector) == []

    def test_export_counter(self):
        collector = MetricsCollector()
        collector.increment("tools_used", 3)
        text = collector.export_prometheus()
        assert "# TYPE tools_used counter" in text
        assert "tools_used 3.0" in text

    def test_export_gauge(self):
        collector = MetricsCollector()
        collector.set_gauge("workers", 2)
        text = collector.export_prometheus()
        assert "# TYPE workers gauge" in text
        assert "workers 2" in text

    def test_export_histogram_with_buckets(self):
        collector = MetricsCollector()
        collector.observe("dur", 1.5)
        collector.observe("dur", 2.5)
        text = collector.export_prometheus()
        assert "# TYPE dur histogram" in text
        assert 'dur_bucket{le="10"} 2' in text
        assert 'dur_bucket{le="+Inf"} 2' in text
        assert "dur_sum 4.0" in text
        assert "dur_count 2" in text
        assert "dur_avg" not in text

    def test_export_escapes_label_values(self):
        collector = MetricsCollector()
        collector.increment("calls", labels={"path": 'a"b\\c\nd'})
        text = collector.export_prometheus()
        assert r'calls{path="a\"b\\c\nd"} 1.0' in text

    def test_export_formats_labels_sorted(self):
        collector = MetricsCollector()
        collector.increment("calls", labels={"b": "2", "a": "1"})
        text = collector.export_prometheus()
        assert 'calls{a="1",b="2"} 1.0' in text

    def test_parse_key_round_trip(self):
        collector = MetricsCollector()
        assert collector._parse_key("m") == ("m", {})
        assert collector._parse_key("m{x=1,y=2}") == ("m", {"x": "1", "y": "2"})


class TestGlobalHelpers:
    def test_helpers_drive_the_global_collector(self, monkeypatch):
        fresh = MetricsCollector()
        monkeypatch.setattr(metrics_mod, "_metrics", fresh)
        metrics_mod.increment_counter("devx_runs_total", 1)
        metrics_mod.set_gauge("devx_sessions_active", 3)
        metrics_mod.observe_histogram("devx_llm_calls_duration_ms", 42.0)
        with metrics_mod.timing("devx_runs_duration_ms"):
            pass
        assert fresh._counters["devx_runs_total"] == 1.0
        assert fresh._gauges["devx_sessions_active"] == 3.0
        assert fresh._histograms["devx_llm_calls_duration_ms"] == [42.0]
        assert len(fresh._histograms["devx_runs_duration_ms"]) == 1
        text = fresh.export_prometheus()
        assert "# TYPE devx_runs_total counter" in text
        assert "devx_runs_total 1.0" in text

    def test_get_metrics_returns_global_collector(self):
        assert metrics_mod.get_metrics() is metrics_mod._metrics


def test_metric_names_constants():
    assert Metrics.RUNS_TOTAL == "devx_runs_total"
    assert Metrics.LLM_TOKENS_INPUT == "devx_llm_tokens_input"
    assert Metrics.ERRORS_TOTAL == "devx_errors_total"
    assert Metrics.CACHE_HITS == "devx_cache_hits"


def test_metric_dataclass_defaults():
    metric = metrics_mod.Metric("x", 1.0)
    assert metric.labels == {}
    assert metric.metric_type == "gauge"
    assert metric.timestamp > 0