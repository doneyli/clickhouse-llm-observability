"""
Deterministic synthetic support-ticket corpus for the Support Triage Parallel
demo (seed data — no external files, no network).

Each ticket carries:
- ``subject`` / ``body``  -> drive the 4 sectioning branches (summary, sentiment,
  category, policy-guard). Some bodies embed an email / API key to trip the guard.
- ``data_question``       -> drives best-of-N SQL voting against the public
  ClickHouse playground datasets (nyc_taxi, github, hackernews, uk, stackoverflow).
- ``designed_moment``     -> the demo beat each ticket is engineered to produce
  (documented in DEMO_SCRIPT.md so the presenter never hunts for a moment live).

Themed on the same ``sql.clickhouse.com`` public datasets used by the text-to-sql
and agentic-rag demos, so the SA has a natural pivot line from those demos.
"""

TICKETS = [
    {
        "id": "TCK-001",
        "subject": "NYC taxi dashboard is slow",
        "body": (
            "Our nyc_taxi analytics dashboard has gotten really sluggish over the "
            "last week — the borough breakdown panel takes 20+ seconds to load. "
            "It was fine before we added the fare columns. While you look into "
            "that, our finance team also needs a number: which pickup borough had "
            "the highest average fare in 2015?"
        ),
        "data_question": (
            "Using the nyc_taxi.trips table, which pickup_ntaname / borough had the "
            "highest average fare_amount for trips in 2015? Return the borough and "
            "the average fare, ordered descending."
        ),
        "designed_moment": "clean 5-0 consensus (consensus_confidence=1.0)",
    },
    {
        "id": "TCK-002",
        "subject": "Which repos are most active? (metric unclear)",
        "body": (
            "We're building an internal leaderboard from the github dataset and "
            "leadership keeps asking for 'the most active repositories last year'. "
            "Nobody can agree whether that means stars, pull requests, pushes, or "
            "total events. Can you give us the top repositories — pick whatever "
            "'most active' should reasonably mean?"
        ),
        "data_question": (
            "From the github.github_events dataset, what were the most active "
            "repositories in the last full year? ('most active' is intentionally "
            "ambiguous — stars vs PRs vs pushes vs total events.)"
        ),
        "designed_moment": "vote splits 2-2-1 -> tie-break judge fires",
    },
    {
        "id": "TCK-003",
        "subject": "Hacker News export keeps failing - here are my creds",
        "body": (
            "The scheduled export from the hackernews dataset failed again. I'm "
            "pasting my details so you can reproduce: reach me at "
            "ops.oncall@example.com and here's the service API key we use for the "
            "job: sk-live-9f8e7d6c5b4a3210. Also, roughly how many stories were "
            "posted to Hacker News in 2015 so I can sanity-check the export size?"
        ),
        "data_question": (
            "From the hackernews.hits table, how many stories (type = 'story') "
            "were posted in the year 2015?"
        ),
        "designed_moment": "policy guard flags (policy_flagged=1)",
    },
    {
        "id": "TCK-004",
        "subject": "UK property price question (date formats are a mess)",
        "body": (
            "We're pulling from the uk price_paid dataset and our numbers look off "
            "— I think there's a date-format trap because some of our queries use "
            "the transfer date as a string. Can you tell us the average paid price "
            "in Greater London for the year 2022?"
        ),
        "data_question": (
            "From the uk.uk_price_paid table, what was the average price for "
            "properties in Greater London (county or town) during 2022? Beware the "
            "date column type when filtering the year."
        ),
        "designed_moment": "1-2 invalid candidates -> sql_validity_rate < 1",
    },
    {
        "id": "TCK-005",
        "subject": "Stack Overflow tag trends - urgent for a deck",
        "body": (
            "Slightly panicked — I have a leadership deck in an hour. From the "
            "stackoverflow dataset, which programming-language tags have the most "
            "questions overall? Just the top handful is fine."
        ),
        "data_question": (
            "From the stackoverflow posts/tags data, which tags are attached to "
            "the most questions? Return the top 10 tags by question count."
        ),
        "designed_moment": "run with FAULT=slow-branch -> dropped branch, degraded synthesis",
    },
    {
        "id": "TCK-006",
        "subject": "Billing overage on our taxi ingestion pipeline",
        "body": (
            "We just got a bill that's roughly double last month and we're not sure "
            "why — ingestion volume shouldn't have changed. Frustrated, honestly. "
            "As a side check: how many nyc_taxi trips were recorded in total in "
            "2015 versus 2016?"
        ),
        "data_question": (
            "From nyc_taxi.trips, how many trips were recorded in 2015 and how many "
            "in 2016? Return one row per year with the trip count."
        ),
        "designed_moment": "filler — billing tone, volume for Monitor/dashboard",
    },
    {
        "id": "TCK-007",
        "subject": "Replication lag panic on our analytics replica",
        "body": (
            "Replica is falling behind and dashboards are stale — this is becoming "
            "an incident. Once you help with that, can you also confirm: in the "
            "github dataset, how many distinct repositories saw activity last year?"
        ),
        "data_question": (
            "From github.github_events, how many distinct repositories had at least "
            "one event in the last full year?"
        ),
        "designed_moment": "filler — high-urgency tone, volume",
    },
    {
        "id": "TCK-008",
        "subject": "Quick question about Hacker News top stories",
        "body": (
            "No rush and everything's working great, just curious — what were the "
            "highest-scoring Hacker News stories of 2015? Love the product, thanks!"
        ),
        "data_question": (
            "From hackernews.hits, what were the top 10 stories by score in 2015? "
            "Return the title and score."
        ),
        "designed_moment": "filler — positive tone, volume",
    },
    {
        "id": "TCK-009",
        "subject": "Airport taxi trips - need a breakdown",
        "body": (
            "For a capacity plan we need to understand airport traffic. From the "
            "nyc_taxi data, what's the average trip distance for trips that started "
            "at an airport in 2015?"
        ),
        "data_question": (
            "From nyc_taxi.trips, what is the average trip_distance for airport "
            "pickups in 2015? Use the pickup location / airport flag available in "
            "the dataset."
        ),
        "designed_moment": "filler — neutral tone, volume",
    },
    {
        "id": "TCK-010",
        "subject": "Stack Overflow answer rate by year",
        "body": (
            "We're studying community health. From the stackoverflow dataset, how "
            "has the number of questions asked changed year over year for the last "
            "five years?"
        ),
        "data_question": (
            "From the stackoverflow posts data, how many questions were asked per "
            "year for the last five years? Return year and question count."
        ),
        "designed_moment": "filler — neutral tone, volume",
    },
]

TICKETS_BY_ID = {t["id"]: t for t in TICKETS}


def get_ticket(ticket_id: str):
    """Return a ticket by id (case-insensitive), or None."""
    return TICKETS_BY_ID.get(ticket_id.upper())
