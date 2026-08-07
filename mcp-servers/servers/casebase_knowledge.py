"""mcp-casebase-knowledge：案例入库/检索（spec §9.7 + T4 任务）。

- kb.search/get/upsert：全文+元数据过滤（Phase 1，D-001 #12）；向量接口预留，degraded=fulltext_only。
- kb.badcase_search：badcase 相似案例查询（doc_type=case 且 metadata.fault_layer 标注）。
- kb.holdout_get：holdout 回放集查询（doc_type=probe_pack，metadata.kind=holdout）。
- 写仅案例官（ACL 由网关执行；工具文档声明）。
"""
import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.audit import AuditService  # noqa: E402
from common.config import Settings, get_settings  # noqa: E402
from common.db import session_scope  # noqa: E402
from common.errors import McpError, not_found, validation  # noqa: E402
from common.ids import new_doc_id  # noqa: E402
from common.serverkit import build_server_app  # noqa: E402
from common.tables import CasebaseDoc  # noqa: E402

logger = logging.getLogger(__name__)

mcp = FastMCP("mcp-casebase-knowledge")


def _settings() -> Settings:
    return get_settings()


def _casebase_url() -> str:
    return _settings().resolved_casebase_url


def _tokenize(query: str) -> list[str]:
    return [t for t in re.split(r"\W+", query.lower()) if len(t) >= 2]


def _score(doc: CasebaseDoc, terms: list[str]) -> float:
    """简单 TF 打分（Phase 1 全文）；向量打分 Phase 2 启用。"""
    if not terms:
        return 0.0
    hay = (doc.content or "").lower()
    meta = doc.meta or {}
    hay += " " + " ".join(str(v) for v in meta.values() if isinstance(v, str)).lower()
    hits = 0
    for t in terms:
        hits += hay.count(t)
    return round(min(1.0, hits / max(len(terms), 1) / 3.0), 4)


def _match_filters(doc: CasebaseDoc, filters: Optional[dict[str, Any]]) -> bool:
    if not filters:
        return True
    meta = doc.meta or {}
    if filters.get("doc_type") and doc.doc_type != filters["doc_type"]:
        return False
    if filters.get("fault_layer") and meta.get("fault_layer") != filters["fault_layer"]:
        return False
    if filters.get("app") and meta.get("app") != filters["app"]:
        return False
    return True


def _snippet(content: str, terms: list[str], length: int = 120) -> str:
    if not content:
        return ""
    low = content.lower()
    pos = -1
    for t in terms:
        idx = low.find(t)
        if idx >= 0 and (pos < 0 or idx < pos):
            pos = idx
    if pos < 0:
        return content[:length]
    start = max(0, pos - length // 2)
    snippet = content[start : start + length]
    return ("…" if start > 0 else "") + snippet + ("…" if start + length < len(content) else "")


@mcp.tool(name="kb.search")
def kb_search(
    query: str,
    top_k: int = 5,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """案例检索（ACL：全员）。全文+元数据过滤；向量接口预留 → degraded=fulltext_only。"""
    terms = _tokenize(query)
    with session_scope(_casebase_url()) as session:
        rows = session.scalars(select(CasebaseDoc)).all()
    hits = []
    for doc in rows:
        if not _match_filters(doc, filters):
            continue
        score = _score(doc, terms)
        if score > 0 or not terms:
            hits.append(
                {
                    "doc_id": doc.doc_id,
                    "score": score,
                    "snippet": _snippet(doc.content or "", terms),
                    "metadata": doc.meta or {},
                }
            )
    hits.sort(key=lambda h: h["score"], reverse=True)
    return {
        "hits": hits[: max(1, min(int(top_k), 50))],
        "degraded": "fulltext_only",
        "note": "Phase 1 全文检索；向量检索 Phase 2 启用（D-001 #12）",
    }


@mcp.tool(name="kb.get")
def kb_get(doc_id: str) -> dict[str, Any]:
    """读案例（ACL：全员）：content + metadata + version。"""
    with session_scope(_casebase_url()) as session:
        doc = session.get(CasebaseDoc, doc_id)
        if doc is None:
            raise not_found(f"doc {doc_id} not found")
        return {
            "doc_id": doc.doc_id,
            "doc_type": doc.doc_type,
            "content": doc.content,
            "metadata": doc.meta or {},
            "version": doc.version,
        }


@mcp.tool(name="kb.upsert")
def kb_upsert(
    doc_type: str,
    content: str,
    metadata: Optional[dict[str, Any]] = None,
    idempotency_key: str = "",
    actor: str = "case-officer",
) -> dict[str, Any]:
    """案例入库（ACL：仅案例官）。doc_type∈case|probe_pack|postmortem|skill_candidate；
    idempotency_key 幂等（同键返回首次结果）。"""
    if doc_type not in ("case", "probe_pack", "postmortem", "skill_candidate"):
        raise validation("doc_type must be case|probe_pack|postmortem|skill_candidate")
    if actor != "case-officer":
        raise validation("kb.upsert 仅案例官可写（gateway 层强制 ACL）")

    with session_scope(_casebase_url()) as session:
        if idempotency_key:
            existing = session.scalar(
                select(CasebaseDoc).where(CasebaseDoc.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return {
                    "doc_id": existing.doc_id,
                    "version": existing.version,
                    "indexed": True,
                    "duplicate": True,
                }
        doc_id = new_doc_id()
        doc = CasebaseDoc(
            doc_id=doc_id,
            doc_type=doc_type,
            content=content,
            meta=metadata or {},
            idempotency_key=idempotency_key or None,
            version=1,
        )
        session.add(doc)
        AuditService(session).record(
            actor=actor,
            action="kb.upsert",
            target=doc_id,
            params={"doc_type": doc_type, "idempotency_key": idempotency_key},
            result="success",
        )
    return {"doc_id": doc_id, "version": 1, "indexed": True, "duplicate": False}


@mcp.tool(name="kb.badcase_search")
def kb_badcase_search(
    query: str,
    top_k: int = 5,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """badcase 相似案例查询（ACL：全员）。限定 doc_type=case 且 fault_layer 标注。"""
    flt = dict(filters or {})
    flt["doc_type"] = "case"
    result = kb_search(query, top_k=top_k, filters=flt)
    result["kind"] = "badcase_similar"
    return result


@mcp.tool(name="kb.holdout_get")
def kb_holdout_get(holdout_name: str) -> dict[str, Any]:
    """holdout 回放集查询（ACL：全员）。返回冻结探针回放集（doc_type=probe_pack）。"""
    with session_scope(_casebase_url()) as session:
        rows = session.scalars(
            select(CasebaseDoc).where(CasebaseDoc.doc_type == "probe_pack")
        ).all()
    for doc in rows:
        meta = doc.meta or {}
        if meta.get("kind") == "holdout" and meta.get("name") == holdout_name:
            return {
                "holdout_name": holdout_name,
                "probe_set_digest": meta.get("probe_set_digest"),
                "items": doc.content,
                "metadata": meta,
            }
    raise not_found(f"holdout set {holdout_name} not found")


def main() -> None:
    import uvicorn

    s = _settings()
    uvicorn.run(build_server_app(mcp), host=s.host, port=s.casebase_port, log_level=s.log_level.lower())


if __name__ == "__main__":
    main()
