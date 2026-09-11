"""树式分解：把一本书拆成带进出口合同的节点树。

—— 为什么要有这个文件 ——

原来的做法是「接着往下写」：模型写 1-20 章，把这 20 章念给它听，再写 21-40。
写到第 500 章，前面念不完，只能挑最近的念。于是它记不住第 30 章埋的东西，
也不知道第 800 章要发生什么，只能一路往前糊。实测后果：

  · 第 163 章的提示词 40125 字里，**73% 在复述过去**（最近 4 章正文 15072 +
    检索召回 7689 + 段落摘要 6618），真正说「这一章要写什么」的细纲只有 871 字、
    占 2%。台账其实是压过的（存量 42702 → 注入 3558），胖的不是台账，
    是**复述本身** —— 而复述量必然随书变长，这是线性写法的物理天花板。
  · 伏笔 159 条没收：排第 7 卷时第 8 卷的进口无处可查，伏笔没有可铺的方向
  · 章与章之间靠「最近几章正文原文」续接，隔一层就断

树的做法是工程分解：全书 1 个节点 → 阶段 → 卷 → 单元 → 章。拆任何一块时，
模型只需要看**它爹和它兄弟**：父节点的合同、左兄弟的出口、右兄弟的进口。
几千字，与全书 100 章还是 1 万章无关。而且它知道右兄弟要从哪开始，
等于知道未来，伏笔自然往那边铺。

—— 合同是表，不是散文 ——

原来的 stages.json 只有 exit 且是散文：
    exit: "肉身筑基大成（内壮五脏初显），手里有金钟罩正法传承，无人死亡…"
人能读，程序没法查。而且**只有出口没有进口**，所以「上一块出口 == 下一块进口」
这个等式根本没有左右两边 —— 我原来只能拿字面重合系数 ≥0.40 模糊告警。

这里改成结构化状态向量，逐字段比对，程序说了算，不用模型判断。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

#: 合同的字段。改这张表要同时改 diff()/merge()，所以字段要少而稳。
#: 原则：只放**下一块必须知道**的东西。人物的性格、外貌、口癖不在这里
#: （那是人物卡，不随节点变），只有「他现在在哪、是什么身份」才在。
HERO_FIELDS = ("身份", "位置", "能力上限", "伤")


@dataclass
class Contract:
    """一个节点的进口或出口状态。固定大小，不随书变长。"""

    hero: Dict[str, str] = field(default_factory=dict)      # 主角：身份/位置/能力上限/伤
    people: Dict[str, str] = field(default_factory=dict)    # 关键人物 → 位置与状态（一句话）
    assets: Dict[str, str] = field(default_factory=dict)    # 资源：灵石/地盘/人手/凭证
    open_threads: List[Dict[str, Any]] = field(default_factory=list)
    #   每条 {"id": "t3", "what": "残碑上的武字疤是谁刻的", "due": "C2"}
    #   due = 该在哪个节点之内收掉。程序据此查「线有没有在它该死的地方死掉」。
    facts: List[str] = field(default_factory=list)          # 已定死、不可翻的事

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[Dict[str, Any]]) -> "Contract":
        d = d or {}
        return Contract(
            hero=dict(d.get("hero") or {}),
            people=dict(d.get("people") or {}),
            assets=dict(d.get("assets") or {}),
            open_threads=[dict(x) for x in (d.get("open_threads") or [])
                          if isinstance(x, dict)],
            facts=[str(x) for x in (d.get("facts") or [])],
        )

    def brief(self, cap: int = 900) -> str:
        """给模型看的紧凑写法。**不带章号** —— 章号是 31 处正文自指的唯一来源。"""
        out = []
        if self.hero:
            out.append("主角：" + "｜".join(f"{k}={v}" for k, v in self.hero.items() if v))
        if self.people:
            out.append("人在哪：" + "；".join(f"{k}={v}" for k, v in self.people.items()))
        if self.assets:
            out.append("手里有：" + "；".join(f"{k}={v}" for k, v in self.assets.items()))
        if self.open_threads:
            out.append("没收的线：" + "；".join(
                f"{t.get('what', '')}（须了结于 {t.get('due', '?')}）"
                for t in self.open_threads))
        if self.facts:
            out.append("已定死：" + "；".join(self.facts))
        s = "\n".join(out)
        return s if len(s) <= cap else s[:cap] + "…"


@dataclass
class Node:
    """树上的一个节点。全书是根，往下阶段/卷/单元/章。"""

    id: str                       # 根 "R"；子节点 "R.1"、"R.1.2"，层级一看便知
    level: str                    # book / stage / volume / unit / chapter
    title: str = ""
    line: str = ""                # 这一块的主线一句话：谁在解决什么
    start: int = 0                # 覆盖的章号区间（闭区间）
    end: int = 0
    entry: Contract = field(default_factory=Contract)
    exit: Contract = field(default_factory=Contract)
    children: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["entry"] = self.entry.to_dict()
        d["exit"] = self.exit.to_dict()
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Node":
        return Node(
            id=d["id"], level=d.get("level", ""), title=d.get("title", ""),
            line=d.get("line", ""), start=int(d.get("start") or 0),
            end=int(d.get("end") or 0),
            entry=Contract.from_dict(d.get("entry")),
            exit=Contract.from_dict(d.get("exit")),
            children=list(d.get("children") or []),
        )


LEVELS = ["book", "stage", "volume", "unit", "chapter"]


def child_level(level: str) -> str:
    i = LEVELS.index(level) if level in LEVELS else 0
    return LEVELS[min(i + 1, len(LEVELS) - 1)]


# ─────────────────────────── 不变式 ───────────────────────────
# 这些是**程序**查的，不是模型判断的。模型总会换个说法，所以比对前先归一化，
# 但归一化只去标点空白，不做同义词匹配 —— 一旦允许模糊，等式就退回成「告警」，
# 而告警没人看就等于没有。

def _norm(v: Any) -> str:
    return re.sub(r"[\s，。、；：,.;:（）()【】\[\]「」『』\"'!！?？~—-]+", "", str(v or ""))


def _diff_map(a: Dict[str, str], b: Dict[str, str], label: str) -> List[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if _norm(a.get(k)) != _norm(b.get(k)):
            out.append(f"{label}「{k}」：出口={a.get(k, '—')} / 进口={b.get(k, '—')}")
    return out


def check_seam(prev: Node, nxt: Node) -> List[str]:
    """相邻兄弟：上一块的出口必须等于下一块的进口。返回违约清单（空=咬合）。"""
    a, b = prev.exit, nxt.entry
    errs: List[str] = []
    errs += _diff_map(a.hero, b.hero, "主角")
    errs += _diff_map(a.people, b.people, "人物")
    errs += _diff_map(a.assets, b.assets, "资源")
    ta = {str(t.get("id") or ""): t for t in a.open_threads}
    tb = {str(t.get("id") or ""): t for t in b.open_threads}
    for tid in sorted(set(ta) | set(tb)):
        if tid not in tb:
            errs.append(f"线「{ta[tid].get('what', tid)}」在出口还开着，进口却没了")
        elif tid not in ta:
            errs.append(f"线「{tb[tid].get('what', tid)}」进口凭空出现，出口没有")
    fa, fb = {_norm(x) for x in a.facts}, {_norm(x) for x in b.facts}
    for lost in fa - fb:
        errs.append(f"已定死的事实在进口丢了：{lost[:30]}")
    return errs


def check_parent(parent: Node, kids: List[Node]) -> List[str]:
    """父子：父的进口 == 长子进口；父的出口 == 幼子出口；区间要连续覆盖。"""
    errs: List[str] = []
    if not kids:
        return ["没有子节点"]
    head, tail = kids[0], kids[-1]
    errs += [f"父进口 vs 长子进口 — {e}" for e in
             check_seam(Node(id="", level="", exit=parent.entry), head)]
    errs += [f"父出口 vs 幼子出口 — {e}" for e in
             check_seam(tail, Node(id="", level="", entry=parent.exit))]
    if head.start != parent.start or tail.end != parent.end:
        errs.append(f"区间没盖住父节点：父 {parent.start}-{parent.end}，"
                    f"子 {head.start}-{tail.end}")
    for i in range(len(kids) - 1):
        if kids[i].end + 1 != kids[i + 1].start:
            errs.append(f"{kids[i].id} 到 {kids[i+1].id} 章号断了："
                        f"{kids[i].end} → {kids[i+1].start}")
    return errs


def check_threads(root: Node, nodes: Dict[str, Node]) -> List[str]:
    """线只能死在它该死的地方：due 指定的节点之内必须收掉，之后不许还开着。"""
    errs: List[str] = []
    for t in root.entry.open_threads + root.exit.open_threads:
        due = t.get("due")
        if due and due not in nodes:
            errs.append(f"线「{t.get('what', '')}」的 due={due} 指向不存在的节点")
    for nd in nodes.values():
        for t in nd.exit.open_threads:
            due = t.get("due")
            if not due or due not in nodes:
                continue
            d = nodes[due]
            # 这条线要在 due 节点内了结：那么 due 的出口里不该还有它
            if nd.end >= d.end and any(x.get("id") == t.get("id")
                                       for x in d.exit.open_threads):
                errs.append(f"线「{t.get('what', '')}」说好在 {due} 了结，"
                            f"但 {due} 的出口里它还开着")
    return errs


def audit_tree(nodes: Dict[str, Node]) -> List[str]:
    """全树体检。这是**唯一**判定树合不合法的地方，其余都不许自己判。"""
    errs: List[str] = []
    for nid, nd in sorted(nodes.items()):
        kids = [nodes[c] for c in nd.children if c in nodes]
        if kids:
            errs += [f"[{nid}] {e}" for e in check_parent(nd, kids)]
            for i in range(len(kids) - 1):
                errs += [f"[{kids[i].id}→{kids[i+1].id}] {e}"
                         for e in check_seam(kids[i], kids[i + 1])]
    root = nodes.get("R")
    if root:
        errs += check_threads(root, nodes)
    return errs


# ─────────────────────── 拆一个节点要给模型看什么 ───────────────────────

def decomposition_context(node: Node, parent: Optional[Node],
                          left: Optional[Node], right: Optional[Node]) -> str:
    """拆这一块时的**全部**上下文：爹 + 左兄弟出口 + 右兄弟进口。

    不给全书前情，不给最近几章正文 —— 那正是「口述长篇」的做法。
    这里的字数与全书长度**无关**，永远是这几千字。
    """
    out = [f"【要拆的这一块】{node.title or node.id}（第 {node.start}-{node.end} 章）",
           f"　主线：{node.line}" if node.line else "",
           "\n【它进来时的状态】\n" + (node.entry.brief() or "（开篇，无前情）"),
           "\n【它出去时必须是这个状态】\n" + (node.exit.brief() or "（未定）")]
    if parent:
        out.append(f"\n【它属于】{parent.title or parent.id}：{parent.line}")
    if left:
        out.append("\n【上一块结束在哪】" + (left.line or "") + "\n"
                   + left.exit.brief(400))
    if right:
        out.append("\n【下一块要从哪开始】" + (right.line or "") + "\n"
                   + right.entry.brief(400)
                   + "\n　↑ 这一块的出口必须正好接上它。埋伏笔就往这个方向埋。")
    return "\n".join(x for x in out if x)
