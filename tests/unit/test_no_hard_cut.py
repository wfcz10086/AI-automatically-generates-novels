"""硬切与提示词完整性的守门测试。

—— 为什么要有这个文件 ——

「不许硬切」这条规矩清理过三轮，每轮都清干净了，每轮又长回来。原因很简单：
它只写在约定里，没有任何程序在查。这正是这个仓库反复吃亏的那一类 ——
**提示词里写了、约定里说了，但程序不查的规矩，早晚被违反**。

硬切的危害不是「少给模型一点信息」，是：
  · 半句话切断 → 模型顺着把它补完，补出来的是它编的，下游看不出来
  · JSON 切断  → 括号不配对，模型脑补出一个「完整」的结构，那是假信息
  · 静默发生   → 下游拿到的东西看起来是完整的，没人知道丢了什么

所以这里用一份**显式白名单**：每一处允许的硬切都要写清楚为什么允许。
想加新的硬切，就得先在这里写下理由 —— 这个摩擦是故意的。

第二部分抄自 webnovel-writer 的 test_prompt_integrity.py：断言提示词与代码
没有漂移。今天刚踩过一次 —— Contract 改成账本制之后 build_tree.py 还在读
早就不存在的 Contract.hero，重建树必崩，藏了很久才暴露。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SRC = ROOT / "server"

#: 超过这个长度的切片，按「切的是长文本」论处。
#: 短的切片(solves[:120] 这类)是模型输出字段的存储上限，不是同一回事。
LONG = 250

#: 允许硬切的地方，每条必须写明理由。(文件, 行内特征, 为什么)
ALLOW = [
    ("app.py", 'str(e)[:400]', "给前端看的错误信息，本来就只看一眼"),
    ("providers/openai_compat.py", "resp.text[:300]",
     "HTTP 错误体，给人读的，不进提示词"),
    ("providers/search.py", "content[:1200]",
     "搜索结果落库上限；搜索当前是关的，且这是存储不是喂给模型"),
    ("retrieval.py", 'out["raw_preview"]', "带 preview 字样的预览字段"),
    ("retrieval.py", "self._bigrams(k + str(card)[:900])",
     "算 bigram 重合度用的，纯数值计算，不进提示词"),
    ("orchestrator.py", 'act.get("note", "")[:300]',
     "返修队列的备注字段上限，是结构化字段不是长文本"),
]


def _py_files():
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in str(p))


def _code_lines(p: Path):
    """跳过注释行和文档串里的内容 —— 注释里提到 `[:2000]` 是在讲历史教训，
    把它当成违规会逼着人把教训删掉。"""
    out, in_doc = [], False
    for i, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        q3 = raw.count('"""') + raw.count("'''")
        if in_doc:
            if q3 % 2:
                in_doc = False
            continue
        if q3 % 2:
            in_doc = True
            continue
        if line.startswith("#"):
            continue
        code = raw.split("#", 1)[0]
        out.append((i, code))
    return out


def test_长文本不许硬切():
    pat = re.compile(r"\[:\s*(\d{3,})\s*\]")
    bad = []
    for p in _py_files():
        rel = str(p.relative_to(SRC))
        for i, code in _code_lines(p):
            for m in pat.finditer(code):
                if int(m.group(1)) < LONG:
                    continue
                if any(f in rel and feat in code for f, feat, _ in ALLOW):
                    continue
                bad.append(f"{rel}:{i}  {code.strip()[:88]}")
    assert not bad, (
        "发现未登记的硬切。喂给模型的长文本要走 distill.soft()（落在句末）"
        "或 distill.distill()（够 3:1 就真提炼）；JSON 走 distill.json_fit()"
        "（丢条目不丢语法）。确实该保留的，去 ALLOW 里写明理由：\n  "
        + "\n  ".join(bad))


def test_JSON不许从中间切断():
    """json.dumps(...)[:N] 交给模型的是一段语法非法的 JSON。

    模型面对残缺结构会自行脑补成它认为完整的样子，而这份脑补看起来跟真的
    一样 —— 切散文只是少了信息，切 JSON 是给了假信息。
    """
    # 必须**括号配平**地判。用 json\.dumps\(.*\)\[:\d+\] 这种贪心正则会误伤:
    # json.dumps([{'detail': str(x)[:160]} for x in xs]) 里那个 [:160] 切的是
    # 字段，json.dumps 本身好好的，而贪心的 .* 会一路吞到它后面。
    def cut_follows_dumps(code: str) -> bool:
        for m in re.finditer(r"json\.dumps\(", code):
            depth, j = 1, m.end()
            while j < len(code) and depth:
                depth += (code[j] == "(") - (code[j] == ")")
                j += 1
            if depth == 0 and re.match(r"\s*\[:\s*\d+\s*\]", code[j:]):
                return True
        return False

    bad = []
    for p in _py_files():
        for i, code in _code_lines(p):
            if cut_follows_dumps(code):
                bad.append(f"{p.relative_to(SRC)}:{i}  {code.strip()[:88]}")
    assert not bad, ("JSON 被从中间切断，改用 distill.json_fit()：\n  "
                     + "\n  ".join(bad))


# ───────────────── 提示词与代码不许漂移 ─────────────────

def test_提示词里点名的合同字段真的存在():
    """Contract 改过一次栏目(hero/people/assets → accounts/threads/facts/notes)。

    改完之后 build_tree.py 还在读 root.entry.hero —— 那一栏已经不存在了，
    于是**任何一次从种子重建树都会 AttributeError 崩掉**，而且因为在用的树
    是重构前建好的，这个 bug 藏了很久没人发现。
    """
    import server.tree as tr
    live = set(tr.Contract().to_dict())
    dead = {"hero", "people", "assets"}
    scan = [ROOT / "scripts" / "build_tree.py",
            ROOT / "scripts" / "build_milestones.py",
            SRC / "tree.py", SRC / "orchestrator.py"]
    bad = []
    for p in scan:
        if not p.exists():
            continue
        for i, code in _code_lines(p):
            for f in dead:
                # from_dict 里有一段兼容老三栏的代码，那是故意的
                if f"entry.{f}" in code or f"exit.{f}" in code:
                    bad.append(f"{p.name}:{i} 读的是已经没有的 Contract.{f}")
    assert not bad, ("提示词/代码引用了 Contract 上不存在的栏目"
                     f"（现有栏目：{sorted(live)}）：\n  " + "\n  ".join(bad))


def test_合同树脚本的入口参数没写错():
    """--out 要的是目录。传成 .../tree.json 会建出一个叫 tree.json 的目录，
    真正的报错要等下一轮读取时才以 IsADirectoryError 冒出来，离真因十万八千里。
    """
    src = (ROOT / "scripts" / "build_tree.py").read_text(encoding="utf-8")
    assert 'out.suffix == ".json"' in src, "build_tree.py 少了 --out 是目录的护栏"


@pytest.mark.parametrize("name", ["soft", "distill", "json_fit"])
def test_收尾三件套都在(name):
    import server.distill as d
    assert hasattr(d, name), f"distill.{name} 不见了，硬切守门测试会失效"
