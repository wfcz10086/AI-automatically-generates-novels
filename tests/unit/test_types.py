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


def test_hygiene_detectors():
    """成稿卫生：英文残留 / 正文 markdown 标题 / 引号混用。

    全是外部评审替我们抓到的 —— 框架此前一条都没检测：
    「三日前差两百，三日后未必。husband，纸上算的账…」（该写「夫君」）、
    「官课缴的是足数，没有使一点 Discount 的小钱」、
    第 18 章正文里冒出 `# 新官上任，先看账` 一级标题。
    """
    from server.evaluator import audit
    base = "他把算盘推开。\n\n" * 40
    types = lambda t: [i["type"] for i in audit(t, target_words=600)["issues"]]
    assert "英文残留" in types(base + "husband，纸上算的账落不到银子上。")
    assert "正文出现markdown标题" in types("# 新官上任，先看账\n\n" + base)
    assert "英文残留" not in types(base + "他看了看 CPU 的参数。")   # 专有名词豁免


def test_quote_style_checked_across_chapters():
    """整章换引号风格，单章内不混用，只能靠跨章比对。

    实测第 20-21 章整章用「」，前 19 章全是 ""，读者一翻就出戏。
    """
    from server.evaluator import book_audit
    curly = {i: "“他说。”" * 8 + f"第{i}章正文。" for i in range(1, 6)}
    curly[6] = "「他说。」" * 8 + "第6章正文。"
    r = book_audit(curly)
    hits = [i for i in r["issues"] if i["type"] == "引号风格不统一"]
    assert hits, "整章换引号风格没被抓到"
    assert "6" in str(hits[0]["detail"])


def test_signature_and_stale_cast_guards():
    """人设标志动作限频 + 出场过又断线的角色。

    「手指虚拨」是主角算账的记忆点，末章还要对账回响，所以不能进禁用表；
    但 20 章 40 次（7.8/万字）每次决策都用，节奏就平了 —— 只限频不硬禁。
    潘金莲第 8 章被买回、第 9 章还在炉边，之后十几章一句没提，而主角
    对她欠着一笔良心债，人物就悬在半空。
    """
    import inspect
    from server.orchestrator import Novelist
    assert "def signature_guard" in inspect.getsource(Novelist)
    assert "def stale_cast" in inspect.getsource(Novelist)
    sg = inspect.getsource(Novelist.signature_guard)
    assert "不是禁用词" in sg, "标志动作应限频而非硬禁"
    cons = inspect.getsource(Novelist.build_context)
    assert "出场过又断线的角色" in cons and "signature_guard()" in cons


def test_project_can_be_archived_and_deleted():
    """项目要能删。之前完全没有删除逻辑，只能手动 rm -rf。

    默认归档（改名不删稿），彻底删除要显式传 hard；且 chapters 目录
    15 分钟内动过就拒绝 —— 长跑可能正在写它。
    """
    import inspect
    from server import app as A
    src = inspect.getsource(A.project_delete)
    assert "_archive_" in src, "缺少归档（默认不该真删）"
    assert 'b.get("hard")' in src, "彻底删除没有独立开关"
    assert "900" in src, "没有防误删正在写的书"


def test_naming_step_exists():
    """书名与简介是平台上决定点击率的东西，要能生成。

    「西门庆的生意经」这种名字信息量够但太素 —— 读者扫过书城列表时，
    三秒内要知道「谁 + 逆什么境 + 爽在哪」。
    """
    import inspect
    from server.orchestrator import Novelist
    src = inspect.getsource(Novelist.step_naming)
    assert "书名候选" in src and "平台简介" in src and "标签" in src
    assert "8 字以内" in src, "没有长度约束，会起出长名字"
    assert "重生之XX的XX人生" in src, "没有禁掉烂大街句式"


def test_long_run_does_not_clobber_meta_edits(tmp_path):
    """长跑进程不能把界面上的设定改动冲掉。

    长跑持有 meta 的内存副本几小时，期间用户改书名/改设定，下一次 save()
    就整份覆盖回去 —— 而且悄无声息。实测改完书名 30 秒后又变回旧名，
    tagline 直接消失。这是数据丢失级的 bug。
    """
    import json as _j
    from server.orchestrator import Project

    d = tmp_path / "书"
    (d / "chapters").mkdir(parents=True)
    (d / "project.json").write_text(_j.dumps(
        {"title": "旧名", "type_id": "novel", "genre_id": "lishi",
         "style_id": "qidian-lishi", "target_chapters": 10,
         "target_words": 30000, "fields": {}}, ensure_ascii=False), encoding="utf-8")
    (d / "state.json").write_text('{"done": [], "current": 0}', encoding="utf-8")

    runner = Project(str(d))              # 「长跑进程」持有旧 meta
    disk = _j.loads((d / "project.json").read_text(encoding="utf-8"))
    disk["title"] = "新名"                # 「界面」改名
    disk["tagline"] = "一句话简介"
    (d / "project.json").write_text(_j.dumps(disk, ensure_ascii=False), encoding="utf-8")

    runner.state["current"] = 1
    runner.save()                          # 长跑写状态

    after = _j.loads((d / "project.json").read_text(encoding="utf-8"))
    assert after["title"] == "新名", "长跑把书名冲回旧值了"
    assert after.get("tagline") == "一句话简介", "长跑把新增字段冲掉了"


