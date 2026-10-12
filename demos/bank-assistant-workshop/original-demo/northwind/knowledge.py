"""
Northwind Bank help-center knowledge base + retriever (OBS-03).

Synthetic, fictional policy content. Each document carries a stable id, a title,
a source URL and an effective date, so a trace shows not just *what* context
the model saw but *where it came from* — the evidence an auditor asks for.

Retrieval is a small in-process TF-IDF index: no embedding API, no network call,
deterministic. In the bank this is the vector store of choice; the tracing
contract (a `retriever` observation whose output lists the documents with ids,
sources and scores) is the same.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

BASE = "https://help.northwind.example"


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    category: str
    effective: str
    text: str

    @property
    def url(self) -> str:
        return f"{BASE}/{self.category}/{self.id.lower()}"


DOCS: list[Doc] = [
    Doc("KB-101", "Lost or stolen debit and credit cards", "cards", "2026-03-01",
        "If your card is lost or stolen, block it immediately in the app (Cards > Block card) or ask the "
        "assistant to block it. Blocking is instant and free. A replacement card is issued automatically "
        "and arrives in 5 to 7 business days; express delivery in 2 business days costs USD 15. "
        "Transactions made after you report the card are not your responsibility. Never share your PIN, "
        "CVV or one-time passcodes with anyone, including bank staff."),
    Doc("KB-102", "Disputing a card transaction", "cards", "2026-03-01",
        "You can dispute a card transaction you do not recognise or that was charged incorrectly within "
        "60 days of the statement date. Open a dispute in the app or through the assistant, citing the "
        "transaction. Northwind issues a provisional credit within 10 business days while the case is "
        "investigated. Investigations conclude within 45 days (90 days for international transactions). "
        "If the dispute is resolved in the merchant's favour, the provisional credit is reversed."),
    Doc("KB-103", "Credit card interest, fees and minimum payment", "cards", "2026-01-15",
        "The Northwind Classic card has a purchase APR of 24.9% and a cash-advance APR of 29.9%. "
        "The Northwind Platinum card has a purchase APR of 21.9%. There is no interest on purchases if "
        "the full statement balance is paid by the due date (grace period of 25 days). Late payment fee: "
        "USD 29. Cash advance fee: 4% of the amount, minimum USD 10. Foreign transaction fee: 3% on "
        "Classic, 0% on Platinum. Minimum payment is the greater of USD 25 or 2% of the balance."),
    Doc("KB-104", "Using your card abroad", "cards", "2026-02-10",
        "Northwind cards work in over 200 countries. You do not need to file a travel notice; our fraud "
        "systems use your mobile app location if you enable it. ATM withdrawals abroad cost USD 5 plus "
        "the foreign transaction fee of your card. Daily ATM withdrawal limit abroad is USD 1,000."),
    Doc("KB-201", "Domestic transfers and daily limits", "transfers", "2026-04-01",
        "Transfers between Northwind accounts are instant and free, 24/7. Transfers to other banks via "
        "instant payments are free up to USD 5,000 per day for Everyday accounts and USD 20,000 per day "
        "for Premier accounts. You can request a temporary limit increase in the app; increases above "
        "USD 20,000 require a call-back verification."),
    Doc("KB-202", "International wire transfers", "transfers", "2026-04-01",
        "International wires are sent via SWIFT. Fee: USD 35 per outgoing wire (USD 0 for Premier "
        "accounts); incoming wires cost USD 10. Cut-off time is 3:00 PM local time on business days; "
        "wires submitted after the cut-off are processed the next business day. Typical delivery is 1 to "
        "3 business days. The exchange rate applied includes a margin of up to 2% over the mid-market rate."),
    Doc("KB-301", "Everyday and Premier accounts", "accounts", "2026-01-01",
        "Everyday account: no monthly fee if you deposit at least USD 500 per month, otherwise USD 8. "
        "Premier account: USD 25 monthly fee, waived with combined balances of USD 25,000 or more; "
        "includes free international wires, 0% foreign transaction fee on debit, and a dedicated "
        "relationship manager. Both include free Northwind ATM withdrawals and the mobile app."),
    Doc("KB-302", "Overdraft protection", "accounts", "2026-01-01",
        "Overdraft protection is opt-in. When enabled, transactions that exceed your balance are covered "
        "up to USD 500 for a fee of USD 15 per day overdrawn (maximum 3 fees per month). You can turn "
        "overdraft protection off at any time in the app. If it is off, transactions that would overdraw "
        "the account are declined at no charge."),
    Doc("KB-303", "Savings accounts and term deposits", "accounts", "2026-05-01",
        "High-Yield Savings pays a variable 3.10% APY with no minimum balance. Term deposits pay fixed "
        "rates: 6 months 3.60% APY, 12 months 3.85% APY, 24 months 3.70% APY, minimum USD 1,000. Early "
        "withdrawal from a term deposit forfeits 90 days of interest. Deposits are protected by the "
        "national deposit insurance scheme up to its legal limit."),
    Doc("KB-304", "Opening an account: identity verification", "accounts", "2026-02-01",
        "To open an account you need a valid government-issued photo ID, proof of address dated within "
        "the last 3 months, and your tax identification number. Identity is verified in the app with a "
        "document scan and a selfie; most applications are approved within 10 minutes. Minors can open a "
        "Junior account with a parent or guardian."),
    Doc("KB-401", "Personal loans", "loans", "2026-06-01",
        "Personal loans from USD 2,000 to USD 50,000, terms of 12 to 72 months. APR ranges from 8.9% to "
        "21.9% depending on credit profile and term; the rate offered is fixed for the life of the loan. "
        "There is no prepayment penalty. Origination fee: 1% of the loan amount. A soft credit check is "
        "used for a pre-qualification quote and does not affect your credit score; a hard check is done "
        "only when you accept an offer."),
    Doc("KB-402", "Mortgages", "loans", "2026-06-01",
        "Northwind offers fixed-rate mortgages of 15, 20 and 30 years and a 5/1 adjustable-rate mortgage. "
        "Maximum loan-to-value is 80% (90% with mortgage insurance). Required documents: two years of tax "
        "returns, two recent pay slips, bank statements for the last 3 months, and the purchase "
        "agreement. Rates change daily; a rate lock is valid for 60 days."),
    Doc("KB-501", "Fraud, phishing and scams", "security", "2026-03-15",
        "Northwind will never ask you for your password, full card number, PIN, CVV or one-time passcode "
        "by phone, SMS, email or chat — not even the assistant. If someone asks, it is a scam: hang up "
        "and block your card. Report suspicious messages to the fraud team from the app. If you shared "
        "credentials, change your password immediately and block your cards."),
    Doc("KB-502", "How the virtual assistant protects your data", "security", "2026-05-20",
        "The assistant can only access the accounts of the customer who is signed in. It cannot see or "
        "act on other customers' information, and it cannot change your contact details, password or "
        "beneficiaries. Conversations are logged for quality and security purposes with sensitive data "
        "such as card numbers and identification numbers redacted."),
    Doc("KB-601", "Complaints and escalation", "support", "2026-01-10",
        "You can file a complaint through the assistant, the app, by phone or in a branch. We acknowledge "
        "complaints within 2 business days and resolve them within 15 business days. If you are not "
        "satisfied with the outcome you can escalate to the bank's independent Customer Ombudsman, and "
        "after that to the national financial consumer protection authority."),
    Doc("KB-602", "Contacting Northwind and service hours", "support", "2026-01-10",
        "The assistant and the app are available 24/7. Phone banking: Monday to Saturday 7:00 AM to "
        "10:00 PM. Lost or stolen cards and fraud: 24/7 hotline. Branches: Monday to Friday 9:00 AM to "
        "4:00 PM. The assistant can schedule a call-back from a human agent."),
    Doc("KB-701", "Investment products and advice", "investments", "2026-02-01",
        "Northwind Investments offers mutual funds, a managed portfolio service and brokerage. The virtual "
        "assistant can describe products and fees but cannot provide personalised investment advice or "
        "recommend specific securities, cryptocurrencies or market timing. For advice, book a session "
        "with a licensed Northwind financial advisor. Investment products are not deposits, are not "
        "insured by the deposit insurance scheme, and may lose value."),
    Doc("KB-702", "Digital banking: password reset and new devices", "digital", "2026-04-15",
        "To reset your password, use 'Forgot password' on the sign-in screen; you will verify your identity "
        "with a one-time passcode sent to your registered phone. Adding a new device requires approval "
        "from an already-enrolled device or a call-back. For your security, the assistant cannot reset "
        "passwords or register devices."),
]

# Spanish titles + keywords per article, indexed alongside the English text so a
# Spanish question retrieves the same policy (bilingual bank). The article body
# stays single-source; the assistant answers in the customer's language.
ES_KEYWORDS = {
    "KB-101": "Tarjeta perdida o robada: bloquear tarjeta, reposición, tarjeta nueva, envío exprés, PIN, CVV",
    "KB-102": "Disputar un cargo, transacción no reconocida, contracargo, crédito provisional, reclamo de tarjeta, días",
    "KB-103": "Tarjeta de crédito: intereses, tasa, comisión por pago tardío, mora, avance de efectivo, pago mínimo, comisión por transacción en el exterior",
    "KB-104": "Usar la tarjeta en el exterior, viaje, aviso de viaje, cajero en el extranjero, retiro, límite diario",
    "KB-201": "Transferencias nacionales, límite diario, transferencia a otros bancos, pagos inmediatos, aumento de límite",
    "KB-202": "Transferencia internacional, giro, SWIFT, comisión, costo, hora de corte, tipo de cambio, recibir giro",
    "KB-301": "Cuenta Everyday, cuenta Premier, cuota de manejo, cuota mensual, exoneración, beneficios",
    "KB-302": "Protección de sobregiro, sobregiro, cargo por sobregiro, desactivar, costo, cobro",
    "KB-303": "Cuenta de ahorros, depósito a término, CDT, tasa de interés, retiro anticipado, penalidad, seguro de depósitos",
    "KB-304": "Abrir una cuenta, verificación de identidad, documento de identidad, comprobante de domicilio, requisitos",
    "KB-401": "Préstamo personal, crédito de libre inversión, tasa, plazo, prepago, pago anticipado, penalidad, comisión de apertura",
    "KB-402": "Crédito hipotecario, hipoteca, vivienda, porcentaje de financiación, documentos, tasa fija",
    "KB-501": "Fraude, phishing, estafa, suplantación, código de un solo uso, contraseña, nunca pedimos",
    "KB-502": "Protección de datos del asistente virtual, privacidad, datos de otros clientes, información sensible",
    "KB-601": "Quejas y reclamos, PQR, escalamiento, defensor del consumidor financiero, tiempos de respuesta",
    "KB-602": "Contacto, horarios de atención, línea telefónica, sucursales, devolución de llamada, asesor humano",
    "KB-701": "Inversiones, asesoría de inversión, fondos, criptomonedas, bitcoin, acciones, asesor financiero",
    "KB-702": "Banca digital, restablecer contraseña, olvidé mi contraseña, nuevo dispositivo, celular nuevo, aplicación",
}

_BY_ID = {d.id: d for d in DOCS}
_TOKEN = re.compile(r"[a-z0-9áéíóúñü]+")
_STOP = set("a an the and or of to in on for is are be by with your you we our it can i my me "
            "do does what how when which this that from at as if not no "
            "el la los las un una de del que y en por para con su mi es se lo le al cómo cuánto "
            "cuál qué puedo quiero tengo".split())


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


_doc_tf = [Counter(_tokens(f"{d.title} {d.title} {d.text} {ES_KEYWORDS.get(d.id, '')} "
                           f"{ES_KEYWORDS.get(d.id, '')}")) for d in DOCS]
_df = Counter(t for tf in _doc_tf for t in tf)
_idf = {t: math.log((1 + len(DOCS)) / (1 + n)) + 1 for t, n in _df.items()}


def _vec(tf: Counter) -> dict:
    v = {t: (1 + math.log(c)) * _idf.get(t, 0.0) for t, c in tf.items()}
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


_doc_vecs = [_vec(tf) for tf in _doc_tf]


def search(query: str, k: int = 3, min_score: float = 0.05) -> list[dict]:
    """Top-k documents for `query`, each with id, title, url, score and text."""
    q = _vec(Counter(_tokens(query)))
    scored = sorted(
        ((sum(w * dv.get(t, 0.0) for t, w in q.items()), d) for dv, d in zip(_doc_vecs, DOCS)),
        key=lambda x: x[0], reverse=True)
    return [{"id": d.id, "title": d.title, "source": d.url, "effective_date": d.effective,
             "score": round(s, 3), "text": d.text}
            for s, d in scored[:k] if s >= min_score]


def get(doc_id: str) -> Doc | None:
    return _BY_ID.get(doc_id)
