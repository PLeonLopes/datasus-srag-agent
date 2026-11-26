"""Trilha de auditoria em JSON Lines, só de acréscimo, com cadeia de hash.

Cada linha guarda o hash da anterior: editar ou apagar uma linha quebra a cadeia. O hash da última linha
também fica num arquivo `.head`, para que cortar o fim do log seja detectado.
Segredos nunca são gravados: campos com nome de chave/token/senha e `SecretStr` viram `[REDACTED]`.
"""

import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import SecretStr

try:
    import fcntl  # trava entre processos (Linux, macOS, WSL)
except ImportError:  # Windows: só a trava entre threads
    fcntl = None

GENESIS_HASH = "0" * 64
REDACTED = "[REDACTED]"
STATUSES = frozenset({"ok", "degraded", "blocked", "error"})
# "token" só no fim do nome (access_token, HF_TOKEN): contagens como input_tokens não são segredo.
_SECRET_KEY = re.compile(r"api_?key|secret|password|senha|authorization|(^|_)token$", re.IGNORECASE)
_MAX_STRING_CHARS = 20_000


@dataclass(frozen=True)
class ChainReport:
    valid: bool
    events: int
    problems: list[str]


class AuditLog:
    _lock = threading.Lock()

    def __init__(self, path: Path, run_id: str | None = None) -> None:
        self.path = path
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def head_path(self) -> Path:
        return head_path_for(self.path)

    def record(self, event: str, status: str = "ok", **data) -> dict:
        """Acrescenta um evento encadeado ao anterior e devolve o registro gravado."""
        if status not in STATUSES:
            raise ValueError(f"Status inválido: {status}")
        with self._lock, self.path.open("a+", encoding="utf-8") as file:
            if fcntl is not None:
                fcntl.flock(file, fcntl.LOCK_EX)
            seq, prev_hash = _last_position(file)
            entry = {
                "seq": seq + 1,
                "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                "run_id": self.run_id,
                "event": event,
                "status": status,
                "data": sanitize(data),
                "prev_hash": prev_hash,
            }
            entry["hash"] = _entry_hash(entry)
            file.write(_canonical(entry) + "\n")
            file.flush()
            _write_head(self.head_path, entry["seq"], entry["hash"])
        return entry

    @contextmanager
    def step(self, name: str, **data) -> Iterator[dict]:
        """Registra a duração e o resultado de uma etapa; `extra` recebe dados para o evento final."""
        extra: dict = {}
        started = time.perf_counter()
        try:
            yield extra
        except Exception as error:
            self.record(
                f"step.{name}", "error", error=type(error).__name__, duration_ms=_elapsed(started), **data
            )
            raise
        self.record(f"step.{name}", extra.pop("status", "ok"), duration_ms=_elapsed(started), **data, **extra)


def sanitize(value):
    """Torna o valor serializável e remove segredos."""
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _SECRET_KEY.search(str(key)) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [sanitize(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return sanitize(asdict(value))
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, str) and len(value) > _MAX_STRING_CHARS:
        return value[:_MAX_STRING_CHARS] + "…[truncado]"
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def read_events(path: Path, run_id: str | None = None) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as file:
        events = [json.loads(line) for line in file if line.strip()]
    return [event for event in events if run_id is None or event["run_id"] == run_id]


def verify_chain(path: Path, partial: bool = False) -> ChainReport:
    """Recalcula os hashes e confere a sequência, o encadeamento e o arquivo `.head`.

    `partial=True` verifica um recorte do log (ex.: uma execução exportada): a primeira linha pode apontar
    para um evento anterior que não está no arquivo, e o `.head` não é exigido.
    """
    if not path.exists():
        return ChainReport(valid=True, events=0, problems=[])
    problems: list[str] = []
    prev_hash, expected_seq, count = GENESIS_HASH, 1, 0
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            count += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                problems.append(f"linha {line_number}: JSON inválido")
                continue
            if partial and count == 1:
                prev_hash, expected_seq = entry.get("prev_hash"), entry.get("seq")
            if entry.get("seq") != expected_seq:
                problems.append(f"linha {line_number}: sequência {entry.get('seq')}, esperada {expected_seq}")
            if entry.get("prev_hash") != prev_hash:
                problems.append(f"linha {line_number}: prev_hash não bate com a linha anterior")
            if entry.get("hash") != _entry_hash({k: v for k, v in entry.items() if k != "hash"}):
                problems.append(f"linha {line_number}: conteúdo alterado (hash não confere)")
            prev_hash, expected_seq = entry.get("hash", ""), (entry.get("seq") or expected_seq) + 1

    head = None if partial else _read_head(head_path_for(path))
    if head is not None and head.get("hash") != prev_hash:
        problems.append("fim do log não confere com o arquivo .head (linhas finais removidas ou alteradas)")
    return ChainReport(valid=not problems, events=count, problems=problems)


def head_path_for(path: Path) -> Path:
    return path.with_name(path.name + ".head")


def _last_position(file) -> tuple[int, str]:
    file.seek(0)
    last_line = ""
    for line in file:
        if line.strip():
            last_line = line
    if not last_line:
        return 0, GENESIS_HASH
    last = json.loads(last_line)
    return last["seq"], last["hash"]


def _write_head(path: Path, seq: int, entry_hash: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"seq": seq, "hash": entry_hash}), encoding="utf-8")
    temporary.replace(path)


def _read_head(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _entry_hash(entry_without_hash: dict) -> str:
    return hashlib.sha256(_canonical(entry_without_hash).encode("utf-8")).hexdigest()


def _elapsed(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
