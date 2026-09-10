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
