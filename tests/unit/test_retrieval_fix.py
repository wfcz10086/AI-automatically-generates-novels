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


def test_plan_queries_strips_column_labels():
    """模型把列名抄进检索式时要剥掉。

    实测发出去过「检索式：宋刑统 告事不实 反坐…」—— 「检索式：」被当成
    搜索词, 白白拉低召回。
    """
    from server.retrieval import Retriever
    rt = Retriever.__new__(Retriever)
    rt.plan = lambda q: ("刑名|检索式：宋刑统 告事不实 反坐\n"
                         "主题：制度|宋代 进纳授官 纳粟\n"
                         "地理|宋代 东平府 建制")
    rt.era = "北宋"
    rt.facts = {}
    rt.topics = {}
    rt.shared = {}
    got = rt.plan_queries(stage="chapter", context="正文" * 20, k=5)
    qs = [g["query"] for g in got]
    assert qs[0] == "宋刑统 告事不实 反坐", qs
    assert got[1]["topic"] == "制度", got
    assert qs[2] == "宋代 东平府 建制"


def test_plan_queries_strips_boolean_syntax():
    """模型写成搜索引擎高级语法时要剥成朴素关键词。

    实测发出去过: 通判" "知县" "侵越" 或 "通判" "县事" "不得
    —— 博查不吃布尔语法, 引号和「或」全是噪声词, 80 字截断还会把它
    腰斩成半个引号。
    """
    from server.retrieval import Retriever
    rt = Retriever.__new__(Retriever)
    rt.plan = lambda q: '职权|"通判" "知县" "侵越" 或 "通判" "县事"'
    rt.era, rt.facts, rt.topics, rt.shared = "北宋", {}, {}, {}
    got = rt.plan_queries(stage="chapter", context="正文" * 20, k=3)
    q = got[0]["query"]
    assert '"' not in q and "或" not in q.split(), q
    assert "通判" in q and "侵越" in q


def test_plan_queries_shows_existing_topics():
    """已有卡片的主题要摆给规划模型看, 免得同一件事换个名字反复重查。

    实测「阳谷县归哪个州」被存成 15 张卡(阳谷东平东京地理 / 宋代郓州济州
    行政隶属 / 宋代 汴京 阳谷县 …), 考据库按模型自起的主题名做键,
    名字每次不同, `topic in facts` 永远 miss。
    """
    from server.retrieval import Retriever
    rt = Retriever.__new__(Retriever)
    seen = {}
    rt.plan = lambda q: seen.setdefault("p", q) or "地理|宋代 东平府 位置"
    rt.era, rt.shared, rt.topics = "北宋", {}, {}
    rt.facts = {"阳谷东平东京地理": {"card": "北宋东京为开封府…"},
                "宋代郓州济州行政隶属": {"card": "郓州济州均属京东西路…"},
                "没卡片的主题": {}}
    rt.plan_queries(stage="chapter", context="正文" * 20, k=3)
    p_ = seen["p"]
    assert "已经查过" in p_ and "阳谷东平东京地理" in p_
    assert "宋代郓州济州行政隶属" in p_
    assert "没卡片的主题" not in p_, "没卡片的不该算已查过"


def test_search_strips_syntax_at_the_choke_point():
    """清洗要放在 search() 这个咽喉处, 不能只放在某一条生成路径上。

    实测在 plan_queries 里剥过一次引号和布尔词, 查询重写那条路仍然发出了
    `宋代 "寄留" OR "托寄" OR "寄藏" 争讼 案例`。
    """
    from server.providers.search import BaseSearch
    got = BaseSearch.plain('宋代 "寄留" OR "托寄" OR "寄藏" 争讼 案例')
    assert '"' not in got and " OR " not in got
    assert got == "宋代 寄留 托寄 寄藏 争讼 案例", got
    # 正常检索式不能被改坏
    keep = "宋代 仵作 检验不实 杖 徒"
    assert BaseSearch.plain(keep) == keep
