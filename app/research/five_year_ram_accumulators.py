"""RAM-bounded five-year research aggregators.

Update after each read-only broker response, retain counters/date keys and
a bounded set of examples, never broker raw candles or an archive.
"""
from __future__ import annotations

from collections import defaultdict

from app.research.option_multiplier_event_scan import LEVELS, RULES
from app.research.vedic_strike_transit_study import compound_number


def _rule_counts():
    return {k: 0 for k in ("tp", "fp", "fn", "tn", "missing")}


class StreamingRollingSummary:
    """Streaming equivalent of summarize_scans; no per-candle/per-day retention."""

    def __init__(self, *, max_examples=24):
        if not isinstance(max_examples, int) or not 0 <= max_examples <= 200:
            raise ValueError("Invalid example cap")
        self.max_examples = max_examples
        self.dates = set()
        self.series_day_groups = 0
        self.bars = 0
        self.segments = 0
        self.switches = 0
        self.gaps = 0
        self.levels = {}
        for level in LEVELS:
            self.levels[level] = {
                "episodes": 0, "positive": 0, "negative": 0,
                "censored": defaultdict(int),
                "rules": {name: _rule_counts() for name in RULES},
                "roots": defaultdict(lambda: {"positive": 0, "negative": 0}),
                "samples": [],
                "sample_count": 0,
                "duplicate_samples": 0,
                "sample_identifiers": set(),
            }

    def add(self, batch):
        for record in batch.values():
            self.dates.add(record["date"])
            self.series_day_groups += 1
            self.bars += record["bars"]
            self.segments += record["segments"]
            self.switches += record["strike_switches"]
            self.gaps += record["missing_minute_splits"]
            for level in LEVELS:
                key = str(level)
                stats = self.levels[level]
                scored = record["scored"][key]
                stats["episodes"] += record["episodes"][key]
                stats["positive"] += scored["positive_windows"]
                stats["negative"] += scored["known_negative_windows"]
                for reason, count in scored["censored"].items():
                    stats["censored"][reason] += count
                for name in RULES:
                    for cell, value in scored["rule_confusion"][name].items():
                        stats["rules"][name][cell] += value
                for root, bucket in scored["strike_root"].items():
                    stats["roots"][root]["positive"] += bucket["positive"]
                    stats["roots"][root]["negative"] += bucket["negative"]
                samples = record["event_examples"][key]
                stats["sample_count"] += len(samples)
                for example in samples:
                    identifier = (
                        example["day"], example["side"],
                        example["rolling_strike"],
                        example["first_observed_crossing_close_ist"],
                    )
                    if identifier in stats["sample_identifiers"]:
                        stats["duplicate_samples"] += 1
                        continue
                    # Bound identifiers to retained examples. The count
                    # of possible alias duplicates is therefore only from
                    # the retained rolling sample, NOT the entire 5-year
                    # event population.
                    if self.max_examples:
                        sample_list = stats["samples"]
                        sample_list.append(example)
                        sample_list.sort(key=lambda e: (
                            e["first_observed_crossing_close_ist"], e["series"]))
                        if len(sample_list) > self.max_examples:
                            sample_list.pop()
                        stats["sample_identifiers"] = {
                            (x["day"], x["side"], x["rolling_strike"],
                             x["first_observed_crossing_close_ist"])
                            for x in sample_list}

    def result(self):
        response = {
            "sampled_session_dates": len(self.dates),
            "rolling_series_day_groups": self.series_day_groups,
            "bars_analysed": self.bars, "segments": self.segments,
            "strike_switches": self.switches, "missing_minute_splits": self.gaps,
            "rolling_identity_verified_fixed_contract": False,
            "historical_gamma_verified": False,
            "executable_trading_accuracy": None,
            "thresholds": {}, "examples": {},
            "chronological_holdout": {
                "available_dates": len(self.dates),
                "contract_expiry_independent_holdout": False,
                "status": "BLOCKED_NO_VERIFIED_FIXED_CONTRACT_IDENTITIES",
            },
            "cautions": [
                "Observed 1m CLOSE crossing vs same-segment OPEN is retrospective, not a fill.",
                "Positive before a future strike change is observable; unobserved negatives are censored.",
                "Rolling strike stability does not authenticate fixed expiry/security ID.",
                "One-minute relative-strike aliases may repeat an underlying trade or different expiries.",
                "Multiple overlapping entry anchors are correlated; episode counts are not independent.",
                "Astrology/numerology associations are descriptive, not Gamma mechanisms or predictions.",
            ],
            "aggregation_mode": "INCREMENTAL_RAM_BOUNDED",
        }
        for level in LEVELS:
            info = self.levels[level]
            key = f"{level}x"
            rules = {}
            for name, counts in info["rules"].items():
                cells = dict(counts)
                tp, fp, fn, tn = (cells[c] for c in ("tp", "fp", "fn", "tn"))
                cells["precision"] = round(tp / (tp + fp), 6) if tp + fp else None
                cells["recall"] = round(tp / (tp + fn), 6) if tp + fn else None
                cells["tested_feature_complete_windows"] = tp + fp + fn + tn
                rules[name] = cells
            total = info["positive"] + info["negative"]
            response["thresholds"][key] = {
                "observed_episode_count_not_deduped_across_aliases": info["episodes"],
                "positive_anchor_windows_overlapping": info["positive"],
                "known_negative_anchor_windows": info["negative"],
                "censored_unobserved_outcomes": dict(info["censored"]),
                "positive_anchor_base_rate": (
                    round(info["positive"] / total, 6) if total else None),
                "past_only_rule_scores": rules,
                "strike_root_counts": dict(info["roots"]),
            }
            response["examples"][key] = {
                "records": list(info["samples"]),
                "potential_alias_coincidences_in_sample": info["duplicate_samples"],
                "alias_duplicate_detection_scope": "RETAINED_EARLIEST_EXAMPLES_ONLY",
                "examples_truncated": info["episodes"] > info["sample_count"],
            }
        return response


