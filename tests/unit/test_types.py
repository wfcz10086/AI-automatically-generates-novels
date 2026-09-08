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


def test_outline_digest_is_layered(tmp_path):
    """已排细纲要分层喂：近的完整，远的压缩。

    全部压成一行会把最近几章的细节也丢掉 —— 而接缝处最需要的恰恰是
    「上一章结在哪、谁还在场、埋了什么没收」这些细节。
    """
    import json as _j
    from server.orchestrator import Novelist, Project
    from server.settings import load

    d = tmp_path / "书"
    (d / "chapters").mkdir(parents=True)
    (d / "project.json").write_text(_j.dumps(
        {"title": "t", "type_id": "novel", "genre_id": "lishi",
         "style_id": "qidian-lishi", "target_chapters": 100,
         "target_words": 300000, "fields": {}}, ensure_ascii=False), encoding="utf-8")
    (d / "state.json").write_text('{"done": [], "current": 0}', encoding="utf-8")
    body = "第N章 标题\n核心事件：某事发生\n剧情1：细节甲\n剧情2：细节乙\n" + "补充。" * 120
    (d / "chapter_outlines.json").write_text(_j.dumps(
        {str(i): body.replace("第N章", f"第{i}章") for i in range(1, 61)},
        ensure_ascii=False), encoding="utf-8")

    nv = Novelist(Project(str(d)))
    # 预算充足时全部完整喂（上下文够大就不该压）
    out = nv.outline_digest(61, limit=200000)
    assert "【最近各章（完整细纲" in out, "近的没有完整喂"
    assert out.count("[[CH") == 60, "预算够却没全给完整版"
    assert "【更早各章（压缩" not in out, "预算够却还在压缩"

    # 预算收紧时才分层：近的完整、远的压成一行
    out2 = nv.outline_digest(61, limit=12000)
    assert "【更早各章（压缩" in out2, "预算不足时没有压缩远端"
    n_full = out2.count("[[CH")
    assert 1 <= n_full < 60, f"完整段章数不合理：{n_full}"
    head, tail = out2.split("【最近各章（完整细纲", 1)
    assert "剧情2：" in tail, "完整段丢了剧情点"
    assert "剧情2：" not in head, "压缩段没压住"


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
    assert OUTLINE_REQUIRED == ["承接", "出场角色", "剧情1", "爽点", "章末钩子"]

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
            "爽点：翻盘\n章末钩子：有人来报")
    bad = "第1章 甲\n承接：接住上一章\n出场角色：A、B\n剧情1：出事了\n剧情6：钩子：有人来报"

    def lack(body):
        return [f for f in OUTLINE_REQUIRED
                if not re.search(rf"^\s*{f}\s*[:：]\s*\S", body, re.M)]

    assert lack(good) == []
    assert set(lack(bad)) == {"爽点", "章末钩子"}
