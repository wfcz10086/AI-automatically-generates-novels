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


# ───────────── 原地打转：合同没变就是没推进 ─────────────

def test_出口和进口一样就是原地打转():
    """「原地打转」不是文笔问题，是合同没变：读者读完一整卷，主角还是那个身份、
    还在那个地方、手里还是那些东西。163 章那本书战斗全走同一套流程，根子在这。"""
    from server.tree import check_progress
    s = C(hero={"身份": "苦役", "位置": "相国寺"}, assets={"灵石": "3"})
    nd = N("R.1", 1, 10, entry=s, exit=s, title="又打了一架")
    errs = check_progress(nd)
    assert errs and "原地打转" in errs[0]


def test_位置变了就算推进():
    from server.tree import check_progress
    a = C(hero={"身份": "苦役", "位置": "相国寺"}, assets={"灵石": "3"})
    b = C(hero={"身份": "苦役", "位置": "罗刹海"}, assets={"灵石": "3"})
    assert check_progress(N("R.1", 1, 10, entry=a, exit=b)) == []


def test_只有资源变了也算推进():
    from server.tree import check_progress
    a = C(hero={"位置": "码头"}, assets={"灵石": "3"})
    b = C(hero={"位置": "码头"}, assets={"灵石": "300", "地盘": "七号栈桥"})
    assert check_progress(N("R.1", 1, 10, entry=a, exit=b)) == []


# ───────────── 分解：模型提方案，程序判，违约打回去 ─────────────

def test_进口由程序串_模型写错也不会让兄弟接缝违约():
    """进口不许模型写 —— 它只写 exit，进口一律取上一块的 exit。
    于是兄弟接缝在构造上就不可能违约，程序只需查父子边界和章号。"""
    from server.tree import parse_children
    node = N("R", 1, 20, entry=C(hero={"位置": "狐寨"}))
    raw = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
           '"exit":{"hero":{"位置":"码头"}}},'
           '{"title":"乙","line":"y","start":11,"end":20,'
           '"exit":{"hero":{"位置":"缥缈阁"}}}]}')
    kids = parse_children(raw, node)
    assert len(kids) == 2
    assert kids[0].entry.hero["位置"] == "狐寨"        # 取自父进口
    assert kids[1].entry.hero["位置"] == "码头"        # 取自左兄弟出口
    assert check_seam(kids[0], kids[1]) == []


def test_分解不合法就把违约清单打回去():
    from server.tree import decompose
    node = N("R", 1, 20, entry=C(hero={"位置": "A"}), exit=C(hero={"位置": "C"}))
    calls = []

    bad = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
           '"exit":{"hero":{"位置":"B"}}}]}')                  # 只盖到 10，父到 20
    good = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
            '"exit":{"hero":{"位置":"B"}}},'
            '{"title":"乙","line":"y","start":11,"end":20,'
            '"exit":{"hero":{"位置":"C"}}}]}')

    def fake_call(prompt):
        calls.append(prompt)
        return bad if len(calls) == 1 else good

    kids, errs = decompose(node, None, None, None, 2, fake_call)
    assert errs == [] and len(kids) == 2
    assert len(calls) == 2, "第一版违约后应该打回去修一次"
    assert "处违约" in calls[1] and "没盖住父节点" in calls[1]


# ───────────── 已定死的事实：只增不减，程序保证 ─────────────

def test_模型漏写的旧事实由程序补回来():
    """实测老做法: canon 300 条按章号排队，只有最近 40 条(13%)进得了提示词，
    第1-145章确立的 260 条模型完全看不到——其中有「戒空已死」「金钟罩第一层报废」
    「阿绣的 12 条状态」。于是出现「金钟罩已坏却生效」「右臂突然恢复」。
    所以事实必须由程序并进去，不能指望模型每次原样带着。"""
    from server.tree import parse_children
    node = N("R", 1, 20, entry=C(facts=["戒空已死", "金钟罩第一层报废"]))
    raw = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
           '"exit":{"hero":{"位置":"码头"},"facts":["阿绣接手账房"]}},'
           '{"title":"乙","line":"y","start":11,"end":20,'
           '"exit":{"hero":{"位置":"缥缈阁"},"facts":[]}}]}')   # ← 模型全漏了
    k1, k2 = parse_children(raw, node)
    assert "戒空已死" in k1.exit.facts and "金钟罩第一层报废" in k1.exit.facts
    assert "阿绣接手账房" in k1.exit.facts
    # 第二块模型一条没写，也必须全带着
    for f in ("戒空已死", "金钟罩第一层报废", "阿绣接手账房"):
        assert f in k2.exit.facts, f"{f} 在第二块丢了"
    assert check_seam(k1, k2) == []


