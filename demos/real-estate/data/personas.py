"""
Persona/scenario dataset for the SIMULATED MULTI-TURN evaluation (7 items).

Where `data/dataset.py` holds one-shot questions with ground-truth constraints,
this dataset holds *people*. Each item is a written buyer the simulated user
(agent/simulated_user.py) role-plays for a whole conversation, so the unit under
test is the trajectory: memory across turns, reference resolution, and knowing
when to say "nothing matches".

Every persona is difficult in EXACTLY ONE specific, named way, recorded as
`awkward_trait`. That is the design constraint that makes the dataset worth
running: an agreeable, well-organised persona states its constraints clearly,
repeats them on request, and passes every trajectory judge — it measures nothing.
A persona is only useful if it gives a judge something concrete to find.

The traits deliberately span the six multi-turn failure modes that single-turn
evals structurally cannot see:
    constraint revised downward mid-conversation   (pcp-01)
    references earlier listings only by pronoun    (pcp-02)
    answers a different question than the one asked(pcp-03)
    switches language mid-conversation             (pcp-04)
    states a constraint once and never repeats it  (pcp-05)
    impossible requirement, must be told so        (pcp-06)
    switches target city without flagging it       (pcp-07)

Scenarios are grounded in the real catalog (agent/catalog.py) — cities,
neighborhoods and price bands where the trait actually discriminates between
listings — but they are PROSE, never listing ids. Handing the simulator an id
would leak the answer and let it (rather than the agent) do the retrieval.

Each item:
  input    -> {"persona": "...", "scenario": "..."}   (what the simulator plays)
  metadata -> {"awkward_trait": ..., "risk": ...}     (the trait, and the concrete
                                                       failure it should provoke)

`expected_output` is intentionally absent: an improvised conversation has no
reference transcript to compare against, so the trajectory evaluators judge the
conversation on its own terms (see evaluators/conversation_evaluators.py).
"""

DATASET_NAME = "property-concierge-personas"
DATASET_DESCRIPTION = (
    "Simulated multi-turn conversations for the Real Estate Property Concierge. Each "
    "item is a buyer persona plus scenario that an LLM role-plays for a full "
    "conversation; every persona is awkward in exactly one named way (revises a "
    "constraint, pronoun-only references, evasive answers, mid-conversation language "
    "switch, a constraint stated once, an impossible requirement, a silent city "
    "change). Scored at the TRAJECTORY level, not per turn."
)

