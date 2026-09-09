import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any


try:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
except Exception:  # FastAPI is listed in requirements; tests can still run without it.
    FastAPI = None
    Request = None
    JSONResponse = None


STARTED_AT = time.time()
VALID_SCOPES = {"category", "merchant", "customer", "trigger"}

contexts: dict[tuple[str, str], dict[str, Any]] = {}
conversations: dict[str, list[dict[str, Any]]] = {}
sent_suppression_keys: set[str] = set()
ended_conversations: set[str] = set()
blocked_merchants: set[str] = set()


app = FastAPI(title="Vera deterministic bot", version="1.0.0") if FastAPI else None


def reset_state() -> None:
    contexts.clear()
    conversations.clear()
    sent_suppression_keys.clear()
    ended_conversations.clear()
    blocked_merchants.clear()


def compose(
    category: dict[str, Any],
    merchant: dict[str, Any],
    trigger: dict[str, Any],
    customer: dict[str, Any] | None = None,
) -> dict[str, str]:
    kind = trigger.get("kind", "")
    if customer or trigger.get("scope") == "customer":
        result = _compose_customer(category, merchant, trigger, customer)
        return _maybe_enhance_with_mistral(category, merchant, trigger, customer, result)

    handlers = {
        "research_digest": _research_digest,
        "regulation_change": _regulation_change,
        "perf_dip": _perf_dip,
        "perf_spike": _perf_spike,
        "renewal_due": _renewal_due,
        "competitor_opened": _competitor_opened,
        "festival_upcoming": _festival_upcoming,
        "ipl_match_today": _ipl_match_today,
        "review_theme_emerged": _review_theme,
        "milestone_reached": _milestone,
        "gbp_unverified": _gbp_unverified,
        "dormant_with_vera": _dormant,
        "curious_ask_due": _curious_ask,
        "category_seasonal": _category_seasonal,
        "cde_opportunity": _cde_opportunity,
        "supply_alert": _supply_alert,
        "active_planning_intent": _active_planning,
    }
    result = _finalize(handlers.get(kind, _generic_merchant)(category, merchant, trigger), trigger, "vera")
    return _maybe_enhance_with_mistral(category, merchant, trigger, customer, result)


async def healthz() -> dict[str, Any]:
    counts = {scope: 0 for scope in VALID_SCOPES}
    for scope, _ in contexts:
        counts[scope] += 1
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - STARTED_AT),
        "contexts_loaded": counts,
    }


async def metadata() -> dict[str, Any]:
    return {
        "team_name": "Impeccable Vera",
        "team_members": ["Kishore"],
        "model": "deterministic-rule-engine",
        "approach": "trigger router + grounded category/merchant/customer templates + reply intent handling",
        "contact_email": os.getenv("VERA_CONTACT_EMAIL", "kishorjnv7329@gmail.com"),
        "version": "1.0.1",
       "submitted_at": "2026-09-09T13:30:00Z",
    }


async def push_context(body: dict[str, Any]) -> dict[str, Any]:
    scope = body.get("scope")
    context_id = body.get("context_id")
    version = body.get("version")
    payload = body.get("payload")

    if scope not in VALID_SCOPES:
        return _httpish(400, {"accepted": False, "reason": "invalid_scope", "details": str(scope)})
    if not context_id or not isinstance(version, int) or not isinstance(payload, dict):
        return _httpish(400, {"accepted": False, "reason": "malformed_context"})

    key = (scope, context_id)
    current = contexts.get(key)
    if current and current["version"] >= version:
        return _httpish(409, {
            "accepted": False,
            "reason": "stale_version",
            "current_version": current["version"],
        })

    contexts[key] = {"version": version, "payload": payload, "stored_at": _now_iso()}
    return {
        "accepted": True,
        "ack_id": f"ack_{_safe_id(context_id)}_v{version}",
        "stored_at": contexts[key]["stored_at"],
    }


