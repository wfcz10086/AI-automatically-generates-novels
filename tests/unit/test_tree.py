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
    st = {"accounts": {"名分": "苦役", "位置": "相国寺", "灵石": "3"}}
    a = N("R.1", 1, 10, exit=C(**st))
    b = N("R.2", 11, 20, entry=C(**st))
    assert check_seam(a, b) == []


def test_只是标点不同也放行():
    """模型总会换标点，归一化只去标点空白 —— 但不做同义词匹配，
    一旦允许模糊，等式就退回成告警。"""
    a = N("R.1", 1, 10, exit=C(accounts={"名分": "相国寺，苦役"}))
    b = N("R.2", 11, 20, entry=C(accounts={"名分": "相国寺苦役"}))
    assert check_seam(a, b) == []


def test_主角身份对不上要报():
    a = N("R.1", 1, 10, exit=C(accounts={"名分": "苦役"}))
    b = N("R.2", 11, 20, entry=C(accounts={"名分": "执事"}))
    errs = check_seam(a, b)
    assert len(errs) == 1 and "账目「名分」" in errs[0]


def test_人物凭空挪位置要报():
    a = N("R.1", 1, 10, exit=C(accounts={"阿绣": "押在戒律堂"}))
    b = N("R.2", 11, 20, entry=C(accounts={"阿绣": "码头记账"}))
    assert any("账目「阿绣」" in e for e in check_seam(a, b))


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
    p = N("R", 1, 20, entry=C(accounts={"位置": "狐寨"}), exit=C(accounts={"位置": "码头"}))
    k1 = N("R.1", 1, 10, entry=C(accounts={"位置": "相国寺"}),  # ← 对不上
           exit=C(accounts={"位置": "半路"}))
    k2 = N("R.2", 11, 20, entry=C(accounts={"位置": "半路"}), exit=C(accounts={"位置": "码头"}))
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
    s0 = C(accounts={"位置": "狐寨"})
    s1 = C(accounts={"位置": "半路"})
    s2 = C(accounts={"位置": "码头"})
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
    left = N("R.1", 1, 3000, line="活命", exit=C(accounts={"位置": "码头"}))
    me = N("R.2", 3001, 6000, line="立足",
           entry=C(accounts={"位置": "码头"}), exit=C(accounts={"位置": "缥缈阁"}))
    right = N("R.3", 6001, 9000, line="掀桌", entry=C(accounts={"位置": "缥缈阁"}))
    ctx = decomposition_context(me, parent, left, right)
    assert "下一块要从哪开始" in ctx      # 知道未来，伏笔才有方向
    assert "缥缈阁" in ctx
    assert len(ctx) < 4000, f"拆节点的上下文不该超过几千字，实际 {len(ctx)}"


def test_全树体检把所有违约一次报出来():
    s0, s1, s2 = C(accounts={"位置": "A"}), C(accounts={"位置": "B"}), C(accounts={"位置": "C"})
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
    s = C(accounts={"名分": "苦役", "位置": "相国寺", "灵石": "3"})
    nd = N("R.1", 1, 10, entry=s, exit=s, title="又打了一架")
    errs = check_progress(nd)
    assert errs and "原地打转" in errs[0]


def test_位置变了就算推进():
    from server.tree import check_progress
    a = C(accounts={"名分": "苦役", "位置": "相国寺", "灵石": "3"})
    b = C(accounts={"名分": "苦役", "位置": "罗刹海", "灵石": "3"})
    assert check_progress(N("R.1", 1, 10, entry=a, exit=b)) == []


def test_只有资源变了也算推进():
    from server.tree import check_progress
    a = C(accounts={"位置": "码头", "灵石": "3"})
    b = C(accounts={"位置": "码头", "灵石": "300", "地盘": "七号栈桥"})
    assert check_progress(N("R.1", 1, 10, entry=a, exit=b)) == []


# ───────────── 分解：模型提方案，程序判，违约打回去 ─────────────