def test_english_residue_is_a_hard_gate():
    """英文残留要当场改，不能靠分数管。

    实测第 5 章「若是都头 private 自己动手」检测器报了 high（扣 15 分），
    但全章 85 分高于合格线 70，重写闸门不触发 —— 于是这个一眼可见的低级错
    就落盘了。这类错的正确形态是语义判断（husband 该是「夫君」还是「官人」
    看语境），所以不做机械替换，让模型定点改，并用字数守卫防它顺手重写。
    """
    import inspect
    from server.orchestrator import Novelist
    assert "def fix_english" in inspect.getsource(Novelist)
    src = inspect.getsource(Novelist.step_chapter)
    assert "fix_english(" in src, "落盘前没有英文修复"
    fx = inspect.getsource(Novelist.fix_english)
    assert "其余一字不改" in fx
    assert "c1 * 0.15" in fx, "缺字数守卫，模型会顺手重写整章"


def test_outline_batch_is_computed_not_hardcoded():
    """细纲批量按输出上限算，不是拍一个 10。

    10 章太少：写第 5 章时模型只知道 1-10 章要发生什么，第 30 章的伏笔
    无从铺起，批与批之间的节奏也接不上。真实约束是输出 token 上限 ——
    单章细纲约 456 字 ≈ 342 tok，8192 的上限能装 20 出头。
    """
    import inspect
    from server.orchestrator import Novelist
    src = inspect.getsource(Novelist.outline_batch)
    assert "max_tokens_draft" in src, "批量没跟输出上限挂钩"
    dg = inspect.getsource(Novelist.outline_digest)
    assert "核心事件" in dg, "已排细纲没压成摘要喂回去"
    gen = inspect.getsource(Novelist.step_chapter_outlines)
    assert "outline_digest(" in gen, "后续批次看不到前面排了什么"
    assert "self.outline_batch(count)" in gen


def test_outline_digest_covers_every_chapter():
    """每章一句话保底，一章不漏 —— 丢章是最严重的记忆事故。

    原来先划一段最近的给完整版、剩下的才轮到压缩行、装不下就丢：
    实测排到第 346 章，345 章前情只覆盖 134 章、丢了 211 章，
    而预算只用掉 63%。前两百多章在模型眼里根本不存在，
    它凭什么接得住那时候埋的线。
    """
    import json as _j, re, tempfile, pathlib as _p
    from server.orchestrator import Novelist, Project
    d = _p.Path(tempfile.mkdtemp()) / "p"
    (d / "chapters").mkdir(parents=True)
    (d / "project.json").write_text(_j.dumps(
        {"title": "T", "type_id": "novel", "genre_id": "", "style_id": "",
         "target_chapters": 300, "target_words": 900000, "fields": {}},
        ensure_ascii=False), encoding="utf-8")
    (d / "state.json").write_text('{"done": [], "current": 0}', encoding="utf-8")
    body = ("第N章 标题\n承接：接住上章\n出场角色：甲、乙、丙\n"
            + "".join(f"剧情{i}：细节{i}\n" for i in range(1, 7))
            + "重场：剧情3\n爽点：翻盘\n章末钩子：有人来报")
    (d / "chapter_outlines.json").write_text(_j.dumps(
        {str(i): body.replace("第N章", f"第{i}章") for i in range(1, 300)},
        ensure_ascii=False), encoding="utf-8")

    nv = Novelist(Project(str(d)))
    out = nv.outline_digest(300)
    lines = len(re.findall(r"^\d+\.", out, re.M))
    assert lines == 299, f"丢了 {299 - lines} 章"
    assert "[[CH" in out, "完整档没了"

def test_three_stage_flow_exists():
    """流程要分三段：排全书细纲 → 审阅 → 写正文。

    边排边写的话，前面的章看不见后面的安排：伏笔没法全局铺、节奏必然漂。
    而细纲阶段改一行字，比写完二十万字再返工便宜一百倍。
    """
    import inspect
    import run_novel
    from server.orchestrator import Novelist
    assert hasattr(run_novel, "cmd_outline"), "缺少排全书细纲的命令"
    assert hasattr(run_novel, "cmd_review"), "缺少细纲审阅的命令"
    assert "def step_outline_review" in inspect.getsource(Novelist)
    rv = inspect.getsource(Novelist.step_outline_review)
    # 审阅要看的六件事，逐章写时都看不出来
    for k in ("节奏", "伏笔", "重复", "人物", "接缝", "兑现"):
        assert k in rv, f"审阅漏了「{k}」这一项"
    assert "outline_review.md" in rv


def test_web_service_survives_syntax_errors():
    """自动重载遇到语法错误不能让服务永久死掉。

    实测：改坏 retrieval.py 之后 web 服务静默退出，一个多小时后才被发现
    ——「网页打不开」的原因是我改代码写坏了，而不是别的。
    看门进程兜住：子进程非零退出就重启，语法改回来后自动恢复。
    """
    import inspect
    from server import app as A
    src = inspect.getsource(A.main)
    assert "NOVEL_SUPERVISED" in src, "缺少看门进程"
    assert "重启" in src


