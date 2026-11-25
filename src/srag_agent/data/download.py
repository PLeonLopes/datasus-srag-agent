"""Download dos arquivos de SRAG (Parquet) do Open DATASUS.

A API CKAN do portal responde 404, então os links são descobertos na página do dataset.
Uso: `uv run python -m srag_agent.data.download [--years 2025 2026] [--url URL ...] [--force]`
"""

import argparse
import hashlib
import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx

from srag_agent.config import get_settings

logger = logging.getLogger(__name__)

DATASET_PAGE_URL = "https://dadosabertos.saude.gov.br/dataset/srag-2019-a-2026"
ALLOWED_HOST = "s3.sa-east-1.amazonaws.com"
ALLOWED_PATH_PREFIX = "/ckan.saude.gov.br/SRAG/"
MANIFEST_FILENAME = "manifest.json"
USER_AGENT = "datasus-srag-agent/0.1"

# INFLUD25-23-11-2026.parquet: ano dos dados (25) e data do snapshot (23/11/2026)
_FILE_PATTERN = re.compile(r"INFLUD(?P<yy>\d{2})-(?P<day>\d{2})-(?P<month>\d{2})-(?P<year>\d{4})\.parquet$")
_RESOURCE_PAGE_PATTERN = re.compile(r"/dataset/[^/]+/resource/[0-9a-f-]{36}/?$")
_CHUNK_SIZE = 1024 * 1024
_TIMEOUT = httpx.Timeout(30.0, read=300.0)


class DownloadError(RuntimeError):
    """Falha ao descobrir, validar ou baixar um arquivo."""


@dataclass(frozen=True)
class RemoteFile:
    url: str
    data_year: int
    snapshot_date: date

    @property
    def filename(self) -> str:
        return PurePosixPath(urlparse(self.url).path).name


@dataclass(frozen=True)
class DownloadedFile:
    filename: str
    url: str
    data_year: int
    snapshot_date: str
    sha256: str
    size_bytes: int
    downloaded_at: str


def parse_remote_file(url: str) -> RemoteFile:
    """Valida a URL contra a allowlist e extrai o ano dos dados e a data do snapshot."""
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != ALLOWED_HOST
        or not parsed.path.startswith(ALLOWED_PATH_PREFIX)
    ):
        raise DownloadError(f"URL fora da allowlist: {url}")
    match = _FILE_PATTERN.search(parsed.path)
    if match is None:
        raise DownloadError(f"Nome de arquivo inesperado: {url}")
    snapshot = date(int(match["year"]), int(match["month"]), int(match["day"]))
    return RemoteFile(url=url, data_year=2000 + int(match["yy"]), snapshot_date=snapshot)


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value for name, value in attrs if name == "href" and value)


def extract_links(html: str, base_url: str) -> list[str]:
    collector = _LinkCollector()
    collector.feed(html)
    return [urljoin(base_url, href) for href in collector.hrefs]


def discover_files(client: httpx.Client, page_url: str = DATASET_PAGE_URL) -> list[RemoteFile]:
    """Arquivos Parquet listados no dataset, direto da página ou das páginas de cada recurso."""
    links = extract_links(_get_text(client, page_url), page_url)
    files = _valid_files(links)
    if files:
        return files

    page_host = urlparse(page_url).hostname
    resource_pages = [
        link
        for link in dict.fromkeys(links)
        if urlparse(link).hostname == page_host and _RESOURCE_PAGE_PATTERN.search(urlparse(link).path)
    ]
    for resource_page in resource_pages:
        files.extend(_valid_files(extract_links(_get_text(client, resource_page), resource_page)))
    return list(dict.fromkeys(files))


def latest_per_year(files: Iterable[RemoteFile], years: Iterable[int]) -> list[RemoteFile]:
    """Snapshot mais recente de cada ano pedido; falha se faltar algum ano."""
    latest: dict[int, RemoteFile] = {}
    for file in files:
        current = latest.get(file.data_year)
        if current is None or file.snapshot_date > current.snapshot_date:
            latest[file.data_year] = file

    wanted = sorted(set(years))
    missing = [year for year in wanted if year not in latest]
    if missing:
        raise DownloadError(f"Nenhum arquivo Parquet encontrado para: {missing}")
    return [latest[year] for year in wanted]


def download_file(
    client: httpx.Client, remote: RemoteFile, dest_dir: Path, force: bool = False
) -> DownloadedFile:
    """Baixa em streaming para um `.part` e renomeia no fim, para nunca deixar arquivo pela metade."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / remote.filename

    if target.exists() and not force:
        logger.info("Já existe, mantendo: %s", target.name)
        digest, size = _hash_file(target)
        return _describe(remote, digest, size)

    partial = target.with_name(target.name + ".part")
    hasher = hashlib.sha256()
    size = 0
    try:
        # Sem seguir redirects: o download só pode vir do host da allowlist.
        with client.stream("GET", remote.url, follow_redirects=False) as response:
            response.raise_for_status()
            with partial.open("wb") as output:
                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    output.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
    except (httpx.HTTPError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"Falha ao baixar {remote.filename}: {error}") from error

    partial.replace(target)
    logger.info("Baixado: %s (%.1f MB)", target.name, size / 1024**2)
    return _describe(remote, hasher.hexdigest(), size)


def update_manifest(dest_dir: Path, downloads: Iterable[DownloadedFile]) -> Path:
    """Registra url, sha256 e tamanho de cada arquivo (proveniência dos dados)."""
    path = dest_dir / MANIFEST_FILENAME
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for item in downloads:
        manifest[item.filename] = asdict(item)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", "utf-8")
    return path


def default_years(today: date) -> list[int]:
    """Ano corrente e anterior: a janela de 12 meses atravessa a virada do ano."""
    return [today.year - 1, today.year]


def run(
    client: httpx.Client,
    dest_dir: Path,
    years: Sequence[int],
    urls: Sequence[str] = (),
    force: bool = False,
) -> list[DownloadedFile]:
    if urls:
        remotes = [parse_remote_file(url) for url in urls]
    else:
        remotes = latest_per_year(discover_files(client), years)
    downloads = [download_file(client, remote, dest_dir, force) for remote in remotes]
    update_manifest(dest_dir, downloads)
    return downloads


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Baixa os dados de SRAG do Open DATASUS.")
    parser.add_argument("--years", type=int, nargs="+", default=default_years(date.today()))
    parser.add_argument("--url", action="append", default=[], help="URL explícita (ignora a descoberta)")
    parser.add_argument("--dest", type=Path, default=get_settings().raw_data_dir)
    parser.add_argument("--force", action="store_true", help="Baixa de novo mesmo se o arquivo existir")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT, follow_redirects=True) as client:
        downloads = run(client, args.dest, args.years, args.url, args.force)
    for item in downloads:
        logger.info("%s  sha256=%s", item.filename, item.sha256)


def _get_text(client: httpx.Client, url: str) -> str:
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise DownloadError(f"Falha ao acessar {url}: {error}") from error
    return response.text


def _valid_files(links: Iterable[str]) -> list[RemoteFile]:
    files = []
    for link in links:
        try:
            files.append(parse_remote_file(link))
        except DownloadError:
            continue
    return list(dict.fromkeys(files))


def _hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest(), path.stat().st_size


def _describe(remote: RemoteFile, digest: str, size: int) -> DownloadedFile:
    return DownloadedFile(
        filename=remote.filename,
        url=remote.url,
        data_year=remote.data_year,
        snapshot_date=remote.snapshot_date.isoformat(),
        sha256=digest,
        size_bytes=size,
        downloaded_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


if __name__ == "__main__":
    main()