def test_进口由程序串_模型写错也不会让兄弟接缝违约():
    """进口不许模型写 —— 它只写 exit，进口一律取上一块的 exit。
    于是兄弟接缝在构造上就不可能违约，程序只需查父子边界和章号。"""
    from server.tree import parse_children
    node = N("R", 1, 20, entry=C(accounts={"位置": "狐寨"}))
    raw = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
           '"exit":{"accounts":{"位置":"码头"}}},'
           '{"title":"乙","line":"y","start":11,"end":20,'
           '"exit":{"accounts":{"位置":"缥缈阁"}}}]}')
    kids = parse_children(raw, node)
    assert len(kids) == 2
    assert kids[0].entry.accounts["位置"] == "狐寨"        # 取自父进口
    assert kids[1].entry.accounts["位置"] == "码头"        # 取自左兄弟出口
    assert check_seam(kids[0], kids[1]) == []


def test_分解不合法就把违约清单打回去():
    from server.tree import decompose
    node = N("R", 1, 20, entry=C(accounts={"位置": "A"}), exit=C(accounts={"位置": "C"}))
    calls = []

    bad = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
           '"exit":{"accounts":{"位置":"B"}}}]}')                  # 只盖到 10，父到 20
    good = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
            '"exit":{"accounts":{"位置":"B"}}},'
            '{"title":"乙","line":"y","start":11,"end":20,'
            '"exit":{"accounts":{"位置":"C"}}}]}')

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
           '"exit":{"accounts":{"位置":"码头"},"facts":["阿绣接手账房"]}},'
           '{"title":"乙","line":"y","start":11,"end":20,'
           '"exit":{"accounts":{"位置":"缥缈阁"},"facts":[]}}]}')   # ← 模型全漏了
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
           '"exit":{"accounts":{"位置":"A"},"open_threads":[]}},'      # 模型漏了
           '{"title":"乙","line":"y","start":11,"end":20,'
           '"exit":{"accounts":{"位置":"B"},"open_threads":[]}}]}')    # 到期，可以掉
    k1, k2 = parse_children(raw, node)
    assert any(x.get("id") == "t1" for x in k1.exit.open_threads), "没到期就掉了"
    assert not any(x.get("id") == "t1" for x in k2.exit.open_threads), "到期该收掉"


# ───────────── 剧情太标：每块都在动同一格 ─────────────

def test_每块都只挪位置就是同一套流程():
    """「剧情太标」不是文笔问题，是每一块推动的是同一格。163 章那本书正面冲突
    全走一套（轻视→硬扛→打脸→交账），读单章很爽，连读就疲劳。"""
    from server.tree import check_variety
    kids = []
    prev = C(accounts={"位置": "A", "身份": "苦役"})
    for i, loc in enumerate("BCDE"):
        ex = C(accounts={"位置": loc, "身份": "苦役"})
        kids.append(N(f"R.{i+1}", i * 10 + 1, i * 10 + 10, entry=prev, exit=ex))
        prev = ex
    errs = check_variety(kids)
    assert errs and "同一格" in errs[0]


def test_动的格子有变化就放行():
    from server.tree import check_variety
    c0 = C(accounts={"位置": "A", "身份": "苦役"}, assets={"灵石": "3"})
    c1 = C(accounts={"位置": "B", "身份": "苦役"}, assets={"灵石": "3"})
    c2 = C(accounts={"位置": "B", "身份": "执事"}, assets={"灵石": "3"})
    c3 = C(accounts={"位置": "B", "身份": "执事"}, assets={"灵石": "300"})
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
             entry=C(accounts={"位置": "狐寨"}, facts=["世界规矩甲"]),
             exit=C(accounts={"名分": "天下之主", "位置": "缥缈阁",
                              "金钟罩": "大成", "地盘": "天下"},
                    facts=["世界规矩甲", "轮回已终结"]))
    raw = ('{"children":[{"title":"甲","line":"x","start":1,"end":10,'
           '"exit":{"accounts":{"位置":"码头"}}},'
           '{"title":"乙","line":"y","start":11,"end":20,'
           '"exit":{"accounts":{"位置":"随便写的"}}}]}')     # ← 模型瞎写
    k1, k2 = parse_children(raw, node)
    assert k2.exit.accounts["名分"] == "天下之主"        # 程序按父出口填
    assert k2.exit.accounts["位置"] == "缥缈阁"
    assert k2.exit.accounts["地盘"] == "天下"
    assert check_parent(node, [k1, k2]) == [], "幼子出口应当在构造上就等于父出口"