async def tick(body: dict[str, Any]) -> dict[str, Any]:
    actions = []
    for trigger_id in body.get("available_triggers", [])[:20]:
        trigger = _get("trigger", trigger_id)
        if not trigger:
            continue
        suppression = trigger.get("suppression_key", trigger_id)
        merchant_id = trigger.get("merchant_id") or trigger.get("payload", {}).get("merchant_id")
        if not merchant_id or merchant_id in blocked_merchants or suppression in sent_suppression_keys:
            continue
        merchant = _get("merchant", merchant_id)
        if not merchant:
            continue
        category = _get("category", merchant.get("category_slug"))
        if not category:
            continue
        customer = _get("customer", trigger.get("customer_id")) if trigger.get("customer_id") else None
        if trigger.get("scope") == "customer" and not _has_consent(customer, trigger):
            continue

        composed = compose(category, merchant, trigger, customer)
        conv_id = _conversation_id(merchant_id, trigger, customer)
        action = {
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": trigger.get("customer_id"),
            "send_as": composed["send_as"],
            "trigger_id": trigger_id,
            "template_name": _template_name(trigger, composed["send_as"]),
            "template_params": _template_params(composed["body"]),
            **composed,
        }
        actions.append(action)
        sent_suppression_keys.add(suppression)
        conversations.setdefault(conv_id, []).append({"from": "bot", "body": composed["body"], "trigger_id": trigger_id})
    return {"actions": sorted(actions, key=lambda a: _trigger_priority(_get("trigger", a["trigger_id"])), reverse=True)[:20]}


async def reply(body: dict[str, Any]) -> dict[str, Any]:
    conversation_id = body.get("conversation_id", "")
    merchant_id = body.get("merchant_id")
    message = body.get("message", "")
    turn_number = int(body.get("turn_number") or 0)
    text = message.lower().strip()

    conversations.setdefault(conversation_id, []).append({"from": body.get("from_role", "merchant"), "body": message})

    if conversation_id in ended_conversations:
        return {"action": "end", "rationale": "Conversation was already closed; no further send."}
    if _is_stop_or_hostile(text):
        ended_conversations.add(conversation_id)
        if merchant_id:
            blocked_merchants.add(merchant_id)
        return {"action": "end", "rationale": "Merchant explicitly refused or asked to stop; closing conversation."}
    if _is_auto_reply(text, conversation_id):
        repeats = _count_same_replies(conversation_id, message)
        if repeats >= 3 or turn_number >= 4:
            ended_conversations.add(conversation_id)
            return {"action": "end", "rationale": "Repeated WhatsApp Business auto-reply detected; ending instead of wasting turns."}
        wait_seconds = 86400 if repeats >= 2 else 14400
        return {"action": "wait", "wait_seconds": wait_seconds, "rationale": "Detected canned auto-reply; backing off for owner response."}
    if _is_off_topic(text):
        return {
            "action": "send",
            "body": "That is outside what I can help with directly. Coming back to this growth task, reply YES and I will prepare the draft/action now.",
            "cta": "binary_yes_no",
            "rationale": "Politely declined out-of-scope ask and returned to the active Vera task.",
        }
    if _is_commitment(text):
        return {
            "action": "send",
            "body": "Drafting it now. I will use the context from your listing and keep it ready for review; reply CONFIRM when you want me to send/publish it.",
            "cta": "binary_confirm_cancel",
            "rationale": "Merchant committed, so the bot moves to action mode instead of asking another qualifying question.",
        }
    if _is_time_request(text):
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Merchant asked for time; waiting before re-engaging."}

    return {
        "action": "send",
        "body": "Got it. I can keep this simple: I will prepare one concrete draft from your current context. Reply YES and I will send it here for review.",
        "cta": "binary_yes_no",
        "rationale": "Acknowledged merchant reply and kept the next step low-friction.",
    }


def _compose_customer(category, merchant, trigger, customer):
    if not customer:
        return _finalize({
            "body": f"{_merchant_name(merchant)}, I found a customer-scoped trigger but customer details are missing. I will wait rather than send an ungrounded message.",
            "cta": "none",
            "rationale": "Customer context missing; avoids fabricating outreach details.",
        }, trigger, "merchant_on_behalf")
    kind = trigger.get("kind", "")
    if kind == "recall_due":
        return _finalize(_customer_recall(category, merchant, trigger, customer), trigger, "merchant_on_behalf")
    if kind == "chronic_refill_due":
        return _finalize(_chronic_refill(category, merchant, trigger, customer), trigger, "merchant_on_behalf")
    if kind == "appointment_tomorrow":
        return _finalize(_appointment_reminder(category, merchant, trigger, customer), trigger, "merchant_on_behalf")
    if kind in {"customer_lapsed_hard", "winback_eligible", "trial_followup", "wedding_package_followup", "customer_lapsed_soft"}:
        return _finalize(_customer_winback(category, merchant, trigger, customer), trigger, "merchant_on_behalf")
    return _finalize(_generic_customer(category, merchant, trigger, customer), trigger, "merchant_on_behalf")


