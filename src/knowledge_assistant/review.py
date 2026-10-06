"""Local human-review packets and explicit receipts; never auto-mark review."""
import argparse
import html
import json
from pathlib import Path

from .common import canonical_hash, read_jsonl, utc_now, write_json, write_jsonl
from .evaluation import load_snapshot, validate_questions
from .dify import STATUSES

EDIT_FIELDS = ["initial_question", "category", "expected_status", "expected_initial_status", "group_id",
               "follow_up_turns", "reference_answer", "required_facts", "forbidden_claims",
               "evidence_doc_ids", "evidence_sections", "evidence_chunk_ids"]
LIST_FIELDS = {"follow_up_turns", "required_facts", "forbidden_claims", "evidence_doc_ids", "evidence_sections", "evidence_chunk_ids"}


def build(root=".", snapshot="snapshot-8e1c4af63cf9b388"):
    root = Path(root)
    _, rows, chunks = load_snapshot(snapshot, root)
    questions = read_jsonl(root / "evaluations/questions.jsonl")
    validate_questions(questions)
    proposals = json.loads((root / "evaluations/review-proposals.dev.json").read_text(encoding="utf-8"))
    if proposals["knowledge_snapshot"] != snapshot:
        raise ValueError("Proposal snapshot mismatch")
    packets = []
    for split in ["dev", "holdout"]:
        items = []
        for q in questions:
            if q["split"] != split:
                continue
            if q["knowledge_snapshot"] != snapshot:
                raise ValueError("Question snapshot mismatch")
            evidence = json.loads((root / "evaluations/evidence" / (q["question_id"] + ".json")).read_text(encoding="utf-8"))
            # Include complete matching sections and children, rather than the
            # generator's first chunk alone. Exact snapshot chunks are preserved.
            expanded = [c for c in chunks if any(c["doc_id"] == e["doc_id"] and
                        (c["section_path"] == e["section_path"] or c["section_path"].startswith(e["section_path"] + " / "))
                        for e in evidence)]
            candidate = dict(q)
            proposal = proposals["proposals"].get(q["question_id"], {}) if split == "dev" else {}
            focus = proposal.get("review_focus", "核对必要事实、例外、状态及分组。候选答案由助手起草，未经人工裁定。")
            candidate.update({k: v for k, v in proposal.items() if k in EDIT_FIELDS})
            if expanded:
                candidate.update(evidence_doc_ids=sorted({c["doc_id"] for c in expanded}),
                                 evidence_sections=sorted({c["section_path"] for c in expanded}),
                                 evidence_chunk_ids=[c["chunk_id"] for c in expanded])
            if proposal.get("required_facts"):
                candidate["reference_answer"] = " ".join(proposal["required_facts"])
            candidate.update(review_status="draft", reviewer=None, reviewed_at_utc=None)
            items.append({"original": q, "candidate": candidate, "evidence": expanded,
                          "review_focus": focus, "candidate_sha256": canonical_hash(candidate)})
        packet = {"format_version": 1, "snapshot": snapshot, "split": split,
                  "base_questions_sha256": canonical_hash(questions), "items": items,
                  "source_documents": rows,
                  "authorship": "Assistant draft, not human-reviewed; no model evaluation outputs included."}
        packet["packet_sha256"] = canonical_hash(packet)
        folder = root / "evaluations/review"
        write_json(folder / (split + "-packet.json"), packet)
        render(folder / (split + ".html"), packet)
        packets.append({"split": split, "questions": len(items), "packet_sha256": packet["packet_sha256"]})
    reviewed_path = root / "evaluations/questions.reviewed.jsonl"
    reviewed = read_jsonl(reviewed_path) if reviewed_path.exists() else questions
    write_json(root / "reports/review-preparation.json", {"packets": packets,
               "human_reviewed": sum(q["review_status"] == "reviewed" for q in reviewed),
               "reviewed_by_split": {s: sum(q["split"] == s and q["review_status"] == "reviewed" for q in reviewed)
                                     for s in ["dev", "holdout"]},
               "holdout_outputs_exposed": False, "note": "Packets prepare manual review; generating them does not approve any question."})
    return packets


