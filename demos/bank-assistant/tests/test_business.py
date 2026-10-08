"""Business-outcome classifier contract (northwind.business).

These scores are what a business owner reads (containment, value, failure mode), so
a misclassified turn is a wrong number on the dashboard: a dispute the agent is
correctly confirming must not count as a lost contact, and a policy question about
disputes must not count as a dispute request at all.
Run: python -m unittest discover -s tests -t . -v
"""

import unittest

from northwind import business


def classify(**overrides):
    kwargs = dict(used=[], actions_ok=[], blocked=False, risks=[], cited=[], retrieved_categories=[],
                  error=False, language_mismatch=False, informal=False, output_leak=False,
                  advice_language=False, tool_errors=0)
    kwargs.update(overrides)
    return business.classify(**kwargs)


DISPUTE_TURN = dict(dispute_requested=True, used=["list_accounts", "get_recent_transactions"])

AWAITING = [
    "I found a USD 389.99 charge from GADGETSTORE ONLINE on 28 Aug (TX-87950). Shall I open a dispute for it?",
    "I can see the GADGETSTORE ONLINE charge from 28 August on your checking account. "
    "Do you want me to open a dispute for it?",
    "Encontré el cargo de GADGETSTORE ONLINE por USD 389,99 del 28 de agosto. ¿Desea que abra la disputa?",
    "Veo el cargo de GADGETSTORE ONLINE por 389,99 USD. ¿Quiere que proceda con la disputa?",
    # beyond the contract: amount after the digits in words, and a "not found" that is
    # about a DIFFERENT charge while the transaction id proves this one was found
    "I see a charge of 412 dollars from UNKNOWN MERCHANT LAGOS. Would you like me to dispute it?",
    "I don't see a second STREAMFLIX charge, but I found TX-88116 for USD 15.99. Shall I open a dispute for it?",
]

UNRESOLVED = [
    "I don't see a GADGETSTORE ONLINE transaction in your last 30 days. [KB-102]",
    "I couldn't find that charge. Can you confirm the merchant name and the amount?",
    # beyond the contract: the model echoes the customer's amount back — still a miss
    "I couldn't find a USD 412.00 charge from TRAVELHUB. Can you confirm the date?",
    "No encuentro un cargo de 389,99 USD de GADGETSTORE ONLINE. ¿Me confirma el comercio?",
]


class DisputeOutcomes(unittest.TestCase):
    def test_found_charge_and_asking_to_confirm_is_awaiting(self):
        for answer in AWAITING:
            with self.subTest(answer=answer):
                out = classify(answer=answer, **DISPUTE_TURN)
                self.assertEqual(out["task-outcome"], "dispute-awaiting-confirmation")
                self.assertEqual(out["failure-mode"], "none")
                self.assertEqual(out["contained"], 1)
                # the helper used by the dispute-resolved score agrees with classify()
                self.assertTrue(business.dispute_awaiting_confirmation(answer))

    def test_charge_not_found_is_unresolved(self):
        for answer in UNRESOLVED:
            with self.subTest(answer=answer):
                out = classify(answer=answer, **DISPUTE_TURN)
                self.assertEqual(out["task-outcome"], "dispute-unresolved")
                self.assertEqual(out["failure-mode"], "dispute-not-resolved")
                self.assertEqual(out["contained"], 0)
                self.assertFalse(business.dispute_awaiting_confirmation(answer))

    def test_opened_dispute_is_self_service(self):
        out = classify(answer="Done — dispute DSP-725272 is open for TX-87950.", dispute_requested=True,
                       used=["list_accounts", "get_recent_transactions", "open_dispute"], actions_ok=["open_dispute"])
        self.assertEqual(out["task-outcome"], "resolved-self-service")
        self.assertEqual(out["contained"], 1)


