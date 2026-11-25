"""Guardrails em código: entrada (banco) e saída (texto do LLM).

A saída é estrita: todo número do texto precisa existir nas evidências (saídas das tools) entregues ao
modelo. Arredondar também é bloqueado, para o texto nunca divergir das tabelas do relatório.
"""

import re
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from srag_agent.data.database import read_load_metadata, reference_date
from srag_agent.data.schema import CASES_COLUMNS, CASES_TABLE
from srag_agent.tools.news import looks_like_injection, normalize

# Entrada
RECENT_WINDOW_DAYS = 21
MIN_RECENT_CASES = 10
MAX_SNAPSHOT_AGE_DAYS = 14

# Saída
MAX_WORDS = 350
ALWAYS_ALLOWED_NUMBERS = frozenset({7.0, 12.0, 14.0, 28.0, 30.0, 90.0})  # janelas do relatório
SMALL_INTEGER_LIMIT = 10  # "dois estados", "3 semanas"

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_MINUS_SIGN = "\u2212"  # sinal de menos tipográfico, comum em texto gerado por LLM
_NUMBER = re.compile(r"(?<![\w.,])[-+\u2212]?\d+(?:[.,]\d+)*(?:\s?%)?")
_THOUSANDS = re.compile(r"\d{1,3}(?:\.\d{3})+")

_PII_PATTERNS = {
    "CPF": re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
    "CNPJ": re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
    "telefone": re.compile(r"\(?\b\d{2}\)?\s?9?\d{4}-\d{4}\b"),
    "e-mail": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
}
_LINK = re.compile(r"https?://|\bwww\.|\b[\w-]+\.(?:com|org|net|gov|edu|br|io)(?:\.br)?\b", re.IGNORECASE)

# Aplicados ao texto normalizado (minúsculo, sem acentos).
_MEDICAL_ADVICE = re.compile(
    "|".join(
        [
            r"\b(procure|procurem|consulte|consultem)\b.{0,30}"
            r"\b(medico|atendimento|servico de saude|unidade)",
            r"\b(tome|tomem|use|usem)\b.{0,20}\b(remedio|medicamento|antiviral|oseltamivir|antibiotico)",
            r"\bautomedica",
            r"\b(recomendamos|recomenda-se|aconselhamos|aconselha-se)\b",
            r"\b(voce deve|voces devem)\b",
        ]
    )
)
# Termos que a base não permite medir: o número estaria certo, mas a interpretação, errada.
_MISLEADING_TERMS = {
    r"\bcobertura vacina\w*": "'cobertura vacinal' (a base não tem a população; é vacinação entre os casos)",
    r"\bocupacao (de |dos |das )?(uti|leitos?)\b": "'ocupação de UTI/leitos' (a base não tem leitos)",
    r"\btaxa de ocupacao\b": "'taxa de ocupação' (a base não tem leitos)",
}
_FORECAST = re.compile(
    "|".join(
        [
            r"\b(vai|vao|ira|irao)\s+(aumentar|subir|cair|diminuir|crescer|piorar|melhorar)",
            r"\b(devera|deverao|tende a|tendem a)\s+(aumentar|subir|cair|diminuir|crescer|piorar|melhorar)",
            r"\b(previsao|projecao|projetamos|prevemos|preve-se)\b",
            r"\bnas proximas semanas\b.{0,40}\b(vai|ira|devera|havera)\b",
        ]
    )
)
_INCREASE = re.compile(r"\b(alta|aumento|aumentou|aumentaram|cresc\w*|subi\w*|elevacao|avanco)\b")
_DECREASE = re.compile(r"\b(queda|caiu|cairam|reducao|reduziu|diminu\w*|recuo|recuou|declinio)\b")
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|\n+")


@dataclass(frozen=True)
class GuardrailResult:
    violations: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class DatabaseCheck:
    reference_date: date | None
    synthetic: bool
    blocked_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.blocked_reasons


