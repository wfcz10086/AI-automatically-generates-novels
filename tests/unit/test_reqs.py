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


# ───────── C 类：落盘字段必须声明谁写谁读 ─────────

def test_字段登记表自检全过():
    bad = R.audit_fields()
    assert not bad, "字段登记表不合格：\n  " + "\n  ".join(bad)


def test_写进audit的字段必须登记过():
    """新加一个字段就得当场回答「谁读」—— 否则它就是下一个 dimension_results。

    实测三次「算了但没人用」：judge() 被维度平均盖掉、blocking 没进台账、
    a["issues"] 键名撞车把检测结果覆盖了。共同点是字段就在那儿、看起来是全的。
    """
    import re
    from pathlib import Path
    from server import orchestrator as o

    src = Path(o.__file__).read_text(encoding="utf-8")
    written = set(re.findall(r'a\["([a-z_]+)"\]\s*=', src))
    declared = {f.name for f in R.AUDIT_FIELDS}
    undeclared = sorted(written - declared)
    assert not undeclared, (
        f"这些字段写进了 audit 却没登记：{undeclared}\n"
        f"去 server/reqs.py 的 AUDIT_FIELDS 补一条，写明谁读它 —— "
        f"没有读者的字段要么删掉，要么标成 human: 并说明看它做什么")


def test_别人产出的字段不许在这里再写一次():
    """a["issues"] 归 evaluator.audit 所有（检测问题**列表**）。

    我把问题台账（一个**字典**）也写成 a["issues"]，一是把检测结果整个覆盖掉，
    二是下游 [i["type"] for i in a["issues"]] 迭代字典拿到键，抛
    TypeError: string indices must be integers。台账后来改叫 ledger。
    """
    import re
    from pathlib import Path
    from server import orchestrator as o

    # 判据要分清**覆盖**和**收紧**：
    #   a["score"] = min(a["score"], crit["overall"])  ← 读了旧值再取严，合法
    #   a["issues"] = self.iss.to_dict()               ← 没读旧值，整个换掉，非法
    # 右边引用了同一个字段就是收紧，没引用就是覆盖。
    # 另外要剥掉注释 —— 注释里写着「上一版我直接写 a["issues"] = 台账」，
    # 那是在讲教训，不该被当成违规（把教训逼删掉是最糟的结果）。
    lines = []
    for raw in Path(o.__file__).read_text(encoding="utf-8").splitlines():
        t = raw.strip()
        if t.startswith("#"):
            continue
        lines.append(raw.split("#", 1)[0])

    clash = []
    for n in R.OWNED_ELSEWHERE:
        for ln in lines:
            m = re.search(rf'a\["{n}"\]\s*=\s*(.+)$', ln)
            if m and f'a["{n}"]' not in m.group(1):
                clash.append(f'{n}: {ln.strip()[:70]}')
    assert not clash, (
        "这些键归别人所有，orchestrator 里整个换掉就是覆盖"
        "（要收紧请写成 min/max(a[...], 新值)）：\n  " + "\n  ".join(clash))


def test_每个字段都说得清谁在读():
    for f in R.AUDIT_FIELDS:
        assert f.consumed, f"{f.name} 没有消费者"
        if f.consumed.startswith("human:"):
            assert len(f.consumed) > len("human:"), \
                f"{f.name} 标了 human 却没说看它做什么"


# ───────── 种子点名的势力：又一条「现成的数据不该问模型要」 ─────────

def test_种子势力由模型认一次并缓存():
    """从一段话里认出哪些词是势力名，是**语义判断**，不是字符串模式。

    我先写的是「势力后缀表 + 虚词表 + 只在某几节里找」三道筛，改了四版：
      · 只有后缀规则时抠出 39 个，一大半是「才不会」「从佛门」这种句中片段
      · 加了词首边界，又漏了破折号 ——「佛门——相国寺」里的相国寺抠不出来
      · 加了虚词表，把**太虚院**误杀了（「太」在表里）
    每补一条限定词就换一种错法。换成模型认一次之后，一条限定词都不用，
    认出 13 家（比正则多认出狐族、蛇族、狐寨、少林）。

    分工是三段：模型认一次 → 程序存下来 → 之后永远用存的。
    「程序能定死的不要问模型」说的是**定死**归程序，不是说程序要自己去猜内容。
    """
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.seed_faction_names)

    # 认的活归模型
    assert "self._ask_planner" in src, "势力识别又变回程序自己猜了"
    # 结果要缓存，不能每批都问一遍
    assert "seed_cast.json" in src and "cached" in src
    # 程序只留一道**字面**校验：名字必须在种子原文里逐字出现
    assert "in txt" in src, "没校验模型给的名字是不是种子里原样有的"
    # 不许再出现硬编码的限定词表。**只扫代码行** —— 文档串里写着当初那三道筛
    # 是怎么一步步错的，那是教训；断言把教训也判成违规，就会逼人把教训删掉
    # （硬切那条测试踩过同一个坑）。
    code, in_doc = [], False
    for raw in src.splitlines():
        q3 = raw.count(chr(34) * 3) + raw.count(chr(39) * 3)
        if in_doc:
            in_doc = not (q3 % 2)
            continue
        if q3 % 2:
            in_doc = True
            continue
        if raw.strip().startswith("#"):
            continue
        code.append(raw.split("#", 1)[0])
    body = "\n".join(code)
    for junk in ("func = set(", "drop = {", "阁|派|院"):
        assert junk not in body, f"又出现了限定词表：{junk}"


def test_势力表由程序钉入种子的名字():
    import inspect
    from server import orchestrator as o
    src = inspect.getsource(o.Novelist.grow_factions)
    assert "self.pin_seed_factions()" in src, \
        "扩充之前没先把种子点名的势力钉进去 —— 模型会继续自造形近新名"
