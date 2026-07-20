"""
Evaluation dataset for the Real Estate Property Concierge (18 items).

A realistic mix of buy/rent, English/Spanish, cities across Europe and difficulty
levels — including one intentionally-hard item (impossible budget) so the
experiment shows scores that genuinely vary. The first 10 items are the original
Spanish-city benchmark; items 11–18 add one query per non-Spanish market so every
city in the catalog is exercised by the offline experiment.

Each item:
  input           -> {"question": "..."}   (what the agent receives)
  expected_output -> {"criteria": "...",    (human-readable pass criteria)
                      "expected": {...}}     (ground-truth constraints the code
                                              evaluators check against)
"""

DATASET_NAME = "property-concierge-eval"
DATASET_DESCRIPTION = (
    "Real-estate property-search assistant evaluation. Natural-language home-search "
    "queries (buy/rent, EN/ES, cities across Europe) with ground-truth constraints "
    "for grounding, budget, location and language checks plus LLM-as-a-Judge quality."
)

ITEMS = [
    {
        "input": {"question": "Find me a 2-bedroom flat to buy in Madrid under €400,000 near a metro, "
                              "and tell me the estimated monthly mortgage."},
        "expected_output": {
            "criteria": "Recommends a real Madrid 2-bed under €400k with metro access; includes a "
                        "mortgage estimate; cites listing id(s).",
            "expected": {"location": "Madrid", "operation": "buy", "max_price": 400000,
                         "min_bedrooms": 2, "query_language": "en", "wants_mortgage": True},
        },
        "metadata": {"city": "Madrid", "operation": "buy", "language": "en", "difficulty": "easy"},
    },
    {
        "input": {"question": "Busca un piso de 2 habitaciones para comprar en Valencia por menos de "
                              "300.000 euros. ¿Cómo es el barrio?"},
        "expected_output": {
            "criteria": "Responde en español; recomienda un piso real de 2 habitaciones en Valencia por "
                        "debajo de 300.000 €; aporta contexto del barrio.",
            "expected": {"location": "Valencia", "operation": "buy", "max_price": 300000,
                         "min_bedrooms": 2, "query_language": "es"},
        },
        "metadata": {"city": "Valencia", "operation": "buy", "language": "es", "difficulty": "easy"},
    },
    {
        "input": {"question": "I need a furnished 3-bedroom apartment to rent in Barcelona for up to "
                              "€2,000 a month, ideally with parking."},
        "expected_output": {
            "criteria": "Recommends a real Barcelona 3-bed rental ≤ €2,000/mo that is furnished and, if "
                        "available, has parking; cites listing id(s).",
            "expected": {"location": "Barcelona", "operation": "rent", "max_price": 2000,
                         "min_bedrooms": 3, "query_language": "en"},
        },
        "metadata": {"city": "Barcelona", "operation": "rent", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "Quiero alquilar un piso de 2 habitaciones en Madrid por menos de 1.400 € "
                              "al mes y que esté cerca del metro."},
        "expected_output": {
            "criteria": "Responde en español; recomienda un alquiler real de 2 habitaciones en Madrid ≤ "
                        "1.400 €/mes con metro cerca.",
            "expected": {"location": "Madrid", "operation": "rent", "max_price": 1400,
                         "min_bedrooms": 2, "query_language": "es"},
        },
        "metadata": {"city": "Madrid", "operation": "rent", "language": "es", "difficulty": "medium"},
    },
    {
        "input": {"question": "We're looking to buy a house near the beach in Málaga with a pool and sea "
                              "views. Budget is around €500,000."},
        "expected_output": {
            "criteria": "Recommends a real Málaga house with pool and sea view within ~€500k; cites "
                        "listing id(s).",
            "expected": {"location": "Málaga", "operation": "buy", "max_price": 500000,
                         "query_language": "en"},
        },
        "metadata": {"city": "Málaga", "operation": "buy", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "I want to buy a family-friendly 3-bedroom home in Seville under €350,000 "
                              "with parking and good schools nearby."},
        "expected_output": {
            "criteria": "Recommends a real Seville 3-bed under €350k with parking; mentions schools/"
                        "family suitability via neighborhood insight.",
            "expected": {"location": "Seville", "operation": "buy", "max_price": 350000,
                         "min_bedrooms": 3, "query_language": "en"},
        },
        "metadata": {"city": "Seville", "operation": "buy", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "Estoy buscando comprar un piso de 2 habitaciones en el barrio de Gràcia o "
                              "el Eixample de Barcelona, con un presupuesto de 500.000 euros."},
        "expected_output": {
            "criteria": "Responde en español; recomienda un piso real de 2 habitaciones en Gràcia o "
                        "Eixample ≤ 500.000 €.",
            "expected": {"location": "Barcelona", "operation": "buy", "max_price": 500000,
                         "min_bedrooms": 2, "query_language": "es"},
        },
        "metadata": {"city": "Barcelona", "operation": "buy", "language": "es", "difficulty": "medium"},
    },
    {
        "input": {"question": "What furnished apartments can I rent in the Ruzafa area of Valencia? "
                              "Two bedrooms would be ideal."},
        "expected_output": {
            "criteria": "Recommends a real furnished Valencia/Ruzafa rental, ~2 bed; cites listing id(s) "
                        "and neighborhood context.",
            "expected": {"location": "Valencia", "operation": "rent", "query_language": "en"},
        },
        "metadata": {"city": "Valencia", "operation": "rent", "language": "en", "difficulty": "easy"},
    },
    {
        "input": {"question": "I'd like to buy a 3-bedroom apartment in central Bilbao close to the "
                              "Guggenheim, and I want to understand the monthly mortgage on a €410,000 place."},
        "expected_output": {
            "criteria": "Recommends a real central Bilbao 3-bed near the Guggenheim; includes a mortgage "
                        "estimate for ~€410k.",
            "expected": {"location": "Bilbao", "operation": "buy", "min_bedrooms": 3,
                         "query_language": "en", "wants_mortgage": True},
        },
        "metadata": {"city": "Bilbao", "operation": "buy", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "Can you find me a one-bedroom apartment to buy in central Barcelona for "
                              "under €150,000?"},
        "expected_output": {
            "criteria": "HARD: no Barcelona listing is this cheap. A good answer honestly reports that "
                        "nothing matches, does NOT invent a listing, and suggests realistic alternatives "
                        "(higher budget, other cities, or renting).",
            "expected": {"location": "Barcelona", "operation": "buy", "max_price": 150000,
                         "min_bedrooms": 1, "query_language": "en"},
        },
        "metadata": {"city": "Barcelona", "operation": "buy", "language": "en", "difficulty": "hard"},
    },

    # ---- Wider-Europe coverage (one item per non-Spanish market) --------------
    {
        "input": {"question": "I'd like to buy a renovated 2-bedroom apartment in Lisbon for up to "
                              "€550,000. What's the neighborhood like?"},
        "expected_output": {
            "criteria": "Recommends a real Lisbon 2-bed to buy ≤ €550,000; adds neighborhood "
                        "context; cites listing id(s).",
            "expected": {"location": "Lisbon", "operation": "buy", "max_price": 550000,
                         "min_bedrooms": 2, "query_language": "en"},
        },
        "metadata": {"city": "Lisbon", "operation": "buy", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "I'm after a furnished 2-bedroom apartment to rent near Canal "
                              "Saint-Martin in Paris, up to €2,500 a month."},
        "expected_output": {
            "criteria": "Recommends a real furnished Paris 2-bed rental ≤ €2,500/mo near Canal "
                        "Saint-Martin; cites listing id(s).",
            "expected": {"location": "Paris", "operation": "rent", "max_price": 2500,
                         "min_bedrooms": 2, "query_language": "en"},
        },
        "metadata": {"city": "Paris", "operation": "rent", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "I want to buy a 2-bedroom apartment in central Berlin for under "
                              "€600,000, and I'd like to know the estimated monthly mortgage."},
        "expected_output": {
            "criteria": "Recommends a real central Berlin 2-bed to buy under €600k; includes a "
                        "mortgage estimate; cites listing id(s).",
            "expected": {"location": "Berlin", "operation": "buy", "max_price": 600000,
                         "min_bedrooms": 2, "query_language": "en", "wants_mortgage": True},
        },
        "metadata": {"city": "Berlin", "operation": "buy", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "What furnished two-bedroom apartments can I rent in Amsterdam for "
                              "under €2,200 a month, ideally with parking?"},
        "expected_output": {
            "criteria": "Recommends a real furnished Amsterdam 2-bed rental ≤ €2,200/mo, with "
                        "parking if available; cites listing id(s).",
            "expected": {"location": "Amsterdam", "operation": "rent", "max_price": 2200,
                         "min_bedrooms": 2, "query_language": "en"},
        },
        "metadata": {"city": "Amsterdam", "operation": "rent", "language": "en", "difficulty": "easy"},
    },
    {
        "input": {"question": "Find me a 3-bedroom apartment to buy in Rome close to a metro, "
                              "budget up to €800,000."},
        "expected_output": {
            "criteria": "Recommends a real Rome 3-bed to buy ≤ €800k with metro access; cites "
                        "listing id(s).",
            "expected": {"location": "Rome", "operation": "buy", "max_price": 800000,
                         "min_bedrooms": 3, "query_language": "en"},
        },
        "metadata": {"city": "Rome", "operation": "buy", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "We're looking to buy a 3-bedroom apartment in Vienna with parking "
                              "and a terrace, up to €750,000."},
        "expected_output": {
            "criteria": "Recommends a real Vienna 3-bed to buy ≤ €750k with parking and a "
                        "terrace; cites listing id(s).",
            "expected": {"location": "Vienna", "operation": "buy", "max_price": 750000,
                         "min_bedrooms": 3, "query_language": "en"},
        },
        "metadata": {"city": "Vienna", "operation": "buy", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "I need a furnished 2-bedroom apartment to rent in Dublin's docklands "
                              "tech quarter, budget around €2,700 a month."},
        "expected_output": {
            "criteria": "Recommends a real furnished Dublin 2-bed rental around €2,700/mo near "
                        "the Grand Canal Dock / docklands; cites listing id(s).",
            "expected": {"location": "Dublin", "operation": "rent", "max_price": 2700,
                         "min_bedrooms": 2, "query_language": "en"},
        },
        "metadata": {"city": "Dublin", "operation": "rent", "language": "en", "difficulty": "medium"},
    },
    {
        "input": {"question": "We'd like to buy a house near the sea in Athens with a pool, budget "
                              "about €650,000."},
        "expected_output": {
            "criteria": "Recommends a real Athens house to buy with a pool and sea view within "
                        "~€650k; cites listing id(s).",
            "expected": {"location": "Athens", "operation": "buy", "max_price": 650000,
                         "query_language": "en"},
        },
        "metadata": {"city": "Athens", "operation": "buy", "language": "en", "difficulty": "medium"},
    },
]
