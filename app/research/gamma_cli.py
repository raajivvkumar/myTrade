"""Offline MyTrade index precursor scanner and fixed-contract option outcome study."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import pandas as pd
from app.data.market_archive import verify_backup
from app.research.gamma_fingerprint import (
    ResearchRules, evaluate_contract, index_precursors, summarize,
)


def build_parser():
    p = argparse.ArgumentParser(description="Gamma precursor research OFFLINE: no broker token or orders")
    p.add_argument("--index-archive", type=Path,
                   default=Path("../MyTradeOfflineArchive/upstox_index_v3"))
    p.add_argument("--option-parquet", type=Path, default=None,
                   help="Optional SINGLE EXACT NIFTY expired CE/PE contract Parquet")
    p.add_argument("--output-dir", type=Path,
                   default=Path("../MyTradeOfflineArchive/research/gamma_fingerprints"))
    p.add_argument("--move-5m-bps", type=float, default=12.0)
    p.add_argument("--range-ratio", type=float, default=1.35)
    p.add_argument("--volatility-ratio", type=float, default=1.3)
    p.add_argument("--cooldown-minutes", type=int, default=15)
    p.add_argument("--horizon-minutes", type=int, default=30)
    p.add_argument("--min-premium", type=float, default=2.0)
    return p


def load_index(archive: Path):
    """Strict source-tagged 1-minute NIFTY backup only; no broker calls."""
    archive = Path(archive)
    if (archive / "archive_catalogue.json").exists():
        verify_backup(archive)
    files = sorted(archive.glob("NIFTY/1minute/*.parquet"))
    if not files:
        raise ValueError(f"No archived NIFTY 1minute data in {archive}")
    frames = []
    for path in files:
        manifest = path.with_suffix(".json")
        if not manifest.is_file():
            raise ValueError(f"Missing provenance for {path}")
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        if (meta.get("data_kind") != "INDEX_CANDLES_NOT_OPTION_CONTRACT"
                or meta.get("index") != "NIFTY" or meta.get("interval_minutes") != 1):
            raise ValueError(f"Not an approved NIFTY index source: {path}")
        frame = pd.read_parquet(path)
        if len(frame) != meta.get("rows"):
            raise ValueError(f"Source manifest row count disagrees: {path}")
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def run(args):
    rules = ResearchRules(
        move_5m_bps=args.move_5m_bps,
        range_ratio=args.range_ratio,
        abs_return_ratio=args.volatility_ratio,
        cooldown_minutes=args.cooldown_minutes,
        horizon_minutes=args.horizon_minutes,
        min_entry_premium=args.min_premium,
    )
    rules.validate()
    features = index_precursors(load_index(args.index_archive), rules)
    outcomes = None
    if args.option_parquet is not None:
        if not args.option_parquet.is_file():
            raise FileNotFoundError(args.option_parquet)
        outcomes = evaluate_contract(
            features, pd.read_parquet(args.option_parquet), rules
        )
    report = summarize(features, outcomes, rules.horizon_minutes)
    report["feature_rules"] = {
        "move_5m_bps": rules.move_5m_bps,
        "range_ratio": rules.range_ratio,
        "abs_return_ratio": rules.abs_return_ratio,
        "consistent_bars": rules.consistent_bars,
        "cooldown_minutes": rules.cooldown_minutes,
        "option_volume_ratio": rules.option_volume_ratio,
        "min_entry_premium": rules.min_entry_premium,
    }
    report["source_archive"] = str(args.index_archive)
    report["exact_option_file"] = str(args.option_parquet) if args.option_parquet else None
    report["not_validated"] = True
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    columns = [
        "timestamp", "available_after_ist", "direction", "move_5m_bps",
        "range_ratio", "abs_return_ratio", "directional_bars_5m",
        "conditions_met", "precursor", "eligible", "close",
    ]
    features.loc[features.precursor, columns].to_csv(
        output / "index_precursor_events.csv", index=False
    )
    if outcomes is not None:
        outcomes.to_csv(
            output / "exact_contract_outcome_observations.csv", index=False
        )
    (output / "gamma_research_summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"Analyzed {report['index_rows']} NIFTY index minutes")
    print(f"Exploratory index precursor events: {report['index_precursor_events']}")
    print(f"Exact-option outcome status: {report['outcomes_status']}")
    print(f"Research outputs (no profit promises): {output}")
    return report


def main():
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