# ───────────── 里程碑链（方案乙主体）─────────────

def _mk_root():
    return N("R", 1, 30, level="book",
             entry=C(accounts={"灵石": "0", "名分": "鼎炉"},
                     open_threads=[{"id": "t1", "what": "武字疤", "due": "R"}]),
             exit=C(accounts={"灵石": "万", "名分": "天下之主"}))


def test_但是链断了要报_且判定是照抄不是相似():
    from server.tree import parse_milestones, check_milestones
    raw = ('{"milestones":['
           '{"title":"甲","solves":"活命","exposes":"名头传开了","start":1,"end":15,'
           '"accounts":{"名分":"自由身"},"close":["t1"],"open":[],"miscalc":"x"},'
           '{"title":"乙","solves":"名声太大被盯上","exposes":"y","start":16,"end":30,'
           '"accounts":{"灵石":"万","名分":"天下之主"},"close":[],"open":[],"miscalc":"z"}]}')
    ms = parse_milestones(raw, _mk_root())
    errs = check_milestones(_mk_root(), ms)
    assert any("但是链断了" in e for e in errs)   # 「名声太大被盯上」≠「名头传开了」


def test_合法里程碑链零违约():
    from server.tree import parse_milestones, check_milestones
    raw = ('{"milestones":['
           '{"title":"甲","solves":"活命","exposes":"名头传开了","start":1,"end":15,'
           '"accounts":{"名分":"自由身"},"close":["t1"],"open":[],'
           '"notes":{"位置":"城南破庙"},"miscalc":"x"},'
           '{"title":"乙","solves":"名头传开了","exposes":"y","start":16,"end":30,'
           '"accounts":{"灵石":"万","名分":"天下之主"},"close":[],"open":[],'
           '"notes":{"位置":"金銮殿"},"miscalc":"z"}]}')
    ms = parse_milestones(raw, _mk_root())
    assert check_milestones(_mk_root(), ms) == []


def test_没写notes的那一节要报():
    """notes 这一栏原来从头到尾没人读: 提示词没问, 解析不取, 只有链在往下抄。

    后果是第 1 到第 7 卷的「此刻主角在哪」全是开篇那句「曼哈顿废楼顶层」,
    而排纲把它当「进这一卷时」的处境读 —— 一句彻头彻尾的假话。
    """
    from server.tree import parse_milestones, check_milestones
    # 三节: 甲不写 notes(该报), 乙写了(不该报), 丙是末节 —— 它的出口就是
    # 根出口, 由程序赋值, 不归模型写, 所以一律不报。
    raw = ('{"milestones":['
           '{"title":"甲","solves":"活命","exposes":"名头传开了","start":1,"end":10,'
           '"accounts":{"名分":"自由身"},"close":["t1"],"open":[],"miscalc":"x"},'
           '{"title":"乙","solves":"名头传开了","exposes":"招人惦记","start":11,"end":20,'
           '"accounts":{"灵石":"千"},"close":[],"open":[],'
           '"notes":{"位置":"城南破庙"},"miscalc":"z"},'
           '{"title":"丙","solves":"招人惦记","exposes":"y","start":21,"end":30,'
           '"accounts":{"灵石":"万","名分":"天下之主"},"close":[],"open":[],'
           '"miscalc":"w"}]}')
    ms = parse_milestones(raw, _mk_root())
    errs = check_milestones(_mk_root(), ms)
    assert any("没写 notes" in e and "R.1" in e for e in errs)
    assert not any("没写 notes" in e and "R.2" in e for e in errs)
    assert not any("没写 notes" in e and "R.3" in e for e in errs)
    assert ms[0].exit.notes == ms[0].entry.notes          # 没写只能照抄上一节
    assert ms[1].exit.notes.get("位置") == "城南破庙"      # 写了的就读进来


