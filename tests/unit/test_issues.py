"""状态由程序派生，未知错误默认拦停。

这几条锁住的是这个仓库最贵的一课：**「✓」原来是无条件打的**。
它不表示这一章没出问题，只表示没抛到最外层。
"""
from __future__ import annotations

import pytest

from server import issues as I


def test_没问题才算完成():
    assert I.Ledger().status() == I.STATUS_DONE


def test_未登记的错误一律拦停():
    """整个机制的关键就在这一条。

    未知情况默认继续（我们原来）和未知情况默认拦停（现在），差别只在这里。
    新故障第一次出现就会把这一章标成需人工，逼人看一眼 —— 这个摩擦是故意的。
    """
    L = I.Ledger()
    item = L.record("这个码从来没登记过", "天知道出了什么事")
    assert item["unregistered"] is True
    assert item["severity"] == I.MUST
    assert L.status() == I.STATUS_NEEDS_USER
    assert L.status() != I.STATUS_DONE          # 永远不可能报「完成」


def test_吞掉的代码bug会把这一章判成需人工():
    """实测原型：自检里 `cons` 未定义，被 except 吞成一行

        系统自检失败(不阻塞写作): name 'cons' is not defined

    跟「模型这次没答好」长得一模一样，于是藏了几小时。自检因此看不见不可逆
    事实与红线，判不了「约束失效」那一类。
    """
    L = I.Ledger()
    item = L.record("step_skipped", "自检",
                    NameError("name 'cons' is not defined"))
    assert item["code"] == "step_skipped"       # 调用方报的码
    assert item["severity"] == I.MUST           # 但被抬档了
    assert "cons" in item["detail"]
    assert "trace" in item
    assert L.status() == I.STATUS_NEEDS_USER


@pytest.mark.parametrize("exc", [
    NameError("x"), AttributeError("x"), TypeError("x"), KeyError("x")])
def test_四类代码bug一律抬档(exc):
    L = I.Ledger()
    assert L.record("step_skipped", "", exc)["severity"] == I.MUST


def test_模型没答好只算部分完成():
    """模型这次没答好 ≠ 我们写错了。前者跳过就跳过，后者必须改代码。"""
    L = I.Ledger()
    L.record("step_skipped", "标题选优跳过", ValueError("模型返回空"))
    assert L.status() == I.STATUS_PARTIAL


def test_程序已兜住的不影响状态():
    L = I.Ledger()
    L.record("continuation", "撞上输出上限，已续写")
    L.record("gate_discard", "剔掉了误判的禁用词")
    assert L.status() == I.STATUS_DONE
    assert "已自动兜住 2" in L.brief()


def test_一个字都没写出来算失败不算需人工():
    L = I.Ledger()
    L.record("chapter_write_failed", "正文为空")
    assert L.status(wrote_text=False) == I.STATUS_FAILED
    assert L.status(wrote_text=True) == I.STATUS_NEEDS_USER


def test_未登记的排在最前面():
    """日志只印前几条，未登记的最值得看一眼，不能被一堆已知项挤掉。"""
    L = I.Ledger()
    for i in range(6):
        L.record("continuation", f"第{i}次续写")
    L.record("谁也没见过的码", "！")
    assert "未登记" in L.lines()[0]


def test_每条登记都写清楚了为什么():
    """登记的意思是「我们知道它为什么会发生、知道它要不要紧」。

    只填个 severity 不填 why，等于把未知错误伪装成已知错误 —— 那比不登记更糟。
    """
    for code, spec in I.CATALOG.items():
        assert spec.get("severity") in (I.MUST, I.CONFIRM, I.AUTO), code
        assert spec.get("title"), code
        assert spec.get("why"), f"{code} 没写为什么会发生"
        if spec["severity"] != I.AUTO:
            assert spec.get("next_action"), f"{code} 没写该怎么办"
    assert I.FALLBACK["severity"] == I.MUST, "兜底必须是拦停，这是整条机制的根"


# ───────── 模型不许打分：分数与放行由程序算 ─────────