def _research_digest(category, merchant, trigger):
    item = _digest_item(category, trigger) or _first(category.get("digest", [])) or {}
    owner = _owner(merchant)
    cohort = _cohort_phrase(merchant)
    source = item.get("source", "this week's category digest")
    title = item.get("title", "a new category update")
    nums = _numbers_from_item(item)
    proof = f"{nums} " if nums else ""
    body = f"{owner}, {source} has one useful item for {cohort}: {proof}{title}. Want me to turn it into a customer WhatsApp draft you can review?"
    return {"body": body, "cta": "open_ended", "rationale": f"Research trigger matched to category digest item {item.get('id', 'unknown')} and merchant cohort/signal."}


def _regulation_change(category, merchant, trigger):
    item = _digest_item(category, trigger) or {}
    deadline = trigger.get("payload", {}).get("deadline_iso")
    deadline_text = f" before {_date_label(deadline)}" if deadline else ""
    body = f"{_owner(merchant)}, important compliance update: {item.get('title', 'new regulation change')} ({item.get('source', 'official category digest')}). Want me to make a 3-point checklist for your team{deadline_text}?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Compliance trigger outranks normal marketing nudges and uses the cited digest item."}


def _supply_alert(category, merchant, trigger):
    p = trigger.get("payload", {})
    batches = _join_list(p.get("batches") or p.get("batch_ids") or [])
    affected = p.get("affected_customers") or p.get("affected_customer_count")
    affected_text = f" I found {affected} potentially affected customers in your repeat list." if affected else ""
    body = f"{_owner(merchant)}, urgent supply alert: {p.get('drug', p.get('item', 'flagged stock'))} {batches} needs review.{affected_text} Want me to draft the customer note + replacement workflow?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Supply alert needs precise, calm handling with action workflow."}


def _perf_dip(category, merchant, trigger):
    p = trigger.get("payload", {})
    metric = p.get("metric", "performance")
    raw_delta = p.get("delta_pct")
    peer_ctr = category.get("peer_stats", {}).get("avg_ctr")
    ctr = merchant.get("performance", {}).get("ctr")
    peer = f" Your CTR is {_pct(ctr)} vs {_pct(peer_ctr)} peer benchmark." if ctr and peer_ctr else ""
    if raw_delta is None:
        body = f"{_owner(merchant)}, I'm seeing a dip signal on {metric} over {p.get('window', '7d')} but don't have the exact drop yet.{peer} Want me to pull the number and draft the fix once it's confirmed?"
        return {"body": body, "cta": "binary_yes_no", "rationale": "Dip trigger fired without a numeric delta in payload; avoided fabricating a percentage."}
    delta = _pct_abs(raw_delta)
    baseline = p.get("vs_baseline")
    base = f" from {baseline}" if baseline else ""
    verb = "are" if str(metric).endswith("s") else "is"
    body = f"{_owner(merchant)}, {metric} {verb} down {delta} over {p.get('window', '7d')}{base}.{peer} Want me to draft the one listing fix most likely to recover discovery?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Performance dip is urgent and paired with the strongest merchant-specific diagnostic signal."}


def _perf_spike(category, merchant, trigger):
    p = trigger.get("payload", {})
    metric = p.get("metric", "views")
    raw_delta = p.get("delta_pct")

    if raw_delta is None:
        body = (
            f"{_owner(merchant)}, {metric} has a recent activity signal, "
            f"but I do not have the exact increase yet. Want me to verify it "
            f"and turn the attention into a quick offer post using "
            f"{_best_offer(merchant, category)}?"
        )
        return {
            "body": body,
            "cta": "binary_yes_no",
            "rationale": "Spike signal arrived without a numeric increase; avoided inventing a percentage.",
        }

    body = (
        f"{_owner(merchant)}, {metric} jumped {_pct(raw_delta)} in the last "
        f"{p.get('window', '7d')}. Want me to convert the spike into a quick "
        f"offer post using {_best_offer(merchant, category)}?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Performance spike is an opportunity trigger paired with an immediate action.",
    }