class StreamingTransitSummary:
    """Incremental transit/strike counts with bounded chart examples."""

    def __init__(self, *, max_examples=24):
        if not isinstance(max_examples, int) or not 0 <= max_examples <= 200:
            raise ValueError("Invalid example cap")
        self.limit = max_examples
        self.transitions = {}
        self.instances = 0
        self.eligible = 0
        self.censored = defaultdict(int)
        self.by_root = {}
        self.by_exact = {}
        self.first_examples = []

    def add(self, group):
        self.instances += group["total_transit_instances"]
        self.eligible += group["eligible_transit_strike_observations"]
        for transition in group["transit_calendar"]:
            key = (transition["calculated_transition_time_ist"],
                   transition["planet"], transition["transition_type"])
            self.transitions.setdefault(key, transition)
        for reason, count in group["censored"].items():
            self.censored[reason] += count
        for obs in group["observations"]:
            if len(self.first_examples) < self.limit:
                self.first_examples.append(obs)
            key = (obs["planet"], obs["transition_type"],
                   obs["option_side"], obs["strike"]["root_number"])
            root = self.by_root.setdefault(key, {
                "observations": 0, "proxy_2x": 0,
                "sum_post30_pct": 0, "sum_peak_ratio": 0,
                "strike_examples": set(),
            })
            root["observations"] += 1
            root["proxy_2x"] += int(obs["proxy_2x_peak_close"])
            root["sum_post30_pct"] += obs["premium_change_post30_pct"]
            root["sum_peak_ratio"] += obs["peak_close_to_start_ratio"]
            root["strike_examples"].add(obs["strike"]["strike_price"])
            key = (obs["planet"], obs["transition_type"],
                   obs["option_side"], obs["strike"]["strike_price"])
            exact = self.by_exact.setdefault(key, {
                "count": 0, "proxy_2x": 0, "sum_post30_pct": 0,
                "controls": 0, "sum_excess": 0,
            })
            exact["count"] += 1
            exact["proxy_2x"] += int(obs["proxy_2x_peak_close"])
            exact["sum_post30_pct"] += obs["premium_change_post30_pct"]
            if obs["controls_available"]:
                exact["controls"] += 1
                exact["sum_excess"] += obs["excess_post30_vs_control_pct_points"]

    def result(self):
        roots = []
        for (planet, change, side, root_num), data in sorted(
                self.by_root.items()):
            n = data["observations"]
            roots.append({
                "planet": planet, "change": change, "side": side,
                "strike_root": root_num,
                "observations": n, "proxy_2x": data["proxy_2x"],
                "mean_post30_pct": round(data["sum_post30_pct"] / n, 4),
                "mean_peak_close_ratio": round(data["sum_peak_ratio"] / n, 4),
                "example_strikes": sorted(data["strike_examples"])[:12],
            })
        exact_groups = []
        for (planet, change, side, strike), data in sorted(self.by_exact.items()):
            n = data["count"]
            numbers = compound_number(strike)
            exact_groups.append({
                "planet": planet, "transition_type": change,
                "option_side": side, "strike_price": strike,
                "compound_total": numbers["compound_total"],
                "root_number": numbers["root_number"],
                "observations": n, "proxy_2x": data["proxy_2x"],
                "mean_post30_pct": round(data["sum_post30_pct"] / n, 4),
                "matched_control_observations": data["controls"],
                "mean_excess_vs_controls_pct_points": (
                    round(data["sum_excess"] / data["controls"], 4)
                    if data["controls"] else None),
            })
        exact_groups.sort(key=lambda x: (
            -x["observations"], x["planet"], x["strike_price"]))
        transitions = [self.transitions[k] for k in sorted(self.transitions)]
        return {
            "unique_session_planetary_transitions": len(transitions),
            "session_planetary_transition_examples": transitions[:100],
            "transit_instances_across_aliases": self.instances,
            "eligible_strike_effect_windows": self.eligible,
            "censor_reasons": dict(self.censored),
            "by_planet_change_side_strike_root": roots,
            "by_exact_strike": exact_groups[:100],
            "total_exact_strike_groups": len(exact_groups),
            "first_examples": self.first_examples,
            "independent_contracts_verified": False,
            "predictive_significance": None,
            "aggregation_mode": "INCREMENTAL_RAM_BOUNDED",
            "note": (
                "Exploratory associations: +/-60m same-strike controls do not"
                " independently validate astrology or gamma; account for"
                " time, volatility, moneyness and correlated aliases."
            ),
        }
