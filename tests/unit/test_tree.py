"""树式分解的不变式测试。

这些不变式是**程序**查的，不是模型判断的 —— 原来那套用字面重合系数 ≥0.40
做模糊匹配，而且注释里写着「这只是告警，不是拒收」，告警没人看就等于没有。
所以这里必须证明：违约一定被抓到，咬合一定放行。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from server.tree import (Contract, Node, check_seam, check_parent,   # noqa: E402
                         check_threads, audit_tree, decomposition_context)


def C(**kw):
    return Contract.from_dict(kw)


def N(nid, start, end, entry=None, exit=None, **kw):
    return Node(id=nid, level=kw.pop("level", "volume"), start=start, end=end,
                entry=entry or Contract(), exit=exit or Contract(), **kw)


# ───────────── 相邻兄弟：出口 == 进口 ─────────────

def test_出口等于进口就放行():
    st = {"hero": {"身份": "苦役", "位置": "相国寺"}, "assets": {"灵石": "3"}}
    a = N("R.1", 1, 10, exit=C(**st))
    b = N("R.2", 11, 20, entry=C(**st))
    assert check_seam(a, b) == []


def test_只是标点不同也放行():
    """模型总会换标点，归一化只去标点空白 —— 但不做同义词匹配，
    一旦允许模糊，等式就退回成告警。"""
    a = N("R.1", 1, 10, exit=C(hero={"身份": "相国寺，苦役"}))
    b = N("R.2", 11, 20, entry=C(hero={"身份": "相国寺苦役"}))
    assert check_seam(a, b) == []


def test_主角身份对不上要报():
    a = N("R.1", 1, 10, exit=C(hero={"身份": "苦役"}))
    b = N("R.2", 11, 20, entry=C(hero={"身份": "执事"}))
    errs = check_seam(a, b)
    assert len(errs) == 1 and "主角「身份」" in errs[0]


def test_人物凭空挪位置要报():
    a = N("R.1", 1, 10, exit=C(people={"阿绣": "被押在戒律堂"}))
    b = N("R.2", 11, 20, entry=C(people={"阿绣": "在商路码头记账"}))
    assert any("人物「阿绣」" in e for e in check_seam(a, b))


def test_已定死的事实不许在进口丢掉():
    a = N("R.1", 1, 10, exit=C(facts=["戒空已死"]))
    b = N("R.2", 11, 20, entry=C(facts=[]))
    assert any("已定死" in e and "戒空已死" in e for e in check_seam(a, b))


def test_线在出口开着进口却没了要报():
    t = {"id": "t1", "what": "武字疤是谁刻的", "due": "R.3"}
    a = N("R.1", 1, 10, exit=C(open_threads=[t]))
    b = N("R.2", 11, 20, entry=C(open_threads=[]))
    assert any("还开着" in e for e in check_seam(a, b))


def test_线凭空出现在进口要报():
    t = {"id": "t9", "what": "谁偷了凡册", "due": "R.4"}
    a = N("R.1", 1, 10, exit=C(open_threads=[]))
    b = N("R.2", 11, 20, entry=C(open_threads=[t]))
    assert any("凭空出现" in e for e in check_seam(a, b))


# ───────────── 父子：包住、覆盖、连续 ─────────────

def test_父进口必须等于长子进口():
    p = N("R", 1, 20, entry=C(hero={"位置": "狐寨"}), exit=C(hero={"位置": "码头"}))
    k1 = N("R.1", 1, 10, entry=C(hero={"位置": "相国寺"}),  # ← 对不上
           exit=C(hero={"位置": "半路"}))
    k2 = N("R.2", 11, 20, entry=C(hero={"位置": "半路"}), exit=C(hero={"位置": "码头"}))
    errs = check_parent(p, [k1, k2])
    assert any("父进口 vs 长子进口" in e for e in errs)


def test_子节点章号断了要报():
    p = N("R", 1, 20, entry=C(), exit=C())
    k1 = N("R.1", 1, 9, exit=C())
    k2 = N("R.2", 11, 20, entry=C())      # 第 10 章没人管
    assert any("章号断了" in e for e in check_parent(p, [k1, k2]))


def test_子节点没盖住父区间要报():
    p = N("R", 1, 30, entry=C(), exit=C())
    k1 = N("R.1", 1, 10, exit=C())
    k2 = N("R.2", 11, 20, entry=C())      # 父到 30，子只到 20
    assert any("没盖住父节点" in e for e in check_parent(p, [k1, k2]))


def test_合法的父子树零违约():
    s0 = C(hero={"位置": "狐寨"})
    s1 = C(hero={"位置": "半路"})
    s2 = C(hero={"位置": "码头"})
    p = N("R", 1, 20, entry=s0, exit=s2)
    k1 = N("R.1", 1, 10, entry=s0, exit=s1)
    k2 = N("R.2", 11, 20, entry=s1, exit=s2)
    assert check_parent(p, [k1, k2]) == []


# ───────────── 线必须死在它该死的地方 ─────────────

def test_线过了due还开着要报():
    t = {"id": "t1", "what": "武字疤是谁刻的", "due": "R.2"}
    nodes = {
        "R.1": N("R.1", 1, 10, exit=C(open_threads=[t])),
        "R.2": N("R.2", 11, 20, entry=C(open_threads=[t]),
                 exit=C(open_threads=[t])),      # 说好在 R.2 了结，却还开着
    }
    assert any("还开着" in e for e in check_threads(N("R", 1, 20), nodes))


def test_due指向不存在的节点要报():
    t = {"id": "t1", "what": "x", "due": "R.99"}
    root = N("R", 1, 20, entry=C(open_threads=[t]))
    assert any("不存在的节点" in e for e in check_threads(root, {"R.1": N("R.1", 1, 20)}))


# ───────────── 拆节点的上下文：与全书长度无关 ─────────────

def test_拆节点只看爹和左右兄弟_且字数恒定():
    """这是整棵树成立的理由：不管全书 100 章还是 1 万章，拆任一块的输入都是这几千字。"""
    parent = N("R", 1, 9000, line="破军从鼎炉走到掀翻六圣")
    left = N("R.1", 1, 3000, line="活命", exit=C(hero={"位置": "码头"}))
    me = N("R.2", 3001, 6000, line="立足",
           entry=C(hero={"位置": "码头"}), exit=C(hero={"位置": "缥缈阁"}))
    right = N("R.3", 6001, 9000, line="掀桌", entry=C(hero={"位置": "缥缈阁"}))
    ctx = decomposition_context(me, parent, left, right)
    assert "下一块要从哪开始" in ctx      # 知道未来，伏笔才有方向
    assert "缥缈阁" in ctx
    assert len(ctx) < 4000, f"拆节点的上下文不该超过几千字，实际 {len(ctx)}"


def test_全树体检把所有违约一次报出来():
    s0, s1, s2 = C(hero={"位置": "A"}), C(hero={"位置": "B"}), C(hero={"位置": "C"})
    nodes = {
        "R": N("R", 1, 20, entry=s0, exit=s2, children=["R.1", "R.2"]),
        "R.1": N("R.1", 1, 10, entry=s0, exit=s1),
        "R.2": N("R.2", 11, 20, entry=s2, exit=s2),   # ← 进口该是 s1
    }
    errs = audit_tree(nodes)
    assert errs and any("R.1→R.2" in e for e in errs)
