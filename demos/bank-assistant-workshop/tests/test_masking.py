"""PII-masking contract (northwind.masking.scrub).

Two failure modes, both tested: a secret that reaches Langfuse unmasked, and banking
data nobody asked to protect (amounts, balances, transaction ids, timestamps) being
corrupted into [REDACTED_…] — that breaks the traces the LLM judges grade.
JSON-escaped variants matter because LangChain serialises message lists with
ensure_ascii, so the exported attribute carries "c\\u00e9dula", not "cédula".
Run: python -m unittest discover -s tests -t . -v
"""

import json
import unittest

from northwind.masking import scrub


def escaped(text: str) -> str:
    return json.dumps(text, ensure_ascii=True)


REDACT = [
    ("My card number is 4111 1111 1111 1111 and the CVV is 123 — is it blocked?", {"card", "otp"}),
    ("card 4111-1111-1111-1111", {"card"}),
    ("card 4111111111111111", {"card"}),
    ("Mi número de tarjeta es 4111 1111 1111 1111, ¿está bloqueada?", {"card"}),
    ("My national ID number is 1020304050 and my phone is +44 20 7946 0958, update my file please.",
     {"national_id", "phone"}),
    ("Wire 5,000 USD to account number 0012345678 at another bank — what will it cost?", {"account"}),
    ("mi cédula es 1020304050", {"national_id"}),
    (escaped("mi cédula es 1020304050"), {"national_id"}),
    ("número de cuenta 0012345678", {"account"}),
    (escaped("número de cuenta 0012345678"), {"account"}),
    ("código de verificación 998877", {"otp"}),
    (escaped("el código de verificación es 998877"), {"otp"}),
    (json.dumps({"role": "user", "content": "Mi cédula de ciudadanía es 1.020.304.050 y mi clave dinámica 998877"},
                ensure_ascii=True), {"national_id", "otp"}),
    ("My email is ben.okafor@example.com, please send me the fee schedule.", {"email"}),
    ("refund to IBAN GB29 NWBK 6016 1331 9268 19, or call me on +1 (415) 555-0134 / maria.g@example.com. "
     "Account 12345678.", {"account", "email", "iban", "phone"}),
    ("SSN 123-45-6789", {"national_id"}),
    ("DNI 12345678A", {"national_id"}),
    ("PIN 1234", {"otp"}),
    ("mi clave es 4321", {"otp"}),
    ("I cannot update your national ID (1020304050) here.", {"national_id"}),
    ("Amex 3782 822463 10005", {"card"}),
]

# The digits that must be gone after scrubbing, per case (spot checks on the value itself).
GONE = {
    "My card number is 4111 1111 1111 1111 and the CVV is 123 — is it blocked?": ["4111", " 123 "],
    "I cannot update your national ID (1020304050) here.": ["1020304050"],
    "Amex 3782 822463 10005": ["822463"],
    "DNI 12345678A": ["12345678"],
}

KEEP = [
    "Transaction TX-88101 on ACC-1001-01 for USD 412.00 on 2026-10-04; balance 2840.55",
    "transfers up to USD 20,000 per day; TRAVELHUB 1,120.00",
    "the card ending 4417",
    "case DSP-725272, callback CB-48210",
    "timestamp 1759900000000 and order 20261008123456",
    "a non-Luhn 16 digits 1234 5678 9012 3456",
    "cuenta corriente con saldo 2840.55",
    "I'd like to send 1 000 000 000 dollars",
    "account balance is 12500 00 USD",
    # beyond the contract: the same balance phrasings in Spanish and as statement figures
    "el saldo de la cuenta es 12500 00 USD",
    "account statement shows 12500000 for the period",
    "cuenta con saldo 12500 00",
]


class Redacts(unittest.TestCase):
    def test_secrets_are_redacted_with_their_category(self):
        for text, categories in REDACT:
            with self.subTest(text=text):
                masked, found = scrub(text)
                self.assertTrue(categories <= found, f"categories {sorted(found)} missing {sorted(categories - found)}")
                self.assertIn("[REDACTED_", masked)
                for fragment in GONE.get(text, []):
                    self.assertNotIn(fragment, masked)

    def test_account_number_next_to_a_balance_word_is_still_redacted(self):
        # The balance exemption must not open a leak: here the number IS the account.
        for text in ("What's the balance of account 12345678?", "el saldo de la cuenta 0012345678",
                     "Check my balance and send it to account 0012345678"):
            with self.subTest(text=text):
                masked, found = scrub(text)
                self.assertIn("account", found)
                self.assertIn("[REDACTED_ACCOUNT]", masked)

    def test_label_is_kept_so_the_text_still_reads(self):
        masked, _ = scrub("número de cuenta 0012345678")
        self.assertEqual(masked, "número de cuenta [REDACTED_ACCOUNT]")


class Preserves(unittest.TestCase):
    def test_banking_data_survives_unchanged(self):
        for text in KEEP:
            with self.subTest(text=text):
                masked, found = scrub(text)
                self.assertEqual(masked, text)
                self.assertEqual(found, set())

    def test_known_limit_number_echoed_without_its_keyword(self):
        # Documented in the module docstring: anchored patterns need the keyword within
        # 20 characters. This pins the limit so a change to it is a deliberate one.
        masked, found = scrub("Your ID 1020304050 cannot be changed")
        self.assertEqual(found, set())
        self.assertIn("1020304050", masked)


if __name__ == "__main__":
    unittest.main()
