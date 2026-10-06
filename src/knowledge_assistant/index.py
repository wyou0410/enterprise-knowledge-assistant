"""Content-addressed embedding cache and verified local SQLite index."""
import json
import math
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .common import canonical_hash, digest, local_path, utc_now
from .providers import Embeddings, check_authorization, validate_vector


def embedding_identity(config):
    return {k: config[k] for k in ("base_url", "model", "dimensions", "max_input_chars")}


def index_identity(config, chunks):
    return {"snapshot": config["knowledge_snapshot"], "chunks_sha256": canonical_hash(chunks),
            "embedding": embedding_identity(config["embedding"])}


def write_index(path, chunks, vectors, identity):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not chunks or len(chunks) != len(vectors) or len({c["chunk_id"] for c in chunks}) != len(chunks):
        raise ValueError("Invalid or duplicate index chunks")
    for v in vectors:
        validate_vector(v, identity["embedding"]["dimensions"])
    meta = {"format": 1, "identity": identity, "payload_sha256": canonical_hash([chunks, vectors]),
            "built_at": utc_now(), "chunks": len(chunks)}
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".sqlite")
    os.close(fd)
    try:
        with closing(sqlite3.connect(temp)) as db, db:
            db.execute("CREATE TABLE meta (value TEXT NOT NULL)")
            db.execute("INSERT INTO meta VALUES (?)", (json.dumps(meta),))
            db.execute("CREATE TABLE chunks (position INTEGER PRIMARY KEY, chunk TEXT NOT NULL, vector TEXT NOT NULL)")
            db.executemany("INSERT INTO chunks VALUES (?,?,?)",
                [(i, json.dumps(c, ensure_ascii=False), json.dumps(v)) for i, (c, v) in enumerate(zip(chunks, vectors))])
        os.replace(temp, path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()
    return meta


def load_index(path, expected=None):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("Local index missing; run build-index after configuring authorized providers")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            meta = json.loads(db.execute("SELECT value FROM meta").fetchone()[0])
            payload = db.execute("SELECT chunk,vector FROM chunks ORDER BY position").fetchall()
        chunks, vectors = [json.loads(p[0]) for p in payload], [json.loads(p[1]) for p in payload]
        if meta["format"] != 1 or meta["chunks"] != len(chunks) or not chunks:
            raise ValueError("Index schema mismatch")
        if expected is not None and meta["identity"] != expected:
            raise ValueError("Stale index: snapshot/embedding configuration changed; rebuild")
        if canonical_hash([chunks, vectors]) != meta["payload_sha256"]:
            raise ValueError("Index payload hash mismatch")
        if meta["identity"].get("chunks_sha256") and canonical_hash(chunks) != meta["identity"]["chunks_sha256"]:
            raise ValueError("Index source chunks differ from snapshot")
        for c, v in zip(chunks, vectors):
            if digest(c["content"]) != c["content_sha256"]:
                raise ValueError("Index content hash mismatch")
            validate_vector(v, meta["identity"]["embedding"]["dimensions"])
        return meta, chunks, vectors
    except (sqlite3.Error, KeyError, TypeError, IndexError, json.JSONDecodeError):
        raise ValueError("Invalid local index") from None


def build_index(config, root=".", embedder=None):
    from .evaluation import load_snapshot
    check_authorization(embedding_only=True)
    root = Path(root)
    _, _, chunks = load_snapshot(config["knowledge_snapshot"], root)
    ec = config["embedding"]
    if any(len(c["content"]) > ec["max_input_chars"] for c in chunks):
        raise ValueError("Snapshot contains oversized embedding inputs; prepare a separate snapshot, never truncate")
    identity = index_identity(config, chunks)
    embedder = embedder or Embeddings(ec)
    cache_path = local_path(root, "data/native/embedding-cache.sqlite")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    vectors, usages, requested, reused = [], [], 0, 0
    batch_size = ec.get("batch_size", 8)
    if type(batch_size) is not int or not 1 <= batch_size <= 10:
        raise ValueError("Embedding batch_size must be 1–10")
    # Commit each known completed batch; an ambiguous failed request is not repeated.
    with closing(sqlite3.connect(cache_path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, vector TEXT NOT NULL)")
        keys, found, missing = [], {}, {}
        for chunk in chunks:
            key = canonical_hash([embedding_identity(ec), chunk["content_sha256"]])
            keys.append(key)
            cached = db.execute("SELECT vector FROM cache WHERE key=?", (key,)).fetchone()
            if cached:
                found[key] = validate_vector(json.loads(cached[0]), ec["dimensions"])
                reused += 1
            else:
                missing[key] = chunk["content"]
        pending = list(missing.items())
        for start in range(0, len(pending), batch_size):
            items = pending[start:start+batch_size]
            batch, usage = embedder.embed([content for _, content in items])
            if len(batch) != len(items):
                raise ValueError("Embedding batch length mismatch")
            for (key, _), vector in zip(items, batch):
                vector = validate_vector(vector, ec["dimensions"])
                db.execute("INSERT INTO cache VALUES (?,?)", (key, json.dumps(vector)))
                found[key] = vector
            db.commit()
            requested += 1
            usages.append(usage)
            print(f"Embedding batches completed: {requested}; new chunks: {min(start+batch_size,len(pending))}/{len(pending)}", flush=True)
        vectors = [found[key] for key in keys]
    path = local_path(root, config["index_path"])
    meta = write_index(path, chunks, vectors, identity)
    return {"index": str(path), "index_sha256": digest(path.read_bytes()), "metadata": meta,
            "requests": requested, "embedded_chunks": len(pending), "cache_hits": reused, "usage": usages, "cost": "unknown", "mode": "live"}


def rank(chunks, vectors, query, top_k=3):
    validate_vector(query, len(vectors[0]))
    def cosine(v):
        return sum(a*b for a, b in zip(v, query)) / (math.sqrt(sum(a*a for a in v)) * math.sqrt(sum(b*b for b in query)))
    scored = [(cosine(v), c) for c, v in zip(chunks, vectors)]
    return [{**c, "score": score} for score, c in sorted(scored, key=lambda p: (-p[0], p[1]["chunk_id"]))[:top_k]]