ITEMS = [
    {
        "input": {
            "persona": "Ana, 34, a cautious first-time buyer who has just had a long talk "
                       "with her bank. She is polite but she moves her own goalposts: once "
                       "she has revised a number, the old number is dead and she will not "
                       "mention it again or explain the change. She does not re-state the "
                       "new figure either — she assumes you wrote it down.",
            "scenario": "You want to buy a 2-bedroom flat in Madrid and you open by saying "
                        "your budget is around €450,000. On your SECOND message, drop it to "
                        "€400,000 as your absolute maximum — say it once, plainly, without "
                        "explaining why. From then on judge everything you are shown "
                        "against €400,000 and get visibly irritated if you are offered "
                        "anything above it. You also care about being near a metro.",
        },
        "metadata": {
            "awkward_trait": "revises a constraint downward mid-conversation and never repeats the new figure",
            "risk": "the agent keeps recommending the €465k Chamberí flat that fitted the "
                    "ORIGINAL €450k budget, because the tightened cap was stated once",
            "city": "Madrid", "operation": "buy", "language": "en",
            "stresses": "stated-constraint-respected",
        },
    },
    {
        "input": {
            "persona": "Tom, 41, relocating for work and chatting from his phone between "
                       "meetings. He never uses names, ids or prices to refer to things — "
                       "only 'that one', 'the second one', 'the cheaper one', 'the first "
                       "place you mentioned'. If asked to be more specific he repeats the "
                       "same pronoun, slightly annoyed, because to him it is obvious.",
            "scenario": "You want to buy a 2-bedroom flat in Barcelona for up to €550,000. "
                        "After the assistant shows you options, ask follow-up questions "
                        "about SPECIFIC ones using only pronouns and ordinals — e.g. ask "
                        "whether 'the second one' is near a metro, then ask what the "
                        "monthly mortgage would be on 'that one', then compare 'those two'. "
                        "Never say a listing id or a price back to the assistant.",
        },
        "metadata": {
            "awkward_trait": "refers to earlier listings only by pronoun or ordinal, never by id",
            "risk": "the agent answers about the wrong listing, or silently re-searches and "
                    "answers about a listing that was never shown",
            "city": "Barcelona", "operation": "buy", "language": "en",
            "stresses": "reference-resolved",
        },
    },
    {
        "input": {
            "persona": "Marta, 29, warm and talkative and completely unable to answer a "
                       "direct question. Asked how many bedrooms she needs, she talks about "
                       "her commute. Asked about her budget, she talks about her last "
                       "flatmate. She never refuses to answer — she just answers a "
                       "different question, cheerfully, and expects you to keep up.",
            "scenario": "You want to rent a flat in Valencia, ideally in or near Ruzafa. "
                        "You know you want somewhere furnished and under about €1,200 a "
                        "month, but you will only ever reveal that if the assistant infers "
                        "it or asks in a very indirect way. Whenever the assistant asks you "
                        "a direct question, answer a DIFFERENT question instead — one about "
                        "your life, your commute, or the neighborhood.",
        },
        "metadata": {
            "awkward_trait": "answers a different question than the one asked, every time",
            "risk": "the agent re-asks the same question turn after turn instead of "
                    "proceeding on reasonable defaults, or invents the answer it never got",
            "city": "Valencia", "operation": "rent", "language": "en",
            "stresses": "no-redundant-questions",
        },
    },
    {
        "input": {
            "persona": "Pablo, 38, bilingual. He starts in English because the site was in "
                       "English, then reverts to his native Spanish once he is comfortable "
                       "— and never goes back. He does not announce the switch, does not "
                       "apologise for it, and does not repeat himself in English.",
            "scenario": "You want to buy a family home in Seville with parking, for under "
                        "€350,000, and you care about schools nearby. Write your first TWO "
                        "messages in English. From your third message onward, write "
                        "ONLY in natural Spanish — no English, no translation, no comment "
                        "about the change. If the assistant keeps replying in English, say "
                        "so in Spanish.",
        },
        "metadata": {
            "awkward_trait": "switches language mid-conversation without announcing it",
            "risk": "the agent stays in English for the rest of the conversation because it "
                    "locked onto the language of turn 1",
            "city": "Seville", "operation": "buy", "language": "en->es",
            "stresses": "stated-constraint-respected",
        },
    },
    {
        "input": {
            "persona": "Helena, 52, buying a home she will share with her elderly mother. "
                       "She mentions the thing that actually rules listings in or out "
                       "exactly ONCE, early, in passing — and then never again, because to "
                       "her it is settled. The rest of the time she asks about "
                       "neighborhoods, light and noise.",
            "scenario": "You want to buy a 2-bedroom apartment in Lisbon for up to "
                        "€650,000. In your FIRST message, mention once and in passing that "
                        "your mother lives with you and cannot manage stairs, so the "
                        "building must have a lift. Never mention the lift again. For the "
                        "rest of the conversation ask only about the neighborhood, the "
                        "light, and how noisy the street is.",
        },
        "metadata": {
            "awkward_trait": "states a hard constraint once, in passing, and never repeats it",
            "risk": "the agent later recommends the walk-up in Alfama, which has no lift, "
                    "because the constraint fell out of its working context",
            "city": "Lisbon", "operation": "buy", "language": "en",
            "stresses": "stated-constraint-respected",
        },
    },
    {
        "input": {
            "persona": "Greg, 45, arriving with a fixed idea and a budget that cannot buy "
                       "it. He is not aggressive, just immovable: he repeats the "
                       "requirement, asks whether you have looked properly, and only "
                       "considers an alternative once someone tells him plainly and "
                       "specifically that what he wants does not exist at that price.",
            "scenario": "You want to buy a 3-bedroom HOUSE with a private garden in central "
                        "Paris for under €400,000. Push for it. If the assistant offers "
                        "apartments or a bigger budget, restate what you asked for at least "
                        "once. Accept an alternative — or end the conversation — only after "
                        "the assistant clearly tells you that nothing in central Paris "
                        "matches those requirements at that price.",
        },
        "metadata": {
            "awkward_trait": "holds an impossible requirement and must be told so explicitly",
            "risk": "the agent hallucinates a listing that fits, or stays vaguely "
                    "encouraging for six turns without ever saying 'this does not exist'",
            "city": "Paris", "operation": "buy", "language": "en",
            "stresses": "reference-resolved",
        },
    },
    {
        "input": {
            "persona": "Nadia, 36, in the middle of a job move that is not yet settled. "
                       "When the plan changes she mentions the new fact once, in the middle "
                       "of another sentence, and carries straight on — she does not say "
                       "'forget everything I said' or ask to start over.",
            "scenario": "You want to rent a 2-bedroom furnished flat for about €1,700 a "
                        "month, and you begin by asking about ROME. On your THIRD message, "
                        "mention in passing that the job actually moved to VIENNA, so it "
                        "needs to be there now — then keep asking your questions as if "
                        "nothing dramatic happened. Never mention Rome again.",
        },
        "metadata": {
            "awkward_trait": "silently switches target city mid-conversation without flagging it",
            "risk": "the agent keeps recommending Rome listings, or blends both cities into "
                    "one answer as though the move never happened",
            "city": "Rome->Vienna", "operation": "rent", "language": "en",
            "stresses": "stated-constraint-respected",
        },
    },
]
