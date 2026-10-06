"""Shared bounded conversational core for terminal and evaluation."""
import json
import re
import time
import uuid
from pathlib import Path

from .common import canonical_hash, digest, local_path, utc_now, write_json
from .index import index_identity, load_index, rank
from .providers import ChatModel, Embeddings, ProviderError, check_authorization

DEMO_DOCS = [
    {"doc_id": "DEMO001", "chunk_id": "DEMO001-0001", "title": "MOCK · 虚构入职流程",
     "source_url": "https://example.invalid/onboarding", "content": "原创虚构资料 / Original synthetic policy: New employees complete a checklist and meet their onboarding buddy on their first day. 新员工第一天完成清单，并与入职伙伴见面。"},
    {"doc_id": "DEMO002", "chunk_id": "DEMO002-0001", "title": "MOCK · 虚构设备申请",
     "source_url": "https://example.invalid/equipment", "content": "原创虚构资料 / Original synthetic policy: Employees request a laptop through the IT portal. Contractors contact their sponsor first. This document does not specify delivery times. 员工通过 IT 门户申请电脑；承包商先联系对接人。未规定送达时间。"}]
for _doc in DEMO_DOCS:
    _doc["content_sha256"] = digest(_doc["content"])
DEMO_SNAPSHOT = "synthetic-cli-" + canonical_hash(DEMO_DOCS)[:16]


def compose_messages(prompt, question, retrieved, history, max_chars=24000, history_turns=3):
    if len(question) > 4000 or not question.strip():
        raise ValueError("Question must contain 1–4000 characters")
    kept, dropped = [], []
    bounded = history[-history_turns:] if history_turns else []
    dropped_history = len(history) - len(bounded)
    base = len(prompt) + len(question)
    for c in retrieved:
        block = json.dumps({k: c[k] for k in ("doc_id", "chunk_id", "content")}, ensure_ascii=False)
        if base + len(block) + 100 <= max_chars:
            kept.append(c)
            base += len(block) + 100
        else:
            dropped.append(c["chunk_id"])
    while bounded and base + sum(len(u)+len(a) for u, a in bounded) > max_chars:
        bounded = bounded[1:]
        dropped_history += 1
    if base > max_chars:
        raise ValueError("Current question/system exceeds message budget")
    messages = [{"role": "system", "content": prompt}]
    for user, assistant in bounded:
        messages.extend([{"role": "user", "content": user}, {"role": "assistant", "content": assistant}])
    messages.append({"role": "user", "content": json.dumps({"retrieved_untrusted_documents": [
        {k: c[k] for k in ("doc_id", "chunk_id", "content")} for c in kept], "current_question": question}, ensure_ascii=False)})
    if sum(len(m["content"]) for m in messages) > max_chars:
        raise ValueError("Serialized model messages exceed budget")
    return messages, kept, {"dropped_chunk_ids": dropped, "dropped_history_turns": dropped_history}


def validate_answer(raw, retrieved, snapshot):
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict) or obj.get("status") not in {"answered", "partial", "clarify", "insufficient", "conflict"}:
            raise ValueError()
        ids = obj.get("citation_chunk_ids")
        if not isinstance(obj.get("answer"), str) or not obj["answer"].strip() or not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
            raise ValueError()
        available = {c["chunk_id"]: c for c in retrieved}
        if not set(ids) <= set(available):
            raise ValueError()
        if obj["status"] in {"answered", "partial", "conflict", "clarify"} and not ids:
            raise ValueError()
        clarification = obj.get("clarification_question")
        if clarification is not None and not isinstance(clarification, str):
            raise ValueError()
        if obj["status"] == "clarify" and not clarification:
            raise ValueError()
        if obj["status"] == "conflict" and len({available[i]["doc_id"] for i in ids}) < 2:
            raise ValueError()
        # Model-supplied links must not bypass the fixed provenance mapping.
        if re.search(r"https?://", obj["answer"] + (clarification or ""), re.I):
            raise ValueError()
        citations = [{"doc_id": available[i]["doc_id"], "chunk_id": i,
            "title": available[i].get("title", available[i]["doc_id"]), "source_url": available[i]["source_url"],
            "excerpt": available[i]["content"], "knowledge_snapshot": snapshot, "support_verified": False}
            for i in dict.fromkeys(ids)]
        return {"status": obj["status"], "answer": obj["answer"], "clarification_question": clarification, "citations": citations, "error": None}
    except (ValueError, TypeError, KeyError):
        return {"status": "error", "answer": "引用或回答格式校验失败；请查看本地记录。",
                "citations": [], "clarification_question": None, "error": {"code": "invalid_answer_or_citations", "retryable": False}}


