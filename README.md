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

## Audit Against the 30 Canonical Test Pairs

```bash
python3 dataset/generate_dataset.py --seed-dir dataset --out expanded
python3 audit_canonical.py
```

This regenerates the official expanded dataset (50 merchants / 200 customers / 100 triggers /
30 canonical test pairs) and runs `compose()` against all 30, flagging any unhandled trigger
kind, literal `"None"` text, or empty-list grammar artifacts. Target output:
`0 / 30 canonical pairs still have issues.`

## Changelog (v1.0.1)

- Added dedicated handling for `appointment_tomorrow` (new `_appointment_reminder`) and
  `customer_lapsed_soft` (routed into the existing `_customer_winback`) trigger kinds, which
  previously fell through to the generic fallback template.
- Hardened `_generic_merchant` / `_generic_customer` fallbacks to surface any real payload
  field found, so still-unrecognized future trigger kinds stay grounded instead of fully generic.
- Fixed `_milestone` to read the real seed field names (`value_now`, `milestone_value`) instead
  of non-existent `value`/`count` keys, and to fall back gracefully when a value is missing.
- Fixed `_competitor_opened` and `_review_theme` to avoid printing a literal `"None"` when
  `distance_km` or `common_quote` is absent from the payload.
- Fixed `_chronic_refill` and `_customer_recall` to avoid broken grammar (`"medicines ()"`,
  `"I can hold ."`) when the medicines/slots list is empty.
- Fixed `_perf_dip` to stop asserting a fabricated `"down 0%"` when `delta_pct` isn't present
  in the payload — it now asks to confirm the number instead of inventing one.
- `/v1/metadata`'s `contact_email` now reads from `VERA_CONTACT_EMAIL` env var (set this on
  Render), falling back to the placeholder if unset.

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