def _renewal_due(category, merchant, trigger):
    p = trigger.get("payload", {})
    days = p.get("days_remaining") or merchant.get("subscription", {}).get("days_remaining")
    plan = p.get("plan") or merchant.get("subscription", {}).get("plan")

    plan_text = f"your {plan} plan" if plan else "your magicpin plan"
    timing_text = f"has {days} days left" if days is not None else "is coming up for renewal"
    proof = _merchant_performance_anchor(merchant)

    body = (
        f"{_owner(merchant)}, {plan_text} {timing_text}.{proof} "
        "Want me to send a one-screen summary of what magicpin drove this month before renewal?"
    )
    return {
        "body": body,
        "cta": "binary_yes_no",
        "rationale": "Renewal trigger explains value before asking for a decision.",
    }


def _competitor_opened(category, merchant, trigger):
    p = trigger.get("payload", {})
    dist = p.get("distance_km") or p.get("distance")
    dist_text = f"{dist} km" if dist is not None else "close by"
    competitor = p.get("competitor_name", "a new competitor")
    body = f"{_owner(merchant)}, {competitor} just appeared {dist_text} from {_locality(merchant)}. Want me to compare their listing against yours and show the 2 gaps customers will notice first?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Competitor trigger uses local threat, curiosity, and a concrete comparison offer."}


def _festival_upcoming(category, merchant, trigger):
    p = trigger.get("payload", {})
    festival = p.get("festival") or p.get("name") or "festival"
    days = p.get("days_until")
    when = f" in {days} days" if days is not None else ""
    body = f"{_owner(merchant)}, {festival}{when} is close. Want me to package {_best_offer(merchant, category)} into a WhatsApp-ready festive post for {_locality(merchant)} customers?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Festival trigger is made specific with timing, locality, and real offer."}


def _ipl_match_today(category, merchant, trigger):
    p = trigger.get("payload", {})
    match = p.get("match") or p.get("teams") or "tonight's IPL match"
    time_text = p.get("time") or p.get("start_time") or "tonight"
    body = f"{_owner(merchant)}, {match} is {time_text}. Use your active {_best_offer(merchant, category)} as a delivery/watch-party push, not a generic dine-in discount. Want me to draft the banner copy?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Restaurant match-day trigger converts event timing into an operator-specific promo recommendation."}


def _review_theme(category, merchant, trigger):
    theme = _first(merchant.get("review_themes", [])) or {}
    quote = theme.get("common_quote")
    quote_text = f' Customer line: "{quote}".' if quote else ""
    body = f"{_owner(merchant)}, {theme.get('occurrences_30d', 'multiple')} recent reviews mention {theme.get('theme', 'one repeated issue')}.{quote_text} Want me to draft a calm reply + one profile update?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Review theme trigger uses the repeated complaint/praise as the strongest verifiable hook."}


def _milestone(category, merchant, trigger):
    p = trigger.get("payload", {})
    metric = p.get("metric", "milestone")
    value = p.get("value_now") or p.get("value") or p.get("count")
    target = p.get("milestone_value")
    if value is not None:
        value_text = f"{value} {metric}"
    else:
        value_text = f"a new {metric} milestone"
    target_text = f" (closing in on {target})" if target and value is not None else ""
    body = f"{_owner(merchant)}, you just crossed {value_text}{target_text}. Want me to turn that into a trust-building Google post for new customers in {_locality(merchant)}?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Milestone trigger turns proof into reputation marketing."}


def _gbp_unverified(category, merchant, trigger):
    body = f"{_owner(merchant)}, your Google profile is still unverified, so edits can be delayed or hidden. Want me to make the exact 3-step verification checklist for {_merchant_name(merchant)}?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "GBP verification is a blocking operational issue with clear next action."}


def _dormant(category, merchant, trigger):
    days = _extract_signal_number(merchant.get("signals", []), "dormant_with_vera") or trigger.get("payload", {}).get("days")
    body = f"{_owner(merchant)}, quick check after {days or 14} days: should I review your current listing and send only the top 1 growth action for this week?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Dormancy trigger uses a low-pressure re-entry ask."}


def _curious_ask(category, merchant, trigger):
    body = f"{_owner(merchant)}, quick question: what service/product are customers asking for most this week at {_merchant_short(merchant)}? I will turn your answer into a 4-line Google post."
    return {"body": body, "cta": "open_ended", "rationale": "Curious-ask trigger uses merchant input as the engagement lever."}


