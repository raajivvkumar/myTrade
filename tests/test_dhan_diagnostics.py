"""Broker error diagnostics preserve useful codes, never broker free text."""
import json

import pytest
import requests

from app.broker.dhan_historical import DhanAPIError, DhanClient


class Response:
    def __init__(self, status, data=None, invalid_json=False):
        self.status_code, self.data, self.invalid_json = status, data, invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("synthetic-secret-parser-error")
        return self.data


class Session:
    def __init__(self, response):
        self.response = response

    def request(self, *args, **kwargs):
        return self.response


@pytest.mark.parametrize("status,code", [(400, "DH-905"), (401, "807"), (403, 806)])
def test_http_error_preserves_status_and_code_without_broker_messages(status, code):
    data = {"errorCode": code, "errorType": "synthetic-secret-type",
            "errorMessage": "synthetic-secret-token"}
    client = DhanClient(token="synthetic-secret-token", session=Session(Response(status, data)))
    with pytest.raises(DhanAPIError) as raised:
        client.profile()
    error = raised.value
    assert error.diagnostic() == dict(reason="HTTP_ERROR", http_status=status, provider_code=str(code))
    assert "synthetic-secret" not in str(error)
    assert "synthetic-secret" not in json.dumps(error.diagnostic())


def test_unknown_broker_error_code_is_not_echoed():
    data = {"status": "failed", "errorCode": "synthetic-secret-token", "errorMessage": "secret"}
    client = DhanClient(token="secret", session=Session(Response(200, data)))
    with pytest.raises(DhanAPIError) as raised:
        client.profile()
    assert raised.value.provider_code is None
    assert "synthetic-secret" not in str(raised.value)


def test_200_error_envelope_is_not_success():
    client = DhanClient(token="secret", session=Session(Response(200, {"errorCode": "807"})))
    with pytest.raises(DhanAPIError) as raised:
        client.profile()
    assert raised.value.category == "API_ERROR"
    assert raised.value.provider_code == "807"


@pytest.mark.parametrize("status", [200, 502])
def test_non_json_errors_stay_sanitized(status):
    client = DhanClient(token="secret", session=Session(Response(status, invalid_json=True)))
    with pytest.raises(DhanAPIError) as raised:
        client.profile()
    assert raised.value.category == ("INVALID_JSON" if status == 200 else "HTTP_ERROR")
    assert "synthetic-secret" not in str(raised.value)


def test_network_exception_is_not_echoed():
    class Offline:
        def request(self, *args, **kwargs):
            raise requests.ConnectionError("synthetic-secret-header")
    client = DhanClient(token="secret", session=Offline())
    with pytest.raises(DhanAPIError) as raised:
        client.profile()
    assert raised.value.category == "NETWORK_ERROR"
    assert "synthetic-secret" not in str(raised.value)


def test_invalid_status_cannot_be_echoed():
    error = DhanAPIError("HTTP_ERROR", http_status="synthetic-secret-token")
    assert error.http_status is None
    assert "synthetic-secret" not in str(error)
