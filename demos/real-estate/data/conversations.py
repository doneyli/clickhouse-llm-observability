"""
N+1 multi-turn evaluation dataset for the Real Estate Property Concierge (10 items).

Multi-turn agents fail *between* turns, not inside one: the budget the user
tightened three turns ago is forgotten, "that one" resolves to the wrong flat,
the city changed but the old city's listings keep coming back. Scoring a whole
conversation end-to-end is expensive and hard to attribute. The N+1 method
(https://langfuse.com/guides/cookbook/example_evaluating_multi_turn_conversations)
avoids both problems: freeze a real conversation just before the turn that broke,
replay turns 1..N as *input*, and put only turn N+1 under test. A cross-turn
problem becomes an ordinary single-item experiment — same runner, same
evaluators, one trace per item.

Every conversation is a plausible Madrid / Barcelona / Valencia buyer thread and
every listing id and price below is a REAL row from `agent/catalog.py`, so the
deterministic scorers can be checked against the catalog rather than trusted.

Each item:
  input           -> {"history":  [{"role","content"}, ...],   turns 1..N, replayed
                      "question": "..."}                        turn N+1, under test
  expected_output -> {"criteria": "...",    (human-readable pass criteria — this is
                                             what a reviewer reads in the UI)
                      "expected": {...}}     (the ACCUMULATED ground-truth
                                             constraints, i.e. everything still in
                                             force at turn N+1, which is what the
                                             code evaluators check against)
  metadata        -> {"failure_mode": ..., "turns_of_context": N, ...}

`expected` reuses `data/dataset.py`'s key vocabulary (location, operation,
max_price, min_bedrooms, query_language, wants_mortgage) so the single-turn code
evaluators work here unchanged, plus one N+1-specific key:

  referenced_listing  the listing id that turn N+1's pronoun/ordinal/description
                      ("that one", "the second option", "the Chamberí flat") must
                      resolve to. Consumed by `code_reference_resolved`; absent on
                      items that carry no reference.

`turns_of_context` counts completed user+assistant exchanges in `history`
(len(history) // 2) — the same arithmetic the runner uses for `turn_index`.
"""

DATASET_NAME = "property-concierge-conversations"
DATASET_DESCRIPTION = (
    "N+1 multi-turn evaluation for the property-search assistant. Each item is a "
    "frozen conversation (turns 1..N, replayed as history) plus the single next turn "
    "under test, targeting a named cross-turn failure mode: forgotten constraints, "
    "unresolved references, stale location, constraint accumulation, mid-thread "
    "language switch, and repeated questions."
)

