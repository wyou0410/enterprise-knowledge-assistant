"""Native-specific evaluation binding/freeze gates. Dify gates remain separate."""
import json
from pathlib import Path

from .common import digest, local_path
from .index import index_identity, load_index
from .providers import ChatModel, Embeddings, check_authorization


def native_preflight(config, root=".", require_frozen=False):
    from .evaluation import experiment_state, load_snapshot
    check_authorization()
    # Validate both endpoint/model settings before any paid call.
    ChatModel(config["chat_model"])
    Embeddings(config["embedding"])
    _, rows, chunks = load_snapshot(config["knowledge_snapshot"], root)
    path = local_path(root, config["index_path"])
    meta, _, _ = load_index(path, index_identity(config, chunks))
    binding = {"backend": "native", "knowledge_snapshot": config["knowledge_snapshot"],
        "index_sha256": digest(path.read_bytes()), "payload_sha256": meta["payload_sha256"],
        "document_map": {r["doc_id"]: r["doc_id"] for r in rows}}
    if require_frozen:
        lock = local_path(root, f"evaluations/frozen/{config['version']}.json")
        if not config.get("frozen") or not lock.exists():
            raise ValueError("Native holdout requires human review and frozen configuration")
        saved = json.loads(lock.read_text(encoding="utf-8"))
        if saved.get("experiment") != experiment_state(config, binding, root):
            raise ValueError("Native frozen experiment changed")
    return binding
