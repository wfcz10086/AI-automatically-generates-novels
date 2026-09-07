"""四个内容类型都必须真的跑得起来、且用自己的格式规范。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
ROOT = Path(__file__).resolve().parents[2]

TYPES = ["novel", "screenplay", "shortdrama", "anime"]


def _pack(tid):
    return json.loads((ROOT / "packs" / "type" / f"{tid}.json").read_text(encoding="utf-8"))


def test_every_type_has_full_level_prompts():
    """每一层都要有提示词 —— 缺一层, 那一步就没法生成。"""
    for tid in TYPES:
        d = _pack(tid)
        assert d["levels"], f"{tid} 没有层级"
        for lv in d["levels"]:
            assert lv.get("prompt", "").strip(), f"{tid}.{lv['id']} 缺提示词"


def test_final_level_not_hardcoded():
    """成品层必须靠「最后一层」取, 不能写死 id 白名单。

    原代码写死 ("content","page","shot"), 动漫的 storyboard 不在里面,
    step_chapter 直接 IndexError —— 四个类型里有一个从来跑不起来。
    """
    finals = {tid: _pack(tid)["levels"][-1]["id"] for tid in TYPES}
    assert finals == {"novel": "content", "screenplay": "page",
                      "shortdrama": "shot", "anime": "storyboard"}
    assert "storyboard" not in ("content", "page", "shot")


def test_type_prompt_vars_are_all_fillable():
    """类型包引用的变量, 引擎必须都能提供, 否则格式规范里是一片空白。"""
    import re
    from server.settings import load
    supplied = {
        "title", "target_chapters", "target_words", "genre_rules", "style_rules",
        "common_rules", "cliche_blacklist", "style", "kb", "roles", "artstyle",
        "logline", "theme", "scenes", "hook", "premise", "background",
        "relationships", "world_bible", "characters", "outline", "chapter_outline",
        "prev_summary", "index", "range", "parent", "parent_title", "story",
        "series", "act", "scene", "episode", "volume", "count",
    }
    load()
    for tid in TYPES:
        for lv in _pack(tid)["levels"]:
            used = set(re.findall(r"\$\{(\w+)\}", lv.get("prompt", "")))
            missing = used - supplied
            assert not missing, f"{tid}.{lv['id']} 用了引擎不提供的变量: {missing}"


def test_default_style_declared():
    """新建项目要能自动选上配套文风, 不让用户猜。"""
    for tid in ["novel", "shortdrama", "anime"]:
        d = _pack(tid)
        sid = d.get("defaultStyle")
        assert sid, f"{tid} 没声明 defaultStyle"
        assert (ROOT / "packs" / "style" / f"{sid}.json").exists(), \
            f"{tid} 的 defaultStyle={sid} 文风包不存在"


def test_paragraph_chars_declared():
    """段落长度是文风属性: 番茄 15-30 字的极短段 vs 起点历史可到 60 字。"""
    fq = json.loads((ROOT / "packs" / "style" / "fanqie-shuangwen.json")
                    .read_text(encoding="utf-8"))
    assert fq["paragraphChars"] == [15, 30]


def test_org_and_identity_ledgers_exist():
    """势力台账与身份变更台账必须真的影响生成。

    角色档案是开书时写死的：写到 125 章主角已统兵一方，注入的卡片还写着
    「未入流抄写吏」，模型照着过时的卡写人物就往回缩。组织/门派/军团同理，
    一直没人记账，同一个门派的掌事者和实力在不同章节各写各的。
    """
    import inspect
    from server.orchestrator import Novelist
    src = inspect.getsource(Novelist)
    assert "def live_roster" in src, "缺少带当前身份的花名册"
    assert "身份变更" in src and "势力变动" in src, "抽取环节没问这两件事"
    assert "【势力现状·不得与此冲突】" in src, "势力台账没进约束层"
    # cast_for 必须用 live_roster 而不是原始 roster
    cast = inspect.getsource(Novelist.cast_for)
    assert "self.live_roster()" in cast, "角色卡注入仍在用静态档案"


def test_research_topics_accepts_both_shapes():
    """题材包的 research_topics 有两种写法，框架都要吃得下。

    旧包用 dict 按阶段分，新写的包常用 list 简写。原来只认 dict，遇到 list
    直接 AttributeError，整个背景落地被静默跳过，报错还是
    「'list' object has no attribute 'get'」这种看不出所以然的信息。
    """
    import inspect
    from server.retrieval import Retriever
    src = inspect.getsource(Retriever.ground)
    assert "isinstance(t, list)" in src and "isinstance(t, dict)" in src
    shapes = set()
    for p in (ROOT / "packs" / "genre").glob("*.json"):
        rt = json.loads(p.read_text(encoding="utf-8")).get("research_topics")
        if rt is not None:
            shapes.add(type(rt).__name__)
    assert shapes <= {"list", "dict"}, f"出现了没处理的形状：{shapes}"


def test_outline_scale_is_corrected(tmp_path):
    """总纲里模型自编的体量数字要被纠正。

    提示词明明传了「全书 231 章 / 约 60 万字」，模型照样在抬头写
    「体量：约 180 万字」—— 这个数字会被分卷与爽点节奏表当依据，一错全错。
    """
    import json as _j
    from server.orchestrator import Novelist, Project
    from server.settings import load

    class _P(Project):
        def __init__(self):
            self.dir = tmp_path
            self.meta = {"title": "t", "type_id": "novel", "genre_id": "lishi",
                         "style_id": "qidian-lishi", "target_chapters": 231,
                         "target_words": 600000, "fields": {}}
            self.state = {"done": []}
            self.cfg = load()

        def read(self, n):
            return ""

        def write(self, n, t):
            pass

        def _load(self, n, d=None):
            return d

    nv = Novelist(_P())
    out = nv.fix_scale("**体量**：约 180 万字 | 全书 500 章，节奏见下")
    assert "60 万字" in out and "180 万字" not in out
    assert "全书 231 章" in out


def test_severe_word_shortfall_is_high_severity():
    """只写到目标 47% 的章不能还给 87 分。

    原来字数偏离一律 mid（扣 7 分），于是短了一半的章照样过合格线，
    重写闸门根本不触发 —— 90 万字的书这样每章少写一半，最后只能靠硬凑
    章数补，节奏全垮。
    """
    from server.evaluator import audit
    text = "他把算盘放下。\n\n" * 60          # 约 500 字
    r = audit(text, target_words=2600)
    types = [i["type"] for i in r["issues"]]
    assert "字数严重偏离" in types
    sev = [i["level"] for i in r["issues"] if i["type"] == "字数严重偏离"][0]
    assert sev == "high"


def test_expand_gate_and_delivery_rate_exist():
    """偏短要扩写（不是重写），并按实际交付率抬高要价。

    换网关后实测连续 5 章只写到 47-74%，提示词里写 2600 也没用。
    重写会把写好的内容弄丢，所以偏短走「扩写」；同时把要价抬到
    目标/交付率，模型稳定少写三成就要三成回来。
    """
    import inspect
    from server.orchestrator import Novelist
    src = inspect.getsource(Novelist)
    assert "def delivery_rate" in src, "缺少交付率统计"
    aw = inspect.getsource(Novelist.ask_words)
    assert "delivery_rate()" in aw, "要价没按交付率补偿"
    tw = inspect.getsource(Novelist.target_words)
    assert "delivery_rate()" not in tw, \
        "验收线不能随补偿浮动 —— 否则模型老实交了 2600 字反被判严重偏离"
    ch = inspect.getsource(Novelist.step_chapter)
    assert "不改变任何已有情节与结局" in ch, "扩写指令缺少「只补量不改剧情」约束"
    assert 'a["target_words"] = target' in ch, "audit 没存目标字数，交付率算不出来"


def test_meta_blocks_are_stripped_from_body():
    """模型附在正文末尾的工作笔记不能进成稿。

    实测扩写稿末尾带出「【本章末状态字段更新】何九叔：已退银、已封骨…」——
    那是给流水线看的，混进成稿会直接印到书里。
    """
    from server.orchestrator import clean
    body = "他吹熄灯，还剩三日。"
    for tail in ("\n\n---\n\n【本章末状态字段更新】\n何九叔：已退银。\n郓哥：受雇。",
                 "\n\n【伏笔登记】\n1. 骨殖火漆",
                 "\n\n【字数统计】本章 2613 字"):
        assert clean(body + tail) == body, f"未清除：{tail[:20]}"


def test_place_name_prefix_not_glued():
    """地名前缀不能硬取两字。

    「孟州」只有一个前缀字，`[一-鿿]{2}(?:州|县…)` 会把它匹配成「在孟州」
    「去孟州」「回孟州」三个不同地名 —— 同一个地方自己跟自己抢主场，
    实测硬生生报出「主场地点漂移」，全书体检白扣 9 分。
    """
    from server.evaluator import book_audit
    chs = {i: f"他在孟州住下。第二日去孟州城南，傍晚回孟州。孟州的雨没停。第{i}章。"
           for i in range(1, 6)}
    r = book_audit(chs)
    drift = [i for i in r["issues"] if i["type"] == "主场地点漂移"]
    assert not drift, f"同一个地名被拆成多个：{drift}"


def test_expand_loops_until_floor():
    """扩写要扩到达标为止，不是只扩一轮。

    实测第 19 章 1117 → 1727 字仍差 473 字照样落盘，19 章里 8 章卡在地板下 ——
    模型对「缺 1400 字」的响应通常只补一半。
    """
    import inspect
    from server.orchestrator import Novelist
    src = inspect.getsource(Novelist.step_chapter)
    assert "for round_ in (1, 2)" in src, "扩写没有多轮"
    assert "这是第二轮扩写" in src, "第二轮没告诉模型上一轮为何不够"