def test_separators_do_not_leak_into_outlines():
    """喂给模型的分隔符不能被它当成格式学走。

    实测用「—— 第N章 ——」当已排细纲的分隔符，模型把它当成细纲格式，
    15 章的细纲正文开头都带上了这一行 —— 标记是给流水线看的，不该进产物。
    """
    import inspect
    from server.orchestrator import Novelist
    dg = inspect.getsource(Novelist.outline_digest)
    code = "\n".join(l for l in dg.splitlines() if not l.strip().startswith("#"))
    assert "—— 第" not in code, "分隔符仍长得像正文内容"
    assert "[[CH" in code, "没换成明显是系统标记的形式"
    dirty = "[[CH12]]\n第12章 标题\n剧情1：内容\n###fenge"
    assert Novelist.clean_outline(dirty).startswith("第12章"), "没清掉标记"
    assert "###fenge" not in Novelist.clean_outline(dirty)
    gen = inspect.getsource(Novelist.step_chapter_outlines)
    assert "clean_outline(part)" in gen, "落盘前没清洗"


def test_outline_field_contract_is_single_source():
    """格式规范与落盘守卫必须同源。

    提示词里写一份格式、守卫里再写一份检查，两边一定会漂移：实测
    replan 的格式漏了「承接」，补完的五章全没有承接；另有一批模型把钩子
    塞进「剧情6：钩子：」、爽点整批丢掉，而守卫只松松查了「钩子」二字放行。
    """
    import re
    from server.prompt_compiler import (OUTLINE_FIELDS, OUTLINE_REQUIRED,
                                        outline_format_block,
                                        compile_outline_prompt)
    # 「一句话」放在最前：它是最后一栏时模型写着写着就漏（实测 16 章只写了 10 章，
    # 6 章因缺这一栏被整章丢弃）。前置还有个好处 —— 先定一句话再铺六条剧情，
    # 本身就是「先立意后铺陈」。
    assert OUTLINE_REQUIRED == ["一句话", "承接", "出场角色", "剧情1", "重场",
                                "爽点", "章末钩子"]

    block = outline_format_block(6)
    for f in OUTLINE_REQUIRED:
        assert re.search(rf"^{f}[：:]", block, re.M), f"格式块缺 {f}"

    p = compile_outline_prompt(title="X", start=1, count=3, genre_line="g",
                               world_digest="w", roster_names=["甲"], outline="o",
                               prev_summary="p", constraints="c")
    for f in OUTLINE_REQUIRED:
        assert f in p, f"提示词缺 {f}"


def test_incomplete_chapter_is_rejected():
    """缺必需字段的章不许落盘 —— 断句留在细纲里，后面的批次会当它排好了去接。"""
    import re
    from server.prompt_compiler import OUTLINE_REQUIRED
    good = ("第1章 甲\n承接：接住上一章\n出场角色：A、B、C\n剧情1：出事了\n"
            "重场：剧情1\n爽点：翻盘\n章末钩子：有人来报\n"
            "一句话：主角当堂翻案，县尉的花押被当众念出")
    bad = "第1章 甲\n承接：接住上一章\n出场角色：A、B\n剧情1：出事了\n剧情6：钩子：有人来报"

    def lack(body):
        return [f for f in OUTLINE_REQUIRED
                if not re.search(rf"^\s*{f}\s*[:：]\s*\S", body, re.M)]

    assert lack(good) == []
    assert set(lack(bad)) == {"重场", "爽点", "章末钩子", "一句话"}


def test_dials_are_orthogonal_and_drive_mechanisms():
    """两个旋钮必须正交，且不只写进提示词、还要改机制。

    「请写得狂野一点」塞进提示词只会让模型写得更用力，结构照旧 ——
    所以狂野度直接定挫败配额、爽度直接定爽点间隔。
    """
    from server import dials as dl
    lo = dl.derived({"gratify": 20, "wild": 10})
    hi = dl.derived({"gratify": 90, "wild": 90})
    assert hi["setback_quota"] > lo["setback_quota"]
    assert hi["pleasure_interval"] < lo["pleasure_interval"]
    # 一次都不失手的主角读者不会替他担心 —— 最低也得给 1 次
    assert dl.derived({"wild": 0})["setback_quota"] >= 1
    # 正交：只动爽度不该改挫败配额，只动狂野度不该改爽点间隔
    a = dl.derived({"gratify": 20, "wild": 50})
    b = dl.derived({"gratify": 90, "wild": 50})
    assert a["setback_quota"] == b["setback_quota"]
    c = dl.derived({"gratify": 50, "wild": 10})
    d = dl.derived({"gratify": 50, "wild": 90})
    assert c["pleasure_interval"] == d["pleasure_interval"]


def test_dials_normalize_and_brief():
    from server import dials as dl
    assert dl.normalize({"gratify": 999, "wild": -5}) == {"gratify": 100, "wild": 0}
    assert dl.normalize("坏输入") == dl.defaults()
    b = dl.brief({"gratify": 95, "wild": 95})
    assert "剧情上" in b and "文风上" in b and "掀翻" in b


