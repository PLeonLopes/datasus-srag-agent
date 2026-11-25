from datetime import date

import httpx

from srag_agent.config import Settings
from srag_agent.data.download import DATASET_PAGE_URL
from srag_agent.data.pipeline import refresh_data
from srag_agent.data.synthetic import generate_snapshots
from srag_agent.governance.audit import AuditLog, read_events

S3 = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG"


def test_refresh_downloads_loads_and_audits(tmp_path):
    files = generate_snapshots(tmp_path / "origem", date(2026, 11, 23), n_cases=500)
    urls = {f"{S3}/{2025 + index}/{path.name}": path.read_bytes() for index, path in enumerate(files)}
    page = "".join(f'<a href="{url}">x</a>' for url in urls)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DATASET_PAGE_URL:
            return httpx.Response(200, text=page)
        return httpx.Response(200, content=urls[str(request.url)])

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", logs_dir=tmp_path / "logs")
    audit = AuditLog(settings.audit_log_path)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    summary = refresh_data(settings, audit, client, today=date(2026, 11, 25))

    assert summary.rows_loaded == 500
    assert settings.database_path.exists()
    events = {event["event"]: event for event in read_events(settings.audit_log_path)}
    assert len(events["step.data.download"]["data"]["files"]) == 2
    assert events["step.data.etl"]["data"]["summary"]["rows_loaded"] == 500