def _category_seasonal(category, merchant, trigger):
    beat = _first(category.get("seasonal_beats", [])) or {}
    body = f"{_owner(merchant)}, seasonal note for {category.get('display_name', category.get('slug', 'your category'))}: {beat.get('note', 'demand is shifting this week')}. Want me to adapt {_best_offer(merchant, category)} into a timely post?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Seasonal trigger uses category timing and a real offer."}


def _cde_opportunity(category, merchant, trigger):
    p = trigger.get("payload", {})
    body = f"{_owner(merchant)}, {p.get('title', 'a category learning opportunity')} is relevant this week. Want me to pull the key points and draft a patient-friendly post from it?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Professional-development trigger is framed as useful content the merchant can reuse."}


def _active_planning(category, merchant, trigger):
    p = trigger.get("payload", {})
    topic = (
        p.get("intent_topic")
        or p.get("topic")
        or p.get("intent")
        or "your plan"
    )
    body = (
        f"{_owner(merchant)}, here is the next practical step for {topic}: "
        f"I will draft one offer, one customer WhatsApp, and one post using "
        f"{_best_offer(merchant, category)}. Reply CONFIRM and I will prepare it."
    )
    return {
        "body": body,
        "cta": "binary_confirm_cancel",
        "rationale": "Active planning means commitment has begun, so the bot moves to action mode.",
    }


def _generic_merchant(category, merchant, trigger):
    p = trigger.get("payload", {})
    fact = next(
        (f"{str(k).replace('_', ' ')}: {v}" for k, v in p.items()
         if k not in {"placeholder", "metric_or_topic"} and v not in (None, "", [])),
        None,
    )
    hook = f" ({fact})" if fact else ""
    kind_label = str(trigger.get("kind") or "a new signal").replace("_", " ")
    body = f"{_owner(merchant)}, I noticed {kind_label}{hook} for {_merchant_short(merchant)}. Want me to turn it into one concrete growth action using your current listing context?"
    return {"body": body, "cta": "binary_yes_no", "rationale": "Fallback surfaces any concrete payload field found for an unrecognized trigger kind and avoids invented details."}


def _customer_recall(category, merchant, trigger, customer):
    p = trigger.get("payload", {})
    name = _customer_name(customer)
    merchant_label = _merchant_customer_label(merchant)
    elapsed = _months_since(customer.get("relationship", {}).get("last_visit"), p.get("due_date"))
    slots = p.get("available_slots") or []
    slot_text = " or ".join(slot.get("label", "") for slot in slots[:2] if slot.get("label"))
    offer = _best_offer(merchant, category)
    mix = _is_hi_en(customer)
    if slot_text:
        hold_text = f"Apke liye 2 slots ready hain: {slot_text}." if mix else f"I can hold {slot_text}."
    else:
        hold_text = "Bataiye aapke liye kaunsa time better hoga." if mix else "Let me know a time that works and I will lock a slot."
    body = f"Hi {name}, {merchant_label} here. It has been {elapsed} since your last visit; your {p.get('service_due', 'recall').replace('_', ' ')} is due. {hold_text} {offer}. Reply 1 for first slot, 2 for second, or share a better time."
    return {"body": body, "cta": "multi_choice_slot", "rationale": "Customer recall uses consented customer context, last visit timing, available slots, and merchant offer."}


def _chronic_refill(category, merchant, trigger, customer):
    p = trigger.get("payload", {})
    medicines = _join_list(p.get("medicines") or p.get("items") or customer.get("relationship", {}).get("services_received", []))
    medicine_text = f" ({medicines})" if medicines else ""
    runout = _date_label(p.get("runout_date") or p.get("due_date"))
    offer = _best_offer(merchant, category)
    body = f"Namaste {_customer_name(customer)}, {_merchant_customer_label(merchant)} here. Your regular medicines{medicine_text} are due around {runout}. {offer}. Reply CONFIRM to keep the same pack ready, or call us if the dose changed."
    return {"body": body, "cta": "binary_confirm_cancel", "rationale": "Refill trigger is precise, respectful, and asks for confirmation before dispatch."}


