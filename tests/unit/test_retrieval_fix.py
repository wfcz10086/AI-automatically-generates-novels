"""检索层修复的回归测试 —— 每条对应一次实测浪费。"""
import sys, re
from pathlib import Path
sys.path.insert(0, "/opt/AI-automatically-generates-novels")
from server.retrieval import Retriever
from server.providers.search import JUNK_HOST


class TestCacheKey:
    def test_synonym_topics_share_key(self):
        """模型每换个说法就绕过缓存, 实测 387 次检索里近百次在重复查同一件事。"""
        ks = {Retriever._key(x) for x in
              ["宋代货币购买力与贯", "宋代 货币购买力 贯", "宋代货币购买力的贯"]}
        assert len(ks) == 1

    def test_different_topics_keep_apart(self):
        assert Retriever._key("宋代盐引") != Retriever._key("宋代茶引")


class TestJunkHost:
    def test_blocks_observed_junk(self):
        """实测一次中文考据检索, bing 返回 10 条里 4 条是这些。"""
        for u in ["https://www.tiktok.com/", "https://jingyan.baidu.com/article/x",
                  "https://www.douyin.com/", "https://item.taobao.com/i.htm"]:
            assert JUNK_HOST.search(u), u

    def test_keeps_real_sources(self):
        for u in ["http://economy.guoxue.com/?p=6960",
                  "https://www.fujian.gov.cn/zwgk/", "https://zh.wikipedia.org/wiki/市舶司"]:
            assert not JUNK_HOST.search(u), u