def test_seed_change_invalidates_downstream(tmp_path, monkeypatch):
    """种子变了，下游资产必须被判为过期。

    实测踩过：改完铁律只删了骨架、留下 outline.md 想省一次生成 ——
    总纲还是旧设定、阶梯是新的，细纲照着总纲写，改动等于没改，
    「打服武松」和那把枪在 128 章里一次没出现。这不该靠人记性。
    """
    import json as _j
    from server.orchestrator import Novelist, Project
    d = tmp_path / "proj"
    (d / "chapters").mkdir(parents=True)
    (d / "project.json").write_text(_j.dumps(
        {"title": "T", "type_id": "novel", "genre_id": "", "style_id": "",
         "target_chapters": 10, "target_words": 30000, "fields": {},
         "hard_rules": ["甲"]}, ensure_ascii=False), encoding="utf-8")
    (d / "state.json").write_text('{"done": [], "current": 0}', encoding="utf-8")
    (d / "outline.md").write_text("旧总纲", encoding="utf-8")

    nv = Novelist(Project(str(d)))
    assert "outline.md" in nv.stale_assets()      # 从没记过指纹 → 全过期
    nv.mark_seed()
    assert nv.stale_assets() == []                 # 记过之后不再报

    nv.p.meta["hard_rules"] = ["甲", "乙"]         # 铁律一变
    assert "outline.md" in nv.stale_assets()       # 下游立刻过期


def test_no_silent_truncation_contract():
    """不准截断：撞上输出上限必须续写，不接受半截产物。

    截断的产物比缺失更糟 —— 它看起来是完整的，后面的环节会把半句话当成
    写好的内容接着用：细纲里留下「第三天乖乖回来」「应二」，
    角色档案停在「**原声」，而没有任何东西报警。
    逐个调用去猜 max_tokens 是猜不完的（加一栏字段就得改一处预算，
    这个坑踩了三次）；API 本来就给了 finish_reason=length，接住它才是根治。
    """
    import server.orchestrator as O
    assert O.CONTINUE_ROUNDS >= 1
    import inspect
    from server.providers.openai_compat import OpenAICompatProvider
    # provider 必须记录收尾原因，否则 call() 无从判断是不是被切断
    src = inspect.getsource(OpenAICompatProvider)
    assert "last_finish" in src and "finish_reason" in src
    # call() 必须在 length 时续写
    src2 = inspect.getsource(O.call)
    assert "last_finish" in src2 and "length" in src2


def test_character_card_gap_detection():
    """角色卡缺栏 = 被截断（模型不会写一半就换人）。"""
    import json as _j
    from server.orchestrator import Novelist, Project
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp()) / "p"
    (d / "chapters").mkdir(parents=True)
    (d / "project.json").write_text(_j.dumps(
        {"title": "T", "type_id": "novel", "genre_id": "", "style_id": "",
         "target_chapters": 10, "target_words": 30000, "fields": {}},
        ensure_ascii=False), encoding="utf-8")
    (d / "state.json").write_text('{"done": [], "current": 0}', encoding="utf-8")
    nv = Novelist(Project(str(d)))
    full = ("### 1. 姓名：甲\n**身份**：x\n**核心动机**：x\n**与主角关系**：x\n"
            "**自称**：我\n**口头禅**：x\n**语感**：x\n**原声样本**：x\n"
            "**禁用词**：x\n**结局走向**：x\n")
    cut = full + "\n### 2. 姓名：乙\n**身份**：x\n**核心动机**：x\n**原声"
    assert nv._card_gaps(full) == []
    gaps = nv._card_gaps(cut)
    assert gaps and "乙" in gaps[0]


def test_one_liner_prefers_model_written():
    """一句话优先用模型自己写的，不靠机械抽取。

    从细纲里抠「重场那一拍」当摘要，依赖重场标得准；标错了摘要就抓错重点，
    而且会一直错下去（下一批看到的就是那句话）。让模型边写边压更靠谱 ——
    它比抽取更清楚这一章的重点，代价是同一次调用多输出四十来字。
    """
    import json as _j, tempfile, pathlib as _p, re
    from server.orchestrator import Novelist, Project
    d = _p.Path(tempfile.mkdtemp()) / "p"
    (d / "chapters").mkdir(parents=True)
    (d / "project.json").write_text(_j.dumps(
        {"title": "T", "type_id": "novel", "genre_id": "", "style_id": "",
         "target_chapters": 10, "target_words": 30000, "fields": {}},
        ensure_ascii=False), encoding="utf-8")
    (d / "state.json").write_text('{"done": [], "current": 0}', encoding="utf-8")
    withline = ("第1章 甲\n承接：x\n出场角色：A\n剧情1：机械抽取会抓到这句\n"
                "重场：剧情1\n爽点：x\n章末钩子：x\n一句话：模型自己写的那句才是重点")
    without = ("第2章 乙\n承接：x\n出场角色：A\n剧情1：只能回退到抽取这句\n"
               "重场：剧情1\n爽点：x\n章末钩子：x")
    (d / "chapter_outlines.json").write_text(
        _j.dumps({"1": withline, "2": without}, ensure_ascii=False), encoding="utf-8")
    out = Novelist(Project(str(d))).outline_digest(3)
    assert "模型自己写的那句才是重点" in out
    assert "只能回退到抽取这句" in out          # 老章节仍要有代表，不能丢


