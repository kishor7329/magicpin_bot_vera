"""Audit bot.compose() against the 30 canonical test pairs.

Usage:
    python3 dataset/generate_dataset.py --seed-dir dataset --out expanded
    python3 audit_canonical.py

Prints every canonical pair that still has a quality issue (unhandled
trigger kind falling to a generic fallback, a literal "None" leaking into
the message body, or empty-list grammar artifacts). Target: 0 issues.
"""

import json
import re
from pathlib import Path

import bot


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


MERCHANT_HANDLED = {
    "research_digest", "regulation_change", "perf_dip", "perf_spike", "renewal_due",
    "competitor_opened", "festival_upcoming", "ipl_match_today", "review_theme_emerged",
    "milestone_reached", "gbp_unverified", "dormant_with_vera", "curious_ask_due",
    "category_seasonal", "cde_opportunity", "supply_alert", "active_planning_intent",
}
CUSTOMER_HANDLED = {
    "recall_due", "chronic_refill_due", "customer_lapsed_hard", "winback_eligible",
    "trial_followup", "wedding_package_followup", "customer_lapsed_soft", "appointment_tomorrow",
}


def main() -> None:
    base = Path("expanded")
    categories = {p.stem: load_json(p) for p in (base / "categories").glob("*.json")}
    merchants = {load_json(p)["merchant_id"]: load_json(p) for p in (base / "merchants").glob("*.json")}
    customers = {load_json(p)["customer_id"]: load_json(p) for p in (base / "customers").glob("*.json")}
    triggers = {load_json(p)["id"]: load_json(p) for p in (base / "triggers").glob("*.json")}
    pairs = load_json(base / "test_pairs.json")["pairs"]

    issues = []
    for pair in pairs:
        trig = triggers[pair["trigger_id"]]
        merch = merchants[pair["merchant_id"]]
        cust = customers.get(pair["customer_id"]) if pair.get("customer_id") else None
        cat = categories[merch["category_slug"]]
        kind = trig.get("kind", "")
        is_customer_flow = bool(cust) or trig.get("scope") == "customer"

        out = bot.compose(cat, merch, trig, cust)
        body = out["body"]
        problems = []

        if is_customer_flow and kind not in CUSTOMER_HANDLED:
            problems.append("unhandled kind -> generic fallback")
        if not is_customer_flow and kind not in MERCHANT_HANDLED:
            problems.append("unhandled kind -> generic fallback")
        if "None" in body:
            problems.append("literal 'None' in body")
        if re.search(r"\(\s*\)", body):
            problems.append("empty parentheses artifact")
        if re.search(r"\bhold\s*\.", body):
            problems.append("empty slot text artifact")

        if problems:
            issues.append((pair["test_id"], kind, problems, body))

    print(f"{len(issues)} / {len(pairs)} canonical pairs still have issues.\n")
    for test_id, kind, problems, body in issues:
        print(f"{test_id} [{kind}]: {problems}")
        print(f"   {body}\n")


if __name__ == "__main__":
    main()
