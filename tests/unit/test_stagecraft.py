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