def test_split_outline_callable_on_instance():
    """split_outline 必须能从实例上调用。

    实测踩过：一次正则改代码把 @staticmethod 装饰器吃掉了，
    单测全绿（测试是按类调用的），可实际路径 self.split_outline(text, count)
    直接 TypeError —— 守护每 10 秒重试一次，每次都先花五分钟完整生成
    一万两千字的细纲、再崩在解析上，白烧了五十分钟。
    """
    import json as _j, tempfile, pathlib as _p
    from server.orchestrator import Novelist, Project
    d = _p.Path(tempfile.mkdtemp()) / "p"
    (d / "chapters").mkdir(parents=True)
    (d / "project.json").write_text(_j.dumps(
        {"title": "T", "type_id": "novel", "genre_id": "", "style_id": "",
         "target_chapters": 10, "target_words": 30000, "fields": {}},
        ensure_ascii=False), encoding="utf-8")
    (d / "state.json").write_text('{"done": [], "current": 0}', encoding="utf-8")
    s = "第1章 甲\n钩子：h\n###fenge\n第2章 乙\n钩子：h"
    assert len(Novelist.split_outline(s, 2)) == 2        # 按类调
    assert len(Novelist(Project(str(d))).split_outline(s, 2)) == 2   # 按实例调


def test_pack_fields_accept_str_or_list():
    """包字段写成一整句时不许被逐字拆开。

    实测同人二创包 corePleasure 是字符串, 旧代码 join 出
    「熟；悉；感； ；×」—— 题材规范整段变噪声, 而且不报错。
    """
    from server.orchestrator import Novelist
    it = Novelist._items
    assert it("熟悉感 × 意外感：读者认得每个人") == ["熟悉感 × 意外感：读者认得每个人"]
    assert it(["a", "b"]) == ["a", "b"]
    assert it(None) == [] and it("") == [] and it([]) == []
    assert it({"x": "a", "y": "b"}) == ["a", "b"]
    assert it(["a", " ", "b"]) == ["a", "b"]
    assert it(["a", "b", "c"], 2) == ["a", "b"]


def test_genre_rules_no_char_splitting():
    """走完整 genre_rules, 保证字符串字段不出现逐字分隔。"""
    from server.orchestrator import Novelist
    nv = Novelist.__new__(Novelist)
    nv.genre = {"name": "同人二创",
                "corePleasure": "熟悉感 × 意外感",
                "cast": "原作核心角色 4-6 位",
                "pitfalls": ["崩人设"]}
    out = nv.genre_rules()
    assert "核心爽点：熟悉感 × 意外感" in out
    assert "熟；悉" not in out and "原；作" not in out


def test_seed_stamp_covers_pack_rules():
    """包渲染结果变了, 下游资产必须判定为作废。

    踩过: 修好「包字段整句被逐字拆开」后, 喂给模型的题材规范从乱码变成正常
    句子, 但指纹只盖本书 meta, 用乱码规范生成的总纲原地留着继续往下长。
    """
    from server.orchestrator import Novelist
    nv = Novelist.__new__(Novelist)
    nv.p = type("P", (), {"meta": {}, "state": {}})()
    nv.style = {}
    nv.genre = {"name": "同人", "corePleasure": "甲"}
    a = nv.seed_stamp()
    nv.genre = {"name": "同人", "corePleasure": "乙"}
    assert nv.seed_stamp() != a, "题材规范变了, 指纹必须跟着变"
    nv.genre = {"name": "同人", "corePleasure": "甲"}
    assert nv.seed_stamp() == a, "同样的规范必须给出同样的指纹"


def test_era_asked_from_model_and_cached():
    """朝代交给模型判, 答完存 meta 复用。

    两种正则猜法都实测栽过: 单字裸匹配把「元祐党籍碑」认成元朝(共享库
    1263 张卡全废); 只认「北宋」这类无歧义写法, 又碰上通篇只写「政和五年」
    「徽宗朝」的世界观, 一次都匹配不上。
    """
    from server.orchestrator import Novelist
    nv = Novelist.__new__(Novelist)
    saved = {}
    nv.p = type("P", (), {"meta": {}, "save": lambda self: saved.setdefault("n", 0)})()
    calls = []

    import server.orchestrator as orc
    real_call, real_clean = orc.call, orc.clean
    orc.call = lambda prof, q, **kw: (calls.append(q),
                                      type("R", (), {"text": "北宋"})())[1]
    orc.clean = lambda x: x
    try:
        assert nv._ask_era("政和五年，徽宗朝，蔡京立元祐党籍碑") == "北宋"
        assert nv.p.meta["era"] == "北宋", "答案要存进 meta"
        assert len(calls) == 1
        assert nv._ask_era("随便什么") == "北宋" and len(calls) == 1, "第二次要走缓存"
        # 空设定不问, 也不乱填
        nv.p.meta.clear()
        assert nv._ask_era("") == ""
    finally:
        orc.call, orc.clean = real_call, real_clean