def _appointment_reminder(category, merchant, trigger, customer):
    p = trigger.get("payload", {})
    when = p.get("appointment_time") or p.get("slot_label") or p.get("time") or "tomorrow"
    service = p.get("service") or p.get("service_due", "your appointment").replace("_", " ")
    body = (
        f"Hi {_customer_name(customer)}, {_merchant_customer_label(merchant)} here. "
        f"Reminder: {service} is booked for {when}. Reply CONFIRM to keep it, "
        f"or let us know if you need to reschedule."
    )
    return {
        "body": body,
        "cta": "binary_confirm_cancel",
        "rationale": "Appointment reminder confirms the booked slot and offers an easy reschedule path.",
    }


def _customer_winback(category, merchant, trigger, customer):
    goal = _join_list(customer.get("relationship", {}).get("services_received", [])[-2:]) or "your last service"
    offer = _best_offer(merchant, category)
    body = f"Hi {_customer_name(customer)}, {_merchant_customer_label(merchant)} here. It has been a while since {goal}; no pressure. We can keep {offer} ready for you this week. Reply YES and we will share the best slot."
    return {"body": body, "cta": "binary_yes_no", "rationale": "Winback is warm, low-pressure, and grounded in the customer's relationship history."}


def _generic_customer(category, merchant, trigger, customer):
    p = trigger.get("payload", {})
    fact = next(
        (f"{str(k).replace('_', ' ')}: {v}" for k, v in p.items()
         if k not in {"placeholder", "metric_or_topic"} and v not in (None, "", [])),
        None,
    )
    hook = f" ({fact})" if fact else " related to your last visit"
    body = f"Hi {_customer_name(customer)}, {_merchant_customer_label(merchant)} here. A quick update{hook}: reply YES and we will share the useful details."
    return {"body": body, "cta": "binary_yes_no", "rationale": "Customer fallback surfaces any concrete payload field found and avoids unsupported claims while keeping a simple consented CTA."}


def _finalize(result, trigger, send_as):
    body = _clean_body(result["body"])
    return {
        "body": body,
        "cta": result.get("cta", "binary_yes_no"),
        "send_as": send_as,
        "suppression_key": trigger.get("suppression_key", trigger.get("id", "")),
        "rationale": result.get("rationale", "Selected strongest grounded trigger and one low-friction CTA."),
    }