def test_没收的线中途不许掉_除非到期():
    from server.tree import parse_children
    t_late = {"id": "t1", "what": "武字疤是谁刻的", "due": "R.2"}
    node = N("R", 1, 20, entry=C(open_threads=[t_late]))
    raw = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
           '"exit":{"hero":{"位置":"A"},"open_threads":[]}},'      # 模型漏了
           '{"title":"乙","line":"y","start":11,"end":20,'
           '"exit":{"hero":{"位置":"B"},"open_threads":[]}}]}')    # 到期，可以掉
    k1, k2 = parse_children(raw, node)
    assert any(x.get("id") == "t1" for x in k1.exit.open_threads), "没到期就掉了"
    assert not any(x.get("id") == "t1" for x in k2.exit.open_threads), "到期该收掉"


# ───────────── 剧情太标：每块都在动同一格 ─────────────

def test_每块都只挪位置就是同一套流程():
    """「剧情太标」不是文笔问题，是每一块推动的是同一格。163 章那本书正面冲突
    全走一套（轻视→硬扛→打脸→交账），读单章很爽，连读就疲劳。"""
    from server.tree import check_variety
    kids = []
    prev = C(hero={"位置": "A", "身份": "苦役"})
    for i, loc in enumerate("BCDE"):
        ex = C(hero={"位置": loc, "身份": "苦役"})
        kids.append(N(f"R.{i+1}", i * 10 + 1, i * 10 + 10, entry=prev, exit=ex))
        prev = ex
    errs = check_variety(kids)
    assert errs and "同一格" in errs[0]


def test_动的格子有变化就放行():
    from server.tree import check_variety
    c0 = C(hero={"位置": "A", "身份": "苦役"}, assets={"灵石": "3"})
    c1 = C(hero={"位置": "B", "身份": "苦役"}, assets={"灵石": "3"})
    c2 = C(hero={"位置": "B", "身份": "执事"}, assets={"灵石": "3"})
    c3 = C(hero={"位置": "B", "身份": "执事"}, assets={"灵石": "300"})
    kids = [N("R.1", 1, 10, entry=c0, exit=c1),
            N("R.2", 11, 20, entry=c1, exit=c2),
            N("R.3", 21, 30, entry=c2, exit=c3)]
    assert check_variety(kids) == []


def test_幼子的出口由程序赋值_不让模型复述():
    """根节点 exit 有 28 个字段。原来要求「幼子出口逐字段等于父出口」，
    等于让模型一字不差抄 28 条字符串 —— 实测连着两轮都是 25 处违约、
    数字一模一样，它不是没修，是这根本不是它能修的。
    能由程序定死的，就不要问模型（和进口同一个道理）。"""
    from server.tree import parse_children
    node = N("R", 1, 20,
             entry=C(hero={"位置": "狐寨"}, facts=["世界规矩甲"]),
             exit=C(hero={"身份": "天下之主", "位置": "缥缈阁", "能力上限": "大成"},
                    assets={"地盘": "天下"}, facts=["世界规矩甲", "轮回已终结"]))
    raw = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
           '"exit":{"hero":{"位置":"码头"}}},'
           '{"title":"乙","line":"y","start":11,"end":20,'
           '"exit":{"hero":{"位置":"随便写的"}}}]}')     # ← 模型瞎写
    k1, k2 = parse_children(raw, node)
    assert k2.exit.hero["身份"] == "天下之主"        # 程序按父出口填
    assert k2.exit.hero["位置"] == "缥缈阁"
    assert k2.exit.assets["地盘"] == "天下"
    assert check_parent(node, [k1, k2]) == [], "幼子出口应当在构造上就等于父出口"