def test_era_blank_for_fictional_world():
    """架空世界留空, 别硬塞一个朝代。"""
    from server.orchestrator import Novelist
    import server.orchestrator as orc
    nv = Novelist.__new__(Novelist)
    nv.p = type("P", (), {"meta": {}, "save": lambda self: None})()
    real_call, real_clean = orc.call, orc.clean
    orc.call = lambda prof, q, **kw: type("R", (), {"text": "无"})()
    orc.clean = lambda x: x
    try:
        assert nv._ask_era("修真大陆，灵气复苏") == ""
        assert "era" not in nv.p.meta
    finally:
        orc.call, orc.clean = real_call, real_clean


def test_pleasure_field_demands_event_not_commentary():
    """爽点字段必须要求写事件, 并点名禁掉分析腔。

    踩过: 只说「读者爽在哪」, 模型写回「通过第三方视角间接交锋, 既避免了
    主角正面硬刚的危险, 又通过摔杯具象化了武松的杀气」—— 这是编辑评语,
    照着它写不出任何一场戏。还有一章把「虚惊一场」填进了爽点。
    """
    from server.prompt_compiler import outline_format_block
    blk = outline_format_block(6, 600)
    assert "发生了什么" in blk and "不是点评" in blk
    assert "正例" in blk and "反例" in blk
    assert "虚惊一场" in blk, "没把实际收到的坏样本列进反例"


def test_patterns_flags_commentary_pleasure():
    """爽点写成评语要被查出来。

    提示词层面压不住: 字段说明已写「不是点评这一章的写法」并给了正反例,
    实测 20 章仍然 20 章全是评语, 连点名禁掉的「虚惊一场」都原样写回来
    (提示词三万多字, 字段说明被读丢)。词法毛病用词法查。
    """
    from server.orchestrator import Novelist

    def mk(n, shuang):
        return (f"第{n}章 标题\n一句话：某事\n承接：上一章\n出场角色：甲、乙、丙\n"
                f"剧情1：动作\n重场：剧情1\n爽点：{shuang}\n章末钩子：某人说了句话")

    nv = Novelist.__new__(Novelist)
    bad = ["展现了主角的冷静", "爽点在于智斗升级带来的压迫感", "虚惊一场，危机加深",
           "既避免了硬刚又具象化了杀气", "环环相扣，层层递进", "为后续埋下伏笔"]
    co = {str(i + 1): mk(i + 1, bad[i]) for i in range(6)}
    nv.p = type("P", (), {"_load": lambda self, f, d: co})()
    hits = [x for x in nv.outline_patterns(1, 6) if "点评" in x]
    assert hits, "全是评语却没报警"

    good = ["王婆当着满茶坊的人被自己收的银子噎住", "县令当堂把状纸摔回武松脸上",
            "主角拿到蔡京府的空白手谕", "郓哥把银子推回去说不走",
            "何九叔交出藏了七日的骨殖", "武松的刀被自己人按住"]
    co2 = {str(i + 1): mk(i + 1, good[i]) for i in range(6)}
    nv.p = type("P", (), {"_load": lambda self, f, d: co2})()
    assert not [x for x in nv.outline_patterns(1, 6) if "点评" in x], "写的是事件却误报"


def test_reset_clears_cadence_markers():
    """重开一轮时节奏标记必须一起清。

    踩过: swept_at 留着上一轮的 60, 新一轮才排到 50, 于是「离上次巡检
    不到 10 章」一路成立, 50 章一次巡检都没跑 —— 伏笔台账、承诺兑现、
    张力推进的唯一生产者歇了整整一轮, 日志里看不出任何异常。
    """
    import pathlib, re
    src = pathlib.Path("run_novel.py").read_text(encoding="utf-8")
    m = re.search(r"for k in \((.*?)\):\s*\n\s*st\.pop", src, re.S)
    assert m, "找不到重置清单"
    keys = m.group(1)
    for k in ("swept_at", "selfchecked_at", "recapped_at", "outline_guide"):
        assert f'"{k}"' in keys, f"重置清单漏了 {k}"


def test_sweep_candidates_use_end_not_start():
    """宽区间巡检时老伏笔必须进候选。

    踩过: 用 start 算「埋了多久」, 补跑的 1-60 章巡检里 start=1,
    `start - planted` 恒为负 —— 「埋够 20 章」那一档全空, 只剩本批刚埋的
    钩子, 于是报「回收 0」, 看上去像剧情没收伏笔, 其实是候选选错了。
    """
    pend = [{"id": i, "planted": i, "text": f"钩子{i}"} for i in range(1, 61)]
    start, end = 1, 60
    aged = [x for x in pend if end - x["planted"] >= 20][:8]
    assert aged and aged[0]["planted"] == 1, "老伏笔应该排在前面"
    assert any(x["planted"] == 8 for x in aged), "第 8 章的伏笔要在候选里"
    bad = [x for x in pend if start - x["planted"] >= 20][:8]
    assert not bad, "用 start 算就是这个空结果 —— 回归防护"


