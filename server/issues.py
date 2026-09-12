"""一次运行里出了什么问题，以及这一章到底算不算写成了。

—— 为什么要有这个文件 ——

这个仓库反复吃的亏是**静默故障**：吞异常、截断不吭声、重试重复同一个错。
翻遍 orchestrator，55 处 `except Exception`，其中 22 处只打一行日志就继续。
然后不管里面塌了多少，最后照样打印一行：

    ✓ 第10章 3683字 得分75 109.1s

那个勾是**无条件**打的。它不表示「这一章没出问题」，只表示「没抛到最外层」。
于是「细纲是空壳」「评审半把尺子没返回」「自检 NameError」「提炼没成」全都
藏在这个勾后面，只看统计根本发现不了 —— 这条教训吃过不止六次。

改法抄自 webnovel-writer：**状态不是谁说了算，是程序从问题清单派生出来的。**

    有 must_handle          → 需人工 / 失败，**永远不可能报「完成」**
    有 needs_confirmation   → 部分完成
    都没有                  → 完成

关键的一条是兜底：**没登记过的错误一律进 must_handle**。
未知情况默认继续（我们现在）和未知情况默认拦停（它），差别就在这一条上。
新错误第一次出现时会把这一章标成需人工，逼人看一眼、给它登记 —— 这个摩擦
是故意的，它保证没有哪种故障能一直躲在勾后面。
"""
from __future__ import annotations

import re
import traceback
from typing import Any, Dict, List, Optional

