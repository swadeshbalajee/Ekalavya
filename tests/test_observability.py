from scholarmotion.observability.metrics import (
    observe_http,
    prometheus_payload,
    record_provider_event,
    record_workflow_terminal,
)


def test_metrics_export_includes_request_provider_and_workflow_outcomes():
    observe_http(method="POST", status=202, duration_seconds=0.125)
    record_provider_event(provider="gemini", outcome="fallback")
    record_workflow_terminal(workflow_type="BuildVideoWorkflow", status="COMPLETED")

    payload = prometheus_payload()

    assert 'scholarmotion_http_requests_total{method="POST",status="202"}' in payload
    assert 'scholarmotion_provider_requests_total{outcome="fallback",provider="gemini"}' in payload
    assert (
        'scholarmotion_workflows_total{status="COMPLETED",workflow_type="BuildVideoWorkflow"}'
        in payload
    )
