import json
import os
import sys
import unittest
import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_seed_data():
    base = ROOT / "dataset"
    categories = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in (base / "categories").glob("*.json")
    }
    merchants = {
        m["merchant_id"]: m
        for m in json.loads((base / "merchants_seed.json").read_text(encoding="utf-8"))["merchants"]
    }
    customers = {
        c["customer_id"]: c
        for c in json.loads((base / "customers_seed.json").read_text(encoding="utf-8"))["customers"]
    }
    triggers = {
        t["id"]: t
        for t in json.loads((base / "triggers_seed.json").read_text(encoding="utf-8"))["triggers"]
    }
    return categories, merchants, customers, triggers


class ComposerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.categories, cls.merchants, cls.customers, cls.triggers = load_seed_data()

    def test_research_digest_uses_real_source_numbers_and_suppression_key(self):
        from bot import compose

        merchant = self.merchants["m_001_drmeera_dentist_delhi"]
        trigger = self.triggers["trg_001_research_digest_dentists"]
        result = compose(self.categories["dentists"], merchant, trigger)

        self.assertEqual(result["send_as"], "vera")
        self.assertEqual(result["suppression_key"], "research:dentists:2026-W17")
        self.assertIn("Dr. Meera", result["body"])
        self.assertIn("2,100", result["body"])
        self.assertIn("38%", result["body"])
        self.assertIn("JIDA Oct 2026", result["body"])
        self.assertIn("Want me to", result["body"])
        self.assertTrue(result["rationale"])

    def test_customer_recall_is_sent_on_behalf_with_slots_and_consent(self):
        from bot import compose

        merchant = self.merchants["m_001_drmeera_dentist_delhi"]
        trigger = self.triggers["trg_003_recall_due_priya"]
        customer = self.customers["c_001_priya_for_m001"]
        result = compose(self.categories["dentists"], merchant, trigger, customer)

        self.assertEqual(result["send_as"], "merchant_on_behalf")
        self.assertEqual(result["cta"], "multi_choice_slot")
        self.assertIn("Priya", result["body"])
        self.assertIn("Wed 5 Nov, 6pm", result["body"])
        self.assertIn("Thu 6 Nov, 5pm", result["body"])
        self.assertIn("Dental Cleaning @", result["body"])
        self.assertNotIn("http", result["body"].lower())

    def test_same_input_is_deterministic(self):
        from bot import compose

        merchant = self.merchants["m_002_bharat_dentist_mumbai"]
        trigger = self.triggers["trg_004_perf_dip_bharat"]
        first = compose(self.categories["dentists"], merchant, trigger)
        second = compose(self.categories["dentists"], merchant, trigger)

        self.assertEqual(first, second)

    def test_mistral_is_env_controlled_and_falls_back_without_key(self):
        from bot import compose

        old_use = os.environ.get("VERA_USE_MISTRAL")
        old_key = os.environ.get("MISTRAL_API_KEY")
        try:
            os.environ["VERA_USE_MISTRAL"] = "1"
            os.environ.pop("MISTRAL_API_KEY", None)
            merchant = self.merchants["m_002_bharat_dentist_mumbai"]
            trigger = self.triggers["trg_004_perf_dip_bharat"]
            result = compose(self.categories["dentists"], merchant, trigger)

            self.assertEqual(result["send_as"], "vera")
            self.assertIn("Bharat", result["body"])
            self.assertIn("50%", result["body"])
        finally:
            if old_use is None:
                os.environ.pop("VERA_USE_MISTRAL", None)
            else:
                os.environ["VERA_USE_MISTRAL"] = old_use
            if old_key is None:
                os.environ.pop("MISTRAL_API_KEY", None)
            else:
                os.environ["MISTRAL_API_KEY"] = old_key


class ApiTests(unittest.TestCase):
    def setUp(self):
        import bot

        bot.reset_state()
        self.bot = bot
        self.categories, self.merchants, self.customers, self.triggers = load_seed_data()

    def test_context_rejects_stale_versions(self):
        payload = {"slug": "dentists"}
        body = {
            "scope": "category",
            "context_id": "dentists",
            "version": 1,
            "payload": payload,
            "delivered_at": "2026-04-26T10:00:00Z",
        }

        first = asyncio.run(self.bot.push_context(body))
        second = asyncio.run(self.bot.push_context(body))

        self.assertTrue(first["accepted"])
        self.assertEqual(second["status_code"], 409)
        self.assertEqual(second["body"]["reason"], "stale_version")

    def test_tick_returns_valid_action_and_suppresses_duplicate(self):
        self._push_seed_contexts(["dentists"], ["m_001_drmeera_dentist_delhi"], [], ["trg_001_research_digest_dentists"])

        first = asyncio.run(self.bot.tick({
            "now": "2026-04-26T10:35:00Z",
            "available_triggers": ["trg_001_research_digest_dentists"],
        }))
        second = asyncio.run(self.bot.tick({
            "now": "2026-04-26T10:40:00Z",
            "available_triggers": ["trg_001_research_digest_dentists"],
        }))

        actions = first["actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["trigger_id"], "trg_001_research_digest_dentists")
        self.assertEqual(actions[0]["template_name"], "vera_research_digest_v1")
        self.assertNotEqual(actions[0]["template_params"][0], "Dr.")
        self.assertEqual(second, {"actions": []})

    def test_reply_handles_auto_stop_and_commitment(self):
        auto = asyncio.run(self.bot.reply({
            "conversation_id": "conv_auto",
            "merchant_id": "m_001_drmeera_dentist_delhi",
            "customer_id": None,
            "from_role": "merchant",
            "message": "Thank you for contacting us! Our team will respond shortly.",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        }))
        stop = asyncio.run(self.bot.reply({
            "conversation_id": "conv_stop",
            "merchant_id": "m_001_drmeera_dentist_delhi",
            "customer_id": None,
            "from_role": "merchant",
            "message": "Not interested. Stop messaging me.",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        }))
        yes = asyncio.run(self.bot.reply({
            "conversation_id": "conv_yes",
            "merchant_id": "m_001_drmeera_dentist_delhi",
            "customer_id": None,
            "from_role": "merchant",
            "message": "Ok lets do it. Whats next?",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2,
        }))

        self.assertEqual(auto["action"], "wait")
        self.assertEqual(stop["action"], "end")
        self.assertEqual(yes["action"], "send")
        self.assertIn("Drafting", yes["body"])

    def _push_seed_contexts(self, categories, merchants, customers, triggers):
        for slug in categories:
            asyncio.run(self.bot.push_context({
                "scope": "category",
                "context_id": slug,
                "version": 1,
                "payload": self.categories[slug],
                "delivered_at": "2026-04-26T10:00:00Z",
            }))
        for mid in merchants:
            asyncio.run(self.bot.push_context({
                "scope": "merchant",
                "context_id": mid,
                "version": 1,
                "payload": self.merchants[mid],
                "delivered_at": "2026-04-26T10:00:00Z",
            }))
        for cid in customers:
            asyncio.run(self.bot.push_context({
                "scope": "customer",
                "context_id": cid,
                "version": 1,
                "payload": self.customers[cid],
                "delivered_at": "2026-04-26T10:00:00Z",
            }))
        for tid in triggers:
            asyncio.run(self.bot.push_context({
                "scope": "trigger",
                "context_id": tid,
                "version": 1,
                "payload": self.triggers[tid],
                "delivered_at": "2026-04-26T10:00:00Z",
            }))


if __name__ == "__main__":
    unittest.main()
