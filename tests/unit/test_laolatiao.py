"""老辣调那一套的单测：结构指令、字段契约、窗口软反馈、滚动排纲。

这些行为全部是实测校准出来的（见 docs/总对标.md），所以要有测试盯着，
免得以后改包或改编译器时静默退化。
"""
import json
from pathlib import Path

import pytest

from server.prompt_compiler import (compile_chapter_prompt, measure_text,
                                    outline_fields, outline_format_block,
                                    outline_required, window_drift)

ROOT = Path(__file__).resolve().parents[2]
LLT = json.loads((ROOT / "packs/style/laolatiao.json").read_text(encoding="utf-8"))
# 架构层在 packs/engine/core.json, 测试按运行时同样方式合成
LLT = {**json.loads((ROOT / "packs/engine/core.json").read_text(encoding="utf-8")), **LLT}
FQ = json.loads((ROOT / "packs/style/fanqie-shuangwen.json").read_text(encoding="utf-8"))


def _prompt(pack, **kw):
    base = dict(title="书", index=3, target_words=3000, genre_line="历史",
                manner="口语", style_pack=pack, background="北宋",
                world_digest="略", roster=[{"name": "赵楷", "card": "身份：郓王"}],
                protagonist="赵楷", relations="", mainline="",
                chapter_outline="剧情1：闯东华门", positive=[], negative=[],
                constraints="", memory="", block_words=500)
    base.update(kw)
    return compile_chapter_prompt(**base)


# ── 结构指令 ──────────────────────────────────────────────

def test_只发常驻结构件():
    """八条全压会互相抢配额（实测 27% vs 16%）。只发 resident 的。"""
    p = _prompt(LLT)
    assert "🧱" in p
    for name in ("对话", "别人算错", "制度解说"):
        assert f"▍{name}" in p
    # 动态那几条不该常驻发出去，靠窗口反馈按需带
    for name in ("情绪要喊出来", "角色心里的问句"):
        assert f"▍{name}" not in p


def test_老包不受影响():
    """没有 structuralItems / sentenceChars 的包走老分支，一个字都不该变。"""
    p = _prompt(FQ)
    assert "🧱" not in p
    assert "一段只写一个动作或一句话" in p       # 老的段落节奏文案
    assert "句子不要切碎" not in p


def test_长句分支():
    """老辣调原作句均 23~30 字，不能被「一段只写一个动作」压成短句。"""
    p = _prompt(LLT)
    assert "句子不要切碎" in p
    assert "一段只写一个动作或一句话" not in p


# ── 字段契约 ──────────────────────────────────────────────

def test_字段契约可被文风包覆盖():
    req = outline_required(LLT)
    for f in ("视角", "解说", "账目", "后果"):
        assert f in req, f"{f} 应为必填（倒推实测 100% 出现）"
    # 误读 81% / 代价 50%：事件级，不该逼每章都填
    names = [n for n, _, _ in outline_fields(LLT)]
    assert "误读" in names and "误读" not in req
    assert "代价" in names and "代价" not in req


def test_默认字段契约不变():
    assert outline_required(FQ) == outline_required(None)
    assert "爽点" in outline_required(FQ)


def test_格式块不重复列剧情():
    blk = outline_format_block(6, 700, LLT)
    assert blk.count("剧情1：") == 1
    for f in outline_required(LLT):
        assert f"{f}：" in blk


# ── 窗口软反馈 ────────────────────────────────────────────

def test_度量():
    m = measure_text("他愣住了。\n「这是怎么回事？」\n这哪儿是忠臣？分明是催收的！\n")
    # 带引号的独立问句必须数进来——原作大量这么写，漏掉会让区间整体偏低一倍
    assert m["独立反问句"] >= 1
    assert m["反讽旁白"] >= 1
    assert m["每千字叹号"] > 0


def test_漂移只报最急的两项且方向正确():
    """段落长度正常、但完全不喊也不发问 → 应报叹号/问号那一类偏低。"""
    flat = "\n".join(["他点了点头，把账本合上，推到桌子对面去。其实这笔账早就对不上了。"] * 40)
    fb = window_drift([flat] * 6, LLT)
    assert fb.count("· ") == 2, "topK=2"
    assert "偏低" in fb
    assert any(k in fb for k in ("叹号", "问号", "独立反问句", "对白")), fb


def test_没配窗口反馈的包返回空():
    assert window_drift(["随便一段正文" * 100], FQ) == ""


def test_反馈能进提示词():
    p = _prompt(LLT, window_feedback="· 每千字叹号偏低（1.0，目标 6.6~11.3）—— 多喊")
    assert "📐" in p and "多喊" in p
    assert "不要为了补这两项牺牲别的" in p


