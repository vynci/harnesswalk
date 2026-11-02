"""The original file of a knowledge document: `GET /v1/packs/{id}/files/docs/{name}?raw=1` and
the `file` field of the knowledge document routes (http_api.md, "Document files").
Fake provider only."""

from pathlib import Path

import httpx
import pytest

from harnesswalk import packs
from harnesswalk.control.http_api import create_app
from harnesswalk.runtime import Harness
from tests.unit.support import settings
from tests.unit.test_http_api import client

RAW = "/v1/packs/healthcare/files/docs/{}?raw=1"


@pytest.mark.anyio
async def test_a_pdf_is_served_as_a_pdf_inline(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path))
    async with client(h) as c:
        r = await c.get(RAW.format("appointment_cancellation_policy.pdf"))
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"].startswith("inline")
        assert "appointment_cancellation_policy.pdf" in r.headers["content-disposition"]
        assert r.headers["cache-control"] == "public, max-age=300"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["etag"] and r.headers["last-modified"]
        assert r.content.startswith(b"%PDF")
        # Without ?raw the route keeps its JSON shape: the extracted text.
        assert (await c.get(RAW.format("billing_faq.pdf")[: -len("?raw=1")])).json()[
            "kind"
        ] == "pdf_text"
    await h.aclose()


@pytest.mark.anyio
async def test_markdown_is_served_as_markdown(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path))
    async with client(h) as c:
        r = await c.get(RAW.format("patient_faq.md"))
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/markdown; charset=utf-8"
        assert r.headers["content-disposition"].startswith("inline")
        on_disk = packs.find(h.settings, "healthcare").path / "docs" / "patient_faq.md"
        assert r.text == on_disk.read_text(encoding="utf-8")
    await h.aclose()


@pytest.mark.anyio
async def test_only_documents_under_docs_and_no_traversal(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path))
    async with client(h) as c:
        for bad in (
            "pack.yaml",  # listed, but not a document
            "system_prompt.md",  # a markdown file outside docs/
            "skills/billing.md",
            "sources/billing_faq.md",  # the source a PDF was rendered from
            "docs",
            "docs/",
            "docs/nope.pdf",  # unknown
            "docs/../pack.yaml",
            "docs/%2e%2e/pack.yaml",
            "docs/..%2fpack.yaml",
            "%2e%2e/%2e%2e/config/harness.yaml",
            "docs/.hidden.md",
        ):
            r = await c.get(f"/v1/packs/healthcare/files/{bad}?raw=1")
            assert r.status_code == 404, bad
            assert r.headers["content-type"].startswith("application/json"), bad
        assert (await c.get("/v1/packs/nope/files/docs/x.md?raw=1")).status_code == 404
    await h.aclose()


def test_a_symlink_out_of_docs_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "packs" / "linked"
    (root / "docs").mkdir(parents=True)
    (root / "system_prompt.md").write_text("You help.\n", encoding="utf-8")
    secret = tmp_path / "secret.md"
    secret.write_text("# not pack content\n", encoding="utf-8")
    (root / "docs" / "leak.md").symlink_to(secret)
    (root / "docs" / "ok.md").write_text("# fine\n", encoding="utf-8")
    pack = packs.load_pack(root)
    assert pack.doc_file("ok.md") == root / "docs" / "ok.md"
    for name in ("leak.md", "../system_prompt.md", "", ".", "..", "sub/ok.md", "ok.txt"):
        assert pack.doc_file(name) is None, name


@pytest.mark.anyio
async def test_the_document_names_its_file(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path / "packs", profile=None))
    async with client(h) as c:
        pdf = (await c.get("/v1/knowledge/docs/billing_faq", params={"pack": "healthcare"})).json()
        assert pdf["file"] == {
            "pack": "healthcare",
            "path": "docs/billing_faq.pdf",
            "url": "/v1/packs/healthcare/files/docs/billing_faq.pdf?raw=1",
            "media_type": "application/pdf",
        }
        served = await c.get(pdf["file"]["url"])
        assert served.status_code == 200 and served.headers["content-type"] == "application/pdf"
        md = (await c.get("/v1/knowledge/docs/patient_faq", params={"pack": "healthcare"})).json()
        assert md["file"]["media_type"] == "text/markdown; charset=utf-8"
        listed = (await c.get("/v1/knowledge/docs", params={"pack": "healthcare"})).json()
        assert len(listed["docs"]) == 14
        assert all(d["file"]["url"].endswith(f"{d['path']}?raw=1") for d in listed["docs"])
    await h.aclose()
    # The conformance profile indexes its own documents directory (`stores.knowledge.docs_dir`):
    # those documents are not a pack's files, so they name none.
    h = Harness(settings(tmp_path / "profile"))
    async with client(h) as c:
        own = (await c.get("/v1/knowledge/docs")).json()["docs"]
        assert own and all(d["file"] is None for d in own)
        doc = (await c.get(f"/v1/knowledge/docs/{own[0]['doc_id']}")).json()
        assert doc["file"] is None
    await h.aclose()


@pytest.mark.anyio
async def test_public_mode_serves_pack_documents(tmp_path: Path) -> None:
    pub = Harness(settings(tmp_path, profile=None, mode="public"))
    transport = httpx.ASGITransport(app=create_app(pub))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(RAW.format("billing_faq.pdf"))
        assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
        assert (await c.get(RAW.format("../pack.yaml"))).status_code == 404
        doc = (await c.get("/v1/knowledge/docs/returns_policy")).json()
        assert doc["file"]["url"] == "/v1/packs/_starter/files/docs/returns_policy.md?raw=1"
    await pub.aclose()
