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


def test_existing_topics_picked_by_relevance():
    """已查主题按相关性挑, 不是按时间取尾巴。

    踩过: 攒到 769 个主题时只给最后 40 个, 而「阳谷县隶属哪个州」是前
    60 章查的, 早滑出窗口 —— 又查了第四遍, 同一件事存成 14 个主题名。
    """
    from server.retrieval import Retriever
    rt = Retriever.__new__(Retriever)
    seen = {}
    rt.plan = lambda q: seen.setdefault("p", q) or "地理|某某"
    rt.era, rt.shared, rt.topics = "北宋", {}, {}
    old = {"阳谷县行政隶属": {"card": "属东平府"}}
    noise = {f"无关主题{i}": {"card": "x"} for i in range(60)}
    rt.facts = {**old, **noise}
    rt.plan_queries(stage="chapter", context="西门庆在阳谷县衙打点行政隶属" * 30, k=3)
    assert "阳谷县行政隶属" in seen["p"], "老主题因为不在尾部就被漏掉了"


def test_cover_hit_uses_bigrams_not_fixed_chunks():
    """按卡片内容找同一件事, 中文要用二字组。

    第一版用 `[\\u4e00-\\u9fff]{2,4}` 切词, 把「北宋阳谷县到东京」切成
    「北宋阳谷」「县到东京」这种不存在的词, 覆盖率永远是 0。
    """
    from server.retrieval import Retriever
    rt = Retriever.__new__(Retriever)
    rt.facts = {
        "宋代仵作验尸流程与洗冤集录": {
            "card": "宋代验尸有报检初检复检免检等程序，验尸格目源于淳熙元年，"
                    "仵作行人常有欺伪舞弊，检验不实致罪出入者一等科罪。"},
        "金国骑兵编制": {"card": "拐子马为两翼骑兵，铁浮屠重甲。"},
    }
    hit = rt._cover_hit("仵作检验格目", "宋代仵作检验格目 初验复验 验尸程序 舞弊")
    assert hit == "宋代仵作验尸流程与洗冤集录", hit
    # 不相干的主题不许被并进来
    assert rt._cover_hit("宋代盐引制度", "宋代盐引 钞引 榷盐 转运 商人") == ""
    # 关键词太少不判, 免得瞎并
    assert rt._cover_hit("验尸", "验尸") == ""


def test_plan_queries_dedupes_within_one_round():
    """同一轮规划里的重复检索式要去掉。

    实测一次吐出四条全是榷场: 禁约私贩越界 / 燕京贸易 / 禁榷管理制度 /
    地点分布。跨批去重管不到 —— 它们出自同一次调用。
    """
    from server.retrieval import Retriever
    rt = Retriever.__new__(Retriever)
    rt.plan = lambda q: ("边贸|宋代 榷场 禁约 私贩 越界\n"
                         "边贸|宋代 榷场 私贩 越界 禁约\n"
                         "地点|宋金 榷场 地点 分布\n"
                         "药材|宋代 生药铺 药材 进货 渠道")
    rt.era, rt.facts, rt.topics, rt.shared = "北宋", {}, {}, {}
    got = [g["query"] for g in rt.plan_queries(stage="chapter",
                                               context="正文" * 30, k=6)]
    assert len(got) == 3, got          # 第二条被去掉
    assert "宋代 榷场 私贩 越界 禁约" not in got
    assert "宋金 榷场 地点 分布" in got  # 不同侧面要留着


def test_cover_hit_survives_concurrent_mutation():
    """遍历 facts 必须先拍快照 —— 并发抓取会同时改它。

    实测日志: [retrieval]「运河浮尸牵连」查失败: dictionary changed size
    during iteration。4 个 worker 同时改 self.facts, 而 _cover_hit 直接
    遍历, 整条检索白花。
    """
    from server.retrieval import Retriever
    rt = Retriever.__new__(Retriever)

    class Racy(dict):
        def items(self):                     # 模拟遍历途中被改
            out = super().items()
            self[f"新主题{len(self)}"] = {"card": "x"}
            return out

    rt.facts = Racy({f"主题{i}": {"card": "宋代验尸格目初检复检程序"}
                     for i in range(5)})
    # 不许抛 RuntimeError
    rt._cover_hit("仵作检验", "宋代仵作检验格目 初检 复检 程序 验尸")


def test_have_list_ranks_by_relevance_not_name_length():
    """已查主题的排序不能被主题名长度带跑。

    第一版用**单字**重合度排序, 而中文长上下文几乎覆盖所有常用字, 于是
    长名字必然得高分 —— 实测 1003 张卡时前 40 名平均 18 字、全库平均 9.4 字,
    「阳谷至东京里程脚程」排第 411 名进不了窗口, 同一件事查了 26 次。
    """
    from server.retrieval import Retriever

    # 偏置只在**真实规模**的上下文下才现形: 短句里长名字反而重合得少。
    # 这里拼一段覆盖面接近 3500 字正文的文本(常用字基本都出现过)。
    # 内容只谈这一趟行程, 但字面覆盖面要广(长上下文的真实特征):
    # 常用字基本都出现过, 而二字组仍集中在行程这一件事上。
    text = ("西门庆天不亮从阳谷县动身去东平府, 走陆路车马同行, 沿途要过"
            "三处税关, 押的是生药与绸缎两样货, 路上遇雨则耽搁, 快则四五日,"
            "慢则七八日才到得州城。随行的伙计问几时能回, 他只说看府里那位"
            "大人几时肯见。这一路经过的村镇渡口驿铺, 都是他早年跑熟了的, "
            "哪家店钱贵、哪段道难行、哪个关口的吏人好说话, 心里有数。" * 12)
    ctx_bi = Retriever._bigrams(text)
    ctx_ch = set(text)

    short_relevant = "阳谷东平府路程"
    long_irrelevant = "完颜宗翰西路军金军后勤粮草真实供应方式与运输损耗"

    def rel(k):
        b = Retriever._bigrams(k)
        return len(b & ctx_bi) / len(b) if b else 0.0

    # 修好之后: 短而相关的排在长而不相关的前面
    assert rel(short_relevant) > rel(long_irrelevant)
    # 真正要守的性质: 分数必须与主题名长度无关。老写法数的是**绝对重合个数**,
    # 名字越长分越高, 所以把一个不相关的主题名接得更长就能挤进窗口。
    padded = long_irrelevant + "及燕云十六州归附女真部族酋长盟誓誓书"
    assert rel(padded) <= rel(long_irrelevant) + 1e-9, "加长名字不该抬高相关性"
    assert len(set(padded) & ctx_ch) > len(set(long_irrelevant) & ctx_ch), (
        "老写法(未归一化的单字重合)会因为名字变长而给出更高的分")