def demo_response(question, previous=""):
    chinese = bool(re.search(r"[\u4e00-\u9fff]", question))
    q = question.lower()
    follow = any(w in q for w in ["那", "它", "呢", "then", "what about", "they"])
    context = q + (" " + previous.lower() if follow else "")
    equipment = any(w in context for w in ["电脑", "设备", "laptop", "equipment"])
    onboarding = any(w in context for w in ["入职", "第一天", "onboard", "first day", "buddy"])
    ids, status, clarification = [], "insufficient", None
    if equipment:
        ids = ["DEMO002-0001"]
        if any(w in q for w in ["多久", "时间", "when", "how long", "delivery"]):
            status = "partial"
            answer = "资料说明申请途径，但未规定送达时间。" if chinese else "The document describes requesting equipment but gives no delivery time."
        elif any(w in q for w in ["承包", "contractor"]):
            status = "answered"
            answer = "承包商先联系对接人。" if chinese else "Contractors contact their sponsor first."
        elif any(w in q for w in ["员工", "employee"]):
            status = "answered"
            answer = "员工通过 IT 门户申请电脑。" if chinese else "Employees request a laptop through the IT portal."
        else:
            status = "clarify"
            answer = "设备申请步骤取决于身份。" if chinese else "Equipment request steps depend on your role."
            clarification = "你是员工还是承包商？" if chinese else "Are you an employee or a contractor?"
    elif any(w in q for w in ["员工", "employee", "承包", "contractor"]) and any(w in previous.lower() for w in ["设备", "电脑", "laptop", "equipment"]):
        return demo_response(question + (" 电脑" if chinese else " laptop"))
    elif onboarding:
        ids, status = ["DEMO001-0001"], "answered"
        answer = "新员工第一天完成清单，并与入职伙伴见面。" if chinese else "On the first day, new employees complete a checklist and meet their onboarding buddy."
    else:
        answer = "演示资料不支持该问题。可试问：入职第一天做什么？员工如何申请电脑？" if chinese else "This demo does not cover that question. Try: What happens on the first day? How does an employee request a laptop?"
    retrieved = [c for c in DEMO_DOCS if c["chunk_id"] in ids]
    return json.dumps({"status": status, "answer": "[MOCK · 原创虚构案例] " + answer,
        "citation_chunk_ids": ids, "clarification_question": clarification}, ensure_ascii=False), retrieved