# ── 数值区间必须与实测一致 ────────────────────────────────

@pytest.mark.parametrize("k,lo,hi", [
    ("对白占比", 0.16, 0.34), ("每千字问号", 3.4, 7.7),
    ("每千字叹号", 6.6, 11.3), ("段均字数", 47, 62),
    # 检测器修好(剥引号)后重标：原作每章 4.2~9.4 个，不是早先误测的 1.7
    ("独立反问句", 3.1, 7.2),
])
def test_区间取自原作282个窗口的5_95分位(k, lo, hi):
    m = LLT["windowFeedback"]["metrics"][k]
    assert (m["lo"], m["hi"]) == (lo, hi)


def test_句长区间():
    """实测原作句均 23~30 字；写「段落短」会把生成句长压到 15.9。"""
    assert LLT["sentenceChars"] == [23, 31]


# ── 误读台账与牌市 ────────────────────────────────────────

def test_误读当燃料放在提示词前部而不是约束里():
    """约束块全是「不得/禁止」，能防倒退不能产生推进。误读要放前面当燃料。"""
    p = _prompt(LLT, fuel="· 老宦：凭「空白诏纸被搬走」，认定「要清算司礼监」，于是「连夜抢解释权」",
                constraints="【已确立的不可逆事实】某某已死")
    i_fuel, i_cons = p.find("🔥 【正在发酵的误会"), p.find("#必守约束")
    assert i_fuel > 0 and i_cons > 0
    assert i_fuel < i_cons, "燃料必须在必守约束之前"


def test_牌市块不按日历派活():
    import server.stagecraft as sc
    th = [{"id": 1, "name": "验尸线", "kind": "谜团", "owner": ["何九叔"], "org": "",
           "span": [2, 220], "cadence": 12, "last_touched": 22, "beats": [],
           "card": "一枚弹头 + 一份验尸格目",
           "valuable_when": ["提刑司来查", "有人翻旧案"],
           "leverage": "互相捏着命门"}]
    cad = sc.thread_brief(th, 150, "cadence")
    crd = sc.thread_brief(th, 150, "cards")
    assert "已超期，本批必须推进" in cad
    assert "已超期" not in crd
    assert "谁手上的牌**突然变值钱了**" in crd
    assert "牌不值钱的人继续消失" in crd
    assert "一枚弹头" in crd


def test_牌市模式停用断线必须回归():
    """牌不值钱就该继续消失——原作里洪承畴消失 781 章、王承恩 1047 章都没问题。"""
    assert LLT.get("threadDriver") == "cards"
    assert FQ.get("threadDriver") in (None, "cadence")


def test_没有牌的支线自动回退到老逻辑():
    import server.stagecraft as sc
    th = [{"id": 1, "name": "旧线", "kind": "人物", "owner": ["某甲"], "org": "",
           "span": [1, 200], "cadence": 10, "last_touched": 5, "beats": []}]
    out = sc.thread_brief(th, 100, "cards")
    assert "已超期" in out, "没有 card 字段时必须退回 cadence 版，别让老书拿到空块"


# ── 卷要的「但是链」 ───────────────────────────────────────

def test_但是链咬合判定():
    """阈值用重合系数 0.40 标定：该咬合的真实链条落在 0.50~0.67，
    另起炉灶的落在 0.00。交并比在该咬合一侧只有 0.29~0.42，离噪声太近。"""
    from server.orchestrator import Novelist
    chk = Novelist._check_volume_chain
    # 咬上：同义改写也要放过
    ok = [{"solves": "活下来", "exposes": "他没有名分"},
          {"solves": "拿到名分", "exposes": "他没钱没地"},
          {"solves": "拿到钱和地", "exposes": ""}]
    assert not [x for x in chk(ok) if "另起炉灶" in x]
    # 没咬上：另起炉灶
    bad = [{"solves": "活下来", "exposes": "他没有名分"},
           {"solves": "北伐中原收复燕云", "exposes": "打不动"}]
    assert any("另起炉灶" in x for x in chk(bad))
    # 缺栏
    miss = [{"solves": "活下来", "exposes": ""}, {"solves": "拿名分", "exposes": ""}]
    assert any("缺" in x for x in chk(miss))


def test_但是链是包开关():
    assert LLT.get("volumeChain") is True
    assert FQ.get("volumeChain") in (None, False)


# ── 反馈控制器的阻尼与耦合保护 ────────────────────────────

