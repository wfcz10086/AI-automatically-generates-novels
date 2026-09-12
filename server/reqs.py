"""要求登记表：这本书的每一条硬要求，从哪来、怎么到达模型、谁来验。

—— 为什么要有这个文件 ——

一天之内修了二十处，归类之后只有五种：

  A 要求没到达模型      约束固化之后又往 cons 里追加（整块没进提示词）；
                        「说话方式」在槽位里被挤掉 66 次；规矩不在格式表上
  B 到达了但没人验      开局落点、自称、专名、硬切
  C 算了但没人用        judge() 被维度平均盖掉；blocking 没进台账；
                        a["issues"] 键名撞车把检测结果覆盖了
  D 验了但门槛错        重写守卫只防腰斩、块间超时 600 秒、指标没有梯度、
                        台账窗口 [-40:] 把开篇根基挤掉
  E 做了被后一步撤销    扩写达标又被重写砍回；返修修好了状态不更新

五类的共同根是**流水线各段之间没有契约**：每段自己产出、自己写文件，没有
任何地方声明「这条要求最终由谁负责」「我这段的产出谁在用」。于是每加一条
要求就多一处可能悄悄失效的地方，而失效在日志上全都长得像正常。

这里把要求变成一等公民。每条要求四个字段缺一不可，其中两条由测试强制：

  · 没有 check 的要求不许存在   —— 杜绝 B 类
  · deliver 声明之后程序回扫    —— 杜绝 A 类

—— 三档送达 ——

今天验了三次，可靠度差别很大：

  prompt   只写在提示词里        会被违反。开局落点把要抄的原文都列出来了、
                                 还写明程序会核对，三稿一个都没写
  table    进格式表（填写路径）  会被填，可能走样。插进表之后五章全写了，
                                 但第 1 章把 FBI 抄成了「联邦调查局」
  program  程序直接写            不会错。那一行改由程序钉进去之后就再没错过

能上 program 的不要停在 prompt。上不了的（比如伏笔要写什么，那是创作）
就停在 table，并且**必须配一份分数**——有栏没分，模型会全填「无」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

#: 送达方式，可靠度从低到高。
PROMPT, TABLE, PROGRAM = "prompt", "table", "program"

#: 违反了怎么办。
ELIMINATE = "eliminate"     # 三选一里直接出局
PENALTY = "penalty"         # 扣分，仍可能被选中
LEDGER = "ledger"           # 记进问题台账（本章判「部分完成」）
BLOCK = "block"             # 拦停（本章判「需人工」，进返修队列）


@dataclass
class Req:
    """一条硬要求。"""

    id: str
    source: str                     # 从哪来：种子/文风包/台账/合同树
    deliver: str                    # PROMPT / TABLE / PROGRAM
    on_fail: str                    # ELIMINATE / PENALTY / LEDGER / BLOCK
    marker: str = ""                # 提示词里的标志串，用来回扫「到达没有」
    stage: str = "chapter"          # outline / chapter / both
    why: str = ""                   # 为什么有这条（多半是踩过的坑）
    #: 程序判据。签名随 stage 不同，由调用方约定；**不许为 None**。
    check: Optional[Callable[..., List[str]]] = None
    #: 条件成立时这条才生效（比如只有前几章有开局落点）
    when: Optional[Callable[..., bool]] = None


#: 全部要求。加一条要求就往这里加一条记录 —— 这个摩擦是故意的：
#: 它逼着人当场回答「谁来验」和「怎么到达模型」。
REGISTRY: List[Req] = []


def register(r: Req) -> Req:
    if any(x.id == r.id for x in REGISTRY):
        raise ValueError(f"要求 id 重复：{r.id}")
    REGISTRY.append(r)
    return r


def for_stage(stage: str) -> List[Req]:
    return [r for r in REGISTRY if r.stage in (stage, "both")]


def missing_delivery(prompt: str, stage: str,
                     active: Optional[Dict[str, bool]] = None) -> List[str]:
    """提示词组装完之后回扫：声明了要进提示词的要求，标志串真的在里面吗。

    **这一条专治 A 类。** 实测最贵的一次：constraints="\\n".join(cons) 在
    第 4850 行就把约束固化进 prompt 了，而「已确立的事实」「到期伏笔本批必须
    收掉 N 条」是在第 4934 行才 cons.append 进去的 —— append 之后 cons 再没有
    任何人读。那一整块从来没有到达过模型，而记忆召回和一次提炼调用照花不误。
    日志上一切正常，只有「本批细纲一条都没碰」这个下游症状，查了三轮才找到。

    有了回扫，这种事在第一章就会炸出来，而且直接指到是哪一条没进去。
    """
    bad = []
    for r in for_stage(stage):
        if r.deliver == PROGRAM or not r.marker:
            continue
        if active is not None and not active.get(r.id, True):
            continue
        if r.marker not in (prompt or ""):
            bad.append(f"{r.id}（{r.source}）声明了要进提示词，"
                       f"但最终提示词里找不到「{r.marker}」")
    return bad


def audit_registry() -> List[str]:
    """登记表自检。由测试跑，不在运行时跑。"""
    bad = []
    for r in REGISTRY:
        if r.check is None:
            bad.append(f"{r.id} 没有程序判据 —— 没人验的要求早晚被违反，"
                       f"要么补 check，要么把这条要求删掉")
        if r.deliver not in (PROMPT, TABLE, PROGRAM):
            bad.append(f"{r.id} 的 deliver 不合法：{r.deliver}")
        if r.on_fail not in (ELIMINATE, PENALTY, LEDGER, BLOCK):
            bad.append(f"{r.id} 的 on_fail 不合法：{r.on_fail}")
        if r.deliver in (PROMPT, TABLE) and not r.marker:
            bad.append(f"{r.id} 声明要进提示词却没给 marker，无法回扫是否到达")
        if not r.why:
            bad.append(f"{r.id} 没写 why —— 要求的来历比要求本身值钱")
        if r.deliver == TABLE and r.on_fail == LEDGER:
            bad.append(f"{r.id} 进了格式表却只记账不扣分 —— "
                       f"有栏没分，模型会全填「无」（伏笔那条踩过）")
    return bad


# ─────────────────── 这本引擎当前的要求 ───────────────────
# 每一条都对应今天踩过的一个坑。marker 取提示词里那一段的固定前缀。

def _reg_all():
    from . import tree as _tr                                    # noqa: F401

    register(Req(
        id="opening_beat", source="种子.开局落点", stage="outline",
        deliver=PROGRAM, on_fail=ELIMINATE, marker="开局落点：",
        why="第 4 条「妖姬一吻夺元阳」整条丢过一次，而那是他成为鼎炉的原因 —— "
            "全书 0 次「元阳」，主角却从第 3 章起就被叫鼎炉：果还在，因没了。",
        check=lambda nv, n, body: nv.beat_missed(n, body)))

    register(Req(
        id="beat_proper_noun", source="种子.开局落点里的专名", stage="chapter",
        deliver=PROMPT, on_fail=LEDGER, marker="原样写进正文",
        why="种子写「FBI 围楼」，两版正文都写成「联邦调查局」。题材包那条"
            "「不许用现代思维嘲笑古人」被泛化成了「整本书别提现代词」——"
            "禁的是姿态，不是词。",
        check=lambda nv, n, text: nv.beat_names_missing(n, text)))

    register(Req(
        id="self_address", source="种子.第一句台词 / 角色卡.自称",
        stage="chapter", deliver=PROGRAM, on_fail=PENALTY, marker="自称",
        why="角色卡写「洒家」，种子里作者写的台词用「我」。两者打架谁也没查，"
            "十章里七章在两个选项之间摇摆。",
        check=lambda nv, n, text: __import__(
            "server.voice", fromlist=["x"]).check_prose_voice(
                text, nv.hero_name(),
                __import__("server.voice", fromlist=["x"]).card_self_address(
                    nv.p.read("characters.md"), nv.hero_name()) or "")))

    register(Req(
        id="overdue_foreshadow", source="台账.到期伏笔", stage="outline",
        deliver=TABLE, on_fail=PENALTY, marker="到期伏笔",
        why="只写在提示词里时日志连报「本批细纲一条都没碰」；查 trace 确认那段话"
            "确实在 5 万字的提示词里 —— 模型看见了没照做。升到格式表并配分数。",
        check=lambda nv, parts, start: []))     # 实际判据在 outline_score 里

    register(Req(
        id="canon_no_contradiction", source="台账.不可逆事实", stage="chapter",
        deliver=PROMPT, on_fail=BLOCK, marker="已确立的不可逆事实",
        why="「玉佩彻底崩碎消失」被第 10、12、16 章连撞三次。"
            "撞够三次说明该质疑的是那条事实本身。",
        check=lambda nv, n, crit: [
            str(c.get("fact", "")) for c in (crit.get("contradictions") or [])]))

    register(Req(
        id="word_range", source="文风包.chapterWords", stage="chapter",
        deliver=PROMPT, on_fail=LEDGER, marker="字",
        why="扩写达标 3163 字，低分重写砍回 2070 还被采纳 —— 守卫只防腰斩，"
            "防不住「掉回下限以下」。",
        check=lambda nv, n, cn, lo, hi: (
            [] if lo <= cn <= hi else [f"{cn} 字，区间 {lo}-{hi}"])))


_reg_all()
