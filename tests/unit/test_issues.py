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
