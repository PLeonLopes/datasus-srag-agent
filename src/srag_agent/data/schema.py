"""Contrato dos dados: o que pode ser lido da fonte e como cada código vira um rótulo.

Allowlist (minimização de dados): só estas colunas saem do Parquet; as demais nunca entram na memória.
A idade exata não é guardada: `NU_IDADE_N` e `TP_IDADE` só servem para derivar a faixa etária.
"""

SYMPTOM_DATE_COLUMN = "DT_SIN_PRI"
AGE_VALUE_COLUMN = "NU_IDADE_N"
AGE_UNIT_COLUMN = "TP_IDADE"

# Coluna da fonte -> coluna no banco, com o mapa código -> rótulo. Código fora do mapa vira nulo.
_YES_NO = {"1": "yes", "2": "no", "9": "unknown"}
CATEGORICAL_COLUMNS: dict[str, tuple[str, dict[str, str]]] = {
    "EVOLUCAO": (
        "outcome",
        {"1": "recovered", "2": "srag_death", "3": "other_death", "9": "unknown"},
    ),
    "HOSPITAL": ("hospitalized", _YES_NO),
    "UTI": ("icu", _YES_NO),
    "VACINA": ("flu_vaccine", _YES_NO),
    "VACINA_COV": ("covid_vaccine", _YES_NO),
    "CLASSI_FIN": (
        "classification",
        {
            "1": "influenza",
            "2": "other_respiratory_virus",
            "3": "other_agent",
            "4": "unspecified",
            "5": "covid19",
        },
    ),
    "CS_SEXO": ("sex", {"M": "male", "F": "female", "I": "unknown"}),
}

STATE_COLUMN = ("SG_UF_NOT", "state")
# fmt: off
STATES = frozenset({
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA",
    "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO",
})
# fmt: on

SOURCE_COLUMNS: list[str] = [
    SYMPTOM_DATE_COLUMN,
    *CATEGORICAL_COLUMNS,
    STATE_COLUMN[0],
    AGE_VALUE_COLUMN,
    AGE_UNIT_COLUMN,
]

# TP_IDADE: 1 = dias, 2 = meses, 3 = anos
AGE_UNIT_IN_YEARS = {"1": 1 / 365.25, "2": 1 / 12, "3": 1.0}
MAX_AGE_YEARS = 120
AGE_GROUP_BINS = [0, 5, 20, 40, 60, 80, float("inf")]
AGE_GROUP_LABELS = ["0-4", "5-19", "20-39", "40-59", "60-79", "80+"]

CASES_TABLE = "cases"
METADATA_TABLE = "load_metadata"
CASES_COLUMNS: list[str] = [
    "symptom_date",
    STATE_COLUMN[1],
    "age_group",
    *(target for target, _ in CATEGORICAL_COLUMNS.values()),
]