def test_三档阻尼措辞():
    """漂出 10% 和漂出 900% 不能给同一句话——全量纠偏会把对白 0.37 一次压到 0.23。"""
    import copy
    pack = copy.deepcopy(LLT)
    # 只留一项，方便断言
    pack["windowFeedback"]["metrics"] = {"每千字叹号": LLT["windowFeedback"]["metrics"]["每千字叹号"]}
    mild = window_drift(["他说。" * 200 + "好！" * 4], pack)      # 略低于下界
    severe = window_drift(["他说。" * 400], pack)                 # 一个叹号都没有
    assert "严重偏离" in severe
    assert "轻微偏离" in mild or "明显偏离" in mild
    assert severe != mild


def test_耦合保护():
    """砍对话时必须明写别把寄生在对话里的东西一起砍了。"""
    import copy
    pack = copy.deepcopy(LLT)
    pack["windowFeedback"]["metrics"] = {"对白占比": LLT["windowFeedback"]["metrics"]["对白占比"]}
    talky = "\n".join(['「你说的这个事，我听着不对。」'] * 60)
    fb = window_drift([talky], pack)
    assert "偏高" in fb
    assert "别把这几样一起砍了" in fb
    assert "心里那句" in fb


def test_死区():
    """漂出不到区间宽度 15% 的不该报——单章方差本来就大，为噪声纠偏只会引入新漂移。"""
    import copy
    pack = copy.deepcopy(LLT)
    pack["windowFeedback"]["metrics"] = {
        "段均字数": {"lo": 47, "hi": 62, "低": "串长", "高": "收短"},
        "每千字叹号": LLT["windowFeedback"]["metrics"]["每千字叹号"],
    }
    # 段均刚过上界一点（区间宽 15，超出 <2.25 才算死区）；叹号一个都没有 → 只该报叹号
    line = "他把账本合上推了回去，那半盏冷茶已经凉透，谁也没再去动它一下。"     # 30 字
    t = "\n".join([line * 2 + "，"] * 30)                                    # 每段约 63 字
    m = __import__("server.prompt_compiler", fromlist=["x"]).measure_text(t)
    assert 62 < m["段均字数"] < 64.3, f"测试样本段均 {m['段均字数']} 不在死区内"
    fb = window_drift([t], pack)
    assert "叹号" in fb
    assert "段均字数" not in fb, fb


# ── 台账健壮性 ────────────────────────────────────────────

def test_误读台账脏数据不许打断写作(tmp_path, monkeypatch):
    """台账是模型抽出来的，字段随时可能缺或脏。这里炸掉会打断整章写作。"""
    import shutil
    from server.orchestrator import Novelist, Project, create_project
    p = create_project("单测脏台账", "novel", "lishi", "laolatiao",
                       target_chapters=20, target_words=60000,
                       fields={"premise": "x", "background": "y", "relationships": ""})
    slug = p.slug
    try:
        p.state["misreads"] = [
            {"at": 1, "who": "甲", "concludes": "错的结论"},   # 缺 because/acts
            {"who": "乙"},                                     # 缺 concludes → 应被过滤
            {}, "不是字典", None,                              # 完全无效
            {"at": "x", "who": "丙", "because": "b", "concludes": "c",
             "acts": "d", "closed_at": 0},                     # at 是非数字字符串
            {"at": None, "who": "丁", "concludes": "戊"},
        ]
        p.save()
        out = Novelist(Project(slug)).live_misreads(30)
        assert "甲" in out and "丙" in out and "丁" in out
        assert "乙" not in out, "缺 concludes 的条目应被过滤掉"
        assert "凭「」" not in out and "于是「」" not in out, "缺字段不许印成残句"
    finally:
        shutil.rmtree(f"projects/{slug}", ignore_errors=True)


# ── 排纲的三层：事件/支线/章节名 ──────────────────────────

def _outline(pack, **kw):
    from server.prompt_compiler import compile_outline_prompt
    base = dict(title="书", start=11, count=8, genre_line="历史", world_digest="略",
                roster_names=["甲", "乙"], outline="总纲", prev_summary="前情",
                constraints="", style_pack=pack)
    base.update(kw)
    return compile_outline_prompt(**base)


def test_章节名规格进了排纲():
    """排纲原来只说「不得与已用过的重复」，没有一句说标题该长什么样。"""
    p = _outline(LLT)
    assert "#章节名规格" in p
    assert "点对手名字的频次必须高于点主角名字" in p
    assert "禁止意象式偏正结构" in p
    assert "直呼对手" in p and "剧透式" in p        # 功能类要给出来
    assert title_spec_missing(FQ), "老包不该拿到这一块"