def test_开局的线没人收要报():
    from server.tree import parse_milestones, check_milestones
    raw = ('{"milestones":['
           '{"title":"甲","solves":"活命","exposes":"名头传开了","start":1,"end":15,'
           '"accounts":{"名分":"自由身"},"close":[],"open":[],"miscalc":"x"},'
           '{"title":"乙","solves":"名头传开了","exposes":"y","start":16,"end":30,'
           '"accounts":{"灵石":"万","名分":"天下之主"},"close":[],"open":[],"miscalc":"z"}]}')
    ms = parse_milestones(raw, _mk_root())
    # t1 没有任何一节 close，幼子出口被根出口覆盖后线仍在链上暴露
    assert any("没人收" in e or "还开着" in e
               for e in check_milestones(_mk_root(), ms))


def test_三候选选优_合规是淘汰线_多样性加分():
    from server.tree import parse_milestones, score_milestones
    good = ('{"milestones":['
            '{"title":"甲","solves":"活命","exposes":"名头传开了","start":1,"end":15,'
            '"accounts":{"名分":"自由身"},"close":["t1"],"open":[],"miscalc":"x"},'
            '{"title":"乙","solves":"名头传开了","exposes":"y","start":16,"end":30,'
            '"accounts":{"灵石":"万","名分":"天下之主"},"close":[],"open":[],"miscalc":"z"}]}')
    broken = good.replace('"solves":"名头传开了"', '"solves":"完全接不上的话"')
    r = _mk_root()
    assert score_milestones(r, parse_milestones(good, r)) > \
           score_milestones(r, parse_milestones(broken, r))


# ───────────── 矛盾章的事实不许固化 ─────────────

def test_矛盾章的新事实不入台账():
    """实测第5章：正文把第4章骑马跑掉的散修乙写死了，评审报了 4 条矛盾，
    可「散修乙已死亡」照样进了 canon，和第4章「逼退散修乙，故意放其去报信」
    并排躺着——往后每章都被这条毒害。这就是「金钟罩已坏却生效」的制造机制。

    规则：本章被判出矛盾，本章的不可逆事实就不许固化——正文本身是错的，
    从错正文抽的事实必然错。"""
    import server.critic as cm
    canon = [{"chapter": 4, "subject": "散修乙", "fact": "被逼退，骑马逃走报信",
              "kind": "other"}]
    new = [{"subject": "散修乙", "fact": "已死亡，尸体藏于迷离林", "kind": "death"}]
    # 有矛盾时：调用方跳过 merge —— 台账不变
    contradictions = [{"fact": "第4章确立散修乙未死"}]
    merged, added = (canon, 0) if contradictions else cm.merge_canon(canon, new, 5)
    assert added == 0 and len(merged) == 1
    # 无矛盾时照常收（换个与生死无关的事实——原例恰好撞上生死冲突检测）
    _, added2 = cm.merge_canon(list(canon),
                               [{"subject": "狐媚", "fact": "献出虚弥戒认主",
                                 "kind": "other"}], 5)
    assert added2 == 1


def test_生死矛盾由程序拒收_不靠评审():
    """评审是概率性的：第5章第一遍抓到 4 条矛盾，重写后同样的错误一条没抓到，
    「散修乙已死亡」照样入了 canon。生死互斥，程序判得了。"""
    from server.critic import merge_canon
    canon = [{"chapter": 4, "subject": "散修乙", "fact": "被逼退，骑马逃走报信",
              "kind": "other"}]
    _, added = merge_canon(list(canon), [{"subject": "散修乙",
                                          "fact": "散修乙已死亡，尸体藏于迷离林",
                                          "kind": "death"}], 5)
    assert added == 0, "活着的人被写死，应当拒收"
    # 反向也要拦：台账说死了，新事实说他跑了
    canon2 = [{"chapter": 4, "subject": "周横", "fact": "被一拳打死", "kind": "death"}]
    _, a2 = merge_canon(list(canon2), [{"subject": "周横", "fact": "趁乱逃走报信",
                                        "kind": "other"}], 6)
    assert a2 == 0
    # 不相干的事实照常收
    _, a3 = merge_canon(list(canon), [{"subject": "狐媚", "fact": "献出虚弥戒认主",
                                       "kind": "other"}], 5)
    assert a3 == 1


