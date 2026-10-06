import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .common import canonical_hash, digest, local_path, read_config, utc_now, write_json, write_jsonl

FIELDS = ["doc_id", "title", "source_url", "fetched_at_utc", "source_updated_at", "content_sha256",
          "topic", "language", "scope_notes", "license_status", "local_path", "included",
          "exclusion_reason", "raw_path", "raw_sha256", "source_status", "synthetic"]


def load_manifest(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save_manifest(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def clean_html(html, source_url):
    soup = BeautifulSoup(html, "html.parser", from_encoding="utf-8") if isinstance(html, bytes) else BeautifulSoup(html, "html.parser")
    root = soup.select_one(".td-content") or soup.find("article") or soup.find("main")
    if root is None:
        raise ValueError("No recognizable content root; manual extraction required")
    for tag in root.select("script, style, nav, footer, .pageinfo, .td-page-meta, .feedback, .breadcrumb"):
        tag.decompose()
    title_tag = root.find("h1") or soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else "Untitled"
    for link in root.find_all("a", href=True):
        href = urljoin(source_url, link["href"])
        if urlparse(href).scheme in {"http", "https"}:
            label = link.get_text(" ", strip=True)
            link.replace_with(f"[{label}]({href})" if label else "")
    lines = []
    # Only block leaves; tables serialized once so header/cell relations survive.
    for tag in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre"]):
        if tag.find_parent(["table", "pre"]):
            continue
        if tag.name == "li":
            clone = BeautifulSoup(str(tag), "html.parser").li
            for nested in clone.find_all(["ul", "ol"]):
                nested.decompose()
            text = clone.get_text(" ", strip=True)
        elif tag.name == "p" and tag.find_parent("li"):
            continue
        elif tag.name == "table":
            rows = []
            for tr in tag.find_all("tr"):
                cells = [c.get_text(" ", strip=True).replace("|", "\\|") for c in tr.find_all(["th", "td"])]
                if cells:
                    rows.append("| " + " | ".join(cells) + " |")
            text = "\n".join(rows)
        else:
            text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if tag.name.startswith("h") and len(tag.name) == 2:
            text = "#" * int(tag.name[1]) + " " + text
        elif tag.name == "li":
            text = "- " + text
        lines.append(text)
    text = "\n\n".join(lines).strip() + "\n"
    if len(text) < 80:
        raise ValueError("Extracted content too short")
    return title, text


def fetch(config_path, root=".", session=None):
    root = Path(root)
    cfg = read_config(config_path)
    sources = cfg["sources"]
    if any(not isinstance(s["doc_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", s["doc_id"]) for s in sources):
        raise ValueError("Source doc_id must be a safe ASCII identifier")
    allowed = {s["url"] for s in sources}
    if len(allowed) != len(sources) or len({s["doc_id"] for s in sources}) != len(sources):
        raise ValueError("Duplicate source or doc_id")
    if any(urlparse(u).scheme != "https" or urlparse(u).hostname != "handbook.gitlab.com" for u in allowed):
        raise ValueError("Whitelist must use HTTPS GitLab Handbook URLs")
    session = session or requests.Session()
    rows, failures = [], []
    for source in sources:
        row = dict.fromkeys(FIELDS, "")
        row.update(doc_id=source["doc_id"], source_url=source["url"], topic=source["topic"],
                   language="en", license_status=cfg["license_status"], included="false", synthetic="false",
                   scope_notes="Public snapshot only; linked internal resources unavailable; policy freshness unverified")
        try:
            url = source["url"]
            for _ in range(4):
                response = session.get(url, timeout=cfg["timeout_seconds"], allow_redirects=False,
                                       headers={"User-Agent": "IndependentKnowledgeStudy/0.1 (fixed whitelist)"})
                if response.status_code in {301, 302, 303, 307, 308}:
                    target = urljoin(url, response.headers.get("Location", ""))
                    if target not in allowed:
                        raise ValueError("Redirect outside fixed whitelist")
                    url = target
                    continue
                response.raise_for_status()
                break
            else:
                raise ValueError("Too many redirects")
            if "text/html" not in response.headers.get("Content-Type", ""):
                raise ValueError("Expected HTML")
            if len(response.content) > cfg["max_bytes"]:
                raise ValueError("Response exceeds size limit")
            row["fetched_at_utc"] = utc_now()
            row["raw_path"] = f"data/raw/{source['doc_id']}.html"
            local_path(root, row["raw_path"]).parent.mkdir(parents=True, exist_ok=True)
            local_path(root, row["raw_path"]).write_bytes(response.content)
            row["raw_sha256"] = digest(response.content)
            title, text = clean_html(response.content, url)
            row.update(title=title, source_url=url, local_path=f"data/processed/{source['doc_id']}.md",
                       content_sha256=digest(text), included="true", source_status="http_200")
            local_path(root, row["local_path"]).parent.mkdir(parents=True, exist_ok=True)
            local_path(root, row["local_path"]).write_text(text, encoding="utf-8")
        except (requests.RequestException, ValueError) as exc:
            # Don't persist exception text (may contain headers/credentials).
            reason = str(exc) if isinstance(exc, ValueError) else (f"http_{exc.response.status_code}" if isinstance(exc, requests.HTTPError) and exc.response is not None else type(exc).__name__)
            row.update(source_status="failed", exclusion_reason=reason)
            failures.append({"doc_id": source["doc_id"], "url": source["url"], "reason": reason})
        rows.append(row)
        time.sleep(cfg.get("delay_seconds", 1))
    save_manifest(root / "data/manifests/documents.csv", rows)
    write_json(root / "data/manifests/fetch-log.json", {"at": utc_now(), "whitelist_sha256": canonical_hash(cfg),
                "license_reference": cfg["license_reference"], "failures": failures, "successes": len(rows)-len(failures)})
    return rows


def chunk_text(text, doc, max_chars=2400, overlap_chars=200):
    if max_chars < 100 or not 0 <= overlap_chars < max_chars // 2:
        raise ValueError("Invalid character chunk parameters")
    chunks, section, buffer = [], [], []
    def emit():
        if buffer:
            content = "\n\n".join(buffer)
            chunks.append({"doc_id": doc["doc_id"], "chunk_id": f"{doc['doc_id']}-{len(chunks)+1:04}",
                           "title": doc["title"], "source_url": doc["source_url"], "topic": doc["topic"],
                           "section_path": " / ".join(section), "scope_notes": doc["scope_notes"],
                           "synthetic": doc.get("synthetic") == "true", "content": content,
                           "content_sha256": digest(content)})
    for block in text.split("\n\n"):
        heading = re.match(r"^(#{1,6}) (.+)", block)
        if heading:
            emit(); buffer = []
            level = len(heading[1])
            section = section[:level-1] + [heading[2]]
        # Preserve whole paragraphs/procedures even if longer than target.
        if buffer and sum(len(x)+2 for x in buffer)+len(block) > max_chars:
            previous = buffer[-1]
            emit()
            buffer = [previous] if len(previous) <= overlap_chars else []
        if block.strip():
            buffer.append(block.strip())
    emit()
    return chunks


def prepare(manifest_path, root=".", max_chars=2400, overlap_chars=200):
    root = Path(root)
    rows = load_manifest(manifest_path)
    chunks = []
    included = [r for r in rows if r["included"] == "true"]
    if not included:
        raise ValueError("No included documents")
    for row in included:
        text = local_path(root, row["local_path"]).read_text(encoding="utf-8")
        if digest(text) != row["content_sha256"]:
            raise ValueError(f"Hash mismatch: {row['doc_id']}")
        chunks.extend(chunk_text(text, row, max_chars, overlap_chars))
    meta = {"format_version": 2, "documents": included, "chunking": {"unit": "characters", "target_max": max_chars,
             "overlap_max": overlap_chars, "oversized_paragraphs": "preserved"}, "chunks_sha256": canonical_hash(chunks)}
    snapshot_id = "snapshot-" + canonical_hash(meta)[:16]
    snapshot_dir = root / "data/snapshots" / snapshot_id
    if snapshot_dir.exists():
        existing = json.loads((snapshot_dir / "snapshot.json").read_text(encoding="utf-8"))
        if existing.get("chunks_sha256") != meta["chunks_sha256"]:
            raise ValueError("Immutable snapshot mismatch")
    else:
        for row in included:
            document_path = snapshot_dir / "documents" / f"{row['doc_id']}.md"
            document_path.parent.mkdir(parents=True, exist_ok=True)
            document_path.write_text(local_path(root, row["local_path"]).read_text(encoding="utf-8"), encoding="utf-8")
            raw = local_path(root, row["raw_path"]).read_bytes()
            if digest(raw) != row["raw_sha256"]:
                raise ValueError(f"Raw hash mismatch: {row['doc_id']}")
            raw_path = snapshot_dir / "raw" / f"{row['doc_id']}.html"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw)
        write_jsonl(snapshot_dir / "chunks.jsonl", chunks)
        write_json(snapshot_dir / "snapshot.json", {**meta, "snapshot_id": snapshot_id, "created_at": utc_now()})
        save_manifest(snapshot_dir / "documents.csv", included)
    write_json(root / "data/manifests/current-snapshot.json", {"snapshot_id": snapshot_id, "path": str(snapshot_dir.as_posix())})
    return snapshot_id, len(chunks)


def validate_data(manifest_path, root="."):
    rows = load_manifest(manifest_path)
    problems = []
    if len({r["doc_id"] for r in rows}) != len(rows):
        problems.append("Duplicate doc_id")
    for row in rows:
        if row["included"] != "true":
            continue
        for field, hash_field in [("raw_path", "raw_sha256"), ("local_path", "content_sha256")]:
            path = local_path(root, row[field])
            content = path.read_bytes() if field == "raw_path" and path.exists() else (path.read_text(encoding="utf-8").encode("utf-8") if path.exists() else b"")
            if not path.exists() or digest(content) != row[hash_field]:
                problems.append(f"{row['doc_id']}: {field} hash mismatch/missing")
        if not row["fetched_at_utc"] or not row["source_url"].startswith("https://handbook.gitlab.com/"):
            problems.append(f"{row['doc_id']}: invalid provenance")
    return {"documents": len(rows), "included": sum(r["included"] == "true" for r in rows), "problems": problems}
