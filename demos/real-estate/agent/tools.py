"""
Tools for the Real Estate Property Concierge agent.

Each tool is a plain Python function over the synthetic catalog. The agent calls
them through Anthropic tool-use; the concierge loop wraps every execution in a
Langfuse span so tool calls show up in the trace tree.
"""

from typing import Any, Dict, List, Optional, Tuple

from .catalog import LISTINGS, get_listing, NEIGHBORHOODS, neighborhood_key

# --------------------------------------------------------- feature vocabulary ---
# Derived from the catalog rather than hard-coded, so the tool schema can never
# drift from the data. Advertised as a JSON-Schema `enum` below, which is the
# actual fix for a real bug the simulated-conversation experiment caught:
# the schema used to give only EXAMPLES, so the model invented tokens from the
# user's own words — `lift`, `air conditioning`, `balcony` — and exact
# set-subset matching turned each into a permanent zero-result search. Worst
# case observed: a user needed a lift (their mother cannot manage stairs), the
# agent searched `features=['lift']`, got nothing, told them no flats existed,
# and offered to drop the lift requirement — while LIS-102 (€620k, 2-bed,
# `elevator`) matched every criterion they had stated.
FEATURE_VOCABULARY: List[str] = sorted({f for l in LISTINGS for f in (l.get("features") or [])})

# Second line of defence, because an enum constrains but does not guarantee.
# Maps the words users actually say onto catalog tokens.
FEATURE_SYNONYMS: Dict[str, str] = {
    "lift": "elevator",
    "ac": "air_conditioning",
    "aircon": "air_conditioning",
    "a_c": "air_conditioning",
    "garage": "parking",
    "car_park": "parking",
    "car_parking": "parking",
    "swimming_pool": "pool",
    "yard": "garden",
    "patio": "terrace",
    "roof_terrace": "terrace",
    "ocean_view": "sea_view",
    "sea_views": "sea_view",
    "underground": "metro",
    "subway": "metro",
    "tube": "metro",
    "refurbished": "renovated",
    "remodeled": "renovated",
    "remodelled": "renovated",
}


def normalize_features(features: Optional[List[str]]) -> Tuple[List[str], List[str]]:
    """Split requested features into (recognised catalog tokens, unrecognised).

    Casing, spaces and hyphens are all normalised to the catalog's snake_case, so
    'Air Conditioning', 'air-conditioning' and 'air conditioning' all land on
    `air_conditioning`. Synonyms are then applied.

    Unrecognised tokens are RETURNED rather than filtered on. Filtering on a
    token no listing can have guarantees zero results, and — as the experiment
    showed — the agent reports that as market scarcity ("nothing available")
    instead of as an unsupported filter. Handing them back lets the caller
    search on what it understands and say plainly what it could not honour.
    """
    known, unknown = [], []
    for raw in features or []:
        token = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
        token = FEATURE_SYNONYMS.get(token, token)
        if token in FEATURE_VOCABULARY:
            if token not in known:
                known.append(token)
        elif raw not in unknown:
            unknown.append(str(raw))
    return known, unknown


# ------------------------------------------------------------- tool schemas ---
ANTHROPIC_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_listings",
        "description": "Search the property catalog. Returns listings matching the filters, "
                       "each with its id, price, bedrooms, size and neighborhood. "
                       "Prices are the full sale price for buy, or monthly rent for rent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City to search, e.g. 'Madrid'."},
                "operation": {"type": "string", "enum": ["buy", "rent"],
                              "description": "Whether the user wants to buy or rent."},
                "max_price": {"type": "number", "description": "Maximum price (sale price for buy, monthly for rent)."},
                "min_price": {"type": "number", "description": "Minimum price."},
                "min_bedrooms": {"type": "integer", "description": "Minimum number of bedrooms."},
                "property_type": {"type": "string",
                                  "description": "apartment, house, penthouse, studio, etc. Optional."},
                # `enum`, not examples: the model must choose from the catalog's
                # actual vocabulary instead of inventing a token from the user's
                # phrasing. Anything unrecognised is reported back in
                # `unsupported_features` rather than silently zeroing the search.
                "features": {"type": "array",
                             "items": {"type": "string", "enum": FEATURE_VOCABULARY},
                             "description": "Desired features. ONLY these exact values are "
                                            "supported: " + ", ".join(FEATURE_VOCABULARY) +
                                            ". Map the user's wording onto them (a 'lift' is "
                                            "'elevator'; a 'garage' is 'parking'). If the user "
                                            "asks for something not in this list, omit it here "
                                            "and tell them it cannot be filtered on."},
            },
            "required": ["city", "operation"],
        },
    },
    {
        "name": "get_listing_details",
        "description": "Get the full detail record for a single listing by its id (e.g. 'MAD-101').",
        "input_schema": {
            "type": "object",
            "properties": {"listing_id": {"type": "string"}},
            "required": ["listing_id"],
        },
    },
    {
        "name": "calculate_mortgage",
        "description": "Estimate a monthly mortgage payment for a purchase price.",
        "input_schema": {
            "type": "object",
            "properties": {
                "price": {"type": "number", "description": "Property sale price in EUR."},
                "down_payment_pct": {"type": "number", "description": "Down payment as a percent, e.g. 20."},
                "term_years": {"type": "integer", "description": "Loan term in years, e.g. 30."},
                "annual_interest_rate": {"type": "number", "description": "Annual nominal rate percent, e.g. 3.2."},
            },
            "required": ["price"],
        },
    },
    {
        "name": "neighborhood_insights",
        "description": "Get insights about a neighborhood: average price per m², transport, "
                       "schools, safety score and vibe.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "neighborhood": {"type": "string"},
            },
            "required": ["city", "neighborhood"],
        },
    },
]


