from pathlib import Path

import pytest

from knowledge_assistant.common import digest, local_path
from knowledge_assistant.data import clean_html, chunk_text, fetch, prepare, save_manifest, validate_data, FIELDS


def test_clean_preserves_conditions_links_tables_and_utf8():
    html = '<html><nav>NOISE</nav><main><h1>Policy</h1><nav>INNER NOISE</nav><p>Employees must obtain approval; contractors are not eligible. Don’t remove this.</p><ul><li>Only in Region A<ul><li>Not for interns</li></ul></li></ul><table><tr><th>Role</th><th>Limit</th></tr><tr><td>Employee</td><td>Only after approval</td></tr></table><p><a href="/policy">source</a></p><footer>FOOTER</footer></main></html>'
    title, body = clean_html(html.encode(), "https://handbook.gitlab.com/")
    assert title == "Policy" and "Don’t" in body
    assert "contractors are not eligible" in body and "Not for interns" in body
    assert "| Employee | Only after approval |" in body
    assert "https://handbook.gitlab.com/policy" in body
    assert "NOISE" not in body and "FOOTER" not in body


def test_heading_chunks_never_truncate_restrictions():
    doc = {"doc_id": "DOC-1", "title": "Test", "source_url": "https://handbook.gitlab.com/test/", "topic": "onboarding", "scope_notes": "Test"}
    text = "# Test\n\n## First\n\n" + "Long procedure. " * 40 + "Only employees; not contractors.\n\n## Second\n\nNo approval authority."
    chunks = chunk_text(text, doc, max_chars=120, overlap_chars=20)
    assert any("Only employees; not contractors." in c["content"] for c in chunks)
    assert any(c["section_path"] == "Test / Second" for c in chunks)
    assert all(digest(c["content"]) == c["content_sha256"] for c in chunks)


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(ValueError):
        local_path(tmp_path, "../outside")


def test_redirect_does_not_follow_unlisted_source(tmp_path):
    (tmp_path / "sources.yaml").write_text('timeout_seconds: 1\ndelay_seconds: 0\nmax_bytes: 1000\nlicense_status: pending\nlicense_reference: test\nsources:\n - {doc_id: DOC-001, topic: onboarding, url: "https://handbook.gitlab.com/test/"}\n')
    class Session:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return type("Response", (), {"status_code": 302, "headers": {"Location": "https://unlisted.invalid/"}})()
    session = Session()
    rows = fetch(tmp_path / "sources.yaml", tmp_path, session)
    assert session.calls == 1 and rows[0]["included"] == "false"
    assert "Redirect outside" in rows[0]["exclusion_reason"]


def test_manifest_hash_tampering_and_immutable_snapshot(tmp_path):
    raw = tmp_path / "raw.html"; raw.write_bytes(b"test")
    body = tmp_path / "body.md"; body.write_text("# Test\n\nOnly eligible employees.\n", encoding="utf-8")
    row = dict.fromkeys(FIELDS, "")
    row.update(doc_id="DOC-1", title="Test", included="true", synthetic="false", source_url="https://handbook.gitlab.com/test/", fetched_at_utc="2026-10-03T00:00:00+00:00", raw_path="raw.html", raw_sha256=digest(raw.read_bytes()), local_path="body.md", content_sha256=digest(body.read_text()), topic="onboarding", scope_notes="Public")
    manifest = tmp_path / "manifest.csv"; save_manifest(manifest, [row])
    assert not validate_data(manifest, tmp_path)["problems"]
    one, count = prepare(manifest, tmp_path)
    assert prepare(manifest, tmp_path) == (one, count)
    body.write_text("Tampered", encoding="utf-8")
    assert validate_data(manifest, tmp_path)["problems"]
    with pytest.raises(ValueError, match="Hash mismatch"):
        prepare(manifest, tmp_path)


def test_full_snapshot_survives_source_changes_and_detects_tampering(tmp_path):
    from knowledge_assistant.evaluation import load_snapshot
    raw = tmp_path / "raw.html"; raw.write_bytes(b"original html")
    body = tmp_path / "body.md"; body.write_text("# Original\n\nOnly eligible employees.\n", encoding="utf-8")
    row = dict.fromkeys(FIELDS, "")
    row.update(doc_id="DOC-1", title="Original", included="true", synthetic="false", source_url="https://handbook.gitlab.com/test/", fetched_at_utc="fixture", raw_path="raw.html", raw_sha256=digest(raw.read_bytes()), local_path="body.md", content_sha256=digest(body.read_text()), topic="onboarding", scope_notes="Public")
    manifest = tmp_path / "manifest.csv"; save_manifest(manifest, [row])
    snapshot, _ = prepare(manifest, tmp_path)
    raw.write_bytes(b"new current source")
    body.write_text("changed current body")
    meta, frozen_rows, chunks = load_snapshot(snapshot, tmp_path)
    assert "Only eligible" in chunks[0]["content"]
    (tmp_path / "data/snapshots" / snapshot / "documents/DOC-1.md").write_text("tamper")
    with pytest.raises(ValueError, match="document integrity"):
        load_snapshot(snapshot, tmp_path)
