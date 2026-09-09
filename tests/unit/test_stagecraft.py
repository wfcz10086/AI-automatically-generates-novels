"""阶段骨架 / 功能位 / 张力账 —— 结构塌陷检测的回归测试。

每条用例对应一次实测踩到的塌陷，不是凭空设计的边界。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import pytest
from server import stagecraft as sc


# 用真实形状的细纲片段，别用 "aaa" —— 出场角色栏的解析要经得起真数据
def outline(n, cast, body="剧情1：出事了。"):
    return {str(n): f"第{n}章 某章名\n出场角色：{cast}\n{body}"}


def outlines(spec):
    d = {}
    for n, cast in spec:
        d.update(outline(n, cast))
    return d


class TestCastAppearances:
    def test_parses_cast_line(self):
        app = sc.cast_appearances(outlines([(1, "西门庆、武松、玳安"),
                                            (2, "西门庆、潘金莲")]))
        assert app["西门庆"] == [1, 2]
        assert app["潘金莲"] == [2]

    def test_ignores_long_descriptions(self):
        """「一群围观的百姓」不是角色, 不该进统计。"""
        app = sc.cast_appearances(outline(1, "西门庆、一群围观看热闹的百姓"))
        assert "西门庆" in app
        assert all(len(k) <= 8 for k in app)

    def test_alias_merges(self):
        """主角在档案里叫「西门庆（林远）」, 出场栏写「西门庆」。

        不合一的话主角会被判定全书没出过场, 所有张力误报静默消解 ——
        实测 346 章的书上就是这么翻车的。
        """
        app = sc.cast_appearances(outlines([(1, "西门庆"), (2, "林远")]),
                                  aliases={"林远": "西门庆"})
        assert app["西门庆"] == [1, 2]
        assert "林远" not in app


class TestCanonName:
    def test_strips_parenthetical(self):
        assert sc.canon_name("何九叔（刑名内线）") == "何九叔"
        assert sc.canon_name("应伯爵(两头通消息)") == "应伯爵"

    def test_group_marker(self):
        assert sc.is_group("群体·阵亡的护商队弟兄")
        assert not sc.is_group("武松")
        assert sc.canon_name("群体·阵亡弟兄") == "阵亡弟兄"


def stage(name, s, e, **roles):
    full = {k: [] for k in sc.SLOT_KEYS}
    full.update(roles)
    return {"name": name, "start": s, "end": e, "goal": "目标", "steps": [],
            "roles": full, "exit": "", "labels": dict(sc._SLOT_LABEL)}


class TestVacancy:
    def test_reports_missing_required_slots(self):
        st = stage("A", 1, 50, giver=["李知县"], witness=["玳安"])
        vac = sc.vacancies(st)
        assert "阻挡者" in vac and "代价承受者" in vac
        assert "给予者" not in vac and "见证者" not in vac

    def test_optional_slots_never_reported(self):
        """同争者与内应不是每阶段都该有, 硬凑反而假。"""
        st = stage("A", 1, 50, giver=["甲"], blocker=["乙"],
                   cost=["丙"], witness=["丁"])
        assert sc.vacancies(st) == []


class TestUnregistered:
    def test_flags_names_missing_from_roster(self):
        """骨架点了名、花名册没有 → 排纲白名单会把他挡掉。

        实测某书总纲承诺「林冲欠下旧案人情留到第四幕引爆」, 林冲不在册,
        全书出现 2 次, 第四幕零出现。
        """
        stages = [stage("C", 1, 50, giver=["林冲"], blocker=["方腊"])]
        out = dict(sc.unregistered(stages, ["西门庆", "武松"]))
        assert "林冲" in out and "方腊" in out

    def test_parenthetical_is_not_a_new_person(self):
        stages = [stage("A", 1, 50, giver=["何九叔（刑名内线）"])]
        assert sc.unregistered(stages, ["何九叔"]) == []

    def test_group_needs_no_registration(self):
        stages = [stage("A", 1, 50, cost=["群体·阵亡的护商队弟兄"])]
        assert sc.unregistered(stages, ["西门庆"]) == []


class TestArcFrozen:
    def test_same_slot_across_stages_is_flagged(self):
        stages = [stage(f"S{i}", i * 10 + 1, i * 10 + 10, witness=["玳安"])
                  for i in range(4)]
        got = " ".join(sc.arc_frozen(stages))
        assert "玳安" in got

    def test_changing_slot_is_not_flagged(self):
        stages = [stage("A", 1, 10, cost=["武松"]),
                  stage("B", 11, 20, blocker=["武松"]),
                  stage("C", 21, 30, giver=["武松"])]
        assert sc.arc_frozen(stages) == []


class TestSilentResolution:
    def tension(self, a, b):
        return [{"between": [a, b], "about": "因某人某事", "state": "压着",
                 "why_unsolvable": "", "cost": "", "last_touched": 0}]

    def test_flags_vanished_party(self):
        """压着的账, 一方悄悄消失 —— 长篇最隐蔽的塌陷。"""
        app = {"武松": list(range(1, 300)), "潘金莲": [8, 10]}
        out = sc.silent_resolution(self.tension("武松", "潘金莲"), app, upto=300)
        assert out and "潘金莲" in out[0]

    def test_both_present_is_clean(self):
        app = {"武松": [290], "潘金莲": [295]}
        assert sc.silent_resolution(self.tension("武松", "潘金莲"), app, upto=300) == []

    def test_settled_tension_is_skipped(self):
        t = self.tension("武松", "潘金莲")
        t[0]["state"] = "已了结"
        assert sc.silent_resolution(t, {"武松": [1]}, upto=300) == []


class TestPromises:
    def test_starving_promise(self):
        """总纲写着的成长线, 125 章之后 200 多章没再出现过。"""
        ps = [{"id": 1, "kind": "成长线", "text": "横练武功逐级成长",
               "keywords": [], "last_advanced": 125}]
        assert sc.starving(ps, upto=346)
        assert not sc.starving(ps, upto=150)

    def test_brief_renders_only_when_hungry(self):
        ps = [{"id": 1, "kind": "成长线", "text": "x", "keywords": [],
               "last_advanced": 340}]
        assert sc.promise_brief(ps, upto=346) == ""


class TestParseJson:
    def test_survives_prose_and_fences(self):
        raw = "好的，结果如下：\n```json\n{\"stages\": [1]}\n```\n以上。"
        assert sc.parse_json(raw, "stages") == {"stages": [1]}

    def test_picks_object_with_wanted_key(self):
        raw = '{"other": 1} 然后 {"tensions": []}'
        assert sc.parse_json(raw, "tensions") == {"tensions": []}

    def test_returns_empty_on_garbage(self):
        assert sc.parse_json("完全没有 JSON", "stages") == {}


class TestThreads:
    """支线断线检测 —— 两个假阴性都是实测踩出来的，锁死。"""

    def threads(self):
        return [
            {"id": 1, "name": "武松线", "kind": "人物线",
             "owner": ["武松", "西门庆"], "org": "", "span": [1, 346],
             "cadence": 10, "beats": [], "ending": "", "last_touched": 0},
            {"id": 2, "name": "梁山线", "kind": "势力线",
             "owner": ["武松", "鲁智深", "林冲", "西门庆"], "org": "梁山",
             "span": [111, 235], "cadence": 25, "beats": [], "ending": "",
             "last_touched": 0},
        ]

    def book(self):
        d = {}
        for n in range(1, 250):
            cast = "西门庆、武松、玳安"
            if n in (150, 158, 161):
                cast += "、鲁智深、林冲"
            d.update(outline(n, cast))
        return d

    def test_protagonist_is_not_evidence(self):
        """每条线都挂着主角, 主角章章出场 —— 留着他, 所有线永远不断。"""
        t = self.threads()[0]
        assert sc.thread_owners(t, protagonist="西门庆") == ["武松"]

    def test_other_threads_pillar_is_not_evidence(self):
        """梁山线挂着武松, 而武松是自己那条线的台柱、全书都在。

        他在场只说明他自己那条线在走, 不说明梁山在走 ——
        第一版就是这样把断了 74 章的梁山线判成了 ok。
        """
        ths = self.threads()
        who = sc.thread_owners(ths[1], protagonist="西门庆", all_threads=ths)
        assert "武松" not in who
        assert set(who) == {"鲁智深", "林冲"}

    def test_never_leaves_thread_without_evidence(self):
        """判据被剔光时要退回原名单 —— 宁可判松, 不能没有判据。"""
        t = {"id": 9, "name": "x", "owner": ["武松"], "org": "", "span": [1, 10],
             "cadence": 5, "beats": [], "last_touched": 0}
        others = [{"id": 1, "name": "y", "owner": ["武松"], "span": [1, 10],
                   "cadence": 5}]
        assert sc.thread_owners(t, protagonist="西门庆", all_threads=others) == ["武松"]

    def test_replay_detects_broken_thread(self):
        ths = self.threads()
        sc.thread_last_seen(ths, self.book(), protagonist="西门庆")
        assert ths[0]["last_touched"] == 249      # 武松线一直在走
        assert ths[1]["last_touched"] == 161      # 梁山线停在最后一次露面
        overdue = " ".join(sc.thread_overdue(ths, 235))
        assert "梁山线" in overdue and "武松线" not in overdue

    def test_active_window(self):
        ths = self.threads()
        assert [t["id"] for t in sc.active_threads(ths, 50)] == [1]
        assert [t["id"] for t in sc.active_threads(ths, 150)] == [1, 2]


class TestLadder:
    def rungs(self):
        return [{"stage": "挨打不倒", "by": 1, "check": "", "reached": 0},
                {"stage": "接二流二十招", "by": 60, "check": "", "reached": 0},
                {"stage": "接一流三十招", "by": 200, "check": "", "reached": 0}]

    def test_rung_by_chapter(self):
        r = self.rungs()
        assert sc.ladder_rung(r, 30)["stage"] == "挨打不倒"
        assert sc.ladder_rung(r, 100)["stage"] == "接二流二十招"

    def test_next_rung_is_the_target(self):
        assert sc.ladder_next(self.rungs(), 100)["by"] == 200
        assert sc.ladder_next(self.rungs(), 300) is None

    def test_stall_detected(self):
        """实测力量线末次推进第 166 章, 之后 180 章没动过。"""
        r = self.rungs()
        r[1]["reached"] = 166
        assert sc.ladder_stalled({"power": r}, 346)
        assert not sc.ladder_stalled({"power": r}, 200)


class TestResolutionModes:
    """赢法单一 —— 对手再强也救不回「读者早就知道他会怎么赢」。"""

    def test_monotony_flagged(self):
        assert sc.mode_monotony(["outwit"] * 6)

    def test_mixed_is_clean(self):
        assert sc.mode_monotony(["outwit", "force", "trade",
                                 "leverage", "outwit", "persuade"]) == []

    def test_short_history_never_flags(self):
        """样本不够就不报 —— 开书前几批本来就只有一两种赢法。"""
        assert sc.mode_monotony(["outwit"] * 3) == []

    def test_brief_lists_cold_modes(self):
        b = sc.mode_brief(["outwit"] * 6)
        assert "久未用" in b and "力破" in b
        assert "智取×6" in b

    def test_unknown_keys_do_not_crash(self):
        assert isinstance(sc.mode_monotony(["nope"] * 6), list)


class TestSetbacks:
    """挫败配额 —— 从不失手的人不值得担心，读者不担心就不往下翻。"""

    def stage(self, s=1, e=50):
        return {"name": "A", "start": s, "end": e}

    def test_brief_when_quota_unmet(self):
        b = sc.setback_brief(self.stage(), [], n=10, quota=1)
        assert "0 次" in b and "判断错" in b

    def test_silent_when_quota_met(self):
        got = [{"ch": 20, "what": "押错船期赔掉半年脚费"}]
        assert sc.setback_brief(self.stage(), got, n=30, quota=1) == ""

    def test_urgent_near_stage_end(self):
        b = sc.setback_brief(self.stage(), [], n=45, quota=1)
        assert "只剩" in b

    def test_setback_in_other_stage_does_not_count(self):
        got = [{"ch": 200, "what": "别的阶段的失手"}]
        assert "0 次" in sc.setback_brief(self.stage(), got, n=10, quota=1)

    def test_missing_only_reports_finished_stages(self):
        stages = [self.stage(1, 50), self.stage(51, 100)]
        out = sc.setback_missing(stages, [], upto=60)
        assert len(out) == 1 and "1-50" in out[0]

    def test_unused_modes_flagged(self):
        """三种在循环、四种从没用过 —— 单一检测一条都不报，这条要报。"""
        hist = ["outwit", "trade", "leverage"] * 3
        assert sc.mode_monotony(hist) == []
        got = sc.mode_unused(hist)
        assert got and "力破" in got[0]

    def test_unused_silent_when_broad(self):
        hist = ["outwit", "force", "trade", "leverage",
                "persuade", "endure", "upend", "outwit", "force"]
        assert sc.mode_unused(hist) == []


class TestFulfillment:
    """推进 ≠ 兑现。这几条锁的是我自己踩过两次的静默失败。"""

    def test_prompt_actually_asks_for_done_when(self):
        """提示词里必须真的出现 done_when。

        实测替换模式没匹配上、`.replace` 静默失败，提示词里一次没提，
        于是 20 条承诺带判据的是 0 条 —— 而检测器明写「没有判据就跳过」，
        专门为此建的检测器整个失效。
        """
        seen = {}
        sc.build_promises(outline="总纲内容" * 50, title="X",
                          ask=lambda p: seen.setdefault("p", p) and "{}")
        assert "done_when" in seen["p"]
        assert "兑现判据" in seen["p"]

    def test_unfulfilled_skips_without_criteria(self):
        p = [{"id": 1, "kind": "铁律", "text": "x", "done_when": "", "done_at": 0}]
        assert sc.unfulfilled(p, upto=300, total=346) == []

    def test_unfulfilled_reports_late_only(self):
        p = [{"id": 1, "kind": "铁律", "text": "那把枪必须开过",
              "done_when": "至少三次真到最后关头开枪", "done_at": 0}]
        assert sc.unfulfilled(p, upto=100, total=346) == []      # 中途不报
        assert sc.unfulfilled(p, upto=300, total=346)            # 快完了才报

    def test_fulfilled_is_silent(self):
        p = [{"id": 1, "kind": "铁律", "text": "x",
              "done_when": "开枪", "done_at": 210}]
        assert sc.unfulfilled(p, upto=300, total=346) == []


def test_threads_prompt_carries_anti_fade_rule():
    """支线提示词必须带张力铁律。

    踩过: 铁律只写进了张力提示词, 支线于是产出「武松转身离去, 血债以时间
    销账」—— 张力账那边同一个人写的是「不死不休、禁止妥协」, 两份资产打架,
    而细纲照着支线拍子写。
    """
    import server.stagecraft as sc
    got = {}
    sc.build_threads(outline="总纲正文" * 50, stages=[], total_chapters=100,
                     title="测试", roster=["甲", "乙"],
                     ask=lambda p: got.setdefault("p", p) or '{"threads":[]}')
    p = got["p"]
    assert "明写的事件" in p, "支线提示词漏了铁律"
    for bad in ("时间冲淡", "转身离去", "不了了之"):
        assert bad in p, f"没禁掉「{bad}」这类淡出写法"
