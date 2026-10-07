import builtins
import importlib
import sys

import pytest


OFFLINE_MODULES = (
    "app.broker.angel_auth",
    "app.broker.angel_historical",
)


def _clear_offline_modules() -> None:
    for name in OFFLINE_MODULES:
        sys.modules.pop(name, None)


def test_offline_broker_modules_do_not_import_smartapi(monkeypatch) -> None:
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "SmartApi" or name.startswith("SmartApi."):
            raise AssertionError("offline imports must not load SmartAPI")
        return real_import(name, *args, **kwargs)

    _clear_offline_modules()
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    for name in OFFLINE_MODULES:
        importlib.import_module(name)


def test_missing_credentials_fail_before_smartapi_load(monkeypatch) -> None:
    for key in (
        "ANGEL_API_KEY",
        "ANGEL_CLIENT_CODE",
        "ANGEL_PIN",
        "ANGEL_TOTP_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    _clear_offline_modules()
    module = importlib.import_module("app.broker.angel_auth")

    def must_not_load():
        raise AssertionError("SmartAPI should not load before config validation")

    monkeypatch.setattr(module, "_load_smart_connect", must_not_load)

    with pytest.raises(RuntimeError, match="Missing local Angel One configuration"):
        module.connect_market_data()
