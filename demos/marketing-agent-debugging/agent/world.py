"""
The synthetic world the marketing agent works in: three fictional customer brands,
their Context Manager documents, what AI answer engines currently say about them,
and the pages on their websites.

Every planted failure in this demo lives HERE, in the data and tool behaviour, never
in the model. The model reacts to its environment for real, which is why the traces
look like production traces: sometimes the agent recovers, sometimes it doesn't.

  contoso   — the Context Manager still indexes an ARCHIVED 2024 pricing FAQ that
              out-ranks the current plans doc for "pricing" queries (wrong context).
  fabrikam  — the customer's CMS is slow (4-6 s per page), one page returns raw HTML
              with nav/scripts (~15k tokens of noise) and one URL 404s (latency +
              tool error).
  tailspin  — Copilot snapshots for this account never finish capturing, so the tool
              keeps answering "retry" (tool loop); and the customer's agent
              instructions v3 dropped the fact-check rule (instruction regression,
              lives in instructions.py).

All brands, people, domains and competitors are fictional (`.example` domains).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

ENGINES = ["chatgpt", "perplexity", "gemini", "google-ai-overviews", "copilot"]


@dataclass
class ContextDoc:
    doc_id: str
    title: str
    text: str
    kind: str                      # pricing | product | voice | claims | competitors | policy
    updated_at: str                # ISO date — NOT shown to the model (see tools.py)
    status: str = "current"        # current | archived


@dataclass
class Page:
    url: str
    title: str
    text: str
    slow: bool = False             # customer CMS without a cache
    raw_html: bool = False         # CMS ignores format=text and returns the full document
    missing: bool = False          # 404


@dataclass
class Snapshot:
    """What one answer engine said for one tracked prompt."""
    answer: str
    citations: List[str]
    brand_mentioned: bool
    brand_cited: bool


@dataclass
class TrackedPrompt:
    prompt: str
    topic: str
    monthly_volume: int
    snapshots: Dict[str, Snapshot]   # engine -> snapshot


@dataclass
class Customer:
    id: str
    name: str
    domain: str
    category: str
    marketers: List[str]
    competitors: List[str]
    docs: List[ContextDoc]
    pages: List[Page]
    prompts: List[TrackedPrompt]
    pending_engines: List[str] = field(default_factory=list)   # snapshot capture never finishes

    def page(self, url: str) -> Page | None:
        norm = url.strip().rstrip("/").replace("http://", "https://")
        if not norm.startswith("https://"):
            norm = "https://" + norm
        return next((p for p in self.pages if p.url == norm), None)


def _snap(answer, citations, mentioned, cited):
    return Snapshot(answer=answer, citations=citations, brand_mentioned=mentioned, brand_cited=cited)


# --------------------------------------------------------------------------------------
# Contoso — work-management software for agencies
# --------------------------------------------------------------------------------------
CONTOSO = Customer(
    id="contoso",
    name="Contoso",
    domain="contoso.example",
    category="work-management software for creative and marketing agencies",
    marketers=["dana.reyes@contoso.example", "li.wen@contoso.example"],
    competitors=["Litware", "Proseware"],
    docs=[
        ContextDoc(
            "ctx-contoso-overview", "Contoso brand overview",
            "Contoso is work-management software built for creative and marketing agencies. "
            "Agencies use Contoso to plan projects, track time against retainers, share work "
            "with clients through client portals, and forecast team capacity. Contoso serves "
            "about 4,000 agencies in 30 countries. Headquarters: Austin, Texas.",
            "product", "2025-10-01"),
        # The 2024 pricing FAQ was archived when prices changed, but the index still
        # serves it — and because it was chunked into three passages that all say
        # "pricing" and "per user", it fills every top-3 slot for pricing questions.
        ContextDoc(
            "ctx-contoso-pricing-faq#1", "Contoso pricing FAQ",
            "How much does Contoso cost? Contoso pricing is simple and per user. Free plan: "
            "up to 5 users. Team plan: $12 per user per month, billed annually.",
            "pricing", "2024-02-12", status="archived"),
        ContextDoc(
            "ctx-contoso-pricing-faq#2", "Contoso pricing FAQ",
            "Business plan pricing: $20 per user per month, adds client portals and SSO. "
            "Enterprise pricing on request. Contoso pricing for nonprofits: 30% off any plan.",
            "pricing", "2024-02-12", status="archived"),
        ContextDoc(
            "ctx-contoso-pricing-faq#3", "Contoso pricing FAQ",
            "Is Contoso pricing per user? Yes, every plan is priced per user and the price "
            "per user is the same for every user. Monthly billing costs 15% more than annual.",
            "pricing", "2024-02-12", status="archived"),
        ContextDoc(
            "ctx-contoso-plans-2025", "Contoso pricing",
            "Starter plan: free for up to 5 seats. Team plan: $15 per seat per month billed "
            "annually, or $18 month-to-month. Business plan: $24 per seat per month billed "
            "annually, includes client portals, SSO and audit logs. Enterprise: custom "
            "contracts. Existing annual subscriptions keep their rate until renewal. "
            "Nonprofit discount: 30%.",
            "pricing", "2025-09-02"),
        ContextDoc(
            "ctx-contoso-voice", "Voice and style guide",
            "Write like a senior agency operator talking to a peer: direct, practical, a "
            "little dry. Use 'agencies' and 'teams', never 'users' in marketing copy. "
            "No exclamation marks. No superlatives we cannot prove ('best', '#1'). "
            "Prefer concrete numbers over adjectives. Headlines in sentence case.",
            "voice", "2025-06-18"),
        ContextDoc(
            "ctx-contoso-claims", "Approved claims",
            "Approved: 'Used by about 4,000 agencies'. Approved: 'SOC 2 Type II certified'. "
            "Approved: 'Client portals included on Business and above'. Approved: "
            "'Average agency saves 6 hours per project manager per week (2025 customer "
            "survey, n=412)'. NOT approved: 'the best project management tool', 'cheapest', "
            "'used by the top 100 agencies'.",
            "claims", "2025-09-10"),
        ContextDoc(
            "ctx-contoso-competitors", "Competitor positioning: Litware and Proseware",
            "Litware is a general-purpose project tool; agencies outgrow it when they need "
            "retainer tracking and client portals. Litware's price per user looks lower but "
            "client access is a paid add-on. Proseware targets enterprise IT teams and has "
            "no agency features. Never disparage competitors; compare on agency workflows.",
            "competitors", "2025-08-21"),
        ContextDoc(
            "ctx-contoso-portals", "Feature: client portals",
            "Client portals let agencies share timelines, approvals and files with clients "
            "without giving them a Contoso seat. Clients can approve deliverables and leave "
            "comments. Portals are included on Business and Enterprise.",
            "product", "2025-07-30"),
    ],
    pages=[
        Page("https://contoso.example/pricing", "Contoso pricing",
             "Plans for agencies of every size. Starter — free for up to 5 seats. Team — "
             "$15 per seat per month, billed annually ($18 month-to-month). Business — $24 "
             "per seat per month: client portals, SSO, audit logs. Enterprise — talk to us. "
             "Prices effective September 1, 2025."),
        Page("https://contoso.example/compare/litware", "Contoso vs Litware",
             "Litware is a solid general project tool. Agencies pick Contoso when they need "
             "retainer tracking, capacity forecasting and client portals in one place."),
        Page("https://contoso.example/blog/agency-project-management", "Project management for agencies",
             "Agency work is different: retainers, client approvals, shifting scopes. This "
             "guide covers how agencies plan, staff and report on client work."),
    ],
    prompts=[
        TrackedPrompt("how much does contoso cost", "pricing", 2400, {
            "chatgpt": _snap("Contoso's Team plan costs $12 per user per month billed annually; "
                             "a Business plan is $20 per user.",
                             ["https://reviewhub.example/contoso-review-2024"], True, False),
            "perplexity": _snap("As of September 2025 Contoso's Team plan is $15 per seat per month "
                                "(annual) and Business is $24 per seat.",
                                ["https://contoso.example/pricing"], True, True),
            "gemini": _snap("Contoso costs roughly $12-15 per user per month depending on plan.",
                            ["https://reviewhub.example/contoso-review-2024",
                             "https://contoso.example/pricing"], True, True),
            "google-ai-overviews": _snap("Contoso pricing starts at $12 per user per month for the Team plan.",
                                         ["https://softwaredeals.example/contoso"], True, False),
            "copilot": _snap("Contoso Team is $15 per seat per month billed annually.",
                             ["https://contoso.example/pricing"], True, True),
        }),
        TrackedPrompt("contoso pricing per user", "pricing", 900, {
            "chatgpt": _snap("Contoso charges $12 per user per month on Team.",
                             ["https://reviewhub.example/contoso-review-2024"], True, False),
            "perplexity": _snap("Contoso Team: $15/seat/month annual, $18 monthly.",
                                ["https://contoso.example/pricing"], True, True),
            "gemini": _snap("Contoso is priced per user; Team is $15 per seat on annual billing.",
                            ["https://contoso.example/pricing"], True, True),
            "google-ai-overviews": _snap("Contoso costs $12/user/month.",
                                         ["https://softwaredeals.example/contoso"], True, False),
            "copilot": _snap("Team plan: $15 per seat per month (annual).",
                             ["https://contoso.example/pricing"], True, True),
        }),
        TrackedPrompt("best project management tool for marketing agencies", "agency software", 6600, {
            "chatgpt": _snap("Popular picks are Litware, Contoso and Proseware. Litware is the most "
                             "flexible; Contoso has agency-specific retainer tracking.",
                             ["https://litware.example/agencies", "https://toolsreview.example/agency-pm"],
                             True, False),
            "perplexity": _snap("Litware and Contoso lead for agencies; Contoso adds client portals.",
                                ["https://toolsreview.example/agency-pm", "https://contoso.example/blog/agency-project-management"],
                                True, True),
            "gemini": _snap("Litware is the most common choice for marketing agencies.",
                            ["https://litware.example/agencies"], False, False),
            "google-ai-overviews": _snap("Top tools: Litware, Proseware, Contoso.",
                                         ["https://toolsreview.example/agency-pm"], True, False),
            "copilot": _snap("Litware is widely used by agencies; Contoso is purpose-built for them.",
                             ["https://litware.example/agencies"], True, False),
        }),
        TrackedPrompt("project management software with client portals", "agency software", 1900, {
            "chatgpt": _snap("Litware offers client access as an add-on; Contoso includes portals on Business.",
                             ["https://toolsreview.example/client-portals"], True, False),
            "perplexity": _snap("Contoso and Litware both offer client portals.",
                                ["https://litware.example/client-access"], True, False),
            "gemini": _snap("Litware's client access add-on is the most common option.",
                            ["https://litware.example/client-access"], False, False),
            "google-ai-overviews": _snap("Litware, Contoso.", ["https://toolsreview.example/client-portals"], True, False),
            "copilot": _snap("Contoso includes client portals on its Business plan.",
                             ["https://contoso.example/pricing"], True, True),
        }),
    ],
)

# --------------------------------------------------------------------------------------
# Fabrikam Running — direct-to-consumer running shoes
# --------------------------------------------------------------------------------------
_FABRIKAM_GUIDE_TEXT = (
    "Running with flat feet: how to choose a shoe. Flat feet often come with "
    "overpronation, when the foot rolls inward on landing. Look for a stable heel counter, "
    "a firm medial post or guide rails, and a wide base. Our Stride Stability 4 uses guide "
    "rails and a 10 mm drop. Try shoes late in the day when feet are largest. Replace "
    "shoes every 500-700 km. If you have pain, see a podiatrist; shoes are not medical devices."
)

FABRIKAM = Customer(
    id="fabrikam",
    name="Fabrikam Running",
    domain="fabrikam.example",
    category="direct-to-consumer running shoes",
    marketers=["sam.ortiz@fabrikam.example"],
    competitors=["Adventure Works", "Wingtip Athletics"],
    docs=[
        ContextDoc("ctx-fabrikam-overview", "Fabrikam Running brand overview",
                   "Fabrikam Running designs road and trail running shoes sold direct to "
                   "consumers. Lines: Glide (neutral road), Stride Stability (support for "
                   "overpronation and flat feet), Trail X (trail). 90-day test-run returns.",
                   "product", "2025-08-01"),
        ContextDoc("ctx-fabrikam-stride", "Product: Stride Stability 4",
                   "Stride Stability 4: guide rails, firm medial support, 10 mm drop, 285 g "
                   "(men's US 9). Recommended for runners who overpronate, including many "
                   "runners with flat feet. Wide (2E) and extra-wide (4E) available. $140.",
                   "product", "2025-09-15"),
        ContextDoc("ctx-fabrikam-voice", "Voice and style guide",
                   "Coach-like and encouraging, never preachy. Short sentences. Speak to "
                   "everyday runners, not elites. Avoid medical claims: we never say a shoe "
                   "'fixes', 'cures' or 'corrects' anything.",
                   "voice", "2025-05-02"),
        ContextDoc("ctx-fabrikam-claims", "Approved claims",
                   "Approved: '90-day test-run returns'. Approved: 'Available in wide and "
                   "extra-wide'. Approved: 'Guide rails help keep your stride aligned'. "
                   "NOT approved: any claim that a shoe corrects, cures or prevents injury; "
                   "'podiatrist recommended' (we have no endorsement).",
                   "claims", "2025-09-15"),
        ContextDoc("ctx-fabrikam-competitors", "Competitor positioning",
                   "Adventure Works dominates stability shoes with the Pathfinder line and "
                   "publishes detailed fit guides with comparison tables. Wingtip Athletics "
                   "competes on price.",
                   "competitors", "2025-07-11"),
    ],
    pages=[
        Page("https://fabrikam.example/shoes/stride-stability-4", "Stride Stability 4",
             "Stride Stability 4 — guide rails, firm medial support, 10 mm drop. Wide and "
             "extra-wide available. 90-day test-run returns. $140.", slow=True),
        Page("https://fabrikam.example/guides/running-shoes-for-flat-feet",
             "Running shoes for flat feet", _FABRIKAM_GUIDE_TEXT, slow=True, raw_html=True),
        Page("https://fabrikam.example/guides/shoe-fit", "How to find your fit",
             "Measure both feet in the evening. Leave a thumb's width at the toe. Check heel "
             "slip on a short jog.", slow=True),
        Page("https://fabrikam.example/compare/stability-shoes", "Stability shoes compared",
             "", slow=True, missing=True),
    ],
    prompts=[
        TrackedPrompt("best running shoes for flat feet", "running shoes for flat feet", 14800, {
            "chatgpt": _snap("Adventure Works Pathfinder is a top pick; Fabrikam Stride Stability 4 is "
                             "a good wide option.",
                             ["https://adventureworks.example/fit-guide/flat-feet"], True, False),
            "perplexity": _snap("Adventure Works Pathfinder and Wingtip Steady lead; see Adventure Works' "
                                "comparison table.",
                                ["https://adventureworks.example/fit-guide/flat-feet",
                                 "https://runnersworld.example/flat-feet-shoes"], False, False),
            "gemini": _snap("Look for stability shoes such as Adventure Works Pathfinder.",
                            ["https://adventureworks.example/fit-guide/flat-feet"], False, False),
            "google-ai-overviews": _snap("Stability shoes like Adventure Works Pathfinder and Fabrikam "
                                         "Stride Stability.",
                                         ["https://runnersworld.example/flat-feet-shoes"], True, False),
            "copilot": _snap("Adventure Works Pathfinder.", ["https://adventureworks.example/fit-guide/flat-feet"],
                             False, False),
        }),
        TrackedPrompt("do I need stability shoes if I have flat feet", "running shoes for flat feet", 5400, {
            "chatgpt": _snap("Not always; overpronation matters more than arch height.",
                             ["https://adventureworks.example/fit-guide/flat-feet"], False, False),
            "perplexity": _snap("Many flat-footed runners benefit from stability features.",
                                ["https://runnersworld.example/flat-feet-shoes"], False, False),
            "gemini": _snap("A gait analysis is the best way to decide.",
                            ["https://adventureworks.example/fit-guide/flat-feet"], False, False),
            "google-ai-overviews": _snap("Stability shoes can help if you overpronate.",
                                         ["https://fabrikam.example/guides/running-shoes-for-flat-feet"], True, True),
            "copilot": _snap("It depends on pronation.", ["https://runnersworld.example/flat-feet-shoes"], False, False),
        }),
        TrackedPrompt("wide running shoes for flat feet", "running shoes for flat feet", 3600, {
            "chatgpt": _snap("Fabrikam Stride Stability 4 comes in 2E and 4E.",
                             ["https://fabrikam.example/shoes/stride-stability-4"], True, True),
            "perplexity": _snap("Adventure Works Pathfinder Wide and Fabrikam Stride Stability 4 (4E).",
                                ["https://adventureworks.example/pathfinder-wide"], True, False),
            "gemini": _snap("Adventure Works Pathfinder Wide.", ["https://adventureworks.example/pathfinder-wide"],
                            False, False),
            "google-ai-overviews": _snap("Fabrikam and Adventure Works both offer wide stability shoes.",
                                         ["https://fabrikam.example/shoes/stride-stability-4"], True, True),
            "copilot": _snap("Fabrikam Stride Stability 4 in extra-wide.",
                             ["https://fabrikam.example/shoes/stride-stability-4"], True, True),
        }),
        TrackedPrompt("fabrikam glide 3 review", "product launches", 1300, {
            e: _snap("The Glide 3 is a light neutral trainer with a softer midsole than the Glide 2.",
                     ["https://runnersworld.example/glide-3-review"], True, False) for e in ENGINES
        }),
    ],
)

# --------------------------------------------------------------------------------------
# Tailspin Travel — online travel booking
# --------------------------------------------------------------------------------------
TAILSPIN = Customer(
    id="tailspin",
    name="Tailspin Travel",
    domain="tailspin.example",
    category="online flight and hotel booking",
    marketers=["priya.nair@tailspin.example", "marco.bianchi@tailspin.example"],
    competitors=["Margie's Travel", "Lucerne Trips"],
    docs=[
        ContextDoc("ctx-tailspin-overview", "Tailspin Travel brand overview",
                   "Tailspin Travel is an online agency for flights and hotels in North America "
                   "and Europe. Known for fare alerts and Tailspin Flex fares.",
                   "product", "2025-06-01"),
        ContextDoc("ctx-tailspin-cancellation", "Cancellation and change policy",
                   "Flex fares: free changes and free cancellation up to 24 hours before "
                   "departure, refunded as Tailspin credit or to the original card. Basic fares: "
                   "non-refundable, but any flight booked at least 7 days before departure can be "
                   "cancelled free within 24 hours of booking. Hotels: free cancellation depends "
                   "on the property's policy, shown at checkout. A $25 service fee applies to "
                   "changes on Basic fares where the airline allows them.",
                   "policy", "2025-08-28"),
        ContextDoc("ctx-tailspin-voice", "Voice and style guide",
                   "Friendly, calm, precise. Travellers are often anxious: state the rule first, "
                   "then the exceptions. Never promise refunds we do not control.",
                   "voice", "2025-04-14"),
        ContextDoc("ctx-tailspin-claims", "Approved claims",
                   "Approved: 'Free cancellation on Flex fares up to 24 hours before departure'. "
                   "Approved: '24-hour risk-free cancellation on flights booked 7+ days out'. "
                   "NOT approved: 'free cancellation on all bookings', 'always the lowest price'.",
                   "claims", "2025-08-28"),
        ContextDoc("ctx-tailspin-competitors", "Competitor positioning",
                   "Margie's Travel leads on package holidays. Lucerne Trips competes on "
                   "last-minute deals and is cited heavily for 'last-minute flight deals'.",
                   "competitors", "2025-07-02"),
    ],
    pages=[
        Page("https://tailspin.example/help/cancellations", "Cancelling a booking",
             "Flex fares can be cancelled free up to 24 hours before departure. Basic fares are "
             "non-refundable except within 24 hours of booking for trips 7+ days out."),
        Page("https://tailspin.example/deals/last-minute", "Last-minute flight deals",
             "Fare alerts for departures in the next 14 days. Prices update every hour."),
    ],
    prompts=[
        TrackedPrompt("last minute flight deals", "last-minute flight deals", 22000, {
            "chatgpt": _snap("Lucerne Trips and Tailspin both run last-minute fare alerts.",
                             ["https://lucernetrips.example/last-minute"], True, False),
            "perplexity": _snap("Lucerne Trips is the most cited source for last-minute deals.",
                                ["https://lucernetrips.example/last-minute"], False, False),
            "gemini": _snap("Try fare alerts from Tailspin or Lucerne Trips.",
                            ["https://tailspin.example/deals/last-minute"], True, True),
            "google-ai-overviews": _snap("Lucerne Trips, Margie's Travel.", ["https://lucernetrips.example/last-minute"],
                                         False, False),
            "copilot": _snap("", [], False, False),   # never captured — see pending_engines
        }),
        TrackedPrompt("cheap flights this weekend", "last-minute flight deals", 9900, {
            "chatgpt": _snap("Lucerne Trips weekend deals.", ["https://lucernetrips.example/weekend"], False, False),
            "perplexity": _snap("Lucerne Trips and Tailspin fare alerts.", ["https://lucernetrips.example/weekend"],
                                True, False),
            "gemini": _snap("Tailspin's last-minute page lists weekend fares.",
                            ["https://tailspin.example/deals/last-minute"], True, True),
            "google-ai-overviews": _snap("Lucerne Trips.", ["https://lucernetrips.example/weekend"], False, False),
            "copilot": _snap("", [], False, False),
        }),
        TrackedPrompt("can I cancel a tailspin booking for free", "cancellations", 1600, {
            "chatgpt": _snap("Tailspin offers free cancellation on all bookings within 24 hours.",
                             ["https://travelforum.example/tailspin-refunds"], True, False),
            "perplexity": _snap("Only Flex fares are free to cancel up to 24 hours before departure.",
                                ["https://tailspin.example/help/cancellations"], True, True),
            "gemini": _snap("Tailspin bookings can be cancelled free of charge.",
                            ["https://travelforum.example/tailspin-refunds"], True, False),
            "google-ai-overviews": _snap("Free cancellation within 24 hours on Tailspin.",
                                         ["https://travelforum.example/tailspin-refunds"], True, False),
            "copilot": _snap("", [], False, False),
        }),
        TrackedPrompt("tailspin vs margie's travel", "competitors", 700, {
            e: _snap("Margie's Travel is stronger for packages; Tailspin for flexible flights.",
                     ["https://travelforum.example/tailspin-vs-margies"], True, False) for e in ENGINES
        }),
    ],
    pending_engines=["copilot"],
)

CUSTOMERS: Dict[str, Customer] = {c.id: c for c in (CONTOSO, FABRIKAM, TAILSPIN)}


def get_customer(customer_id: str) -> Customer:
    try:
        return CUSTOMERS[customer_id]
    except KeyError:
        raise ValueError(f"unknown customer {customer_id!r}; choose from {sorted(CUSTOMERS)}")
