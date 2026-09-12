"""自称与口吻：角色卡声明了，程序就得查。

—— 为什么要有这个文件 ——

实测这一版：角色卡写着「**自称**：洒家」，还配了三句「洒家不信神佛…」的
原声样本。而种子里作者亲手指定的两句台词是：

    「哼，你们这些只知道服丹炼药、画符念咒的，也配和**我**比肉身？」
    「**我**命由我不由天——这句话，我是用拳头一个字一个字打出来的。」

卡是模型自己编的，跟种子打架。于是正文在两者之间摇摆：第 2 章用了八次
「洒家」，第 1、6 章各一次，**第 3-5、7-9 章一次都没有**。

同一个病的第十例：**声明了但没人查的东西，早晚对不上。**
这里判的全是字面，不做语义推断 —— 自称是有限的封闭词表，恰好判得了。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

#: 汉语自称的封闭词表。长的排前面，避免「本座」被「本」之类切掉。
#: 「我」放最后 —— 它是缺省值，别的都匹配不上时才算它。
SELF_ADDRESS = ("老衲", "贫道", "贫僧", "洒家", "本座", "本尊", "本王", "本宫",
                "本官", "哀家", "老夫", "老朽", "老子", "在下", "不才", "小人",
                "奴家", "奴婢", "属下", "末将", "微臣", "孤家", "寡人",
                "朕", "孤", "俺", "咱", "某", "吾", "我")

#: 种子里「作者亲手指定的台词」长什么样。这几栏是**作者的原话**，
#: 优先级高于模型生成的角色卡。
_SEED_LINE_TAGS = ("第一句台词", "反复回响的那句", "口头禅", "原声样本",
                   "台词", "标志性台词")

_QUOTE = re.compile(r"[「“\"]([^」”\"]{2,120})[」”\"]")


def _first_self(text: str) -> Optional[str]:
    """一段话里用的是哪个自称。按词表顺序取第一个命中的。"""
    for w in SELF_ADDRESS:
        if w in text:
            return w
    return None


def seed_self_address(premise: str) -> Tuple[Optional[str], List[str]]:
    """种子里作者亲手写的台词用的是哪个自称。

    返回 (自称, 证据台词)。作者没指定台词就返回 (None, [])。
    """
    lines: List[str] = []
    for tag in _SEED_LINE_TAGS:
        for m in re.finditer(rf"{tag}\s*[:：]\s*(.+)", premise or ""):
            seg = m.group(1)
            got = _QUOTE.findall(seg)
            lines += got if got else [seg.strip()]
    hits = [(_first_self(x), x) for x in lines]
    hits = [(w, x) for w, x in hits if w]
    if not hits:
        return None, []
    # 出现最多的那个算数（作者可能一句用「我」一句用别的）
    tally: Dict[str, int] = {}
    for w, _ in hits:
        tally[w] = tally.get(w, 0) + 1
    best = max(tally, key=lambda k: tally[k])
    return best, [x for w, x in hits if w == best]


def card_self_address(card_md: str, name: str = "") -> Optional[str]:
    """角色卡里声明的自称。给了名字就只看那个人的那一段。"""
    block = card_md or ""
    if name:
        i = block.find(name)
        if i < 0:
            return None
        j = block.find("\n### ", i)
        block = block[i:j if j > 0 else len(block)]
    m = re.search(r"\*\*自称\*\*\s*[:：]\s*(\S+)", block)
    if not m:
        return None
    return _first_self(m.group(1)) or m.group(1).strip()[:4]


def check_card_vs_seed(premise: str, card_md: str,
                       name: str = "") -> List[str]:
    """角色卡的自称不许跟种子里作者写的台词打架。

    这是**根因**那一层：卡一旦编错，后面每一章都在错的两个选项之间摇摆。
    """
    want, evid = seed_self_address(premise)
    if not want:
        return []
    # 种子里的台词是**主角**的。角色卡按模板第一条就是主角, 名字对不上就
    # 什么都不做 —— 拿主角的自称去要求法海自称「我」是错的(他该自称老衲)。
    if name:
        m = re.search(r"^###\s*\d+[.．、]?\s*([^：:\n]{2,8})", card_md or "", re.M)
        if m and m.group(1).strip() != name.strip():
            return []
    got = card_self_address(card_md, name)
    if not got or got == want:
        return []
    return [f"角色卡把{name or '主角'}的自称定成「{got}」，"
            f"但种子里作者亲手写的台词用的是「{want}」："
            f"{evid[0][:40] if evid else ''} —— 以种子为准。"]


def protagonist_quotes(text: str, name: str, window: int = 45) -> List[str]:
    """从正文里挑出**大概率是主角说的**那些话。

    归属只看**这句引语和上一句引语之间的那段叙述**，不跨过别人的话去够。
    原来用固定窗口往前扫 45 字, 实测会跨过上一句引语: 「许破军咧嘴一笑。
    "我不懂灵力。" 法海闭目念珠。"老衲只问因果。"」—— 法海那句被算到
    许破军头上, 于是「老衲」成了主角的自称漂移。

    不求全，求准：漏掉几句不要紧，认错人会误报。
    """
    if not name:
        return []
    out = []
    spans = list(_QUOTE.finditer(text or ""))
    for i, m in enumerate(spans):
        lo = spans[i - 1].end() if i else 0            # 不跨过上一句引语
        hi = spans[i + 1].start() if i + 1 < len(spans) else len(text)
        before = text[max(lo, m.start() - window):m.start()]
        after = text[m.end():min(hi, m.end() + window)]
        if name in before or name in after:
            out.append(m.group(1))
    return out


def check_prose_voice(text: str, name: str, want: str) -> List[str]:
    """主角说话时用的自称，跟角色卡声明的对不对得上。

    只在**他确实自称了**的时候判：一章里他可能一句自称都没有（正常），
    但如果他用了别的自称，那就是漂移。
    """
    if not want:
        return []
    quotes = protagonist_quotes(text, name)
    if not quotes:
        return []
    wrong: Dict[str, int] = {}
    right = 0
    for q in quotes:
        if want in q:
            right += 1
            continue
        w = _first_self(q)
        if w and w != want:
            wrong[w] = wrong.get(w, 0) + 1
    if wrong and not right:
        got = "、".join(f"「{k}」×{v}" for k, v in
                        sorted(wrong.items(), key=lambda x: -x[1])[:3])
        return [f"{name}的自称漂了：角色卡定的是「{want}」，"
                f"本章他说话时用的是 {got}"]
    return []
