"""四个内容类型的守门：这套流水线不单单写小说。

—— 为什么要有这个文件 ——

四个内容类型（长篇小说/短剧剧本/影视剧本/动漫分镜）里，动漫曾经**从来
跑不起来**：成品层的 id 写死成 ("content","page","shot") 三个，动漫的
storyboard 不在里面，直接 IndexError —— 而没有任何测试守着，坏了半个月
没人知道。README 也一直只说「小说」。

这里全部**零网络**（不建项目、不调模型）：光靠类型包 + 源码断言。
实弹冒烟（四类型各建临时项目走 Novelist 装配）在改动内容类型相关代码时
手工跑：见本文件底部 __main__。
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
TYPE_DIR = ROOT / "packs" / "type"

#: 四个类型：id → (名字, 成品层 id)。改类型包时这里要跟着改 —— 故意的摩擦。
EXPECTED = {
    "novel": ("长篇小说", "content"),
    "shortdrama": ("短剧剧本", "shot"),
    "screenplay": ("影视剧本", "page"),
    "anime": ("动漫分镜", "storyboard"),
}


def _packs():
    return {json.loads(f.read_text(encoding="utf-8"))["id"]:
            json.loads(f.read_text(encoding="utf-8"))
            for f in TYPE_DIR.glob("*.json")}


def test_四个类型一个不少():
    packs = _packs()
    assert set(packs) == set(EXPECTED), \
        f"类型包与预期不符：{sorted(packs)} vs {sorted(EXPECTED)}"
    for tid, (name, final) in EXPECTED.items():
        assert packs[tid]["name"] == name
        levels = packs[tid].get("levels") or []
        assert levels, f"{tid} 没有层级"
        assert levels[-1]["id"] == final, \
            f"{tid} 的成品层是 {levels[-1]['id']}，预期 {final}"
        for lv in levels:
            assert str(lv.get("prompt") or "").strip(), \
                f"{tid}.{lv.get('id')} 层没有提示词模板"


def test_成品层不许写死id白名单():
    """当年动漫跑不起来的根因：写死 ("content","page","shot")。

    正确写法是 type["levels"][-1] —— 最末一层就是成品层，不管它叫什么。
    """
    import server.orchestrator as O
    src = inspect.getsource(O)
    # 只扫代码行 —— 注释里那句正是在讲当年的教训, 断言把教训判成违规就会
    # 逼人删教训（这坑在硬切/势力两条测试上各踩过一次, 这是第三次）。
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert not re.search(r'\(\s*"content"\s*,\s*"page"\s*,\s*"shot"\s*\)', code), \
        "成品层 id 白名单又回来了 —— 新类型会再次 IndexError"
    assert 'self.type["levels"][-1]' in code


def test_类型包模板变量都有人供():
    """模板里的 ${var}，render 时不在 ctx 里就替换成**空串** ——
    静默失踪，正文里那一段就是空的，没人报错。所以变量必须可解析：
    要么是 base_ctx 提供的，要么是该类型自己声明的 fields。
    """
    import server.orchestrator as O
    base_src = inspect.getsource(O.Novelist.base_ctx)
    base_keys = set(re.findall(r'"([a-z_]+)"\s*:', base_src))
    # base_ctx 还会把 fields 全量摊进 ctx（fields 循环），加上题材注入的键
    extra = {"outline", "world_bible", "characters", "relationships",
             "premise", "background", "plot", "era_card", "synopsis",
             "prev_summary", "chapter_outline", "recent", "kb",
             # 流程变量: 由各 step 在 render 前临时塞进 ctx（父层内容、
             # 本批章号区间、话数序号等）, 不是 base_ctx 的常驻键
             "parent", "parent_title", "range", "index", "count",
             "series", "prev", "prev_tail", "episode", "story", "acts"}
    packs = _packs()
    bad = []
    for tid, pk in packs.items():
        fields = {f["id"] for f in pk.get("fields") or []}
        allowed = base_keys | fields | extra
        for lv in pk.get("levels") or []:
            for var in set(re.findall(r"\$\{(\w+)\}", str(lv.get("prompt") or ""))):
                if var not in allowed:
                    bad.append(f"{tid}.{lv['id']} 用了没人供的 ${{{var}}}")
    assert not bad, ("模板变量会被替换成空串而不报错：\n  " + "\n  ".join(bad))


def test_每个类型有默认文风与导出器():
    for tid, pk in _packs().items():
        assert pk.get("defaultStyle"), f"{tid} 没有默认文风包"
        assert pk.get("exporters"), f"{tid} 没有导出器（写完导不出去）"


if __name__ == "__main__":
    # 实弹冒烟（会调一次模型生成世界基底卡）：四类型各建临时项目走装配
    import shutil
    import sys
    sys.path.insert(0, str(ROOT))
    from server.orchestrator import create_project, Novelist, slugify
    F = {"novel": {"premise": "翻身"}, "shortdrama": {"logline": "摊牌"},
         "screenplay": {"logline": "追凶"}, "anime": {"logline": "得剑"}}
    for tid, f in F.items():
        t = f"_冒烟_{tid}"
        try:
            p = create_project(t, tid, "tongren", "fanqie-shuangwen", 6, 9000, f)
            nv = Novelist(p)
            print("✓", tid, nv.type["levels"][0]["id"], "→",
                  nv.type["levels"][-1]["id"], nv.target_words())
        finally:
            shutil.rmtree(ROOT / "projects" / slugify(t), ignore_errors=True)
