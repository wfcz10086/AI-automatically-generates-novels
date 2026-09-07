"""settings 覆盖层 / critic 解析 / 拆书切分 边界测试。"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from server.settings import _merge, _diff
from server.critic import parse, merge_canon
from server.splitter import split_chapters


class TestSettingsOverlay:
    def test_diff_only_changes(self):
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        d = _diff(base, {"a": {"b": 9, "c": 2}, "d": 3})
        assert d == {"a": {"b": 9}}

    def test_merge_nested(self):
        assert _merge({"a": {"x": 1}}, {"a": {"y": 2}}) == {"a": {"x": 1, "y": 2}}

    def test_diff_empty_when_same(self):
        assert _diff({"k": "v"}, {"k": "v"}) == {}


class TestCriticParse:
    def test_parse_with_noise(self):
        raw = '前言噪音 {"scores":{"a":80,"b":60},"issues":[]} 尾巴'
        d = parse(raw)
        assert d["overall"] == 70

    def test_parse_garbage(self):
        assert parse("完全不是 json") == {}

    def test_canon_merge_dedup(self):
        canon, added = merge_canon([], [{"subject": "甲", "fact": "死亡", "kind": "death"},
                                        {"subject": "甲", "fact": "又死", "kind": "death"}], 5)
        assert added == 1 and len(canon) == 1

    def test_canon_rejects_overlong(self):
        canon, added = merge_canon([], [{"subject": "甲", "fact": "x" * 200}], 1)
        assert added == 0


class TestSplitter:
    def test_split_by_header(self):
        book = "\n".join(f"第{i}章 标题\n" + "正文内容。" * 80 for i in range(1, 5))
        chs = split_chapters(book)
        assert len(chs) == 4 and chs[0]["title"].startswith("第1章")

    def test_fallback_fixed_size(self):
        chs = split_chapters("没有任何章节标记。" * 800)
        assert len(chs) >= 2
