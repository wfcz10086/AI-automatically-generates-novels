"""变量遮蔽静态追踪 —— 本项目踩过两次的坑，用 AST 锁死。

案例1: 角色状态用 `recent = sorted(roles...)` 覆盖了 L2 层的最近章节列表
案例2: 伏笔中段用 `mid = [...]` 覆盖了 L3 层的段摘要列表
两次都崩在 memory_ctl 深处的 join，极难定位。此检查在函数级扫描：
同一函数内，同名变量被赋成「明显不同的容器语义」（str 列表 vs dict 列表）
超过一次即失败。
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 这些名字在 build_context 数据流里有固定语义, 函数内只允许赋值一次
GUARDED = {"recent", "mid", "resident", "recall", "constraints", "cons"}


def iter_functions(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _is_empty_init(value):
    """空容器初始化（recall, x = [], {}）是安全模式, 不计数。"""
    if isinstance(value, (ast.List, ast.Dict, ast.Tuple, ast.Set)):
        elts = getattr(value, "elts", None)
        if elts is not None:
            return all(_is_empty_init(e) for e in elts) if elts else True
        return not value.keys                     # Dict
    return isinstance(value, ast.Constant) and value.value in ("", None, 0)


def assign_counts(fn):
    """只数「有实义的纯赋值」:
    - += / AnnAssign 不算（拼接与类型标注不是遮蔽）
    - 空容器初始化不算（init + 条件覆盖是安全模式）
    - except 兜底分支里的赋值不算（与 try 分支互斥）
    当年两次事故都是「非空赋值 + 又一次非空赋值改了语义」, 恰好落在此判据内。"""
    handler_lines = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler):
            for sub in ast.walk(node):
                if hasattr(sub, "lineno"):
                    handler_lines.add(sub.lineno)
    counts = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        if node.lineno in handler_lines or _is_empty_init(node.value):
            continue
        for t in node.targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name):
                    counts.setdefault(n.id, []).append(node.lineno)
    return counts


def test_no_guarded_shadowing_in_orchestrator():
    src = (ROOT / "server" / "orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for fn in iter_functions(tree):
        for name, lines in assign_counts(fn).items():
            if name in GUARDED and len(lines) > 1:
                offenders.append(f"{fn.name}(): `{name}` 赋值 {len(lines)} 次 "
                                 f"@ 行 {lines}")
    assert not offenders, (
        "受保护变量被多次赋值（遮蔽风险，此坑已踩过两次）:\n  "
        + "\n  ".join(offenders))


def test_no_bare_pkill_in_scripts():
    """pkill -f 会误杀自己的命令行 —— 本会话踩过三次。"""
    for f in (ROOT / "scripts").glob("*.sh"):
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "pkill -f" not in stripped, \
                f"{f.name}:{i} 使用了 pkill -f（会误杀自身，用 pidfile+进程组）"