# ------------------------------------------------------------ implementations ---
def _summary(l: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": l["id"], "city": l["city"], "neighborhood": l["neighborhood"],
        "operation": l["operation"], "price": l["price"], "bedrooms": l["bedrooms"],
        "bathrooms": l["bathrooms"], "size_m2": l["size_m2"],
        "property_type": l["property_type"], "features": l["features"],
    }


def search_listings(city: str, operation: str, max_price: Optional[float] = None,
                    min_price: Optional[float] = None, min_bedrooms: Optional[int] = None,
                    property_type: Optional[str] = None,
                    features: Optional[List[str]] = None, **_) -> Dict[str, Any]:
    city_l = (city or "").strip().lower()
    # Unrecognised feature words are dropped from the filter and reported back;
    # see normalize_features for why filtering on them is worse than ignoring them.
    wanted_features, unsupported_features = normalize_features(features)
    results = []
    for l in LISTINGS:
        # Match the location term against BOTH city and neighborhood, so a query
        # like "Gràcia" (a district) still finds listings in Barcelona/Gràcia.
        if city_l and city_l not in f'{l["city"]} {l["neighborhood"]}'.lower():
            continue
        if operation and l["operation"] != operation:
            continue
        if max_price is not None and l["price"] > max_price:
            continue
        if min_price is not None and l["price"] < min_price:
            continue
        if min_bedrooms is not None and l["bedrooms"] < min_bedrooms:
            continue
        if property_type and property_type.lower() not in l["property_type"].lower():
            continue
        if wanted_features:
            have = {f.lower() for f in l["features"]}
            if not set(wanted_features).issubset(have):
                continue
        results.append(_summary(l))
    results.sort(key=lambda r: r["price"])
    out: Dict[str, Any] = {"count": len(results), "listings": results}
    if wanted_features:
        out["filtered_on_features"] = wanted_features
    if unsupported_features:
        # Surfaced so the agent can say WHICH request it could not honour, rather
        # than presenting an empty result as "nothing on the market".
        out["unsupported_features"] = unsupported_features
        out["note"] = ("These features are not tracked in the catalog and were NOT used as "
                       "filters: " + ", ".join(unsupported_features) + ". Say so explicitly "
                       "instead of implying no properties exist. Supported features: "
                       + ", ".join(FEATURE_VOCABULARY) + ".")
    return out


def get_listing_details(listing_id: str, **_) -> Dict[str, Any]:
    l = get_listing((listing_id or "").strip().upper())
    if not l:
        return {"error": f"No listing with id '{listing_id}'."}
    return dict(l)


def calculate_mortgage(price: float, down_payment_pct: float = 20.0,
                       term_years: int = 30, annual_interest_rate: float = 3.2, **_) -> Dict[str, Any]:
    down_payment = price * (down_payment_pct / 100.0)
    principal = price - down_payment
    monthly_rate = (annual_interest_rate / 100.0) / 12.0
    n = term_years * 12
    if monthly_rate == 0:
        monthly_payment = principal / n
    else:
        monthly_payment = principal * (monthly_rate * (1 + monthly_rate) ** n) / (((1 + monthly_rate) ** n) - 1)
    total_paid = monthly_payment * n
    return {
        "price": round(price, 2),
        "down_payment": round(down_payment, 2),
        "loan_principal": round(principal, 2),
        "term_years": term_years,
        "annual_interest_rate": annual_interest_rate,
        "monthly_payment": round(monthly_payment, 2),
        "total_interest": round(total_paid - principal, 2),
    }


def neighborhood_insights(city: str, neighborhood: str, **_) -> Dict[str, Any]:
    key = neighborhood_key(city.strip().title(), neighborhood.strip().title())
    data = NEIGHBORHOODS.get(key)
    if not data:
        # Best-effort: match on neighborhood name alone.
        for k, v in NEIGHBORHOODS.items():
            if neighborhood.strip().lower() in k.lower():
                data = v
                key = k
                break
    if not data:
        return {"error": f"No insights for '{city} / {neighborhood}'."}
    return {"location": key, **data}


_DISPATCH = {
    "search_listings": search_listings,
    "get_listing_details": get_listing_details,
    "calculate_mortgage": calculate_mortgage,
    "neighborhood_insights": neighborhood_insights,
}


def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"Unknown tool '{name}'."}
    try:
        return fn(**(args or {}))
    except Exception as e:  # tools should never crash the agent loop
        return {"error": f"Tool '{name}' failed: {e}"}
