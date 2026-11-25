import hashlib
import json
from datetime import date

import httpx
import pytest

from srag_agent.data.download import (
    DATASET_PAGE_URL,
    MANIFEST_FILENAME,
    DownloadError,
    RemoteFile,
    default_years,
    discover_files,
    download_file,
    extract_links,
    latest_per_year,
    parse_remote_file,
    run,
)

S3 = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG"
URL_25_OLD = f"{S3}/2025/INFLUD25-16-11-2026.parquet"
URL_25 = f"{S3}/2025/INFLUD25-23-11-2026.parquet"
URL_26 = f"{S3}/2026/INFLUD26-23-11-2026.parquet"
RESOURCE_PATH = "/dataset/srag-2019-a-2026/resource/74091efc-3f75-42e8-a6fa-6b79a8d30582"
CONTENT = b"conteudo parquet de teste"


def _page(*hrefs: str) -> str:
    return "<html><body>" + "".join(f'<a href="{href}">link</a>' for href in hrefs) + "</body></html>"


def _client(routes: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestParseRemoteFile:
    def test_extracts_data_year_and_snapshot_date(self):
        remote = parse_remote_file(URL_25)

        assert remote.data_year == 2025
        assert remote.snapshot_date == date(2026, 11, 23)
        assert remote.filename == "INFLUD25-23-11-2026.parquet"

    @pytest.mark.parametrize(
        "url",
        [
            URL_25.replace("https://", "http://"),
            "https://evil.example.com/ckan.saude.gov.br/SRAG/2025/INFLUD25-23-11-2026.parquet",
            "https://s3.sa-east-1.amazonaws.com/outro-bucket/SRAG/2025/INFLUD25-23-11-2026.parquet",
            f"{S3}/2025/INFLUD25-23-11-2026.csv",
            f"{S3}/dicionario-de-dados-2019-a-2025.pdf",
        ],
    )
    def test_rejects_urls_outside_allowlist_or_pattern(self, url):
        with pytest.raises(DownloadError):
            parse_remote_file(url)


def test_extract_links_resolves_relative_hrefs():
    links = extract_links(_page("/dataset/x", URL_26), "https://dadosabertos.saude.gov.br/dataset/y")

    assert links == ["https://dadosabertos.saude.gov.br/dataset/x", URL_26]


class TestDiscoverFiles:
    def test_uses_direct_links_on_dataset_page(self):
        client = _client({DATASET_PAGE_URL: httpx.Response(200, text=_page(URL_25, URL_26, URL_26))})

        files = discover_files(client)

        assert [file.url for file in files] == [URL_25, URL_26]

    def test_follows_resource_pages_when_dataset_page_has_no_direct_links(self):
        resource_url = f"https://dadosabertos.saude.gov.br{RESOURCE_PATH}"
        client = _client(
            {
                DATASET_PAGE_URL: httpx.Response(
                    200, text=_page(RESOURCE_PATH, "https://outro.site/resource")
                ),
                resource_url: httpx.Response(200, text=_page(URL_26)),
            }
        )

        files = discover_files(client)

        assert [file.url for file in files] == [URL_26]

    def test_raises_when_portal_is_unavailable(self):
        client = _client({DATASET_PAGE_URL: httpx.Response(503)})

        with pytest.raises(DownloadError, match="Falha ao acessar"):
            discover_files(client)


class TestLatestPerYear:
    def test_picks_most_recent_snapshot_of_each_year(self):
        files = [parse_remote_file(url) for url in (URL_25_OLD, URL_26, URL_25)]

        latest = latest_per_year(files, [2026, 2025])

        assert [file.url for file in latest] == [URL_25, URL_26]

    def test_raises_when_a_year_is_missing(self):
        with pytest.raises(DownloadError, match="2024"):
            latest_per_year([parse_remote_file(URL_25)], [2024, 2025])


class TestDownloadFile:
    def test_writes_file_and_reports_sha256(self, tmp_path):
        client = _client({URL_26: httpx.Response(200, content=CONTENT)})

        result = download_file(client, parse_remote_file(URL_26), tmp_path)

        assert (tmp_path / result.filename).read_bytes() == CONTENT
        assert result.sha256 == hashlib.sha256(CONTENT).hexdigest()
        assert result.size_bytes == len(CONTENT)
        assert not list(tmp_path.glob("*.part"))

    def test_keeps_existing_file_without_downloading(self, tmp_path):
        remote = parse_remote_file(URL_26)
        (tmp_path / remote.filename).write_bytes(CONTENT)
        client = _client({})  # qualquer requisição responderia 404

        result = download_file(client, remote, tmp_path)

        assert result.sha256 == hashlib.sha256(CONTENT).hexdigest()

    def test_force_downloads_again(self, tmp_path):
        remote = parse_remote_file(URL_26)
        (tmp_path / remote.filename).write_bytes(b"versao antiga")
        client = _client({URL_26: httpx.Response(200, content=CONTENT)})

        download_file(client, remote, tmp_path, force=True)

        assert (tmp_path / remote.filename).read_bytes() == CONTENT

    def test_http_error_leaves_no_partial_file(self, tmp_path):
        client = _client({URL_26: httpx.Response(500)})

        with pytest.raises(DownloadError):
            download_file(client, parse_remote_file(URL_26), tmp_path)

        assert list(tmp_path.iterdir()) == []

    def test_does_not_follow_redirects(self, tmp_path):
        redirect = httpx.Response(302, headers={"Location": "https://evil.example.com/arquivo.parquet"})
        client = _client({URL_26: redirect})

        with pytest.raises(DownloadError):
            download_file(client, parse_remote_file(URL_26), tmp_path)


def test_run_downloads_latest_files_and_writes_manifest(tmp_path):
    client = _client(
        {
            DATASET_PAGE_URL: httpx.Response(200, text=_page(URL_25_OLD, URL_25, URL_26)),
            URL_25: httpx.Response(200, content=b"2025"),
            URL_26: httpx.Response(200, content=b"2026"),
        }
    )

    downloads = run(client, tmp_path, years=[2025, 2026])

    manifest = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert [item.filename for item in downloads] == [
        "INFLUD25-23-11-2026.parquet",
        "INFLUD26-23-11-2026.parquet",
    ]
    assert set(manifest) == {item.filename for item in downloads}
    assert manifest["INFLUD26-23-11-2026.parquet"]["sha256"] == hashlib.sha256(b"2026").hexdigest()


def test_run_with_explicit_url_skips_discovery(tmp_path):
    client = _client({URL_26: httpx.Response(200, content=CONTENT)})

    downloads = run(client, tmp_path, years=[2025, 2026], urls=[URL_26])

    assert [item.filename for item in downloads] == ["INFLUD26-23-11-2026.parquet"]


def test_default_years_are_current_and_previous():
    assert default_years(date(2026, 11, 25)) == [2025, 2026]


def test_remote_file_is_hashable_for_deduplication():
    assert len({RemoteFile(URL_26, 2026, date(2026, 11, 23))} | {parse_remote_file(URL_26)}) == 1