def check_database(connection: sqlite3.Connection, today: date) -> DatabaseCheck:
    """Bloqueia banco fora do contrato ou sem casos recentes; avisa sobre snapshot antigo e dado sintético."""
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({CASES_TABLE})")]
    if columns != CASES_COLUMNS:
        return DatabaseCheck(None, False, [f"Colunas fora da allowlist: {columns}"])

    reference = reference_date(connection)
    synthetic = bool(read_load_metadata(connection).get("synthetic"))
    recent = connection.execute(
        f"SELECT COUNT(*) FROM {CASES_TABLE} WHERE symptom_date BETWEEN ? AND ?",  # noqa: S608
        ((reference - timedelta(days=RECENT_WINDOW_DAYS)).isoformat(), reference.isoformat()),
    ).fetchone()[0]

    blocked = []
    if recent < MIN_RECENT_CASES:
        blocked.append(
            f"Só {recent} casos nos {RECENT_WINDOW_DAYS} dias antes do snapshot: "
            "arquivo antigo ou incompleto."
        )
    warnings = []
    age = (today - reference).days
    if age > MAX_SNAPSHOT_AGE_DAYS:
        warnings.append(f"Snapshot de {age} dias atrás: baixe dados novos.")
    if synthetic:
        warnings.append("Dados sintéticos: o relatório não descreve a situação real.")
    return DatabaseCheck(reference, synthetic, blocked, warnings)


def check_explanation(text: str, evidence: object, growth_values: Sequence[float] = ()) -> GuardrailResult:
    """Valida o texto do LLM contra as evidências entregues a ele."""
    violations: list[str] = []
    normalized = normalize(text)

    for label, pattern in _PII_PATTERNS.items():
        if pattern.search(text):
            violations.append(f"contém dado pessoal ({label})")
    if _LINK.search(text):
        violations.append("contém link ou domínio (as fontes são listadas pelo código)")
    if _MEDICAL_ADVICE.search(normalized):
        violations.append("contém conselho médico ou recomendação individual")
    if _FORECAST.search(normalized):
        violations.append("contém previsão")
    violations += [
        f"usa termo enganoso {explanation}"
        for pattern, explanation in _MISLEADING_TERMS.items()
        if re.search(pattern, normalized)
    ]
    if looks_like_injection(text):
        violations.append("contém padrão de instrução ao modelo")
    if len(text.split()) > MAX_WORDS:
        violations.append(f"passa de {MAX_WORDS} palavras")

    allowed = collect_numbers(evidence)
    unknown = sorted({number for number in extract_numbers(text) if not _is_allowed(number, allowed)})
    if unknown:
        violations.append(
            "números que não estão nos dados: " + ", ".join(_format(number) for number in unknown)
        )
    violations += _trend_contradictions(text, growth_values)
    return GuardrailResult(violations)


def extract_numbers(text: str) -> list[float]:
    """Números do texto em formato brasileiro ("14.173", "5,0%", "-9,0"), ignorando datas."""
    without_dates = _DATE.sub(" ", text)
    return [abs(value) for token in _NUMBER.findall(without_dates) if (value := _parse(token)) is not None]


def collect_numbers(evidence: object) -> set[float]:
    """Todos os números das evidências: valores numéricos e números escritos em textos (notícias)."""
    found: set[float] = set()
    for value in _walk(evidence):
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            found.add(round(abs(float(value)), 1))
        elif isinstance(value, str):
            found.update(round(number, 1) for number in extract_numbers(value))
    return found


def _is_allowed(number: float, allowed: set[float]) -> bool:
    rounded = round(number, 1)
    is_integer = rounded == int(rounded)
    return (
        rounded in allowed
        or rounded in ALWAYS_ALLOWED_NUMBERS
        or (is_integer and rounded <= SMALL_INTEGER_LIMIT)
        or (is_integer and 2000 <= rounded <= 2100)  # anos
    )


def _trend_contradictions(text: str, growth_values: Sequence[float]) -> list[str]:
    violations = []
    for sentence in _SENTENCE_END.split(text):
        numbers = {round(number, 1) for number in extract_numbers(sentence)}
        normalized = normalize(sentence)
        says_increase = bool(_INCREASE.search(normalized))
        says_decrease = bool(_DECREASE.search(normalized))
        for growth in growth_values:
            if round(abs(growth), 1) not in numbers:
                continue
            if growth < 0 and says_increase and not says_decrease:
                violations.append(f"chama de alta uma variação negativa ({_format(growth)}%)")
            elif growth > 0 and says_decrease and not says_increase:
                violations.append(f"chama de queda uma variação positiva ({_format(growth)}%)")
    return violations


def _parse(token: str) -> float | None:
    cleaned = token.replace("%", "").replace(_MINUS_SIGN, "-").strip().lstrip("+-")
    if _THOUSANDS.fullmatch(cleaned):
        cleaned = cleaned.replace(".", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _walk(value) -> Iterable:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list | tuple | set):
        for item in value:
            yield from _walk(item)
    else:
        yield value


def _format(number: float) -> str:
    return f"{number:g}".replace(".", ",")
