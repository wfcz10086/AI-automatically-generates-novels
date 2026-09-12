"""自称：声明了，程序就得查。

实测这一版的完整病历：
  · 角色卡（模型生成）写着「**自称**：洒家」，还配了三句「洒家不信神佛…」
  · 种子（作者亲手写）指定的台词是「也配和**我**比肉身？」「**我**命由我不由天」
  · 两者打架，谁也没查，于是正文在中间摇摆：第 2 章用了八次「洒家」，
    第 1、6 章各一次，第 3-5、7-9 章一次都没有 —— 十章里七章漂

修的是根因（卡以种子为准）。修完拿同样十章回测：3/10 → 9/10。
**正文一直是对的，错的是卡。**
"""
from __future__ import annotations

from server import voice as V

SEED = (
    "【反直觉动作】满天下都在求丹求符，他偏不修仙。\n"
    "第一句台词：「哼，你们这些只知道服丹炼药、画符念咒的，也配和我比肉身？」\n"
    "反复回响的那句：「我命由我不由天——这句话，我是用拳头一个字一个字打出来的。」\n"
    "【六本账】肉身／内力／地盘\n")

CARD_BAD = (
    "### 1. 许破军：金钟莽夫\n"
    "**自称**：洒家\n"
    "**原声样本**：洒家不信神佛，只信拳头硬不硬。｜别跟洒家讲道理。\n"
    "\n### 2. 法海：相国寺监院\n"
    "**自称**：老衲\n")


def test_从种子里读出作者用的自称():
    want, evid = V.seed_self_address(SEED)
    assert want == "我"
    assert evid and "比肉身" in evid[0]


def test_种子没指定台词就不管():
    assert V.seed_self_address("【基调】要爽。")[0] is None


def test_读得出角色卡声明的自称():
    assert V.card_self_address(CARD_BAD, "许破军") == "洒家"
    assert V.card_self_address(CARD_BAD, "法海") == "老衲"


def test_卡跟种子打架要报出来():
    bad = V.check_card_vs_seed(SEED, CARD_BAD, "许破军")
    assert bad and "洒家" in bad[0] and "我" in bad[0]


def test_卡跟种子一致就不报():
    good = CARD_BAD.replace("洒家", "我")
    assert V.check_card_vs_seed(SEED, good, "许破军") == []


def test_别人的自称不受主角那条管():
    """法海自称老衲是对的 —— 种子说的是主角，别动别人的卡。"""
    assert V.check_card_vs_seed(SEED, CARD_BAD, "法海") == []


def test_台词归属靠就近不认错人():
    txt = ('许破军咧嘴一笑。\n\n“我不懂什么灵力。”\n\n'
           '法海闭目念珠。\n\n“老衲只问因果。”\n')
    qs = V.protagonist_quotes(txt, "许破军")
    assert any("不懂什么灵力" in q for q in qs)
    assert not any("老衲" in q for q in qs)


def test_主角说话用了别的自称就报漂移():
    txt = '许破军冷笑：“洒家只懂拳头。”\n许破军又道：“洒家不讲道理。”'
    bad = V.check_prose_voice(txt, "许破军", "我")
    assert bad and "洒家" in bad[0]


def test_用对了不报():
    txt = '许破军冷笑：“我只懂拳头。”'
    assert V.check_prose_voice(txt, "许破军", "我") == []


def test_这一章没自称不算漂():
    """一章里主角本来就可能一次都不自称 —— 那是正常的，不是错。"""
    txt = '许破军冷笑：“拳头就是道理。”'
    assert V.check_prose_voice(txt, "许破军", "我") == []


def test_对错都有时以对的为准():
    """三稿选优里，只要他有一句说对了，就不该判成漂移。"""
    txt = ('许破军说：“我只懂拳头。”\n'
           '许破军又说：“咱不跟你废话。”')
    assert V.check_prose_voice(txt, "许破军", "我") == []