#: 主机名/URL 一律脱敏后再落盘。
#: 异常文本会被原样记进 audit/*.json 和日志, 而网关地址是用户的私有基础设施 ——
#: 实测一次网关读超时就把它写进了两个 audit 文件。这类东西一旦进了文件,
#: 后面每一次归档、打包、贴日志都可能把它带出去。在**写入的那一刻**就去掉,
#: 比事后到处去搜可靠。
_HOST = re.compile(
    r"(?<![\\\w.])(?:https?://)?(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"([A-Za-z]{2,24})(?::\d{2,5})?")

#: 这些结尾的不是主机名, 是文件名。栈里全是 orchestrator.py / settings.yaml,
#: 一律当主机名替换掉, 栈就看不懂了 —— 而且实测还改坏过 JSON:
#: 「\nserver.py」里的「nserver.py」被匹配上, 替换后成了「\<host>」,
#: 非法转义, 两个 audit 文件直接读不出来。
_NOT_HOST = {
    "py", "pyc", "json", "md", "sh", "txt", "log", "yaml", "yml", "js", "ts",
    "html", "css", "ini", "cfg", "toml", "csv", "db", "sqlite", "lock", "bak",
    "jsonl", "tmp", "alt", "ckpt",
}


def redact(text: str) -> str:
    """把主机名、URL 换成占位符。文件名、错误类型、中文一律不动。"""
    def _sub(m):
        return m.group(0) if m.group(1).lower() in _NOT_HOST else "<host>"
    return _HOST.sub(_sub, str(text or ""))

#: 三档。auto_handled 只作记录，不影响状态。
MUST = "must_handle"
CONFIRM = "needs_confirmation"
AUTO = "auto_handled"

STATUS_DONE = "完成"
STATUS_PARTIAL = "部分完成"
STATUS_NEEDS_USER = "需人工"
STATUS_FAILED = "失败"

#: 已登记的问题。登记的意思是「我们知道它为什么会发生、知道它要不要紧」。
#: severity 决定这一章的状态；auto_handle=True 表示程序已经把它兜住了。
CATALOG: Dict[str, Dict[str, Any]] = {
    # ── 程序已经兜住的，记录但不拦 ──
    "distill_soft_cut": {
        "severity": AUTO, "auto_handle": True,
        "title": "压缩比不够，按句子收尾",
        "why": "原文没超过目标的 3 倍，提炼一次不划算，已落在句末收尾。"},
    "continuation": {
        "severity": AUTO, "auto_handle": True,
        "title": "撞上输出上限，已续写",
        "why": "模型一次没写完，程序自动续了下去。"},
    "gate_discard": {
        "severity": AUTO, "auto_handle": True,
        "title": "守门丢弃了误判的禁用词",
        "why": "禁用表里混进了正常词汇，已剔除。"},
    "faction_grow": {
        "severity": AUTO, "auto_handle": True,
        "title": "势力扩充",
        "why": "按卷推进新增了势力。"},

    # ── 要人确认的：能写完，但这一章的质量没保住 ──
    "critique_partial": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "评审只回来了一部分维度",
        "why": "模型没按骨架逐维度返回，缺的维度没有分数。",
        "next_action": "看 audit/NNN.json 的 critique.scores 缺了哪几项。"},
    "expand_no_effect": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "扩写没起作用，已保留原稿",
        "why": "扩写结果不如原稿或没变长，程序退回了原稿。",
        "next_action": "字数不达标的话进返修队列重写。"},
    "words_off_target": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "字数不在区间内",
        "why": "正文长度偏离目标区间。",
        "next_action": "进返修队列，或放宽区间。"},
    "foreshadow_overdue": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "到期伏笔一条都没安排",
        "why": "点名的到期伏笔在本批细纲里没有着落。",
        "next_action": "看 threads.json 里最老的那几条。"},
    "step_skipped": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "有一步跳过了",
        "why": "这一步抛异常被吞掉、流程继续往下走了。单看一行日志不要紧，"
               "攒起来才看得出这一章缺了多少道工序。",
        "next_action": "看日志里那一行「…失败/跳过」。"},
    "voice_drift": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "主角的自称漂了",
        "why": "角色卡声明了自称，正文里他说话时却用了别的。声明了没人查的"
               "东西早晚对不上 —— 实测十章里七章在「洒家」和「我」之间摇摆。",
        "next_action": "看 characters.md 的「自称」栏与本章对白。"},
    "voice_card_conflict": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "角色卡的自称跟种子打架",
        "why": "卡是模型编的，种子里的台词是作者亲手写的。两者不一致时，"
               "正文会在两个选项之间摇摆。",
        "next_action": "已按种子自动改回；确认一下 characters.md。"},
    "beat_name_swapped": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "开局落点点名的专名被换掉了",
        "why": "种子写「FBI 围楼」，正文写成了「联邦调查局」。题材包那条"
               "「不许用现代思维嘲笑古人」被泛化成了「整本书别提现代词」——"
               "禁的是姿态，不是词；何况这几章发生在穿越之前的现实世界。",
        "next_action": "把专名改回原样。"},
    "metric_blowout": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "文风指标塌方",
        "why": "离文风包给的区间两倍宽以上。区间是从原著反推出来的，"
               "差这么多说明这一章的节奏跟这本书要的不是一回事。",
        "next_action": "看 windowFeedback 里这一项的「低」/「高」怎么说。"},
    "seam_violation": {
        "severity": CONFIRM, "auto_handle": False,
        "title": "合同接缝没咬合",
        "why": "上一块的出口与下一块的进口对不上。",
        "next_action": "跑 audit_tree 看具体哪一格。"},

    # ── 必须处理：这一章不能算写成了 ──
    "outline_empty": {
        "severity": MUST, "auto_handle": False,
        "title": "细纲是空壳",
        "why": "细纲的键存在但内容为空 —— 这种最阴，看键的数量查不出来。",
        "next_action": "删掉这一章的细纲重排。"},
    "code_bug": {
        "severity": MUST, "auto_handle": False,
        "title": "代码 bug（不是模型没答好）",
        "why": "NameError/AttributeError/TypeError/KeyError 这一类是我们自己"
               "写错了，跟模型无关，必须改代码。",
        "next_action": "看日志里的栈尾。"},
    "critique_blocking": {
        "severity": MUST, "auto_handle": False,
        "title": "评审判定该拦，重写之后仍然该拦",
        "why": "按扣分表算，这一章有证据的问题多到过线（严重≥2 条，或扣分"
               "≥40，或与已确立事实冲突）。重写过一轮仍没降下来。",
        "next_action": "看 audit/NNN.json 的 critique.issues，每条都带正文原句。"},
    "canon_fact_suspect": {
        "severity": MUST, "auto_handle": False,
        "title": "同一条既定事实被反复推翻，该事实本身存疑",
        "why": "一条事实被三章以上连撞，多半是它写得比作者的原话更绝对"
               "（抽取时加重了），而故事本身需要另一种读法。一章章返修治不了 —— "
               "要么改那条事实，要么认定前面几章写错了。",
        "next_action": "看 canon_conflicts.json 与 canon.json 里的这一条，"
                       "由人定夺改哪一边。"},
    "contract_broken": {
        "severity": MUST, "auto_handle": False,
        "title": "已定死的事实被推翻",
        "why": "正文与台账里不可逆的事实冲突。",
        "next_action": "看 canon.json 与本章正文。"},
    "chapter_write_failed": {
        "severity": MUST, "auto_handle": False,
        "title": "这一章根本没写出来",
        "why": "正文生成抛异常或返回空。",
        "next_action": "看日志最后一次模型调用。"},
}