def title_spec_missing(pack):
    from server.prompt_compiler import title_spec_block
    return title_spec_block(pack) == ""


def test_一事多章进了排纲():
    p = _outline(LLT)
    assert "不要一章一个新事件" in p
    assert "过半必须是对手或输家的眼睛" in p
    assert "只在其中一章动账" in p
    assert "不要一章一个新事件" not in _outline(FQ)


def test_缺字段提示不再硬写爽点():
    """老辣调没有「爽点」这一栏，硬写「尤其是重场、爽点与章末钩子」是错的。"""
    p = _outline(LLT)
    assert "**爽点**" not in p
    for f in outline_required(LLT)[-4:]:
        assert f"**{f}**" in p
    assert "**爽点**" in _outline(FQ)               # 老包有这一栏，仍该提


def test_扩写带上了调子与常驻结构件():
    """扩写补出来的字占最终篇幅三到五成，不能是文风盲的。"""
    import inspect
    from server import orchestrator
    src = inspect.getsource(orchestrator.Novelist.step_chapter)
    assert "grow_style" in src
    assert "补进去的文字必须是这个调子" in src
    assert "render_item" in src


def test_当众失态是常驻结构件():
    """全书 520 章抽样里 56% 的章都有大人物当众丢脸——这是这个调子的体温。"""
    names = [i["名"] for i in LLT["structuralItems"]["items"] if i.get("resident")]
    assert "让有身份的人当众失态" in names
    it = [i for i in LLT["structuralItems"]["items"] if i["名"] == "让有身份的人当众失态"][0]
    assert "演出来，不许叙述" in it["做法"]
    assert "他心里一紧" in it["关键"]               # 要给出反例
    p = _prompt(LLT)
    assert "▍让有身份的人当众失态" in p


# ── 世界自转（横向扩散因子）────────────────────────────────

def test_自转在引擎层且文风包不得携带架构键():
    """架构层与文风层分家后的纪律: 推进机制只有 engine/core.json 一份,
    文风包再夹带这些键就是在分叉架构 —— 那正是「迭代几十遍越改越乱」的根源。"""
    import json as _j
    from pathlib import Path as _P
    root = _P(__file__).resolve().parents[2]
    core = _j.loads((root / "packs/engine/core.json").read_text(encoding="utf-8"))
    assert (core.get("worldTurn") or {}).get("every") == 8
    engine_keys = {"outlineFields", "threadDriver", "eventSpan", "volumeChain",
                   "worldTurn", "windowFeedback", "structuralItems"}
    assert engine_keys <= set(core), "引擎层缺架构键"
    for f in ("laolatiao", "roushen-shuangwen"):
        pk = _j.loads((root / f"packs/style/{f}.json").read_text(encoding="utf-8"))
        leaked = engine_keys & set(pk)
        assert not leaked, f"文风包 {f} 夹带架构键: {leaked}"
    assert (FQ.get("worldTurn") or {}).get("every") in (None, 0, 8), "老包不受影响"


def test_世界回合提示词的硬要求():
    """没有这几条, 各势力会写成一致对外的背景板, 扩散就不发生。"""
    import server.stagecraft as sc
    fs = [{"name": "缥缈阁", "wants": "十年一割维持格局", "inner": "六圣里有两位想提前",
           "fears": "有人修到能威胁他们", "state": "执掌天下", "reads_hero": "一个凡人罢了"},
          {"name": "狐族", "wants": "补足元气化形", "inner": "老狐王与少壮派争鼎炉",
           "fears": "被道门剿", "state": "元气大伤", "reads_hero": "极品鼎炉"}]
    p = sc.world_turn_prompt(fs, 9, "三年后开罗刹海", "8 章")
    assert "主角不在场" in p
    assert "对它自己不利" in p            # 内部斗争压倒外部理性
    assert "误判主角" in p
    assert "不许所有势力都在针对主角" in p
    assert "直接撞在一起" in p            # 势力互撞才长出第三条线
    assert "缥缈阁" in p and "六圣里有两位想提前" in p


def test_自转结果进排纲且排在最前():
    """世界自转要让排纲先看见世界变成什么样, 再决定主角撞上哪一条。"""
    import inspect
    from server import orchestrator
    src = inspect.getsource(orchestrator.Novelist.step_chapter_outlines)
    assert "world_turn(start)" in src
    assert "🌍" in src
    assert 'cons.insert(0' in src
    assert "撞上" in src


# ─────────────── 作废与换壳（五算子的纵向两条）───────────────

