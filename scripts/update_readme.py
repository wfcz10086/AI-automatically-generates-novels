#!/usr/bin/env python3
"""刷新 README 里的实测数字。

书名与章数天天在变，写死在 README 里就等于天天过时；而且书名是私有作品，
不该硬编码进公开仓库。这里只统计聚合数字，在标记之间替换。

    python3 scripts/update_readme.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BEGIN = "<!-- STATS:BEGIN -->"
END = "<!-- STATS:END -->"


def collect() -> dict:
    from server.orchestrator import Project           # noqa: E402
    books, finished, chapters, words = 0, 0, 0, 0
    longest = 0
    for sj in sorted((ROOT / "projects").glob("*/state.json")):
        slug = sj.parent.name
        if slug.startswith("_v") or slug.startswith("_archive"):
            continue                                   # 归档残稿不计入
        p = Project(slug)
        done = p.state.get("done") or []
        if not done:
            continue
        books += 1
        chapters += len(done)
        w = p.total_words
        words += w
        longest = max(longest, w)
        if len(done) >= (p.meta.get("target_chapters") or 10 ** 9):
            finished += 1
    return {"books": books, "finished": finished,
            "chapters": chapters, "words": words, "longest": longest}


def counts() -> dict:
    # 用 pytest 自己数, 不要数 def test_ —— 参数化与 fixture 会让两者对不上
    # (实测源码里 45 个 def, pytest 实际收集 82 条)
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/unit", "-q",
                        "--collect-only"], cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"(\d+)\s+tests? collected", r.stdout)
    n_unit = int(m.group(1)) if m else 0
    e2e = (ROOT / "tests/e2e/run.py").read_text(encoding="utf-8")
    return {"unit": n_unit,
            "e2e": len(re.findall(r"^@case\(", e2e, re.M)),
            "genre": len(list((ROOT / "packs/genre").glob("*.json"))),
            "style": len(list((ROOT / "packs/style").glob("*.json"))),
            "type": len(list((ROOT / "packs/type").glob("*.json")))}


def main() -> int:
    s = collect()
    c = counts()
    block = f"""{BEGIN}
| | |
|---|---|
| 已写长篇 | **{s['books']} 本**（{s['finished']} 本完本），累计 **{s['words'] / 10000:.0f} 万字** / {s['chapters']} 章 |
| 单本最长 | **{s['longest'] / 10000:.1f} 万字** |
| 插件包 | {c['type']} 种内容类型 / {c['genre']} 个题材包 / {c['style']} 个文风包 |
| 测试 | 单元 {c['unit']} 条 + E2E {c['e2e']} 条 |
{END}"""

    f = ROOT / "README.md"
    t = f.read_text(encoding="utf-8")
    if BEGIN in t and END in t:
        t = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), block, t, flags=re.S)
    else:
        print("README 里找不到 STATS 标记", file=sys.stderr)
        return 1
    f.write_text(t, encoding="utf-8")
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