ITEMS = [
    # ---- 1. constraint-forgotten -------------------------------------------
    # The classic: a budget tightened mid-thread must survive every later turn.
    # MAD-102 was a perfectly good recommendation in turn 1 and is the only Madrid
    # 2-bed with parking — which is exactly why re-offering it here is tempting.
    {
        "input": {
            "history": [
                {"role": "user", "content": "I'm looking to buy a 2-bedroom flat in Madrid. "
                                            "Budget is around €500,000."},
                {"role": "assistant", "content": "Two strong options in Madrid: [MAD-101] in "
                                                 "Malasaña (€389,000, 2 bed, 72 m², renovated, four "
                                                 "minutes from Tribunal metro) and [MAD-102] in "
                                                 "Chamberí (€465,000, 2 bed, 85 m², with private "
                                                 "parking and a lift)."},
                {"role": "user", "content": "Talking to the bank changed things — we have to keep it "
                                            "under €400,000."},
                {"role": "assistant", "content": "Understood. Of the two, [MAD-101] in Malasaña at "
                                                 "€389,000 sits comfortably under €400,000."},
            ],
            "question": "We also need a lift and air conditioning. What else can you show me at that price?",
        },
        "expected_output": {
            "criteria": "The €400,000 cap set in turn 3 is still in force. [MAD-101] has both a lift "
                        "and air conditioning and remains the only Madrid 2-bed to buy under "
                        "€400,000, so a good answer confirms it and says plainly that there is "
                        "nothing else at that price. [MAD-102] (€465,000) must NOT come back as an "
                        "option just because it also has a lift.",
            "expected": {"location": "Madrid", "operation": "buy", "max_price": 400000,
                         "min_bedrooms": 2, "query_language": "en"},
        },
        "metadata": {"failure_mode": "constraint-forgotten", "turns_of_context": 2,
                     "city": "Madrid", "operation": "buy", "language": "en", "difficulty": "hard",
                     "trap": "MAD-102 (€465,000) matches the new feature ask but breaks the "
                             "tightened budget"},
    },

    # ---- 2. pronoun-unresolved ---------------------------------------------
    # "the Chamberí flat" is a DESCRIPTION, not an id: resolving it requires
    # reading turn 2's neighborhoods. Getting it wrong quotes a mortgage on the
    # wrong property — a mistake a user acts on.
    {
        "input": {
            "history": [
                {"role": "user", "content": "I want to buy a 2-bedroom flat in Madrid for up to "
                                            "€500,000, ideally with somewhere to park the car."},
                {"role": "assistant", "content": "Two that fit: [MAD-101] in Malasaña (€389,000, "
                                                 "2 bed, 72 m², renovated, metro at the door) and "
                                                 "[MAD-102] in Chamberí (€465,000, 2 bed, 85 m², with "
                                                 "private parking and a lift)."},
            ],
            "question": "What would the monthly mortgage look like on the Chamberí flat?",
        },
        "expected_output": {
            "criteria": "'The Chamberí flat' must resolve to [MAD-102] at €465,000 — not [MAD-101], "
                        "which is in Malasaña. The answer must cite MAD-102 and give a monthly "
                        "mortgage estimate based on €465,000.",
            "expected": {"location": "Madrid", "operation": "buy", "max_price": 500000,
                         "min_bedrooms": 2, "query_language": "en", "wants_mortgage": True,
                         "referenced_listing": "MAD-102"},
        },
        "metadata": {"failure_mode": "pronoun-unresolved", "turns_of_context": 1,
                     "city": "Madrid", "operation": "buy", "language": "en", "difficulty": "medium",
                     "trap": "MAD-101 is the cheaper, more-quoted option and is in the wrong "
                             "neighborhood"},
    },

    # ---- 3. pronoun-unresolved (ordinal) -----------------------------------
    # An ordinal reference points at the ORDER the assistant chose two turns ago,
    # which is only recoverable from the history — nothing in turn N+1 names it.
    {
        "input": {
            "history": [
                {"role": "user", "content": "We're buying in Barcelona — a 2-bedroom flat, up to "
                                            "€550,000."},
                {"role": "assistant", "content": "Two options: 1) [BCN-202] in Gràcia — €398,000, "
                                                 "2 bed, 68 m², renovated. 2) [BCN-201] in the "
                                                 "Eixample — €520,000, 2 bed, 90 m², with a terrace."},
                {"role": "user", "content": "Are both of them near a metro?"},
                {"role": "assistant", "content": "Yes — both [BCN-202] and [BCN-201] have metro "
                                                 "access; BCN-201 is a short walk from Passeig de "
                                                 "Gràcia."},
            ],
            "question": "Tell me more about the second option — what's the neighbourhood like?",
        },
        "expected_output": {
            "criteria": "'The second option' is the second listing the assistant numbered in turn 2: "
                        "[BCN-201] in the Eixample (€520,000). The answer must cite BCN-201 and "
                        "describe the Eixample — describing Gràcia (BCN-202) means the ordinal was "
                        "resolved off the wrong list.",
            "expected": {"location": "Barcelona", "operation": "buy", "max_price": 550000,
                         "min_bedrooms": 2, "query_language": "en",
                         "referenced_listing": "BCN-201"},
        },
        "metadata": {"failure_mode": "pronoun-unresolved", "turns_of_context": 2,
                     "city": "Barcelona", "operation": "buy", "language": "en",
                     "difficulty": "hard",
                     "trap": "turn 4 re-lists both ids in the opposite emphasis, so a lazy 'last id "
                             "mentioned' heuristic picks BCN-201 for the wrong reason"},
    },

    # ---- 4. stale-location -------------------------------------------------
    # The city changed in the final turn. MAD-106 is still inside the budget and
    # was the subject of the two turns immediately before, so it has every kind of
    # recency pull — and is now simply the wrong country-side of the map.
    {
        "input": {
            "history": [
                {"role": "user", "content": "I'm looking to rent a 2-bedroom flat in Madrid for up "
                                            "to €1,400 a month, close to a metro."},
                {"role": "assistant", "content": "[MAD-106] in Tetuán fits — €1,150/mo, 2 bed, "
                                                 "65 m², lift, metro nearby. [MAD-105] in Chamberí "
                                                 "is furnished and larger at €1,650/mo, but that is "
                                                 "above your cap."},
                {"role": "user", "content": "How is Tetuán as a neighbourhood?"},
                {"role": "assistant", "content": "Tetuán is an up-and-coming, very well-connected "
                                                 "district — good value for money, plenty of metro, "
                                                 "and increasingly popular with young professionals."},
            ],
            "question": "Change of plan — the job moved to Valencia. Same budget, still 2 bedrooms. "
                        "What can I rent there?",
        },
        "expected_output": {
            "criteria": "The search city switched to Valencia in the last turn. New recommendations "
                        "must be Valencia rentals of 2+ bedrooms at or under €1,400/mo — [VLC-304] "
                        "in Ruzafa (€1,100/mo, 2 bed, furnished) is the fit. The Madrid ids from "
                        "turns 1–2 must not be cited as options any more.",
            "expected": {"location": "Valencia", "operation": "rent", "max_price": 1400,
                         "min_bedrooms": 2, "query_language": "en"},
        },
        "metadata": {"failure_mode": "stale-location", "turns_of_context": 2,
                     "city": "Valencia", "operation": "rent", "language": "en",
                     "difficulty": "medium",
                     "trap": "MAD-106 is in budget, matches the bedroom count and was the topic of "
                             "the previous two turns — but it is in the abandoned city"},
    },

    # ---- 5. constraint-accumulation ----------------------------------------
    # Three constraints, one per turn, none repeated. Only the conjunction of all
    # three isolates a single listing; each pair still leaves a wrong answer
    # available (BCN-205 satisfies "Barcelona / furnished / under €2,000").
    {
        "input": {
            "history": [
                {"role": "user", "content": "We need to rent a flat in Barcelona. Our ceiling is "
                                            "€2,000 a month."},
                {"role": "assistant", "content": "Understood — Barcelona rentals up to €2,000/mo. "
                                                 "What size are you thinking, and are there areas "
                                                 "you'd like to focus on?"},
                {"role": "user", "content": "It has to come furnished — we're relocating with "
                                            "nothing."},
                {"role": "assistant", "content": "Noted: furnished, up to €2,000/mo, in Barcelona. "
                                                 "Any preference on the number of bedrooms?"},
            ],
            "question": "Yes — three bedrooms minimum, we have two kids. What fits all of that?",
        },
        "expected_output": {
            "criteria": "Three constraints were stated one per turn — Barcelona rental ≤ €2,000/mo "
                        "(turn 1), furnished (turn 3), 3+ bedrooms (turn 5) — and all three must "
                        "hold simultaneously. [BCN-204] in Poblenou (€1,900/mo, 3 bed, furnished, "
                        "with parking) is the only listing that satisfies all of them. [BCN-205] "
                        "(€1,250/mo, furnished, 1 bed) satisfies two and must not be offered as a "
                        "match.",
            "expected": {"location": "Barcelona", "operation": "rent", "max_price": 2000,
                         "min_bedrooms": 3, "query_language": "en"},
        },
        "metadata": {"failure_mode": "constraint-accumulation", "turns_of_context": 2,
                     "city": "Barcelona", "operation": "rent", "language": "en",
                     "difficulty": "hard",
                     "trap": "BCN-205 is furnished, in Barcelona and well under budget — it fails "
                             "only the constraint stated in the turn under test"},
    },

    # ---- 6. language-switch-midconversation (EN -> ES) ----------------------
    # Four turns of English history vs. one Spanish question: the reply must follow
    # the LATEST turn. The concierge's `lang` is derived per turn, so an agent that
    # leans on conversational momentum answers in the wrong language.
    {
        "input": {
            "history": [
                {"role": "user", "content": "I'd like to buy a flat in Valencia for under €300,000."},
                {"role": "assistant", "content": "Two to consider: [VLC-302] in El Carmen — €198,000, "
                                                 "1 bed, 60 m², renovated. And [VLC-301] in Ruzafa — "
                                                 "€245,000, 2 bed, 78 m², renovated, with a terrace "
                                                 "and metro nearby."},
                {"role": "user", "content": "How do those two compare on space?"},
                {"role": "assistant", "content": "[VLC-301] is the larger of the two at 78 m² with "
                                                 "2 bedrooms; [VLC-302] is 60 m² with 1 bedroom."},
            ],
            "question": "Perdona, sigo mejor en español. ¿Cuál de los dos tiene terraza?",
        },
        "expected_output": {
            "criteria": "El usuario cambia al español en el último turno: la respuesta debe estar en "
                        "español (el idioma del turno N+1, no el del historial). Debe identificar "
                        "[VLC-301] en Ruzafa como el único de los dos con terraza, sin volver a "
                        "empezar la búsqueda.",
            "expected": {"location": "Valencia", "operation": "buy", "max_price": 300000,
                         "query_language": "es", "referenced_listing": "VLC-301"},
        },
        "metadata": {"failure_mode": "language-switch-midconversation", "turns_of_context": 2,
                     "city": "Valencia", "operation": "buy", "language": "es",
                     "difficulty": "hard",
                     "trap": "every prior turn is English, so answering in English 'matches the "
                             "conversation' while failing the user"},
    },

    # ---- 7. language-switch-midconversation (ES -> EN) ----------------------
    # The mirror image, and it doubles as a budget check: MAD-105 is the obvious
    # thing to reach for when asked "anything else", and it is over the cap.
    {
        "input": {
            "history": [
                {"role": "user", "content": "Quiero alquilar un piso de 2 habitaciones en Madrid por "
                                            "menos de 1.400 € al mes, cerca del metro."},
                {"role": "assistant", "content": "[MAD-106] en Tetuán encaja: 1.150 €/mes, "
                                                 "2 habitaciones, 65 m², ascensor y metro cerca."},
                {"role": "user", "content": "¿Y cómo es la zona?"},
                {"role": "assistant", "content": "Tetuán es un barrio en auge, muy bien conectado y "
                                                 "con una buena relación calidad-precio."},
            ],
            "question": "Sorry, switching to English so my partner can follow — is there anything "
                        "else in Madrid at that budget?",
        },
        "expected_output": {
            "criteria": "The user switched to English in the last turn, so the reply must be in "
                        "English even though the four turns of history are Spanish. The €1,400/mo "
                        "cap and the 2-bedroom requirement from turn 1 both still apply: [MAD-105] "
                        "in Chamberí (€1,650/mo) is over budget and must not be presented as an "
                        "option, and [MAD-106] (€1,150/mo) remains the only fit.",
            "expected": {"location": "Madrid", "operation": "rent", "max_price": 1400,
                         "min_bedrooms": 2, "query_language": "en"},
        },
        "metadata": {"failure_mode": "language-switch-midconversation", "turns_of_context": 2,
                     "city": "Madrid", "operation": "rent", "language": "en",
                     "difficulty": "hard",
                     "trap": "MAD-105 is the only other Madrid 2-bed rental and is €250/mo over the "
                             "cap stated four turns earlier"},
    },

    # ---- 8. repeat-question ------------------------------------------------
    # The answer is already in the history. Re-running the whole search is not
    # wrong output, it is wrong BEHAVIOUR — latency, cost, and a reply that reads
    # like the assistant met the user for the first time.
    # NOTE for reviewers: `used-search-tool` is INFORMATIONAL on this item. A
    # correct answer here may legitimately skip search_listings, so a 0 on that
    # score is not a defect; `stated-constraint-respected`, `reference-resolved`
    # and `context-retention` are the ones to read.
    {
        "input": {
            "history": [
                {"role": "user", "content": "Busco un piso de 2 habitaciones para comprar en "
                                            "Valencia por menos de 300.000 euros."},
                {"role": "assistant", "content": "Te recomiendo [VLC-301] en Ruzafa: 245.000 €, "
                                                 "2 habitaciones, 78 m², reformado, con terraza y "
                                                 "metro cerca."},
                {"role": "user", "content": "¿Cuántos metros tiene?"},
                {"role": "assistant", "content": "78 m², con terraza."},
            ],
            "question": "Perdona, ¿me recuerdas el precio del piso de Ruzafa?",
        },
        "expected_output": {
            "criteria": "La respuesta ya está en el historial: [VLC-301] cuesta 245.000 €. El agente "
                        "debe responder desde el contexto — citar VLC-301 y su precio, en español — "
                        "sin volver a preguntar qué busca el usuario ni rehacer la búsqueda desde "
                        "cero. No debe introducir otros pisos.",
            "expected": {"location": "Valencia", "operation": "buy", "max_price": 300000,
                         "min_bedrooms": 2, "query_language": "es",
                         "referenced_listing": "VLC-301"},
        },
        "metadata": {"failure_mode": "repeat-question", "turns_of_context": 2,
                     "city": "Valencia", "operation": "buy", "language": "es",
                     "difficulty": "medium", "search_expected": False,
                     "trap": "re-searching produces a correct-looking answer that ignores two turns "
                             "of established context"},
    },

    # ---- 9. impossible-after-narrowing -------------------------------------
    # Successive narrowing empties the result set. The only correct answer is to
    # say so — and the pressure to please is what makes agents invent a listing or
    # quietly drop the newest constraint.
    {
        "input": {
            "history": [
                {"role": "user", "content": "I need to rent a flat in Valencia, up to €1,200 a "
                                            "month."},
                {"role": "assistant", "content": "[VLC-304] in Ruzafa is the match — €1,100/mo, "
                                                 "2 bed, 72 m², furnished, with metro and air "
                                                 "conditioning."},
                {"role": "user", "content": "It has to have at least 3 bedrooms."},
                {"role": "assistant", "content": "That narrows things considerably — [VLC-304] has "
                                                 "2 bedrooms, so it no longer qualifies."},
            ],
            "question": "And parking, ideally. What's available?",
        },
        "expected_output": {
            "criteria": "HARD / DECLINE: no Valencia rental in the catalog has 3+ bedrooms at any "
                        "price, let alone under €1,200/mo with parking. The correct behaviour is to "
                        "state plainly that nothing matches, NOT invent a listing, NOT silently drop "
                        "the 3-bedroom or budget constraint, and suggest a realistic trade-off "
                        "(raise the budget, consider buying, or another city).",
            "expected": {"location": "Valencia", "operation": "rent", "max_price": 1200,
                         "min_bedrooms": 3, "query_language": "en"},
        },
        "metadata": {"failure_mode": "impossible-after-narrowing", "turns_of_context": 2,
                     "city": "Valencia", "operation": "rent", "language": "en",
                     "difficulty": "hard",
                     "trap": "VLC-304 was the answer two turns ago and is still the closest thing — "
                             "re-offering it means the 3-bedroom constraint was dropped"},
    },

    # ---- 10. stale-location (ES) -------------------------------------------
    # Same failure mode as item 4 but in Spanish and on the buy side, where the
    # budget carries over to the new city too — so a correct answer has to keep one
    # constraint while replacing another.
    {
        "input": {
            "history": [
                {"role": "user", "content": "Estoy buscando comprar un piso de 2 habitaciones en "
                                            "Barcelona, hasta 550.000 euros."},
                {"role": "assistant", "content": "Te propongo [BCN-202] en Gràcia (398.000 €, "
                                                 "2 habitaciones, 68 m², reformado) y [BCN-201] en "
                                                 "el Eixample (520.000 €, 2 habitaciones, 90 m², con "
                                                 "terraza)."},
                {"role": "user", "content": "¿Y cómo es Gràcia?"},
                {"role": "assistant", "content": "Gràcia es un barrio bohemio y muy peatonal, lleno "
                                                 "de plazas y cafés, con buena conexión de metro."},
            ],
            "question": "Cambio de planes: la oficina se muda a Valencia. Busco lo mismo allí, con "
                        "el mismo presupuesto.",
        },
        "expected_output": {
            "criteria": "La ciudad pasa a ser Valencia, pero el presupuesto (550.000 €) y las "
                        "2 habitaciones se mantienen. Las nuevas recomendaciones deben ser de "
                        "Valencia — [VLC-301] en Ruzafa (245.000 €, 2 hab.) y/o [VLC-303] en el "
                        "Cabañal (320.000 €, 3 hab.) — y la respuesta debe estar en español. Los "
                        "pisos de Barcelona no deben volver a ofrecerse como opciones.",
            "expected": {"location": "Valencia", "operation": "buy", "max_price": 550000,
                         "min_bedrooms": 2, "query_language": "es"},
        },
        "metadata": {"failure_mode": "stale-location", "turns_of_context": 2,
                     "city": "Valencia", "operation": "buy", "language": "es",
                     "difficulty": "medium",
                     "trap": "BCN-202 and BCN-201 are both inside the carried-over budget, so only "
                             "the city constraint rules them out"},
    },
]