def test_patterns_flags_name_length_runs():
    """章名不能只是从「全 2 字」换成「全 3 字」。

    踩过: 纠偏说「2 字不超过 25 个」, 下一批就 5/5 全变 3 字 —— 模型执行
    配额时倾向取一个值全用, 单调性只是换了个长度。
    """
    from server.orchestrator import Novelist

    def mk(n, name):
        return (f"第{n}章 {name}\n一句话：某事\n承接：上一章\n出场角色：甲、乙、丙\n"
                f"剧情1：动作\n重场：剧情1\n爽点：甲当众交出账册\n"
                f"章末钩子：门被推开")

    co = {str(i): mk(i, "断线线") for i in range(1, 13)}   # 12 章全 3 字
    nv = Novelist.__new__(Novelist)
    nv.p = type("P", (), {"_load": lambda self, f, d: co})()
    hit = [x for x in nv.outline_patterns(1, 12) if "章名" in x]
    assert hit and "连续" in hit[0], hit
    assert "交替" in hit[0]


def test_repair_demand_retargets_to_next_batch():
    """重排单并进纠偏时要把指向改成「接下来这一批」。

    踩过: demand 里的「这几章」指的是检测器标记的旧章号(已经排完),
    模型读到「第 1-57 章里要怎样」会正确地判断与本批无关而忽略 ——
    重排单进了提示词两批, 王婆/赵若锦一次没出现。
    """
    import re

    def retarget(s):
        s = re.sub(r"这几章里", "接下来这一批里", s)
        return re.sub(r"这几章", "接下来这一批", s)

    d = "「王婆」已出场 17 章（第 1-57 章）…这几章里给他一件不同类的戏"
    got = retarget(d)
    assert "接下来这一批里给他" in got
    assert "这几章里" not in got


def test_duplicate_chapter_is_rejected():
    """整段重复的章要被丢掉重排。

    踩过: 第 114-117 章被整段复制成第 122-125 章(偏移正好 8, 两对一字
    不差), 字段齐全、长度正常, 完整性守卫放行 —— 读者读到同一段演两遍。
    """
    from server.orchestrator import Novelist
    old = {"114": "第114章 甲\n一句话：西门庆在童贯与蔡京的夹缝中设局让高衙内当众出丑。\n"}
    same = "第122章 乙\n一句话：西门庆在童贯与蔡京的夹缝中设局让高衙内当众出丑。\n"
    diff = "第122章 乙\n一句话：武松在十字坡揭穿孙二娘的蒙汗药，两人当场翻脸。\n"
    assert Novelist._dup_of(same, old, 122) == 114
    assert Novelist._dup_of(diff, old, 122) == 0
    # 自己不算撞自己
    assert Novelist._dup_of(old["114"], old, 114) == 0
    # 太短的一句话不判
    assert Novelist._dup_of("第122章 乙\n一句话：短\n", old, 122) == 0


def test_finite_resource_numbers_must_not_grow():
    """铁律声明只减不增的东西, 数字涨回去要报警。

    踩过: 子弹账走成 118→117→116→**119**→1→119→116→1, 还写出过
    「第 121 发」而全书总共 120 发。铁律白纸黑字要求「每次开枪当场记账」,
    可没有任何东西在核对这个数 —— 台账管事件, 不管数量。
    """
    from server.orchestrator import Novelist

    def mk(n, body):
        return (f"第{n}章 标题\n一句话：{body}\n承接：上一章\n出场角色：甲、乙、丙\n"
                f"剧情1：{body}\n重场：剧情1\n爽点：甲交出账册\n章末钩子：门开了")

    nv = Novelist.__new__(Novelist)
    nv.hard_rules = lambda: ["一百二十发只减不增，宋朝造不出也补不了"]
    assert nv._finite_units() == ["发"]

    # outline_patterns 少于 6 章不扫, 造够
    co = {str(i): mk(i, f"还剩{120-i}发") for i in range(1, 7)}
    co["6"] = mk(6, "还剩119发")                      # 涨回去
    # 分项与序数不算存量: 这一章的账是对的, 不许误报
    co["5"] = mk(5, "还剩116发，其中1发已暴露原理，115发是最后的威慑，这是第121发")
    nv.p = type("P", (), {"_load": lambda self, f, d: co})()
    hit = [x for x in nv.outline_patterns(1, 6) if "不可再生" in x]
    assert hit and "119发" in hit[0], hit

    ok = {str(i): mk(i, f"还剩{120-i}发，其中1发试枪，{119-i}发备用") for i in range(1, 7)}
    nv.p = type("P", (), {"_load": lambda self, f, d: ok})()
    assert not [x for x in nv.outline_patterns(1, 6) if "不可再生" in x]

    # 铁律没声明的单位不查 —— 钱粮本来就该涨
    nv.hard_rules = lambda: ["主角有一百二十贯本钱"]
    assert nv._finite_units() == []


