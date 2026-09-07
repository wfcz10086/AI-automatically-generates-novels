"""内置提示词必须是通用的：模式专属的话只能待在分模式的表里。

起因：角色档案模板里写死了「不许出现真实朝代名（大明/大宋…）」和
「架空世界禁用真实历史人物」，而 real 模式写的就是真实朝代 —— 朱元璋、
蓝玉、洪武年间都是必需的，这两条在那种书里完全说反了。世界观那一步早就
按 MODE_RULES 分了，角色这一步、检索资料注入那两处都漏了。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.orchestrator import BUILTIN_PROMPTS, CHAR_MODE_RULES, MODE_RULES  # noqa: E402

# 只对某一种历史模式成立的措辞
MODE_SPECIFIC = [
    (r"架空世界", "只对 alt 成立"),
    (r"虚构国号", "只对 alt 成立"),
    (r"不得出现真实存在过的", "只对 alt 成立"),
    (r"真实朝代名（|不许出现真实朝代", "只对 alt 成立"),
    (r"不要自造国号", "只对 real 成立"),
    (r"写的就是真实朝代", "只对 real 成立"),
]


def test_builtin_prompts_carry_no_mode_specific_wording():
    bad = []
    for key, tpl in BUILTIN_PROMPTS.items():
        for pat, why in MODE_SPECIFIC:
            if re.search(pat, tpl):
                bad.append(f"{key} 含「{pat}」（{why}）")
    assert not bad, "内置模板里混入了模式专属措辞：" + "；".join(bad)


def test_every_mode_has_character_rules():
    """每种世界基底都要有自己的角色约束，缺一种就会退化成默认值。"""
    assert set(CHAR_MODE_RULES) == set(MODE_RULES) == {
        "alt", "real", "modern", "fanfic", "mythos", "multiworld", "invented"}


def test_real_mode_allows_real_people():
    """real 模式的角色约束不能禁真实历史人物 —— 那正是这类书的主角配角。"""
    real = CHAR_MODE_RULES["real"]
    assert "禁用真实历史人物" not in real
    assert "真名" in real and "史实" in real


def test_alt_mode_still_forbids_real_people():
    """架空模式的护栏不能被顺手删掉。"""
    alt = CHAR_MODE_RULES["alt"]
    assert "真实历史人物" in alt and "虚构国号" in alt