def _maybe_enhance_with_mistral(category, merchant, trigger, customer, draft):
    if os.getenv("VERA_USE_MISTRAL", "").lower() not in {"1", "true", "yes"}:
        return draft
    api_key = os.getenv("MISTRAL_API_KEY")
    if not api_key:
        return draft

    try:
        from mistralai.client import Mistral

        prompt = _mistral_prompt(category, merchant, trigger, customer, draft)
        with Mistral(api_key=api_key) as client:
            response = client.chat.complete(
                model=os.getenv("MISTRAL_MODEL", "mistral-large"),
                messages=[
                    {
                        "role": "system",
                        "content": "You improve WhatsApp growth messages. Return only valid JSON. Do not invent facts.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                stream=False,
                response_format={"type": "json_object"},
            )
        content = response.choices[0].message.content
        data = json.loads(content)
        improved = {
            "body": _clean_body(str(data.get("body") or draft["body"])),
            "cta": str(data.get("cta") or draft["cta"]),
            "send_as": draft["send_as"],
            "suppression_key": draft["suppression_key"],
            "rationale": str(data.get("rationale") or draft["rationale"]),
        }
        if _valid_enhancement(improved, draft):
            return improved
    except Exception:
        return draft
    return draft


def _mistral_prompt(category, merchant, trigger, customer, draft):
    allowed_context = {
        "category": {
            "slug": category.get("slug"),
            "voice": category.get("voice"),
            "peer_stats": category.get("peer_stats"),
            "digest": category.get("digest"),
            "offer_catalog": category.get("offer_catalog"),
            "seasonal_beats": category.get("seasonal_beats"),
            "trend_signals": category.get("trend_signals"),
        },
        "merchant": merchant,
        "trigger": trigger,
        "customer": customer,
        "draft": draft,
    }
    return (
        "Polish the draft for the magicpin Vera challenge.\n"
        "Rules: preserve factual grounding, use only the JSON context below, one clear CTA, no URLs, concise WhatsApp tone, "
        "same send_as and suppression_key are already fixed outside your output.\n"
        "Return JSON with exactly: body, cta, rationale.\n\n"
        f"{json.dumps(allowed_context, ensure_ascii=False)}"
    )


def _valid_enhancement(improved, draft):
    if not improved["body"] or len(improved["body"]) > 700:
        return False
    if "http" in improved["body"].lower():
        return False
    if improved["cta"] not in {"binary_yes_no", "binary_confirm_cancel", "open_ended", "none", "multi_choice_slot"}:
        return False
    if improved["send_as"] != draft["send_as"]:
        return False
    if improved["suppression_key"] != draft["suppression_key"]:
        return False
    return True


def _get(scope, context_id):
    if not context_id:
        return None
    item = contexts.get((scope, context_id))
    return item.get("payload") if item else None


def _httpish(status_code, body):
    return {"status_code": status_code, "body": body}


def _route_result(result):
    if isinstance(result, dict) and "status_code" in result and "body" in result:
        if JSONResponse:
            return JSONResponse(status_code=result["status_code"], content=result["body"])
        return result
    return result


def _safe_id(value):
    return re.sub(r"[^A-Za-z0-9_:-]+", "_", str(value)).strip("_")


def _now_iso():
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clean_body(body):
    body = re.sub(r"https?://\S+", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body


def _owner(merchant):
    first = merchant.get("identity", {}).get("owner_first_name")
    if first:
        if merchant.get("category_slug") == "dentists" and not first.lower().startswith("dr"):
            return f"Dr. {first}"
        return first
    return _merchant_short(merchant)


def _merchant_name(merchant):
    return merchant.get("identity", {}).get("name", "your business")


def _merchant_short(merchant):
    return _merchant_name(merchant).split(",")[0]


def _merchant_customer_label(merchant):
    name = _merchant_name(merchant)
    if merchant.get("category_slug") == "dentists" and "clinic" in name.lower():
        owner = _owner(merchant)
        return f"{owner}'s clinic"
    return name


def _locality(merchant):
    ident = merchant.get("identity", {})
    return ident.get("locality") or ident.get("city") or "your locality"


def _customer_name(customer):
    return customer.get("identity", {}).get("name", "there").split("(")[0].strip()


def _best_offer(merchant, category):
    active = [o.get("title") for o in merchant.get("offers", []) if o.get("status") == "active" and o.get("title")]
    if active:
        return active[0]
    catalog = [o.get("title") for o in category.get("offer_catalog", []) if o.get("type") in {"service_at_price", "bogo", "free_addon"} and o.get("title")]
    return catalog[0] if catalog else "your strongest current offer"


def _first(items):
    return items[0] if items else None


def _digest_item(category, trigger):
    top_id = trigger.get("payload", {}).get("top_item_id")
    for item in category.get("digest", []):
        if item.get("id") == top_id:
            return item
    return None


def _numbers_from_item(item):
    bits = []
    if item.get("trial_n"):
        bits.append(f"{int(item['trial_n']):,}-person")
    text = " ".join(str(item.get(field, "")) for field in ("title", "summary", "actionable"))
    if "%" in text:
        match = re.search(r"\d+%", text)
        if match:
            bits.append(match.group(0))
    return " / ".join(bits)


def _cohort_phrase(merchant):
    agg = merchant.get("customer_aggregate", {})
    if agg.get("high_risk_adult_count"):
        return f"your {agg['high_risk_adult_count']} high-risk adult customers"
    if agg.get("lapsed_180d_plus"):
        return f"your {agg['lapsed_180d_plus']} lapsed customers"
    return "your customers"


def _merchant_performance_anchor(merchant):
    perf = merchant.get("performance", {})
    if perf.get("views") and perf.get("calls"):
        return f" Last 30 days: {perf['views']} views and {perf['calls']} calls."
    return ""


def _pct(value):
    if value is None:
        return "0%"
    try:
        value = float(value)
    except Exception:
        return str(value)
    if abs(value) < 1:
        return f"{value * 100:.1f}".rstrip("0").rstrip(".") + "%"
    return f"{value:.0f}%"


def _pct_abs(value):
    try:
        return _pct(abs(float(value)))
    except Exception:
        return _pct(value)


def _join_list(items):
    if isinstance(items, str):
        return items
    cleaned = [str(i) for i in items if i and str(i) != "..."]
    return ", ".join(cleaned)


def _date_label(value):
    if not value:
        return "the deadline"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y").lstrip("0")
    except Exception:
        return str(value)


def _months_since(start, end):
    if not start or not end:
        return "a few months"
    try:
        s = datetime.fromisoformat(start[:10])
        e = datetime.fromisoformat(end[:10])
        months = max(1, (e.year - s.year) * 12 + e.month - s.month)
        return f"{months} months"
    except Exception:
        return "a few months"


def _extract_signal_number(signals, prefix):
    for signal in signals:
        if str(signal).startswith(prefix):
            match = re.search(r"(\d+)", str(signal))
            if match:
                return int(match.group(1))
    return None


def _has_consent(customer, trigger):
    if not customer:
        return False
    prefs = customer.get("preferences", {})
    if prefs.get("reminder_opt_in") is False:
        return False
    scopes = set(customer.get("consent", {}).get("scope", []))
    kind = trigger.get("kind", "")
    if kind == "recall_due":
        return "recall_reminders" in scopes
    if kind == "chronic_refill_due":
        return bool(scopes)
    return bool(scopes) or prefs.get("reminder_opt_in") is True


def _is_hi_en(customer):
    return "hi" in str(customer.get("identity", {}).get("language_pref", "")).lower()


def _conversation_id(merchant_id, trigger, customer):
    who = customer.get("customer_id") if customer else merchant_id
    return f"conv_{_safe_id(who)}_{_safe_id(trigger.get('kind', 'trigger'))}_{_safe_id(trigger.get('id', ''))[-8:]}"


def _template_name(trigger, send_as):
    kind = trigger.get("kind", "generic")
    if send_as == "merchant_on_behalf":
        return f"merchant_{kind}_v1"
    return f"vera_{kind}_v1"


def _template_params(body):
    words = body.split()
    if len(words) <= 12:
        return [body, "", ""]
    first = " ".join(words[: min(10, len(words))])
    remaining = words[len(first.split()):]
    midpoint = max(1, len(remaining) // 2)
    second = " ".join(remaining[:midpoint])
    third = " ".join(remaining[midpoint:])
    return [first, second, third]


def _trigger_priority(trigger):
    if not trigger:
        return 0
    kind_bonus = {
        "regulation_change": 50,
        "supply_alert": 50,
        "recall_due": 45,
        "chronic_refill_due": 45,
        "perf_dip": 40,
        "gbp_unverified": 35,
        "active_planning_intent": 34,
        "competitor_opened": 30,
        "perf_spike": 25,
        "research_digest": 20,
        "curious_ask_due": 15,
    }.get(trigger.get("kind"), 10)
    return kind_bonus + int(trigger.get("urgency") or 0)


def _is_auto_reply(text, conversation_id):
    patterns = [
        "thank you for contacting",
        "will respond shortly",
        "automated assistant",
        "away message",
        "business hours",
    ]
    return any(p in text for p in patterns)


def _count_same_replies(conversation_id, message):
    turns = conversations.get(conversation_id, [])
    return sum(1 for turn in turns if turn.get("from") != "bot" and turn.get("body") == message)


def _is_stop_or_hostile(text):
    stop_words = ["stop messaging", "not interested", "unsubscribe", "don't message", "do not message", "useless spam"]
    hostile_words = ["bothering me", "useless", "spam"]
    return any(w in text for w in stop_words + hostile_words)


def _is_commitment(text):
    phrases = ["yes", "ok lets do it", "ok let's do it", "go ahead", "confirm", "do it", "send it", "publish", "please send"]
    return any(p in text for p in phrases)


def _is_time_request(text):
    return any(p in text for p in ["later", "tomorrow", "after some time", "busy", "call later"])


def _is_off_topic(text):
    return any(p in text for p in ["gst", "tax filing", "loan", "personal", "salary", "rent agreement"])


if app:
    @app.get("/v1/healthz")
    async def api_healthz():
        return await healthz()

    @app.get("/v1/metadata")
    async def api_metadata():
        return await metadata()

    @app.post("/v1/context")
    async def api_push_context(request: Request):
        return _route_result(await push_context(await request.json()))

    @app.post("/v1/tick")
    async def api_tick(request: Request):
        return await tick(await request.json())

    @app.post("/v1/reply")
    async def api_reply(request: Request):
        return await reply(await request.json())
