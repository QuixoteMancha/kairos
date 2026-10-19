"""Build auditable weather samples from immutable evidence (read-only)."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math

from database.historical.weather import WeatherContract, WeatherEvidence
from models.fairprice.weather_research import settlement_yes, utc
from scripts.weather_probability import report as probability_report


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def quotes(payload, kind):
    """Ask prices come from opposing bids; never infer depth from volume."""
    output = {}
    for side, opposite in (("yes", "no"), ("no", "yes")):
        price, size = None, None
        if kind == "market_orderbook":
            fixed = payload.get("orderbook_fp")
            book = fixed if fixed is not None else payload.get("orderbook", {})
            levels = book.get(opposite + "_dollars" if fixed is not None else opposite) or []
            parsed = []
            for level in levels:
                if not isinstance(level, (tuple, list)) or len(level) != 2:
                    continue
                bid, quantity = number(level[0]), number(level[1])
                if bid is not None and quantity is not None and quantity > 0:
                    bid = bid if fixed is not None else bid / 100
                    if 0 <= bid <= 1:
                        parsed.append((bid, quantity))
            if parsed:
                bid = max(p[0] for p in parsed)
                price, size = 1 - bid, sum(q for p, q in parsed if p == bid)
        else:
            price = number(payload.get(side + "_ask_dollars"))
            if price is None and side + "_ask_dollars" not in payload:
                cents = number(payload.get(side + "_ask"))
                price = cents / 100 if cents is not None else None
            size = number(payload.get(side + "_ask_size_fp"))
        output[side + "_ask"] = price if price is not None and 0 < price < 1 else None
        output[side + "_size"] = size if size is not None and size > 0 else None
    return output


def rule_signature(raw):
    keys = ("rules_primary", "rules_secondary", "strike_type", "floor_strike", "cap_strike")
    return tuple(raw.get(key) or "" for key in keys)


def build_dataset(*, as_of=None, rounding="nearest_integer", limit=None):
    """Labels use revisions known at build time; features only decision-time data.

    The explicit rounding scenario is retained; ready means suitable for research,
    not authorization to trade. All dates from a station share a split group across
    high/low and bracket contracts. No archive publication delay is invented.
    """
    cutoff = utc(as_of or datetime.now(timezone.utc))
    stamp = cutoff.replace(tzinfo=None)
    outcomes, coverage, rejected = {}, Counter(), Counter()
    by_source = Counter()
    evidence = WeatherEvidence.select().where(
        WeatherEvidence.kind.in_(("market_quote", "market_orderbook")),
        WeatherEvidence.available_at <= stamp, WeatherEvidence.captured_at <= stamp,
        WeatherEvidence.contract.is_null(False),
    ).order_by(WeatherEvidence.available_at, WeatherEvidence.id)
    total = evidence.count()
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be positive")
        evidence = evidence.limit(limit)
    quoted_ids = {row[0] for row in evidence.select(WeatherEvidence.contract).tuples()}
    coverage["contracts"] = WeatherContract.select().count()
    revision_alias = WeatherEvidence.alias()
    latest = (revision_alias.select(revision_alias.id).where(
        revision_alias.contract == WeatherEvidence.contract, revision_alias.kind == "contract",
        revision_alias.available_at <= stamp, revision_alias.captured_at <= stamp,
    ).order_by(revision_alias.available_at.desc(), revision_alias.id.desc()).limit(1))
    revisions = WeatherEvidence.select(
        WeatherEvidence.id, WeatherEvidence.contract, WeatherEvidence.payload_json,
        WeatherEvidence.available_at, WeatherEvidence.captured_at,
    ).where(WeatherEvidence.kind == "contract", WeatherEvidence.id == latest)
    seen_revisions = 0
    for revision in revisions.iterator():
        seen_revisions += 1
        raw = json.loads(revision.payload_json)
        rules = (raw.get("rules_primary") or "") + " " + (raw.get("rules_secondary") or "")
        by_source["TWC" if "The Weather Company" in rules else
                  "NWS_CLI" if "National Weather Service" in rules else "unknown"] += 1
        # Hold only labels with candidate quote snapshots, not decades of every
        # climate market's JSON. The deployed archive already has 40k+ contracts.
        if (revision.contract_id in quoted_ids and raw.get("status") == "finalized"
                and raw.get("result") in {"yes", "no"}):
            outcomes[revision.contract_id] = (revision, raw)
    by_source["unknown"] += coverage["contracts"] - seen_revisions
    rows = []
    for quote in evidence.iterator():
        coverage["decision_snapshots_checked"] += 1
        label = outcomes.get(quote.contract_id)
        if label is None:
            rejected["no_final_result_as_of_build"] += 1
            continue
        revision, final = label
        value = number(final.get("expiration_value"))
        if value is None:
            rejected["missing_numeric_settlement"] += 1
            continue
        decision = max(utc(quote.available_at), utc(quote.captured_at))
        # Numeric values and binary outcomes become usable only when actually
        # captured. Initial publication timestamps are never guessed.
        label_at = max(utc(revision.available_at), utc(revision.captured_at))
        if label_at <= decision:
            rejected["label_precedes_decision"] += 1
            continue
        ticker = quote.contract.ticker
        result = probability_report(ticker, as_of=decision, sigma_f=2.2,
                                    rounding=rounding, max_disagreement_f=1e6)
        if result["status"] != "scenario":
            rejected.update(result["reasons"])
            continue
        original = WeatherEvidence.get_by_id(result["contract_evidence_id"])
        if rule_signature(json.loads(original.payload_json)) != rule_signature(final):
            rejected["rules_changed_since_decision"] += 1
            continue
        if settlement_yes(result["raw_predicate"], value) != (final["result"] == "yes"):
            rejected["settlement_predicate_mismatch"] += 1
            continue
        start = utc(result["window_start"])
        lead_hours = (start - decision).total_seconds() / 3600
        horizon = ("intraday" if lead_hours <= 0 else "0_12h" if lead_hours <= 12
                   else "12_36h" if lead_hours <= 36 else "36h_plus")
        rows.append(dict(result, group=f"{result['station_code']}:{result['target_date']}",
            lead_hours=lead_hours, horizon=horizon, target_f=value,
            outcome_yes=int(final["result"] == "yes"), label_available_at=label_at.isoformat(),
            label_evidence_id=revision.id, quote_evidence_id=quote.id,
            quote_available_at=decision.isoformat(), quote_kind=quote.kind,
            **quotes(json.loads(quote.payload_json), quote.kind)))
    values = defaultdict(set)
    for row in rows:
        values[(row["group"], row["kind"])].add(row["target_f"])
    ambiguous = {group for group, labels in values.items() if len(labels) != 1}
    kept = [row for row in rows if (row["group"], row["kind"]) not in ambiguous]
    rejected["conflicting_station_day_settlements"] += len(rows) - len(kept)
    coverage.update(research_samples=len(kept),
        point_in_time_training_ready_contracts=len({r["ticker"] for r in kept}),
        independent_station_days=len({r["group"] for r in kept}),
        snapshots_with_depth=sum(any(r[s + "_ask"] is not None and r[s + "_size"] is not None
                                     for s in ("yes", "no")) for r in kept))
    return dict(schema_version=1, research_only=True, built_as_of=cutoff.isoformat(),
        rounding_assumption=rounding, total_decision_snapshots=total,
        truncated=limit is not None and total > limit, coverage=dict(coverage),
        sources=dict(by_source), rejected=dict(rejected), samples=kept)