def render(path, packet):
    cards = []
    for item in packet["items"]:
        q = item["candidate"]
        controls = []
        for key in EDIT_FIELDS:
            value = "\n".join(q.get(key, [])) if key in LIST_FIELDS else q.get(key, "")
            controls.append(f'<label>{key}<textarea data-field="{key}">{html.escape(str(value))}</textarea></label>')
        evidence = "".join(f'<details><summary>{html.escape(e["chunk_id"] + " · " + e["section_path"])}</summary>'
                           f'<p><a href="{html.escape(e["source_url"], quote=True)}" target="_blank" rel="noopener noreferrer">原始来源</a> · '
                           f'SHA-256 {e["content_sha256"]}</p><pre>{html.escape(e["content"])}</pre></details>' for e in item["evidence"])
        cards.append(f'<article data-id="{q["question_id"]}"><h2>{q["question_id"]} · {html.escape(q["category"])}</h2>'
                     f'<p class="focus">{html.escape(item["review_focus"])}</p><div class="evidence">{evidence or "没有个人/实时数据证据；请核对快照和能力范围。"}</div>'
                     + "".join(controls) + '<label>审阅决定<select data-decision><option value="pending">待审阅</option>'
                     '<option value="approve">我已核对并批准当前字段</option><option value="reject">需修改，暂不批准</option></select></label>'
                     '<label>人工审阅备注<textarea data-notes></textarea></label></article>')
    payload = json.dumps(packet, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>题目人工审阅</title>
<style>body{font:16px/1.55 system-ui;max-width:980px;margin:32px auto;padding:0 24px;background:#f7f8fa;color:#17202a}article{background:white;border:1px solid #d8dfe6;padding:24px;margin:24px 0;border-radius:12px}h1,h2{line-height:1.3}label{display:block;margin:14px 0}textarea{display:block;box-sizing:border-box;width:100%;min-height:72px;padding:10px;font:14px/1.5 system-ui}input,select,button{font:inherit;padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 system-ui}.focus{background:#fff2cc;padding:12px}.toolbar{position:sticky;top:0;background:#eef5ff;padding:12px;border:1px solid #9ebfe9}details{margin:10px 0}small{overflow-wrap:anywhere}</style>
<h1>题目人工审阅 · SPLIT</h1><p>所有题目仍为草案。仅逐题明确批准才会进入审阅回执。页面不调用模型，不包含留出模型输出，不自动提交或修改项目文件。</p>
<p>请核对证据、必要事实、限制条件、澄清必要性及近似任务分组。事实清单与参考答案由助手起草，不能直接冒认人工裁定。留出集仅做题目审阅，不用于调参。</p>
<small>固定快照 SNAPSHOT · packet SHA-256 PACKETHASH</small>
<div class="toolbar"><label>审阅者姓名/代号 <input id="reviewer" autocomplete="off"></label>
<label><input type="checkbox" id="attestation"> 我实际核对了所批准题目的证据、答案及语义分组，且未利用留出模型输出调参。</label>
<button id="export">导出人工审阅回执</button><span id="status" role="status"></span></div>
CARDS
<script>const packet=PAYLOAD; const listFields=new Set(LISTFIELDS);
document.querySelector('#export').addEventListener('click',()=>{
const name=document.querySelector('#reviewer').value.trim();const att=document.querySelector('#attestation').checked;
if(!name||!att){document.querySelector('#status').textContent='请填写审阅者并确认实际审阅声明。';return;}
const reviews=[];for(const card of document.querySelectorAll('article')){
const decision=card.querySelector('[data-decision]').value;if(decision==='pending')continue;
const fields={};for(const el of card.querySelectorAll('[data-field]')){fields[el.dataset.field]=listFields.has(el.dataset.field)?el.value.split(/\\r?\\n/).map(x=>x.trim()).filter(Boolean):el.value.trim();}
if(decision==='approve'&&(!fields.reference_answer||fields.reference_answer.startsWith('DRAFT')||fields.required_facts.some(x=>x.startsWith('DRAFT'))||!fields.required_facts.length)){document.querySelector('#status').textContent=card.dataset.id+' 尚有草案字段或缺少事实清单。';return;}
reviews.push({question_id:card.dataset.id,decision,fields,notes:card.querySelector('[data-notes]').value.trim()});}
if(!reviews.length){document.querySelector('#status').textContent='没有逐题审阅决定。';return;}
const receipt={format_version:1,packet_sha256:packet.packet_sha256,base_questions_sha256:packet.base_questions_sha256,snapshot:packet.snapshot,split:packet.split,reviewer:name,reviewed_at_utc:new Date().toISOString(),human_attestation:true,reviews};
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(receipt,null,2)+'\\n'],{type:'application/json'}));a.download=packet.split+'-review-receipt.json';a.click();URL.revokeObjectURL(a.href);document.querySelector('#status').textContent='已导出 '+reviews.length+' 项决定；项目题集尚未修改。';
});</script></html>'''
    page = page.replace("SPLIT", packet["split"]).replace("SNAPSHOT", packet["snapshot"]).replace("PACKETHASH", packet["packet_sha256"])
    page = page.replace("CARDS", "\n".join(cards)).replace("PAYLOAD", payload).replace("LISTFIELDS", json.dumps(sorted(LIST_FIELDS)))
    path.write_text(page, encoding="utf-8")


def apply_receipt(receipt_path, packet_path, root=".", output=None):
    root = Path(root)
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    packet = json.loads(Path(packet_path).read_text(encoding="utf-8"))
    unsigned = {k: v for k, v in packet.items() if k != "packet_sha256"}
    if canonical_hash(unsigned) != packet["packet_sha256"]:
        raise ValueError("Review packet changed")
    original = read_jsonl(root / "evaluations/questions.jsonl")
    if receipt.get("base_questions_sha256") != canonical_hash(original) or receipt.get("packet_sha256") != packet["packet_sha256"]:
        raise ValueError("Stale or mismatched review receipt")
    if receipt.get("snapshot") != packet["snapshot"] or receipt.get("split") != packet["split"]:
        raise ValueError("Review split/snapshot mismatch")
    if receipt.get("human_attestation") is not True or not str(receipt.get("reviewer", "")).strip() or not receipt.get("reviewed_at_utc"):
        raise ValueError("Explicit human attestation, reviewer and date required")
    from datetime import datetime
    date = datetime.fromisoformat(receipt["reviewed_at_utc"].replace("Z", "+00:00"))
    if date.tzinfo is None:
        raise ValueError("Review timestamp must have a timezone")
    _, snapshot_rows, snapshot_chunks = load_snapshot(packet["snapshot"], root)
    doc_ids = {r["doc_id"] for r in snapshot_rows}
    chunk_by_id = {c["chunk_id"]: c for c in snapshot_chunks}
    allowed = {x["original"]["question_id"]: x for x in packet["items"]}
    approved, rejected, seen = {}, [], set()
    for review in receipt.get("reviews", []):
        qid = review["question_id"]
        if qid in seen or qid not in allowed:
            raise ValueError("Unknown or duplicate reviewed question")
        seen.add(qid)
        if review["decision"] == "reject":
            rejected.append(qid)
            continue
        if review["decision"] != "approve" or set(review["fields"]) != set(EDIT_FIELDS):
            raise ValueError("Invalid review decision/fields")
        q = dict(allowed[qid]["original"])
        q.update(review["fields"])
        q.update(review_status="reviewed", reviewer=receipt["reviewer"], reviewed_at_utc=receipt["reviewed_at_utc"],
                 review_notes=review.get("notes", ""), review_packet_sha256=packet["packet_sha256"])
        validate_questions([q])
        if q["expected_initial_status"] not in STATUSES or q["category"] not in {"single", "multi", "ambiguous", "unanswerable", "partial", "conflict"}:
            raise ValueError("Invalid reviewed initial status/category")
        if not set(q["evidence_doc_ids"]) <= doc_ids or not set(q["evidence_chunk_ids"]) <= set(chunk_by_id):
            raise ValueError("Reviewed evidence outside fixed snapshot")
        selected = [chunk_by_id[k] for k in q["evidence_chunk_ids"]]
        if {c["doc_id"] for c in selected} != set(q["evidence_doc_ids"]) or {c["section_path"] for c in selected} != set(q["evidence_sections"]):
            raise ValueError("Reviewed evidence document/section/chunk mismatch")
        if not q["required_facts"] or not q["reference_answer"].strip():
            raise ValueError("Approved question must have a fact checklist and reference answer")
        approved[qid] = q
    if not seen:
        raise ValueError("No review decisions")
    destination = Path(output) if output else root / "evaluations/questions.reviewed.jsonl"
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError("Reviewed output outside project")
    if destination.resolve() == (root / "evaluations/questions.jsonl").resolve():
        raise ValueError("Preserve the original draft question set")
    merged = read_jsonl(destination) if destination.exists() else original
    if {q["question_id"] for q in merged} != {q["question_id"] for q in original}:
        raise ValueError("Reviewed output question IDs differ from original")
    merged = [approved.get(q["question_id"], q) for q in merged]
    validate_questions(merged)
    write_jsonl(destination, merged)
    # A separate approved dev subset permits initial human-reviewed smoke without
    # incorrectly claiming that the remaining draft dev/holdout questions passed.
    write_jsonl(root / "evaluations/questions.reviewed-dev-smoke.jsonl", [q for q in merged if q["split"] == "dev" and q["review_status"] == "reviewed"])
    audit = {"packet_sha256": packet["packet_sha256"], "receipt_sha256": canonical_hash(receipt),
             "reviewer": receipt["reviewer"], "reviewed_at_utc": receipt["reviewed_at_utc"],
             "imported_at_utc": utc_now(), "approved": sorted(approved), "rejected": rejected,
             "note": "Receipt records human assertion, not cryptographic identity verification."}
    write_json(root / "evaluations/review/receipts" / (canonical_hash(receipt) + ".json"), receipt)
    write_json(root / "evaluations/review/receipts" / (canonical_hash(receipt) + ".audit.json"), audit)
    preparation_path = root / "reports/review-preparation.json"
    preparation = json.loads(preparation_path.read_text(encoding="utf-8")) if preparation_path.exists() else {}
    preparation.update(human_reviewed=sum(q["review_status"] == "reviewed" for q in merged),
                       reviewed_by_split={s: sum(q["split"] == s and q["review_status"] == "reviewed" for q in merged)
                                          for s in ["dev", "holdout"]},
                       last_receipt_sha256=audit["receipt_sha256"],
                       note="Explicit human assertions imported; this is question review, not model scoring.")
    write_json(preparation_path, preparation)
    return audit


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    a = sub.add_parser("apply")
    a.add_argument("--receipt", required=True)
    a.add_argument("--packet", required=True)
    args = p.parse_args()
    result = build() if args.command == "build" else apply_receipt(args.receipt, args.packet)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