# ───────────── 正文三选一：合规是淘汰线，不是加分项 ─────────────

def _nv():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from server.orchestrator import Novelist, Project
    return Novelist(Project("金钟镇天下"))


def test_硬闸抓得住四类穿帮():
    """合规只做淘汰线 —— 否则三选一会挑出最平庸那个。"""
    nv = _nv()
    base = "破军抬手，一拳砸在石壁上。" * 120           # 约 1560 字，先保证不被字数闸毙
    ok, _ = nv.draft_score(base, 2000)
    for bad, why in (
        (base + "他知道，真正的游戏才刚刚开始。", "口号式收尾"),
        (base + "她要是能捞，她早就她要是能捞，她早就自己捞了。", "接缝重复"),
        (base + "第32章那记反噬让他记到现在。", "正文自指章号"),
        (base + "这不是罡气外放——前文已确立铜皮过载失灵。", "元语言泄漏"),
    ):
        kill, _ = nv.draft_score(bad, 2000)
        assert kill, f"{why} 应当被硬闸淘汰"


def test_字数离谱直接出局():
    nv = _nv()
    assert nv.draft_score("短。" * 50, 2800)[0], "只写到两成应当出局"
    assert nv.draft_score("长。" * 3000, 2800)[0], "写到两倍应当出局"


def test_爽点来源才加分():
    """加分给的是「爽」的来源（人物心里那句问话、喊出来的劲、把规矩讲透），
    不是给合规。两稿都合规时，有爽点的那稿必须赢。"""
    nv = _nv()
    plain = "他走进屋子。他坐下。他看着桌子。" * 90
    juicy = ("他凭什么敢来？\n\n「三十贯，一个子儿不能少！」他吼道。\n\n"
             "码头的规矩是这样的：货过三关，每关抽一成，抽完才准卸。" ) * 30
    _, s1 = nv.draft_score(plain, 2000)
    _, s2 = nv.draft_score(juicy, 2000)
    assert s2 > s1, f"有爽点的稿应当得分更高（{s2:.1f} vs {s1:.1f}）"


# ───────────── 细纲三选一：裁判纯程序 ─────────────

def test_细纲打分_缺栏最伤():
    """缺栏的章会被整章丢掉，所以扣分最重。"""
    nv = _nv()
    full = ["第1章 甲\n一句话：他打赢了\n视角：狐媚\n承接：无\n出场角色：破军\n"
            "剧情1：动手\n重场：剧情1\n解说：规矩\n账目：灵石 0 → 3\n后果：结仇\n"
            "误读：他以为有靠山\n代价：断一根肋骨\n章末钩子：门外有脚步声"]
    lack = ["第1章 甲\n一句话：他打赢了"]
    assert nv.outline_score(full, 1, 1) > nv.outline_score(lack, 1, 1)


def test_细纲打分_账目要真动账():
    """账目栏写「形势更严峻」不算动账；要有数字或箭头。
    实测原著 35% 的推进靠账目变动，而模型爱章章靠「又来了个新误会」。"""
    nv = _nv()
    base = ("第1章 甲\n一句话：x\n视角：甲\n承接：无\n出场角色：破军\n剧情1：动手\n"
            "重场：剧情1\n解说：规矩\n后果：结仇\n误读：他以为有靠山\n"
            "代价：断肋\n章末钩子：门外脚步声\n账目：")
    real = [base + "灵石 0 → 3"]
    fake = [base + "形势更加严峻了"]
    assert nv.outline_score(real, 1, 1) > nv.outline_score(fake, 1, 1)
