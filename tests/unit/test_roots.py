"""种子根系：可控的发散。

守的是三件事，每件都对应一个实测踩过的坑：
  · 打分器认得出「同源」—— 一个仵作占 43% 章节就是同源池的下场
  · 配发按类轮转 —— 让模型自己挑素材它只会挑最熟的那类，等于没换方向
  · 节尾收口 —— 只发散不收口，到节点末尾出口合同对不上，那才是真失控
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import roots as R           # noqa: E402


def _pool(**kw):
    return {k: [{"id": f"{k[:2]}{i+1}", "what": w, "used": None}
                for i, w in enumerate(v)] for k, v in kw.items()}


def test_同源池得分必须低于分散池():
    """这是打分器唯一真正要分辨的事。全都是「何九叔又翻案」的池子，
    条数再多也撑不起一本书 —— 实测正是它拖垮了 68 章里的 29 章。"""
    same = _pool(conflict=["何九叔又翻供说尸检有问题，主角要压住他"] * 6)
    div = _pool(
        conflict=["漕帮截了主角的粮船索要买路钱", "县丞的小舅子盯上药铺想强买"],
        card=["手上有蔡京门生的欠条可以兑现"],
        reversal=["账房先生其实是提刑司的眼线"],
        cost=["要保住铺子就得交出一个自己人"],
        arena=["沧州盐场，另一套规矩另一批人"],
        device=["一份能对上官仓底账的假账本"])
    assert R.score(div) > R.score(same)
    assert R.score({}) < 0                      # 空池不许被选中


def test_配发按类轮转_不会一直发同一类():
    """模型自己挑会一直挑冲突（它最熟）。程序按批次轮转，保证六类都轮得到。"""
    pool = _pool(**{k: [f"{k}素材{i}" for i in range(5)] for k in R.KINDS})
    seen = set()
    for batch_start in (1, 6, 11, 16, 21, 26):
        got = R.pick(pool, batch_start, 2)
        seen |= {p["kind"] for p in got}
        R.mark_used(pool, got, batch_start)
    assert len(seen) >= 5, f"六批只轮到 {seen}"


def test_用过即焚_同一条不会发第二次():
    pool = _pool(conflict=["甲事", "乙事"], card=["丙事"])
    a = R.pick(pool, 1, 3)
    R.mark_used(pool, a, 1)
    b = R.pick(pool, 6, 3)
    assert not ({(x["kind"], x["id"]) for x in a}
                & {(x["kind"], x["id"]) for x in b})
    assert len(R.unused(pool)) == 3 - len(a)


def test_节尾进收口模式():
    """一节 46-85 共 40 章，尾 30% 即第 74 章起停止配发新素材。"""
    assert not R.closing_mode(50, 46, 85)
    assert not R.closing_mode(73, 46, 85)
    assert R.closing_mode(74, 46, 85)
    assert R.closing_mode(85, 46, 85)


def test_收口令必须带出口合同原文():
    """「收口到哪里去」不能靠模型猜 —— 出口合同得逐字在场。"""
    class _C:
        def brief(self, cap=900):
            return "西门庆已在沧州落户，盐引到手，何九叔已死"

    class _N:
        exit = _C()

    t = R.closing_brief(_N(), 8)
    assert "盐引到手" in t and "还剩 8 章" in t
    assert "不许引入新的敌人" in t          # 收口段不许再开新线


def test_配发块说清了是必须用掉而不是备选():
    picks = [{"id": "co1", "kind": "conflict", "what": "漕帮截粮船索要买路钱"}]
    t = R.brief(picks, 17)
    assert "必须" in t and "co1" in t and "17" in t


def test_解析容得下模型的坏输出():
    assert R.parse("这不是 JSON") == {}
    assert R.parse('```json\n{"conflict":["漕帮截了粮船要买路钱"]}\n```')["conflict"]
    # 太短的条目是占位符，不要
    assert "card" not in R.parse('{"card":["无","略"]}')


def test_id前缀不许撞号():
    """conflict 和 cost 都是 "co" —— 第一次真跑就撞上了：日志里的 co2 看不出
    是哪一类，而退回池子按 id 匹配会把另一类的同号素材一起退回去。"""
    assert len(set(R.PREFIX.values())) == len(R.KINDS)
    assert set(R.PREFIX) == set(R.KINDS)
    pool = R.parse('{"conflict":["甲方来闹事要收保护费"],'
                   '"cost":["赢了也得赔上一个自己人"]}')
    ids = [r["id"] for rows in pool.values() for r in rows]
    assert len(set(ids)) == len(ids), f"撞号: {ids}"
