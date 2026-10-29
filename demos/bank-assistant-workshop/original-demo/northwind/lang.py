"""Language helpers for bilingual (English / Spanish) evaluation.

Deterministic on purpose: a stop-word vote is cheap, explainable and good enough
for EN-vs-ES on customer-service text. Used by the online `language-match`
score, the Spanish `formal-register` check and the offline evaluators.
"""

from __future__ import annotations

import re

_ES = set("el la los las un una unos unas de del que y en por para con su sus mi mis es son está están "
          "cómo como cuánto cuanto cuál cual qué donde dónde puedo puede quiero necesito tengo tiene "
          "cuenta tarjeta transferencia comisión hora corte usted gracias hola por favor también pero "
          "muy más sí no le lo se al".split())
_EN = set("the a an of and to in for with your my is are was how what which can could would should i "
          "you it this that on at be have has do does please thanks account card transfer fee".split())
_WORD = re.compile(r"[a-záéíóúñü]+", re.I)

# Informal second person ("tú" forms) — a retail bank's Spanish copy is usually
# formal ("usted"). Heuristic: possessive "tu", pronouns, and common -as/-es verb
# forms used with tú in customer service.
_INFORMAL = re.compile(
    r"(?i)\b(tú|tu|tus|contigo|te ayudo|puedes|tienes|quieres|necesitas|debes|haz|dime|mira|"
    r"recuerda|ingresa|revisa|escríbeme|avísame)\b")
_FORMAL = re.compile(r"(?i)\b(usted|ustedes|su|sus|puede|tiene|desea|necesita|le ayudo|lo siento)\b")


def detect(text: str) -> str:
    words = [w.lower() for w in _WORD.findall(text or "")]
    es = sum(w in _ES for w in words) + 2 * len(re.findall(r"[ñ¿¡áéíóú]", text or ""))
    en = sum(w in _EN for w in words)
    return "es" if es > en else "en"


def informal_markers(text: str) -> list[str]:
    """Informal (tú) markers found in a Spanish answer — empty means formal register."""
    return sorted({m.group(0).lower() for m in _INFORMAL.finditer(text or "")})