def test_引擎层带作废与换壳配置():
    import json as _j
    from pathlib import Path as _P
    core = _j.loads((_P(__file__).resolve().parents[2] /
                     "packs/engine/core.json").read_text(encoding="utf-8"))
    assert int((core.get("obsolete") or {}).get("afterUses") or 0) >= 2
    assert int((core.get("reshell") or {}).get("tailChapters") or 0) >= 1


def test_同路数模糊归并():
    """模型即使被要求统一说法也会飘，全等匹配会让作废令永不触发。"""
    from server.orchestrator import Novelist
    same = Novelist._same_move
    assert same("肉身硬抗法器", "以肉身硬接法器")
    assert same("一拳砸断腿骨", "一拳打断腿")
    assert not same("肉身硬抗法器", "抵押货单换船期")


def test_作废令要写明失灵理由与禁止加强():
    import server.stagecraft as sc
    p = sc.obsolete_prompt("肉身硬抗法器",
                           [{"at": 3, "solved": "挡下法器"},
                            {"at": 7, "solved": "硬吃一符"}], 9)
    assert "当众失灵" in p
    assert "不许写成「遇到了更强的敌人」" in p   # 失灵理由必须内生
    assert "更用力、更熟练、更高层次都不算" in p  # 不许靠加强同一招过关
    assert "代价" in p


def test_换壳令要长在但是链上且不许原地升级():
    import server.stagecraft as sc
    vol = {"name": "第一卷", "solves": "解决了活命", "exposes": "肉身暴露在戒律堂视线中"}
    p = sc.reshell_prompt(vol, "苦役院扫地杂役", 4, 42)
    assert "肉身暴露在戒律堂视线中" in p      # 收壳理由来自本卷 exposes
    assert "性质不同" in p                    # 换赛道, 不是升一级
    assert "看错" in p                        # 换壳本身要产生新误读


def test_评审骨架必须列全本遍维度():
    """静态样例只列三五个维度就打省略号，模型照着提前收尾，实测每遍稳定漏评
    2-3 维；于是每章总分按不同数量的维度平均出来，章与章不可比。"""
    import server.critic as c
    dims = [("人物一致性", "a"), ("对白质感", "b"), ("开篇与钩子", "c"),
            ("描写配给", "d")]
    p = c.build_prompt(title="T", n=1, text="正文", prev_texts=[], world="",
                       roster="", canon=[], outline="", budget_chars=3000,
                       recalled=[], digests=[], roles={}, timeline=[],
                       dims_override=dims, pass_name="文字读", real_mode=False)
    for name, _ in dims:
        assert f'"{name}"' in p, f"骨架里缺维度 {name}"
    assert "一个都不能少" in p
    assert "..." not in c.schema_for([d[0] for d in dims])


def test_红线卡点名的词要进验收黑名单_但别把替代词也拉黑():
    """era_card 连加五条「严禁写出'纽约'」，而 hard_blacklist 里压根没有它 ——
    规则一直在累加，验收一次都没查。同一句里点名的**替代写法**不能被误伤。"""
    import re as _re
    pat = _re.compile(r"[‘'「『\"“]([^’'」』\"”，。；：\n]{2,10})[’'」』\"”]")
    line = ("【时代红线】严禁直接写出'纽约'等现代专有名词；"
            "必须通过'梦境碎片'呈现")
    keep = []
    for m in pat.finditer(line):
        seg = line[max(0, m.start() - 16):m.start()]
        if _re.search(r"转化为|转化成|替代|代之|写成|改成|呈现|体现|"
                      r"如：|例如|通过|以.{0,6}方式", seg):
            continue
        w = m.group(1)
        if len(w) <= 6 and "的" not in w:
            keep.append(w)
    assert keep == ["纽约"], keep


def test_口号式收尾只在结尾判_且不误伤具体画面():
    """59 章里 4 章用了同一句「才刚刚开始」+「在这个人人XX的世界里」。
    提示词早写了「不要在结尾进行总结」——禁令没人查就等于不存在。"""
    from server.evaluator import SLOGAN_END
    bad = ("他知道，真正的游戏才刚刚开始。在这个人人都修仙的世界里，"
           "他要做的，就是用拳头打破他们的规则，然后用自己的方式，活下去。")
    good = ("状元印表面的古篆在他掌心纹路里彻底沉寂下去，仿佛从未活过来过。")
    assert SLOGAN_END.search(bad)
    assert not SLOGAN_END.search(good)
    # 同一句出现在章中是人物心声，不该判——所以只查结尾 180 字
    mid = "破军心说这只是开始。" + "正文" * 200
    assert not SLOGAN_END.search(mid.rstrip()[-180:])
