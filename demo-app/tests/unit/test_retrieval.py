"""检索打分单元测试（对齐 probes 的检索预期）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.models import KBEntry
from app.retrieval import search_kb


def _entry(entry_id, title, content, keywords, category="product", kb_id="products"):
    return KBEntry(
        entry_id=entry_id, kb_id=kb_id, category=category, title=title,
        content=content, keywords=keywords, version="1.0.0",
        digest=f"sha256:{'a' * 64}",
    )


def _sample_corpus():
    return [
        _entry(
            "x200-earbuds", "X200 蓝牙耳机",
            "X200 真无线蓝牙耳机续航 30 小时，主动降噪深度 42dB，Bluetooth 5.4。",
            ["X200", "蓝牙耳机", "续航", "降噪", "battery", "anc"],
        ),
        _entry(
            "y90-keyboard", "Y90 机械键盘",
            "Y90 机械键盘采用线性红轴，支持热插拔，全键无冲。",
            ["Y90", "键盘", "红轴", "机械键盘"],
        ),
        _entry(
            "ship-within-48h", "发货时效",
            "现货商品下单后 48 小时内发货。",
            ["发货", "48 小时", "时效"],
            category="logistics", kb_id="logistics",
        ),
        _entry(
            "return-policy-7day", "7 天无理由退货",
            "我们支持 7 天无理由退货（自签收次日起算）。",
            ["7 天无理由", "退货"],
            category="after_sales", kb_id="after_sales",
        ),
    ]


def test_probe_cs006_x200_battery():
    hits = search_kb(_sample_corpus(), "X200 蓝牙耳机续航多久？", top_k=3).hits
    assert hits, "应检索到条目"
    assert hits[0].entry_id == "x200-earbuds"
    assert "30 小时" in hits[0].content


def test_probe_cs008_y90():
    hits = search_kb(_sample_corpus(), "Y90 机械键盘用的是什么轴体？").hits
    assert hits[0].entry_id == "y90-keyboard"
    assert "红轴" in hits[0].content


def test_probe_cs013_logistics():
    hits = search_kb(_sample_corpus(), "下单后多久发货？").hits
    assert hits[0].kb_id == "logistics"
    assert "48 小时" in hits[0].content


def test_metadata_filter_after_sales():
    hits = search_kb(_sample_corpus(), "激活后还能退货吗？").hits
    assert hits[0].category == "after_sales"
