# Impeccable Vera Bot

Deterministic submission for the magicpin Vera AI challenge.

## What It Does

- Implements `compose(category, merchant, trigger, customer=None)`.
- Exposes the judge endpoints: `/v1/healthz`, `/v1/metadata`, `/v1/context`, `/v1/tick`, `/v1/reply`.
- Uses a rule-based trigger router instead of a runtime LLM, so the same input returns the same output.
- Can optionally polish messages with Mistral when `VERA_USE_MISTRAL=1` and `MISTRAL_API_KEY` are set.
- Grounds every message in provided category, merchant, trigger, and customer context.
- Handles auto-replies, explicit stop/hostility, commitment intent, and off-topic replies.

## Run Locally

```bash
pip install -r requirements.txt
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Then point the judge simulator at:

```text
http://localhost:8080
```

## Test

```bash
python -m unittest discover -s tests
```

## Generate Seed Submission

```bash
python generate_submission.py
```

This writes `submission.jsonl` for the 25 seed triggers included in this ZIP.

## Strategy

The bot chooses the strongest grounded trigger, suppresses duplicates by `suppression_key`, and composes via category-specific templates. Customer-scoped triggers require consent and are sent as `merchant_on_behalf`; merchant-scoped triggers are sent as `vera`.

Tradeoff: the rule engine is deliberately deterministic and fast. Optional Mistral polishing uses `temperature=0` and falls back to the deterministic draft if the key/package/API is unavailable.

## Render

Use these settings:

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn bot:app --host 0.0.0.0 --port $PORT
```

Environment variables:

```text
VERA_USE_MISTRAL=1
MISTRAL_MODEL=mistral-large
MISTRAL_API_KEY=<set in Render only>
```

Do not commit real API keys.