def test_模型给的分不再是闸门():
    """原来是让模型给 0-100、取平均、拿平均卡门 —— 等于让被考的人自己填分。

    同一篇稿子重评一遍能差十几分，而且它可以「问题照列、分照给高」，
    两者之间没有任何约束。
    """
    from server.critic import parse
    # 模型把每一维都打了 95，却列出两条严重问题
    raw = ('{"scores":{"人物":95,"设定":95},"issues":['
           '{"dim":"设定","severity":"high","what":"甲又活了",'
           ' "evidence":"甲站起身"},'
           '{"dim":"人物","severity":"high","what":"性格突变",'
           ' "evidence":"他忽然温柔起来"}]}')
    d = parse(raw)
    assert d["dim_avg"] == 95           # 模型自己给的分
    assert d["overall"] == 70           # 程序按扣分表算: 100 - 15*2
    assert d["blocking"] is True        # 两条严重 → 拦下
    assert "严重问题 2 条" in "；".join(d["blocking_why"])


def test_没有正文原句为证的问题不扣分():
    """这条逼着模型给证据：空口说的问题不算数。"""
    from server.critic import parse
    raw = ('{"scores":{"a":80},"issues":['
           '{"dim":"a","severity":"high","what":"感觉不太行","evidence":""}]}')
    d = parse(raw)
    assert d["claimed"] == 1 and d["evidenced"] == 0
    assert d["overall"] == 100 and d["blocking"] is False


def test_与已确立事实冲突一票拦下():
    """冲突一旦固化就一路错到底，是最贵的一类。"""
    from server.critic import parse
    raw = ('{"scores":{"a":90},"issues":[],'
           '"contradictions":[{"fact":"甲已死","evidence":"甲开口说道"}]}')
    d = parse(raw)
    assert d["blocking"] is True
    assert d["overall"] == 65           # 100 - 35


def test_评审合并后必须用程序的判定而不是维度平均():
    """「算了但没人用」比「没算」更难发现 —— 字段就在那儿，看起来是全的。

    实测第 8 章：overall=88 正好是 15 个维度的算术平均（1318/15），而
    blocking/penalty/severity_counts 全是 None。因为 step_critique 多遍合并
    之后又写了一句 merged["overall"] = 各维平均，把每一遍 parse() 里
    judge() 算好的结果整个盖掉了。
    """
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.step_critique)
    assert "critic_mod.judge(merged)" in src, \
        "多遍合并之后没有再过一次程序判定，judge() 的结果会被平均分盖掉"
    assert 'merged["overall"] = round(sum(vals) / len(vals))' not in src, \
        "overall 又被写回成维度平均了 —— 那就是让被考的人自己填分"


# ───────── 开局落点：把不可判定的语义问题换成可判定的字面问题 ─────────

def test_开局落点必须逐字抄进细纲(tmp_path, monkeypatch):
    """实测落点四「妖姬一吻夺元阳」整条丢了，换成了自创的「瓷片挟持老太监」。

    丢的不是一场戏 —— 夺元阳是他**成为鼎炉的原因**。全书 0 次「元阳」，
    主角却从第 3 章起就被叫鼎炉：果还在，因没了。

    opening_beats() 早就把「一条一章，不许合并也不许跳过」写进提示词了，
    可没有任何程序在查 —— 第九次栽在同一件事上。

    语义覆盖判不了（试过名物匹配：第 1 章明明写全了纽约/FBI/加特林/玉佩，
    命中率却只算出 21%，因为切出来的 2-4 字组大半是「一枚」「一枚古」这类
    碎片）。但**逐字照抄**判得了，跟但是链那条是同一个办法。
    """
    from server.orchestrator import Novelist

    class _P:
        meta = {"fields": {"premise":
                           "【开局落点（前六章骨架，按此写）】\n"
                           "一 纽约曼哈顿，被小弟出卖，FBI 围楼。\n"
                           "二 玉佩崩碎，光华卷走他。落在迷离林。\n"
                           "【基调】要爽。\n"}}

    nv = Novelist.__new__(Novelist)
    nv.p = _P()
    assert nv.beat_for(1).startswith("纽约曼哈顿")
    assert nv.beat_for(2).startswith("玉佩崩碎")
    assert nv.beat_for(9) == ""                      # 超出范围不管

    # 没抄 → 报缺
    assert nv.beat_missed(1, "第1章 主角很强\n一句话：他打赢了")
    # 抄了 → 放行
    good = "第1章 突围\n开局落点：" + nv.beat_for(1) + "\n一句话：…"
    assert nv.beat_missed(1, good) == []
    # 改写了也算没抄 —— 「原样」就是原样
    bad = "第1章\n开局落点：主角在美国某大城市被警方包围\n"
    assert nv.beat_missed(1, bad)
    # 超出落点范围的章节不受这条管
    assert nv.beat_missed(9, "随便写的") == []