#: **没登记过的一律进这里。** 这一条是整个机制的关键。
FALLBACK: Dict[str, Any] = {
    "severity": MUST, "auto_handle": False,
    "title": "遇到没登记过的问题",
    "why": "这是系统还没见过的故障。在搞清楚它要不要紧之前，"
           "**不把这一章当成已完成**。",
    "next_action": "看日志里的类型与栈尾；确认无害就去 issues.CATALOG 登记它。",
}


class Ledger:
    """一次运行（通常是一章）里攒下的问题。"""

    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def record(self, code: str, detail: str = "",
               exc: Optional[BaseException] = None) -> Dict[str, Any]:
        spec = CATALOG.get(code)
        if spec is None:
            spec = dict(FALLBACK)
            spec["unregistered"] = True
        # 代码 bug 单独抬一档: 这四类异常一定是我们自己写错了, 跟模型无关。
        # 把它和「模型这次没答好」混成一句「失败」, 正是自检里那个 NameError
        # 能静默这么久的原因。
        if exc is not None and isinstance(
                exc, (NameError, AttributeError, TypeError, KeyError)):
            spec = dict(CATALOG["code_bug"])
            detail = f"{type(exc).__name__}: {exc}｜{detail}".strip("｜")
        item = {"code": code, "severity": spec["severity"],
                "title": spec["title"], "detail": redact(detail)[:400],
                "auto_handle": bool(spec.get("auto_handle")),
                "unregistered": bool(spec.get("unregistered"))}
        if exc is not None:
            item["trace"] = redact("".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ))[-600:]
        self.items.append(item)
        return item

    def by_severity(self) -> Dict[str, List[Dict[str, Any]]]:
        out = {MUST: [], CONFIRM: [], AUTO: []}
        for it in self.items:
            out.setdefault(it["severity"], []).append(it)
        return out

    def status(self, wrote_text: bool = True) -> str:
        """状态由程序派生 —— 不问模型，也不由调用方自己宣布。"""
        b = self.by_severity()
        if b[MUST]:
            return STATUS_NEEDS_USER if wrote_text else STATUS_FAILED
        if b[CONFIRM]:
            return STATUS_PARTIAL
        return STATUS_DONE

    def brief(self) -> str:
        b = self.by_severity()
        bits = []
        if b[MUST]:
            bits.append(f"必须处理 {len(b[MUST])}")
        if b[CONFIRM]:
            bits.append(f"待确认 {len(b[CONFIRM])}")
        if b[AUTO]:
            bits.append(f"已自动兜住 {len(b[AUTO])}")
        return "／".join(bits)

    def lines(self, cap: int = 6) -> List[str]:
        """给日志用的人话。未登记的排最前 —— 它们最值得看一眼。"""
        order = {MUST: 0, CONFIRM: 1, AUTO: 2}
        xs = sorted(self.items,
                    key=lambda x: (0 if x["unregistered"] else 1,
                                   order.get(x["severity"], 9)))
        out = []
        for it in xs[:cap]:
            tag = "‼未登记" if it["unregistered"] else {
                MUST: "必须处理", CONFIRM: "待确认", AUTO: "已兜住"
            }.get(it["severity"], "")
            out.append(f"    [{tag}] {it['title']}"
                       + (f"：{it['detail'][:90]}" if it["detail"] else ""))
        if len(xs) > cap:
            out.append(f"    …另有 {len(xs)-cap} 条")
        return out

    def to_dict(self) -> Dict[str, Any]:
        b = self.by_severity()
        return {"status": self.status(), "counts":
                {k: len(v) for k, v in b.items()}, "items": self.items}
