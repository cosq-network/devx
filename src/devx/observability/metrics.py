from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Metric:
    """A single metric with labels."""
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metric_type: str = "gauge"  # gauge, counter, histogram


class MetricsCollector:
    """Thread-safe metrics collector for Prometheus exposition."""
    
    def __init__(self):
        self._metrics: dict[str, list[Metric]] = defaultdict(list)
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
    
    def increment(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increment a counter."""
        labels = labels or {}
        key = self._make_key(name, labels)
        with self._lock:
            self._counters[key] += value
            self._metrics[name].append(Metric(name, self._counters[key], labels, metric_type="counter"))
    
    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Set a gauge value."""
        labels = labels or {}
        key = self._make_key(name, labels)
        with self._lock:
            self._gauges[key] = value
            self._metrics[name].append(Metric(name, value, labels, metric_type="gauge"))
    
    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        """Observe a value for histogram."""
        labels = labels or {}
        key = self._make_key(name, labels)
        with self._lock:
            self._histograms[key].append(value)
            self._metrics[name].append(Metric(name, value, labels, metric_type="histogram"))
    
    @contextmanager
    def timing(self, name: str, labels: dict[str, str] | None = None):
        """Context manager for timing operations."""
        labels = labels or {}
        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self.observe(name, duration_ms, labels)
    
    def _make_key(self, name: str, labels: dict[str, str]) -> str:
        """Create a unique key for metric + labels."""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"
    
    def get_metrics(self) -> list[list[Metric]]:
        """Get all metrics, grouped by metric name."""
        with self._lock:
            return list(self._metrics.values())
    
    def export_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        with self._lock:
            # Export counters
            for key, value in self._counters.items():
                name, labels = self._parse_key(key)
                label_str = self._format_labels(labels)
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name}{label_str} {value}")
            
            # Export gauges
            for key, value in self._gauges.items():
                name, labels = self._parse_key(key)
                label_str = self._format_labels(labels)
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name}{label_str} {value}")
            
            # Export histograms with cumulative buckets, sum, and count
            _buckets_ms = (10.0, 50.0, 100.0, 250.0, 500.0, 1000.0, 5000.0, 10000.0, 30000.0, 60000.0)
            for key, values in self._histograms.items():
                if not values:
                    continue
                name, labels = self._parse_key(key)
                label_str = self._format_labels(labels)
                lines.append(f"# TYPE {name} histogram")
                for bucket in _buckets_ms:
                    le_labels = self._format_labels({**labels, "le": f"{bucket:g}"})
                    lines.append(f"{name}_bucket{le_labels} {sum(1 for value in values if value <= bucket)}")
                le_labels = self._format_labels({**labels, "le": "+Inf"})
                lines.append(f"{name}_bucket{le_labels} {len(values)}")
                lines.append(f"{name}_sum{label_str} {sum(values)}")
                lines.append(f"{name}_count{label_str} {len(values)}")
        
        return "\n".join(lines)
    
    def _parse_key(self, key: str) -> tuple[str, dict[str, str]]:
        """Parse metric key back to name and labels."""
        if "{" not in key:
            return key, {}
        name, rest = key.split("{", 1)
        labels_str = rest.rstrip("}")
        labels = {}
        for pair in labels_str.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                labels[k] = v
        return name, labels
    
    def _format_labels(self, labels: dict[str, str]) -> str:
        """Format labels for Prometheus, escaping label values."""
        if not labels:
            return ""
        return "{" + ",".join(f'{k}="{self._escape_label_value(v)}"' for k, v in sorted(labels.items())) + "}"

    @staticmethod
    def _escape_label_value(value: str) -> str:
        """Escape a label value per the Prometheus text exposition format."""
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


# Global metrics collector
_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector."""
    return _metrics


def increment_counter(name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
    """Increment a counter metric."""
    _metrics.increment(name, value, labels)


def set_gauge(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """Set a gauge metric."""
    _metrics.set_gauge(name, value, labels)


def observe_histogram(name: str, value: float, labels: dict[str, str] | None = None) -> None:
    """Observe a histogram value."""
    _metrics.observe(name, value, labels)


def timing(name: str, labels: dict[str, str] | None = None):
    """Context manager for timing operations."""
    return _metrics.timing(name, labels)


# Predefined metric names
class Metrics:
    """Standard metric names for devx."""
    # Run metrics
    RUNS_TOTAL = "devx_runs_total"
    RUNS_DURATION_MS = "devx_runs_duration_ms"
    RUNS_STATUS = "devx_runs_status"
    
    # Tool metrics
    TOOL_CALLS_TOTAL = "devx_tool_calls_total"
    TOOL_CALLS_DURATION_MS = "devx_tool_calls_duration_ms"
    TOOL_CALLS_STATUS = "devx_tool_calls_status"
    
    # LLM metrics
    LLM_CALLS_TOTAL = "devx_llm_calls_total"
    LLM_CALLS_DURATION_MS = "devx_llm_calls_duration_ms"
    LLM_TOKENS_INPUT = "devx_llm_tokens_input"
    LLM_TOKENS_OUTPUT = "devx_llm_tokens_output"
    LLM_CIRCUIT_BREAKER_STATE = "devx_llm_circuit_breaker_state"
    
    # Session metrics
    SESSIONS_ACTIVE = "devx_sessions_active"
    SESSIONS_TOTAL = "devx_sessions_total"
    
    # Error metrics
    ERRORS_TOTAL = "devx_errors_total"
    
    # Cache metrics
    CACHE_HITS = "devx_cache_hits"
    CACHE_MISSES = "devx_cache_misses"