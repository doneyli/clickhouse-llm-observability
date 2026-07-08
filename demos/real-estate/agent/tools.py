"""
Tools for the Real Estate Property Concierge agent.

Each tool is a plain Python function over the synthetic catalog. The agent calls
them through Anthropic tool-use; the concierge loop wraps every execution in a
Langfuse span so tool calls show up in the trace tree.
"""

from typing import Any, Dict, List, Optional

from .catalog import LISTINGS, get_listing, NEIGHBORHOODS, neighborhood_key


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
                "features": {"type": "array", "items": {"type": "string"},
                             "description": "Desired features, e.g. ['metro','terrace','parking','pool','sea_view']."},
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
        if features:
            wanted = {f.strip().lower() for f in features}
            have = {f.lower() for f in l["features"]}
            if not wanted.issubset(have):
                continue
        results.append(_summary(l))
    results.sort(key=lambda r: r["price"])
    return {"count": len(results), "listings": results}


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