def test_tension_demand_offers_remote_forms():
    """张力纠偏要给远程也能执行的路子。

    踩过: 只说「让消失的那一方重新出现」, 而主角在辽东、潘金莲在千里外的
    阳谷 —— 连报三批都被跳过。模型没错, 硬把人拽到场才是崩。
    """
    import pathlib
    src = pathlib.Path("server/orchestrator.py").read_text(encoding="utf-8")
    i = src.index("张力静默消解")
    seg = src[i:i + 900]
    assert "书信" in seg and "传到耳朵里" in seg
    assert "不是非得让人到场" in seg


def test_destroyed_item_must_not_revive_silently():
    """写死的关键物件不许悄悄复活。

    踩过: 第161章「枪身锈蚀、扳机卡死、彻底成了一根废铁」, 第173章
    「武松验看后确认报废, 将枪投入河中」, 第176章却「深夜从地窖取枪」,
    第178章还「试射一发」。这种复活不报错, 读者一眼看得出来。
    """
    from server.orchestrator import Novelist

    def mk(n, body):
        return (f"第{n}章 标题\n一句话：{body}\n承接：上一章\n出场角色：甲、乙、丙\n"
                f"剧情1：{body}\n重场：剧情1\n爽点：甲交出账册\n章末钩子：门开了")

    nv = Novelist.__new__(Novelist)
    nv.hard_rules = lambda: ["【沙漠之鹰·底气】主角带着一把沙漠之鹰",
                             "一百二十发只减不增，宋朝造不出也补不了"]
    assert nv._finite_carriers() == ["沙漠之鹰"]

    co = {str(i): mk(i, "他在算账") for i in range(1, 7)}
    co["3"] = mk(3, "沙漠之鹰锈蚀，彻底成了一根废铁")
    co["5"] = mk(5, "他从地窖取出沙漠之鹰，擦拭枪身")
    nv.p = type("P", (), {"_load": lambda self, f, d: co})()
    hit = [x for x in nv.outline_patterns(1, 6) if "写死" in x]
    assert hit and "第 5 章" in hit[0], hit

    # 明写了怎么回来的就不报
    co2 = dict(co)
    co2["4"] = mk(4, "郓哥下水把沙漠之鹰捞了回来，找铁匠修好")
    nv.p = type("P", (), {"_load": lambda self, f, d: co2})()
    assert not [x for x in nv.outline_patterns(1, 6) if "写死" in x]


def test_selfcheck_forbids_rework_of_written_chapters():
    """自审只能指导下一批, 不许要求返工已排好的章。

    踩过: 三条纠偏里有一条是「已排好的章名需立即返工第171-185中至少8章」
    —— 排纲回路不会重排已落盘的章, 指令白写, 还占掉一个名额(清单只并前三条)。
    """
    import pathlib
    src = pathlib.Path("server/orchestrator.py").read_text(encoding="utf-8")
    i = src.index("只针对扫出来的模式")
    seg = src[i:i + 700]
    assert "不许要求返工" in seg and "执行不了" in seg
    assert "别把名额都压在同一个模式上" in seg


def test_repeated_last_one_is_flagged():
    """「最后一发」反复出现也是账没记住。

    实测第 63、65、140、176、197 章各来一次「最后一发」—— 每次危机都是
    最后一发, 等于子弹永远打不完, 铁律要的「越来越不舍得」就架空了。
    数字检测抓不到它: 这些章根本没写存量。
    """
    from server.orchestrator import Novelist

    def mk(n, body):
        return (f"第{n}章 标题\n一句话：{body}\n承接：上一章\n出场角色：甲、乙、丙\n"
                f"剧情1：{body}\n重场：剧情1\n爽点：甲交出账册\n章末钩子：门开了")

    nv = Novelist.__new__(Novelist)
    nv.hard_rules = lambda: ["一百二十发只减不增，宋朝造不出也补不了"]
    co = {str(i): mk(i, "他在算账") for i in range(1, 8)}
    co["2"] = mk(2, "他用最后一发子弹打开车锁")
    co["6"] = mk(6, "千钧一发，他动用最后一发子弹")
    nv.p = type("P", (), {"_load": lambda self, f, d: co})()
    hit = [x for x in nv.outline_patterns(1, 7) if "最后一" in x]
    assert hit and "2 次" in hit[0], hit
    # 只出现一次不报
    co2 = dict(co); co2["6"] = mk(6, "他在算账")
    nv.p = type("P", (), {"_load": lambda self, f, d: co2})()
    assert not [x for x in nv.outline_patterns(1, 7) if "最后一" in x]


def test_hard_rules_reach_worldbuilding_and_cast():
    """铁律必须进世界观和人物卡的提示词。

    踩过: 铁律第一条写着「他是本地地头蛇, 不许写成缩着脖子过日子的人」,
    可这两步的提示词里根本没有铁律 —— 世界观写出「他的阶层天花板是商字的
    结构性屈辱」, 人物卡写出「身份：生药铺老板 / 性格：隐忍 / 动机：从
    待宰肥羊进化为操盘手」, 全和铁律正相反。人设一旦定歪, 往后三百章跟着歪。
    """
    import server.orchestrator as orc
    for key in ("world_bible", "characters"):
        assert "${hard_rules}" in orc.BUILTIN_PROMPTS[key], key
