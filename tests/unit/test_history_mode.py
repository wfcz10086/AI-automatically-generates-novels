"""世界基底四模式：映射由题材包声明，穿帮检测按时代锚点开关。

起因：原来只有 real/alt/none 三种，且映射硬编码在 orchestrator 里。
none 同时表示「当代现实」和「纯虚构世界」—— 2005 年的都市重生有硬时代锚点
（那年没有 4G、没有微信），玄幻大陆根本没有。混为一谈的后果是都市重生书的
穿帮词检测完全关闭（实测《重生之妖孽人生》正文出现智能手机 8 次、云计算 2 次，
虽然多在重生者预言语境里是合法的，但框架根本没在管）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
ROOT = Path(__file__).resolve().parents[2]
from server.orchestrator import CHAR_MODE_RULES, MODE_RULES, Novelist  # noqa: E402

MODES = {"real", "modern", "alt", "fanfic", "mythos", "multiworld", "invented"}
GENRES = sorted(p.stem for p in (ROOT / "packs" / "genre").glob("*.json"))


def _pack(gid):
    return json.loads((ROOT / "packs" / "genre" / f"{gid}.json").read_text(encoding="utf-8"))


def test_every_genre_declares_history_mode():
    """映射写在题材包里，不在引擎里 —— 加新题材不该回头改 orchestrator。"""
    bad = [g for g in GENRES if _pack(g).get("historyMode") not in MODES]
    assert not bad, f"这些题材没声明合法的 historyMode：{bad}"


def test_rule_tables_cover_all_modes():
    assert set(MODE_RULES) == MODES
    assert set(CHAR_MODE_RULES) == MODES


def test_mode_is_not_hardcoded_in_engine():
    """引擎里不该再有题材 id 白名单。"""
    import inspect
    src = inspect.getsource(Novelist.history_mode)
    assert "genre.get(" in src, "没有从题材包读取"
    for gid in ("lishi", "gongdou", "xiuzhen"):
        assert f'"{gid}"' not in src, f"引擎里还写死着题材 id：{gid}"


def test_modern_genres_get_anachronism_check():
    """当代现实题材必须开穿帮检测 —— 这是原设计漏掉的一整类。"""
    import inspect
    src = inspect.getsource(Novelist.anachronism_check)
    assert "modern" in src, "modern 模式没纳入穿帮检测"
    for gid in ("urban", "zhongsheng", "dianjing"):
        assert _pack(gid)["historyMode"] == "modern", f"{gid} 应是当代现实"


def test_modern_rules_allow_rebirth_foreknowledge():
    """重生者可以预言未来技术，但世界本身不能已经有 —— 这条必须写明。"""
    r = MODE_RULES["modern"]
    assert "重生者" in r and "预言" in r
    assert "周围的世界不能已经有" in r


def test_ambiguous_genres_are_flagged():
    """穿越/武侠/军事哪种基底都可能，要提示用户确认，不能默默给一个。"""
    for gid in ("chuanyue", "wuxia", "junshi"):
        assert _pack(gid).get("historyModeAmbiguous"), f"{gid} 应标记为两可"


def test_basis_card_is_the_general_mechanism():
    """内置七种基底是预设不是穷举 —— 通用机制是让模型为本书判定基底卡。

    起因：恋爱国度、赛博废土、克苏鲁神话…… 永远有装不进七种的新题材，
    一路枚举只会越补越漏。建项目时让模型读设定自己判定基底并写红线清单，
    存成 basis.md（用户可改、可重新判定），约束层照此执行。
    """
    import inspect
    src = inspect.getsource(Novelist)
    assert "def basis_card" in src, "缺少模型判定基底的机制"
    assert "def basis_words" in src, "基底卡的检测词没被提取"
    assert '"custom"' in src, "七种都不贴切时没有兜底分支"
    cons = inspect.getsource(Novelist.build_context)
    assert "世界基底红线" in cons, "基底卡红线没进约束层"


def test_detection_words_drive_the_check_not_the_mode_name():
    """有检测词就查穿帮，与模式名无关。

    「恋爱国度」被判成 invented，可它自己列出了人民币/支付宝/微信这些写了
    即穿帮的词 —— 按模式名开关就会把它们全漏掉。
    """
    import inspect
    src = inspect.getsource(Novelist.anachronism_check)
    assert "basis_words()" in src, "检测开关仍只看模式名"
    bl = inspect.getsource(Novelist.blacklist)
    assert "basis_words()" in bl, "本书专属穿帮词没进验收黑名单"


def test_modifier_genres_flagged_ambiguous():
    """系统流/无限流是修饰性题材，世界基底得另选。

    系统流自己的描述就写着「可叠加所有题材（玄幻系统/都市系统/末世系统）」，
    给它钉死一个基底，对都市系统就是错的。
    """
    for gid in ("xitongliu", "wuxianliu", "chuanyue", "wuxia", "junshi"):
        assert _pack(gid).get("historyModeAmbiguous"), f"{gid} 应标记为两可"


def test_fanfic_and_mythos_packs_exist():
    """同人与神话是两大缺口：重生成魔人布欧、穿越洪荒，原来连题材包都没有。"""
    tr = _pack("tongren")
    assert tr["historyMode"] == "fanfic"
    assert any("崩人设" in r for r in tr["rules"]), "同人没写崩人设这条雷"
    hh = _pack("honghuang")
    assert hh["historyMode"] == "mythos"
    assert any("体系" in r for r in hh["rules"])