class ChatSession:
    def __init__(self, mode="demo", config=None, root=".", trace_dir=None, embedder=None, model=None):
        if mode not in {"demo", "live"}:
            raise ValueError("Unknown chat mode")
        self.mode, self.config, self.root = mode, config or {}, Path(root)
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.new()
        if mode == "live":
            check_authorization()
            if (not 1 <= self.config.get("history_turns", 3) <= 10
                    or not 1 <= self.config["retrieval"]["top_k"] <= 10
                    or not 6000 <= self.config.get("max_message_chars", 24000) <= 100000):
                raise ValueError("Invalid bounded history/retrieval/message configuration")
            from .evaluation import load_snapshot
            _, rows, snapshot_chunks = load_snapshot(self.config["knowledge_snapshot"], self.root)
            self.meta, self.chunks, self.vectors = load_index(local_path(root, self.config["index_path"]), index_identity(self.config, snapshot_chunks))
            self.snapshot = self.config["knowledge_snapshot"]
            self.embedder = embedder or Embeddings(self.config["embedding"])
            self.model = model or ChatModel(self.config["chat_model"])
            self.prompt = local_path(root, self.config["prompt"]).read_text(encoding="utf-8")
        else:
            self.snapshot = DEMO_SNAPSHOT

    def new(self):
        self.history, self.last_citations = [], []
        self.conversation_id = uuid.uuid4().hex

    def ask(self, question):
        if not question.strip() or len(question) > 4000:
            raise ValueError("Question must contain 1–4000 characters")
        request_id, start = uuid.uuid4().hex, time.perf_counter()
        trace = {"request_id": request_id, "conversation_id": self.conversation_id,
            "mode": self.mode, "snapshot": self.snapshot, "input": question, "at": utc_now(), "cost": "unknown" if self.mode == "live" else "no API calls"}
        raw, retrieved, usage = None, [], None
        try:
            if self.mode == "demo":
                raw, retrieved = demo_response(question, self.history[-1][0] if self.history else "")
            else:
                query = question + ("\nPrevious user question: " + self.history[-1][0] if self.history else "")
                if len(query) > self.config["embedding"]["max_input_chars"]:
                    query = question
                    trace["query_history_omitted"] = True
                vectors, embedding_usage = self.embedder.embed([query])
                retrieved = rank(self.chunks, self.vectors, vectors[0], self.config["retrieval"]["top_k"])
                messages, sent, budget = compose_messages(self.prompt, question, retrieved, self.history,
                    self.config.get("max_message_chars", 24000), self.config.get("history_turns", 3))
                trace.update(query=query, retrieved_chunks=retrieved, sent_chunks=sent, messages=messages,
                    messages_sha256=canonical_hash(messages), retrieval_sha256=canonical_hash(retrieved), budget=budget,
                    config_sha256=canonical_hash(self.config), index_sha256=digest(local_path(self.root, self.config["index_path"]).read_bytes()), embedding_usage=embedding_usage)
                self._save(request_id, {**trace, "state": "before_model_request"})
                raw, usage = self.model.complete(messages)
                retrieved = sent
            output = validate_answer(raw, retrieved, self.snapshot)
        except ProviderError as exc:
            output = {"status": "error", "answer": "API 调用失败：" + str(exc), "citations": [],
                      "clarification_question": None, "error": {"code": str(exc), "retryable": False}}
        output.update(request_id=request_id, conversation_id=self.conversation_id, mode=self.mode,
            workflow_version=self.config.get("version", "native-demo"), knowledge_snapshot=self.snapshot,
            retrieved_chunks=trace.get("retrieved_chunks") if self.mode == "live" else retrieved, latency_ms=round((time.perf_counter()-start)*1000, 3) if self.mode == "live" else None, usage=usage)
        self.last_citations = output["citations"]
        if output["status"] != "error":
            self.history.append((question, output["answer"]))
            self.history = self.history[-max(1, self.config.get("history_turns", 3)):]
        trace.update(raw_model_answer=raw, output=output, state="completed")
        self._save(request_id, trace)
        return output

    def _save(self, request_id, trace):
        if self.trace_dir:
            write_json(self.trace_dir / f"{request_id}.json", trace)


def terminal(session):
    print(f"Enterprise Knowledge Assistant | {session.mode} | {'MOCK · 原创虚构资料' if session.mode == 'demo' else session.config['chat_model']['model']}")
    print(f"快照：{session.snapshot}\n/help 帮助；/source 1 查看上一轮引用；/new 新对话；/exit 退出")
    try:
        while True:
            try:
                text = input("你 > ").strip()
            except EOFError:
                break
            if not text:
                continue
            if text == "/exit":
                break
            if text == "/help":
                print("/source 1 查看引用；/new 清空会话；/help 帮助；/exit 退出。demo 为 MOCK，live 才调用模型。")
            elif text == "/new":
                session.new()
                print("已开始新对话。")
            elif text.startswith("/source "):
                try:
                    number = int(text.split()[1])
                    if number < 1:
                        raise IndexError()
                    c = session.last_citations[number-1]
                    print(f"{c['title']} [{c['chunk_id']}]\n{c['source_url']}\n快照：{c['knowledge_snapshot']}\n{c['excerpt']}")
                except (ValueError, IndexError):
                    print("引用编号不可用。请先提问，再输入 /source 1。")
            elif text.startswith("/"):
                print("未知命令，输入 /help。")
            else:
                print("处理中…")
                try:
                    answer = session.ask(text)
                except ValueError as exc:
                    print(str(exc))
                    continue
                print(f"助手 [{answer['status']}] > {answer['answer']}")
                if answer.get("clarification_question"):
                    print(answer["clarification_question"])
                for n, c in enumerate(answer["citations"], 1):
                    print(f"[{n}] {c['title']} · {c['chunk_id']} · {c['source_url']}")
    except KeyboardInterrupt:
        if session.trace_dir:
            write_json(session.trace_dir / f"interrupted-{uuid.uuid4().hex}.json", {"mode": session.mode, "at": utc_now(), "state": "interrupted_remote_completion_unknown"})
        print("\n已中断；已发送的远端请求是否完成未知。")
    print("再见。")
    return 0
