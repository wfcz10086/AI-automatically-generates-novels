"""要求登记表：把二十处补丁收敛成一套契约。

一天之内修了二十处，归类之后只有五种（A 没到达 / B 没人验 / C 算了没人用 /
D 门槛错 / E 被后一步撤销）。五类的共同根是流水线各段之间没有契约。

这个文件守住其中两条最要紧的：
  · 没有 check 的要求不许存在      —— 杜绝 B 类
  · 声明要进提示词的必须真的进去    —— 杜绝 A 类
"""
from __future__ import annotations

import pytest

from server import reqs as R


def test_登记表自检全过():
    """每条要求都要有：程序判据、送达方式、失败处置、标志串、来历。

    其中「没有 check 不许存在」是这套东西的地基：没人验的要求早晚被违反，
    这件事今天验证了十余次。想加一条没有判据的要求，就得先在这里删断言 ——
    那个摩擦是故意的。
    """
    bad = R.audit_registry()
    assert not bad, "登记表不合格：\n  " + "\n  ".join(bad)
    assert len(R.REGISTRY) >= 6


def test_回扫能抓住今天最难查的那个bug():
    """实测最贵的一次 A 类。

    constraints="\\n".join(cons) 在第 4850 行就把约束固化进 prompt 了，而
    「已确立的事实」「到期伏笔本批必须收掉 N 条」是在第 4934 行才 cons.append
    进去的 —— append 之后 cons 再没有任何人读。那一整块**从来没有到达过
    模型**，而记忆召回和一次提炼调用照花不误。

    日志上一切正常，只有下游「本批细纲一条都没碰」这个症状。我改了两轮措辞、
    一轮打分权重，第三轮才想到去 trace 里看提示词本身。有了回扫，这种事当场
    指名道姓。
    """
    act = {"opening_beat": True, "overdue_foreshadow": True}

    bug = "【开局落点】开局落点：纽约曼哈顿…【已确立的不可逆事实】…"
    got = R.missing_delivery(bug, "outline", act)
    assert got and "overdue_foreshadow" in got[0]
    assert "到期伏笔" in got[0]          # 报的是标志串，直接能顺着找断点

    ok = bug + "【到期伏笔·本批必须收掉其中至少 2 条】"
    assert R.missing_delivery(ok, "outline", act) == []


def test_不适用的要求不许误报():
    """第 7 章往后没有开局落点，本批没有到期伏笔时也不该报。"""
    bare = "什么都没有"
    assert R.missing_delivery(
        bare, "outline",
        {"opening_beat": False, "overdue_foreshadow": False}) == []


def test_程序直写的要求不参与回扫():
    """deliver=program 的东西由程序钉进去，不靠提示词，回扫它没有意义。"""
    beat = next(r for r in R.REGISTRY if r.id == "opening_beat")
    assert beat.deliver == R.PROGRAM
    got = R.missing_delivery("空", "outline", {"opening_beat": True,
                                               "overdue_foreshadow": False})
    assert not any("opening_beat" in g for g in got)


def test_进了格式表就必须配分数():
    """有栏没分，模型会全填「无」—— 伏笔那条踩过。"""
    for r in R.REGISTRY:
        if r.deliver == R.TABLE:
            assert r.on_fail in (R.PENALTY, R.ELIMINATE, R.BLOCK), \
                f"{r.id} 进了格式表却只记账，模型会敷衍它"


@pytest.mark.parametrize("rid", ["opening_beat", "self_address",
                                 "canon_no_contradiction", "word_range"])
def test_关键要求都登记了(rid):
    assert any(r.id == rid for r in R.REGISTRY), f"{rid} 没进登记表"


def test_三档送达都用上了():
    """能上 program 的不要停在 prompt。三档同时存在说明这个判断在被实际使用。"""
    kinds = {r.deliver for r in R.REGISTRY}
    assert kinds == {R.PROMPT, R.TABLE, R.PROGRAM}
