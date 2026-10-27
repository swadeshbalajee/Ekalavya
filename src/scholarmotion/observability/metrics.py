"""Small dependency-free Prometheus metrics for API, provider, and workflow health."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock

_lock = Lock()
_counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
_durations: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)


def _labels(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in values.items()))


def _inc(name: str, **labels: object) -> None:
    with _lock:
        _counters[(name, _labels(**labels))] += 1


def observe_http(*, method: str, status: int, duration_seconds: float) -> None:
    labels = {"method": method, "status": status}
    with _lock:
        _counters[("scholarmotion_http_requests_total", _labels(**labels))] += 1
        _durations[("scholarmotion_http_request_duration_seconds", _labels(**labels))].append(
            duration_seconds
        )


def record_provider_event(*, provider: str, outcome: str) -> None:
    _inc("scholarmotion_provider_requests_total", provider=provider, outcome=outcome)


def record_workflow_terminal(*, workflow_type: str, status: str) -> None:
    _inc("scholarmotion_workflows_total", workflow_type=workflow_type, status=status)


def prometheus_payload() -> str:
    """Render bounded in-process metrics in Prometheus' text exposition format."""
    lines = [
        "# HELP scholarmotion_http_requests_total Total completed HTTP requests.",
        "# TYPE scholarmotion_http_requests_total counter",
        "# HELP scholarmotion_http_request_duration_seconds Completed HTTP request duration.",
        "# TYPE scholarmotion_http_request_duration_seconds summary",
        "# HELP scholarmotion_provider_requests_total Provider call outcomes.",
        "# TYPE scholarmotion_provider_requests_total counter",
        "# HELP scholarmotion_workflows_total Terminal workflow outcomes.",
        "# TYPE scholarmotion_workflows_total counter",
    ]
    with _lock:
        counters = list(_counters.items())
        durations = [(key, list(values)) for key, values in _durations.items()]
    for (name, labels), value in sorted(counters):
        lines.append(f"{name}{_format_labels(labels)} {value:.0f}")
    for (name, labels), values in sorted(durations):
        if not values:
            continue
        base = _format_labels(labels)
        total, count = sum(values), len(values)
        lines.append(f"{name}_count{base} {count}")
        lines.append(f"{name}_sum{base} {total:.6f}")
    return "\n".join(lines) + "\n"


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in labels) + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
