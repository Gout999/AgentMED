"""Quality client request binding and timeout tests (no network)."""
from __future__ import annotations

import requests

from eval_harness.client import QualityAPIClient
from eval_harness.config import Settings


class _Response:
    status_code = 200
    text = ""

    def __init__(self, body: dict):
        self._body = body

    def json(self):
        return self._body


class _Session:
    def __init__(self, response=None, error=None):
        self.headers = {}
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.response


def _client(timeout=7.5):
    return QualityAPIClient(
        Settings(
            quality_api_base_url="http://quality.test",
            quality_api_timeout_seconds=timeout,
        )
    )


def test_evaluate_versionset_targets_exact_candidate_and_passes_timeout():
    response = _Response(
        {
            "request_id": "req_1",
            "answer": "ok",
            "status": "ok",
            "versionset_id": "vs_candidate",
            "prompt_digest": "sha256:" + "a" * 64,
            "kb_manifest_digest": "sha256:" + "b" * 64,
            "model_digest": "sha256:" + "c" * 64,
            "trace_id": "tr_1",
            "retrieval": [],
        }
    )
    client = _client()
    session = _Session(response=response)
    client._read_session = session

    result = client.evaluate_versionset("vs_candidate", "probe", timeout_seconds=2.25)

    assert result.versionset_id == "vs_candidate"
    assert result.status == "ok"
    assert result.trace_id == "tr_1"
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == "http://quality.test/v2/versionsets/vs_candidate/evaluate"
    assert kwargs["json"] == {"message": "probe"}
    assert 0 < kwargs["timeout"] <= 2.25


def test_http_timeout_is_real_requests_timeout_not_a_success():
    client = _client(timeout=0.1)
    client._read_session = _Session(error=requests.Timeout("deadline exceeded"))

    try:
        client.get_versionset("vs_candidate")
    except requests.Timeout as exc:
        assert "deadline exceeded" in str(exc)
    else:  # pragma: no cover - fail loudly if timeout is swallowed
        raise AssertionError("requests.Timeout must propagate to the gate executor")
