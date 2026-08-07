"""Phase 1 检索：全文子串 + 元数据过滤（D-001 #12：向量检索 Phase 2 启用）。

实现：对 live KB 全量条目做确定性打分——
- 关键词命中（entry.keywords）权重最高
- 标题/内容子串命中次之
- 元数据：查询含分类触发词时对该分类条目加权（售后/物流/产品型号）
返回 top-k；返回结果同时带「用到的过滤条件」便于 trace。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import KBEntry

# 分类触发词（元数据过滤）：查询命中即对相应分类加权
CATEGORY_TRIGGERS: dict[str, list[str]] = {
    "after_sales": ["退", "换货", "保修", "售后", "激活", "运费谁出", "无理由", "退款", "价格保护", "人工"],
    "logistics": ["物流", "发货", "快递", "配送", "运费", "签收", "单号", "包邮", "自提", "时效", "偏远"],
    "product": ["耳机", "键盘", "充电宝", "手机", "音箱", "鼠标", "平板", "相机", "续航", "降噪", "参数"],
}
# 产品型号直接映射到对应 kb_id 下的条目（keywords 已覆盖，这里仅作提示）
_PRODUCT_MODELS = ["X200", "Y90", "Z30", "A50", "S100", "M40", "T60", "C70"]


@dataclass
class RetrievalHit:
    entry_id: str
    kb_id: str
    category: str
    title: str
    content: str
    score: float
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    hits: list[RetrievalHit]
    filter_applied: dict = field(default_factory=dict)


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def score_entry(entry: KBEntry, q: str) -> tuple[float, list[str]]:
    score = 0.0
    matched: list[str] = []
    text = q

    for kw in entry.keywords or []:
        kw_n = _normalize(kw)
        if kw_n and kw_n in text:
            score += 4.0
            matched.append(kw)

    title_n = _normalize(entry.title or "")
    if title_n and (title_n in text or text in title_n):
        score += 3.0

    content_n = _normalize(entry.content or "")
    # 英文/数字 token 子串命中（如 X200、42dB）
    for tok in re.findall(r"[a-z0-9]+", text):
        if len(tok) >= 2 and tok in content_n:
            score += 1.0

    # 元数据：分类触发词加权
    for cat, triggers in CATEGORY_TRIGGERS.items():
        if any(t in text for t in triggers) and entry.category == cat:
            score += 2.0
            break

    return score, matched


def search_kb(entries: list[KBEntry], query: str, top_k: int = 3, min_score: float = 1.0) -> RetrievalResult:
    q = _normalize(query)
    if not q:
        return RetrievalResult(hits=[], filter_applied={"query": query})

    scored: list[tuple[float, KBEntry, list[str]]] = []
    for e in entries:
        s, matched = score_entry(e, q)
        if s >= min_score:
            scored.append((s, e, matched))

    scored.sort(key=lambda x: (-x[0], x[1].id))
    hits = [
        RetrievalHit(
            entry_id=e.entry_id,
            kb_id=e.kb_id,
            category=e.category,
            title=e.title,
            content=e.content,
            score=s,
            matched_keywords=m,
        )
        for s, e, m in scored[:top_k]
    ]
    filter_applied = {
        "query": query,
        "top_k": top_k,
        "matched_category": next(
            (c for c in CATEGORY_TRIGGERS if any(t in q for t in CATEGORY_TRIGGERS[c])), None
        ),
    }
    return RetrievalResult(hits=hits, filter_applied=filter_applied)
