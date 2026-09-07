"""提示词引擎 / 编译器 / 评估器边界测试。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import pytest
from server.prompt_engine import render, budget, est_tokens
from server.prompt_compiler import to_plot_list, compile_chapter_prompt, cast_block
from server.evaluator import audit, book_audit, window_audit


class TestRender:
    def test_global_replace(self):
        """老版 .replace 只换首个 —— 这是当年重构的起因，必须锁死。"""
        out = render("A:${x} B:${x} C:${x}", {"x": "v"})
        assert out.count("v") == 3

    def test_missing_var_empty_not_crash(self):
        assert render("有${nope}没有", {}) == "有没有"

    def test_strict_raises(self):
        with pytest.raises(KeyError):
            render("${nope}", {}, strict=True)

    def test_budget_priority(self):
        out = budget([("hi", "重" * 5000, 1), ("lo", "轻" * 5000, 9)], 3000)
        assert len(out["hi"]) > len(out["lo"])


class TestCompiler:
    def test_plot_quota_matches_words(self):
        """10 条剧情写 2600 字必超 —— 条数必须随字数配额压缩。"""
        ol = "\n".join(f"剧情{i}：事件{i}发生了" for i in range(1, 11))
        p = compile_chapter_prompt(title="t", index=1, target_words=2600,
                                   genre_line="", manner="", background="",
                                   world_digest="", roster=[], protagonist="",
                                   relations="", mainline="", chapter_outline=ol,
                                   positive=[], negative=[], block_words=500)
        import re
        n = len(re.findall(r"^剧情\d+：", p, re.M))
        assert n <= 7, f"2600 字却给了 {n} 条剧情"

    def test_absent_mark(self):
        roster = [{"name": "甲", "card": "身份：主角"},
                  {"name": "乙", "card": "身份：路人"}]
        block = cast_block(roster, "本章只有甲出场", protagonist="甲")
        assert "[本章未出现]乙" in block
        assert "[本章未出现]甲" not in block

    def test_plot_list_from_fields(self):
        ol = "第7章 灵堂\n核心事件：对质；罢手。\n爽点：反杀"
        assert len(to_plot_list(ol)) >= 2


class TestAudit:
    def test_dialogue_curly_quotes(self):
        """中文弯引号识别 —— 曾因源码引号被规范化而恒为 0。"""
        t = "他说：“你来了。”她答：“嗯。”" * 30
        r = audit(t)
        assert r["stats"]["dialog_ratio"] > 0.1

    def test_short_text_rejected(self):
        assert audit("太短")["score"] == 0

    def test_tic_variants_caught(self):
        t = ("他瞳孔骤缩，随即冷笑。" + "正常叙述。" * 10) * 8
        types = [i["type"] for i in audit(t)["issues"]]
        assert "小说套话" in types

    def test_hard_blacklist_hits(self):
        t = "大宋的规矩就是这样。" + "正常内容。" * 40
        types = [i["type"] for i in audit(t, extra_blacklist=["大宋"])["issues"]]
        assert "题材黑名单" in types

    def test_env_opening_flagged(self):
        t = ("日光灯管滋滋作响，光线忽明忽暗，空气里弥漫着灰尘的味道，"
             "墙壁斑驳，窗外风声呼啸不止。") + "后续正文。" * 60
        types = [i["type"] for i in audit(t)["issues"]]
        assert "环境铺陈开篇" in types

    def test_dialogue_opening_not_flagged(self):
        t = "“滚出去。”他头也没抬。" + "后续正文。" * 60
        types = [i["type"] for i in audit(t)["issues"]]
        assert "环境铺陈开篇" not in types


class TestBookAudit:
    CH = {i: ("“对白。”他做了事。" + f"第{i}章内容各不相同标记{i}。" * 40)
          for i in range(1, 8)}

    def test_forbidden_terms_counted(self):
        ch = dict(self.CH); ch[3] = ch[3] + "大宋律法如此。"
        r = book_audit(ch, forbidden_terms=["大宋"])
        assert any(i["type"] == "禁用术语出现" for i in r["issues"])

    def test_tic_density_not_absolute(self):
        """口癖判密度不判绝对次数 —— 54 万字 119 次曾被误判 HIGH。"""
        big = {i: "冷笑。" + "正常的内容写满很多字。" * 400 for i in range(1, 4)}
        r = book_audit(big)
        assert not any(i["type"] == "口癖泛滥" and i["level"] == "high"
                       for i in r["issues"])

    def test_window_needs_center(self):
        assert "error" in window_audit(self.CH, 99)
