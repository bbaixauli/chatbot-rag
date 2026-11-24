"""Reglas deterministas de la auditoría (#50): no necesitan el modelo y siempre se aplican.

- Información sensible en la **respuesta**: DNI/NIE (con letra de control), IBAN (dígitos de
  control), tarjetas (Luhn), correos, teléfonos y secretos (claves, tokens, contraseñas).
  No se avisa de un dato que ya estaba en la pregunta (lo aportó quien pregunta).
- Intenciones maliciosas en la **pregunta**: intentos de saltarse las instrucciones, de
  obtener el prompt de sistema o de inyectar código.

Son la red de seguridad del modelo revisor, que también puede ser manipulado por el texto
que revisa.
"""

import re
import unicodedata
from dataclasses import dataclass

MALICIOUS_USER = "malicious_user"
INAPPROPRIATE_RESPONSE = "inappropriate_response"
SENSITIVE_INFORMATION = "sensitive_information"
CATEGORIES = (MALICIOUS_USER, INAPPROPRIATE_RESPONSE, SENSITIVE_INFORMATION)
SEVERITIES = ("low", "medium", "high")


@dataclass(frozen=True, slots=True)
class Finding:
    category: str
    severity: str
    reason: str
    excerpt: str | None = None
    source: str = "rule"  # rule o model


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def mask(value: str) -> str:
    """Deja ver lo justo para reconocer el dato: los 2 primeros y los 2 últimos caracteres."""
    compact = value.strip()
    if len(compact) <= 6:
        return "*" * len(compact)
    return f"{compact[:2]}{'*' * (len(compact) - 4)}{compact[-2:]}"


# --- Identificadores con dígito de control ---

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_DNI = re.compile(r"\b([XYZ]?)(\d{7,8})[\s-]?([A-Z])\b", re.IGNORECASE)


def _valid_dni(prefix: str, digits: str, letter: str) -> bool:
    """DNI: 8 cifras y letra. NIE: X/Y/Z (0/1/2), 7 cifras y letra."""
    if len(digits) != (7 if prefix else 8):
        return False
    number = int(f"{'XYZ'.index(prefix.upper())}{digits}" if prefix else digits)
    return _DNI_LETTERS[number % 23] == letter.upper()


_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?)\b")


def _valid_iban(raw: str) -> bool:
    iban = raw.replace(" ", "").upper()
    if not 15 <= len(iban) <= 34:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _valid_card(raw: str) -> bool:
    digits = [int(c) for c in raw if c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        total += d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2)
    return total % 10 == 0


_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?<![\d+])(?:\+34[ ]?)?[6789]\d{2}[ ]?\d{3}[ ]?\d{3}(?!\d)")
_SECRETS = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "Clave de API"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "Clave de acceso de AWS"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "Token de GitHub"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "Clave privada"),
    (
        re.compile(r"\b(?:password|passwd|contrase[nñ]a|clave)\s*[:=]\s*\S{4,}", re.IGNORECASE),
        "Contraseña",
    ),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}"), "Token de acceso"),
]


def sensitive_information(answer: str | None, question: str = "") -> list[Finding]:
    """Datos sensibles en la respuesta que no venían en la pregunta."""
    if not answer:
        return []
    known = re.sub(r"[\s-]", "", question).upper()
    findings: list[Finding] = []
    seen: set[str] = set()

    def add(kind: str, value: str, severity: str) -> None:
        key = re.sub(r"[\s-]", "", value).upper()
        if key in seen or (key and key in known):
            return
        seen.add(key)
        findings.append(
            Finding(
                SENSITIVE_INFORMATION,
                severity,
                f"La respuesta contiene un dato sensible: {kind}",
                mask(value),
            )
        )

    for match in _DNI.finditer(answer):
        if _valid_dni(match.group(1), match.group(2), match.group(3)):
            add("DNI/NIE", match.group(0), "high")
    for match in _IBAN.finditer(answer):
        if _valid_iban(match.group(1)):
            add("número de cuenta (IBAN)", match.group(1), "high")
    for match in _CARD.finditer(answer):
        if _valid_card(match.group(0)):
            add("número de tarjeta", match.group(0), "high")
    for pattern, kind in _SECRETS:
        for match in pattern.finditer(answer):
            add(kind.lower(), match.group(0), "high")
    for match in _EMAIL.finditer(answer):
        add("correo electrónico", match.group(0), "medium")
    for match in _PHONE.finditer(answer):
        add("teléfono", match.group(0), "medium")
    return findings


# --- Intenciones maliciosas en la pregunta ---

_MALICIOUS = [
    (
        r"\b(ignora|olvida|omite|salta(te)?)\b.{0,40}\b(instrucciones|indicaciones|reglas|normas)",
        "Intenta que el asistente ignore sus instrucciones",
        "high",
    ),
    (
        r"\b(ignore|disregard|forget)\b.{0,40}\b(instructions|rules|prompt)",
        "Intenta que el asistente ignore sus instrucciones",
        "high",
    ),
    (
        r"\b(prompt|mensaje|instrucciones) (de|del) sistema\b|\bsystem prompt\b",
        "Pregunta por el prompt de sistema",
        "medium",
    ),
    (
        # Con o sin pronombre pegado: «muestra», «muéstrame», «revélanos»…
        r"\b(muestra|revela|dime|ensena|repite|escribe)(me|nos)?\b.{0,30}"
        r"\b(tus|las) (instrucciones|reglas)",
        "Intenta obtener las instrucciones del asistente",
        "medium",
    ),
    (
        r"\b(jailbreak|modo desarrollador|developer mode|sin (restricciones|filtros|censura))\b",
        "Intento de saltarse las restricciones (jailbreak)",
        "high",
    ),
    (
        r"\b(actua|haz) como si\b.{0,40}\b(no tuvieras|sin) (reglas|restricciones|limites)",
        "Intento de saltarse las restricciones (jailbreak)",
        "high",
    ),
    (r"<\s*script\b|javascript:", "Intento de inyectar código en la página", "medium"),
    (
        r"\b(drop|truncate)\s+table\b|'\s*or\s+'?1'?\s*=\s*'?1|;\s*--",
        "Intento de inyección SQL",
        "medium",
    ),
]
_MALICIOUS_PATTERNS = [(re.compile(p), reason, sev) for p, reason, sev in _MALICIOUS]


def malicious_intent(question: str) -> list[Finding]:
    text = _normalize(question)
    findings = []
    reasons: set[str] = set()
    for pattern, reason, severity in _MALICIOUS_PATTERNS:
        match = pattern.search(text)
        if match and reason not in reasons:
            reasons.add(reason)
            start = max(0, match.start() - 20)
            findings.append(
                Finding(MALICIOUS_USER, severity, reason, text[start : match.end() + 20])
            )
    return findings


def review_with_rules(question: str, answer: str | None) -> list[Finding]:
    return malicious_intent(question) + sensitive_information(answer, question)
