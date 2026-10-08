"""Legacy gamma data quarantine: synthetic tests, NEVER proof of NSE data."""
import pandas as pd
import pytest

from app.research.gamma_legacy_validation import audit_legacy_tick_csv


def sample(n=375, *, start="2024-04-03 09:15:00"):
    times = pd.date_range(start, periods=n, freq="min")
    return pd.DataFrame({
        "date": times.strftime("%Y-%m-%d"),
        "time": times.strftime("%H:%M:%S"),
        "price": [0.50] * n,
        "volume": [50] * n,
        "oi": [300_000] * n,
    })


def test_full_minutes_are_quarantined_not_auto_admitted():
    r = audit_legacy_tick_csv(sample(), "NIFTY2440421500PE.csv")
    assert r["status"] == "QUARANTINED_STRUCTURAL_OK_NEEDS_INDEPENDENT_REFERENCE"
    assert not r["eligible_for_gamma_research"]
    assert r["inferred_expiry"] == "2024-04-04"
    assert r["inferred_strike"] == 21500
    assert r["inferred_side"] == "PE"
    assert r["observed_minutes"] == 375
    assert r["source_provenance"] == "UNVERIFIED"


def test_duplicate_second_with_different_prices_does_not_invent_second_order():
    f = sample()
    repeat = f.iloc[[100]].copy()
    repeat.loc[:, "price"] = 0.55
    f = pd.concat([f.iloc[:101], repeat, f.iloc[101:]], ignore_index=True)
    r = audit_legacy_tick_csv(f, "NIFTY2440421500PE.csv")
    assert r["same_second_multiple_prices"] == 1
    assert r["observed_minutes"] == 375
    assert r["status"].startswith("QUARANTINED")
    assert r["eligible_for_gamma_research"] is False


def test_sparse_contract_is_rejected_even_if_values_look_good():
    r = audit_legacy_tick_csv(sample(20), "NIFTY2440421550CE.csv")
    assert r["status"] == "REJECT_STRUCTURAL_OR_COVERAGE"
    assert "Sparse" in "; ".join(r["reasons"])
    assert r["eligible_for_gamma_research"] is False


def test_expiry_and_file_name_identity_are_not_broker_provenance():
    good = sample()
    assert (audit_legacy_tick_csv(good, "NIFTY2440421500PE.csv")[
        "identity_source"] == "FILENAME_ONLY")
    r = audit_legacy_tick_csv(good, "not_an_option.csv")
    assert r["status"] == "REJECT_FILENAME"
    r = audit_legacy_tick_csv(good, "NIFTY2423021500PE.csv")
    assert r["status"] == "REJECT_EXPIRY"


def test_wrong_or_missing_columns_rejected():
    r = audit_legacy_tick_csv(sample().drop(columns=["oi"]), "NIFTY2440421500PE.csv")
    assert r["status"] == "REJECT_COLUMNS"


def test_invalid_prices_and_oi_are_rejected():
    f = sample()
    f.loc[125, "price"] = 0
    f.loc[127, "oi"] = -1
    r = audit_legacy_tick_csv(f, "NIFTY2440421500PE.csv")
    assert r["status"] == "REJECT_STRUCTURAL_OR_COVERAGE"
    assert "invalid" in ";".join(r["reasons"])


def test_rows_after_expiry_are_rejected():
    f = sample(start="2024-04-05 09:15:00")
    r = audit_legacy_tick_csv(f, "NIFTY2440421500PE.csv")
    assert r["status"] == "REJECT_STRUCTURAL_OR_COVERAGE"
    assert "after expiry" in ";".join(r["reasons"])


def test_out_of_session_rejected():
    f = sample()
    f.loc[125, "time"] = "15:31:00"
    r = audit_legacy_tick_csv(f, "NIFTY2440421500PE.csv")
    assert r["status"] == "REJECT_STRUCTURAL_OR_COVERAGE"
    assert "outside" in ";".join(r["reasons"])


def test_unsupported_tick_increment_rejected():
    f = sample()
    f.loc[125, "price"] = 0.53
    r = audit_legacy_tick_csv(f, "NIFTY2440421500PE.csv")
    assert r["status"] == "REJECT_STRUCTURAL_OR_COVERAGE"
    assert "tick" in ";".join(r["reasons"])


def test_unsupported_lot_volume_rejected():
    f = sample()
    f.loc[125, "volume"] = 35
    r = audit_legacy_tick_csv(f, "NIFTY2440421500PE.csv")
    assert r["status"] == "REJECT_STRUCTURAL_OR_COVERAGE"
    assert "lot 50" in ";".join(r["reasons"])


def test_validator_has_no_side_effect_archive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    audit_legacy_tick_csv(sample(), "NIFTY2440421500PE.csv")
    assert not list(tmp_path.iterdir())
