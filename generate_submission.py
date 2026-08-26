import json
from pathlib import Path

from bot import compose


ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    categories = {
        path.stem: load_json(path)
        for path in (DATASET / "categories").glob("*.json")
    }
    merchants = {
        item["merchant_id"]: item
        for item in load_json(DATASET / "merchants_seed.json")["merchants"]
    }
    customers = {
        item["customer_id"]: item
        for item in load_json(DATASET / "customers_seed.json")["customers"]
    }
    triggers = load_json(DATASET / "triggers_seed.json")["triggers"]

    rows = []
    for index, trigger in enumerate(triggers, start=1):
        merchant = merchants[trigger["merchant_id"]]
        category = categories[merchant["category_slug"]]
        customer = customers.get(trigger.get("customer_id")) if trigger.get("customer_id") else None
        result = compose(category, merchant, trigger, customer)
        rows.append({"test_id": f"T{index:02d}", **result})

    out = ROOT / "submission.jsonl"
    out.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
