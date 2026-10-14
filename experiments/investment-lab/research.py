"""Documented Perplexity Agent API adapter; drafts never auto-certify their claims.

Request shape: https://docs.perplexity.ai/docs/agent-api/quickstart (reviewed 2026-10-14).
A provider-side spending cap and conservative per-request bound must be verified before use.
"""

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from uuid import uuid4

from risk import number


def fallback(reason, now):
    return {
        "status": "unavailable",
        "provider": "zero_cost_fallback",
        "retrieved_at_utc": now.isoformat(),
        "sources": [],
        "claims": [],
        "limitations": [reason, "No current news inferred; new entry blocked"],
    }


class ResearchClient:
    def __init__(
        self,
        store,
        key="",
        model="",
        monthly_limit="0",
        request_bound=None,
        billing_cap_verified=False,
        transport=None,
    ):
        self.store, self.key, self.model = store, key, model
        self.limit = number(monthly_limit)
        self.bound = number(request_bound) if request_bound else None
        self.billing_verified = billing_cap_verified
        self.transport = transport or urllib.request.urlopen

    def gather(self, tickers, now=None):
        now = now or datetime.now(UTC)
        if (
            not self.key
            or not self.model
            or not self.billing_verified
            or self.bound is None
            or self.bound <= 0
        ):
            return fallback("API credentials, model and verified billing limits required", now)
        with self.store.transaction() as c:
            events = self.store.events(c)
            reservations = [
                e
                for e in events
                if e["kind"] == "RESEARCH_RESERVATION"
                and e["payload"]["at_utc"][:7] == now.isoformat()[:7]
            ]
            settled = {
                e["payload"]["reservation_id"]: e["payload"]
                for e in events
                if e["kind"] == "RESEARCH_COST"
            }
            if any(e["kind"] == "RESEARCH_BUDGET_BREACH" for e in events):
                return fallback("Billing bound violation requires operator review", now)
            daily = sum(e["payload"]["at_utc"][:10] == now.isoformat()[:10] for e in reservations)
            spent = sum(
                number(settled.get(e["id"], {}).get("cost_usd", e["payload"]["reserved_usd"]))
                for e in reservations
            )
            if daily >= 3 or spent + self.bound > self.limit:
                return fallback("Daily call limit or monthly spending ceiling reached", now)
            reservation = self.store.append(
                c,
                "RESEARCH_RESERVATION",
                {"at_utc": now.isoformat(), "reserved_usd": str(self.bound)},
                str(uuid4()),
            )
        payload = {
            "model": self.model,
            "input": "Review current company news, earnings, filings, negative catalysts and sector risks for "
            + ", ".join(tickers)
            + ". Cite primary URLs and exact publication dates. Do not report account information, executable prices, quantities or trade instructions.",
            "tools": [{"type": "web_search"}, {"type": "finance_search"}],
            "max_steps": 4,
            "max_output_tokens": 2048,
        }
        try:
            request = urllib.request.Request(
                "https://api.perplexity.ai/v1/agent",
                data=json.dumps(payload).encode(),
                headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            )
            with self.transport(request, timeout=60) as response:
                data = json.load(response)
            cost = data.get("usage", {}).get("cost", {})
            if cost.get("currency") == "USD" and cost.get("total_cost") is not None:
                actual = number(cost["total_cost"])
                if actual < 0:
                    raise ValueError("Invalid usage cost")
                self.store.save(
                    "RESEARCH_COST", {"reservation_id": reservation, "cost_usd": str(actual)}
                )
                if actual > self.bound:
                    self.store.save(
                        "RESEARCH_BUDGET_BREACH",
                        {"reservation_id": reservation, "reason": "request_bound_exceeded"},
                    )
                    return fallback(
                        "Provider exceeded verified request bound; further calls disabled", now
                    )
            sources, claims = [], []
            for item in data.get("output", []):
                for source in item.get("results", []):
                    if source.get("url"):
                        sources.append(
                            {
                                "url": source["url"],
                                "published_at_utc": None,
                                "reported_publication_date": source.get("date"),
                                "retrieved_at_utc": now.isoformat(),
                                "primary": False,
                            }
                        )
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        claims.append(
                            {
                                "text": content.get("text", ""),
                                "citations": content.get("annotations", []),
                            }
                        )
            result = {
                "status": "draft",
                "provider": "perplexity",
                "tickers": tickers,
                "retrieved_at_utc": now.isoformat(),
                "sources": sources,
                "claims": claims,
                "independent_review": False,
                "limitations": [
                    "Verify primary sources, publication timestamps and cited claims before entry use. Model text is not an authority for pricing or accounting."
                ],
            }
            self.store.save("RESEARCH_DRAFT", result)
            return result
        except (OSError, ValueError, TypeError, AttributeError):
            result = fallback(
                "Research API unavailable or invalid response; reservation retained", now
            )
            self.store.save("RESEARCH_FAILURE", result)
            return result
