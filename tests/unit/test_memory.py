"""记忆体边界测试。"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import pytest
from server.memory import Memory, _bigrams
from server.memory_ctl import MemoryController


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "t.db")


class TestFTS:
    def test_chinese_bigram(self):
        assert "阳谷" in _bigrams("阳谷县药铺")
        assert "县药" in _bigrams("阳谷县药铺")

    def test_search_hit(self, mem):
        mem.add("role", "x", "西门庆", "阳谷县生药铺东家，重生者")
        hits = mem.search("生药铺在哪")
        assert hits and hits[0]["title"] == "西门庆"

    def test_search_empty_query(self, mem):
        assert mem.search("") == [] or isinstance(mem.search(""), list)

    def test_upsert_same_ref(self, mem):
        mem.add("role", "x", "A", "第一版")
        mem.add("role", "x", "A", "第二版内容")
        hits = mem.search("第二版")
        assert any("第二版" in h["text"] for h in hits)
        # 旧版不应残留两份
        assert mem.stats()["role"] == 1

    def test_foreshadow_lifecycle(self, mem):
        mem.add_foreshadow(3, "玉佩来历不明")
        pend = mem.pending_foreshadow()
        assert len(pend) == 1 and pend[0]["planted"] == 3
        mem.resolve_foreshadow(pend[0]["id"], 10)
        assert mem.pending_foreshadow() == []
        assert mem.stats()["foreshadow_resolved"] == 1

    def test_index_document_chunking(self, mem):
        text = "\n".join(f"### 小节{i}\n" + "内容" * 30 for i in range(5))
        n = mem.index_document("world", "wb", text)
        assert n >= 4


class TestMemoryController:
    def kw(self, **over):
        base = dict(outline="细纲", resident="世界观" * 100,
                    recent=[f"第{i}章：事" for i in range(5)],
                    mid=["段摘要" * 50], recall=[], constraints="约束")
        base.update(over)
        return base

    def test_normal_assembly(self):
        r = MemoryController(50000).assemble(**self.kw())
        assert r["report"]["used"] > 0
        assert "世界观" in r["text"]

    def test_zero_budget(self):
        r = MemoryController(0).assemble(**self.kw())
        assert r["report"]["total_budget"] == 0   # 不崩即可

    def test_tiny_budget_keeps_hard_layers(self):
        r = MemoryController(300).assemble(**self.kw())
        layers = {l["key"]: l for l in r["report"]["layers"]}
        assert layers["L0_outline"]["tokens"] > 0    # hard 层永不清空

    def test_recent_drops_oldest_first(self):
        recent = [f"第{i}章：" + "很长的内容" * 40 for i in range(10)]
        r = MemoryController(3000).assemble(**self.kw(recent=recent))
        txt = r["layers"]["L2_recent"]
        assert "第9章" in txt          # 最新保留
        assert "第0章" not in txt      # 最旧被丢

    def test_type_guard_rejects_dicts(self):
        """变量遮蔽把 dict 列表传进来 —— 踩过两次的坑必须有护栏。"""
        with pytest.raises(TypeError, match="List\\[str\\]"):
            MemoryController(5000).assemble(**self.kw(mid=[{"a": 1}]))

    def test_custom_ratio_override(self):
        mc = MemoryController(10000, {"L2_recent": 0.5})
        assert mc.layers["L2_recent"]["ratio"] == 0.5
