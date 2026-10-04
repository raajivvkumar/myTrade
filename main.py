"""myTrade command-line entry point."""

from __future__ import annotations

import argparse
import json
import os

from app.ai.openai_client import OpenAIMarketAnalyst
from app.broker.instruments import load_instruments, search_instruments
from app.config import Settings


def cmd_status(settings: Settings) -> None:
    settings.ensure_local_directories()
    print("myTrade configuration")
    print(f"  data directory: {settings.data_dir}")
    print(f"  OpenAI model: {settings.openai_model}")
    print(
        "  OPENAI_API_KEY: "
        + ("configured" if os.getenv("OPENAI_API_KEY") else "optional / missing")
    )
    print("  Angel instrument master: ready")
    print("  Angel authenticated historical/live connection: next milestone")


def cmd_find_instrument(settings: Settings, args: argparse.Namespace) -> None:
    settings.ensure_local_directories()
    cache_path = settings.data_dir / "reference" / "angel_instruments.json"
    instruments = load_instruments(cache_path, force_refresh=args.refresh)
    matches = search_instruments(
        instruments,
        args.query,
        exchange=args.exchange,
        limit=args.limit,
    )

    if not matches:
        print("No matching instruments found.")
        return

    for item in matches:
        print(
            json.dumps(
                {
                    "token": item.get("token"),
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "exchange": item.get("exch_seg"),
                    "instrument_type": item.get("instrumenttype"),
                    "expiry": item.get("expiry"),
                    "lot_size": item.get("lotsize"),
                },
                ensure_ascii=False,
            )
        )


def cmd_openai_test(settings: Settings) -> None:
    analyst = OpenAIMarketAnalyst(model=settings.openai_model)
    snapshot = {
        "symbol": "TEST_ONLY",
        "timeframe": "5m",
        "trend": "unknown",
        "note": "Connectivity test. No real market prediction requested.",
    }
    print(analyst.explain_snapshot(snapshot))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myTrade",
        description="Local-first Indian market research and prediction engine.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "status",
        help="Show local project readiness without exposing secret values",
    )
    subparsers.add_parser(
        "openai-test",
        help="Optional: verify the OpenAI explanation layer",
    )

    find_parser = subparsers.add_parser(
        "find-instrument",
        help="Search Angel One's public instrument master",
    )
    find_parser.add_argument("query")
    find_parser.add_argument("--exchange", default=None)
    find_parser.add_argument("--limit", type=int, default=20)
    find_parser.add_argument("--refresh", action="store_true")

    return parser


def main() -> None:
    settings = Settings.from_env()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "status":
        cmd_status(settings)
    elif args.command == "find-instrument":
        cmd_find_instrument(settings, args)
    elif args.command == "openai-test":
        cmd_openai_test(settings)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