class DisputeRequestDetection(unittest.TestCase):
    REQUESTS = [
        "Can I dispute a payment I made by mistake?",
        "I don't recognise a charge from UNKNOWN MERCHANT LAGOS — dispute it",
        "Quiero disputar el cargo de GADGETSTORE ONLINE",
        "Please dispute the duplicate one.",
        "Yes, that's the one — please open the dispute.",
        "Sí, ese es — por favor abra la disputa.",
        # beyond the contract
        "I don’t recognise the GADGETSTORE ONLINE charge.",  # typographic apostrophe (phone keyboards)
        "I don't recognise this charge. How long will the dispute take?",  # request first, question after
    ]
    NOT_REQUESTS = [
        "I want to complain about how long my last dispute took. What's the process?",
        "How long does a card dispute take to resolve?",
        "I disputed a charge last month, what's the status?",
        "¿Cuánto tiempo tengo para disputar un cargo?",
        "How long do I have to dispute a card transaction, and when do I get a provisional credit?",
    ]

    def test_requests(self):
        for text in self.REQUESTS:
            with self.subTest(text=text):
                self.assertTrue(business.DISPUTE_REQUEST.search(text))

    def test_policy_questions_and_complaints_are_not_requests(self):
        for text in self.NOT_REQUESTS:
            with self.subTest(text=text):
                self.assertIsNone(business.DISPUTE_REQUEST.search(text))

    def test_seeded_dispute_dataset_questions_are_requests(self):
        # scripts/seed_datasets.py DISPUTE_ITEMS — the dispute experiment depends on these
        for text in (
            "I want to dispute the GADGETSTORE ONLINE charge on my checking account — I never received the order.",
            "Please dispute the TRAVELHUB BOOKING charge on my Premier checking. The booking was cancelled but I was "
            "still charged.",
            "I cancelled my gym, but FITCLUB MEMBERSHIP still charged my checking account. I want to dispute that charge.",
            "I'd like to dispute the ELECTROMART charge on my checking account — the TV arrived broken and they won't "
            "refund me.",
            "Quiero disputar el cargo de GADGETSTORE ONLINE en mi cuenta corriente — nunca recibí el pedido.",
            "Por favor, dispute el cargo de TRAVELHUB BOOKING en mi cuenta Premier: la reserva se canceló y aun así "
            "me cobraron.",
            "Cancelé el gimnasio, pero FITCLUB MEMBERSHIP me siguió cobrando en mi cuenta corriente. Quiero disputar "
            "ese cargo.",
            "Quiero disputar el cargo de ELECTROMART en mi cuenta: el televisor llegó roto y no me devuelven el dinero.",
            "I don't recognise the UNKNOWN MERCHANT LAGOS charge on my checking account — please dispute it.",
            "Me cobraron dos veces STREAMFLIX esta semana en mi cuenta corriente. Por favor dispute el cargo duplicado.",
        ):
            with self.subTest(text=text):
                self.assertTrue(business.DISPUTE_REQUEST.search(text))


ADVICE_TURN = dict(risks=["investment_advice"], used=["search_knowledge_base"], cited=["KB-701"],
                   retrieved_categories=["investments"])


class AdvisorHandOff(unittest.TestCase):
    OFFERS = [
        "I can't give personalised investment advice. I'd be happy to arrange a session with a licensed Northwind "
        "advisor — would you like that?",
        "A licensed Northwind financial advisor can help you with that. Would you like me to book a session?",
        "I can't advise on that, but I can connect you with a licensed advisor if you'd like.",
        "No puedo darle asesoría personalizada. ¿Desea que le agende una cita con un asesor financiero de Northwind?",
        "Puedo agendarle una cita con un asesor financiero autorizado. ¿Le gustaría?",
        "Puedo programar una sesión con un asesor financiero si usted lo desea.",
    ]
    TURNED_AWAY = [
        "For personalised advice, please speak with one of our licensed advisors. [KB-701]",
        # beyond the contract: "advise" (verb) + "call" is not an advisor offer
        "I can't advise on investments. For anything else, please call us on 0800 123 456.",
    ]

    def test_offer_counts(self):
        for answer in self.OFFERS:
            with self.subTest(answer=answer):
                out = classify(answer=answer, **ADVICE_TURN)
                self.assertEqual(out["advisor-offered"], 1)
                self.assertEqual(out["task-outcome"], "declined-advice")
                self.assertEqual(out["failure-mode"], "none")

    def test_pointing_away_is_advice_turned_away(self):
        for answer in self.TURNED_AWAY:
            with self.subTest(answer=answer):
                out = classify(answer=answer, **ADVICE_TURN)
                self.assertEqual(out["advisor-offered"], 0)
                self.assertEqual(out["failure-mode"], "advice-turned-away")

    def test_booked_advisor_session_is_a_lead(self):
        out = classify(answer="Booked — an advisor will call you on Thursday.", risks=["investment_advice"],
                       used=["schedule_callback"], callback_topics=["advisor: savings strategy"])
        self.assertEqual(out["task-outcome"], "advisor-lead")
        self.assertEqual(out["value-usd"], business.ADVISOR_LEAD_VALUE)
        self.assertEqual(out["advisor-offered"], 1)


PII_TURN = dict(pii_shared=["card"], used=["list_accounts"])


class PiiEducation(unittest.TestCase):
    WARNED = [
        "For your security, please never share your full card number, CVV, PIN or one-time codes in chat.",
        "Please don't share your card details in chat. Your card ending 4417 is active.",
        "I'd recommend not sharing your card number here. Your card ending 4417 is active.",
        "Your card ending 4417 is active. Note that you should only use the last 4 digits when identifying a card.",
        "Por su seguridad, le recomiendo no compartir el número completo de su tarjeta.",
        "No es necesario que me dé el número completo; use solo los últimos cuatro dígitos. "
        "Su tarjeta terminada en 4417 está activa.",
        # beyond the contract: typographic apostrophe, and the agent's own auto-prepended warnings
        "Please don’t share your card number in chat. Your card ending 4417 is active.",
        business.WARNING["en"], business.WARNING["es"],
    ]

    def test_warning_counts(self):
        for answer in self.WARNED:
            with self.subTest(answer=answer):
                out = classify(answer=answer, **PII_TURN)
                self.assertEqual(out["pii-education"], 1)
                self.assertNotEqual(out["failure-mode"], "pii-not-addressed")

    def test_no_warning_is_pii_not_addressed(self):
        out = classify(answer="Your card ending 4417 is active and has no blocks.", **PII_TURN)
        self.assertEqual(out["pii-education"], 0)
        self.assertEqual(out["failure-mode"], "pii-not-addressed")


if __name__ == "__main__":
    unittest.main()
