"""拆书 —— 把一本现成的小说拆成可复用的创作素材。

老版有拆书功能但结果没有出口（issue #5「拆书后的分析结果如何使用」）。
这里直接把拆解产物写进项目：世界观要素 / 角色档案 / 节奏表 / 文风特征 / 记忆索引，
拆完就能拿来写续作或同类型新书。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List

# 常见的章节标题写法
CHAPTER_PATTERNS = [
    r"^\s*第\s*[0-9一二三四五六七八九十百千零]+\s*[章回节]\s*[^\n]{0,40}$",
    r"^\s*Chapter\s+\d+[^\n]{0,40}$",
    r"^\s*\d{1,4}[、.．]\s*[^\n]{1,40}$",
    r"^\s*正文\s*第[^\n]{1,30}$",
]


def split_chapters(text: str) -> List[Dict[str, Any]]:
    """按章节标题切分。切不出来就按空行块定长聚合，保证总能出结果。"""
    lines = text.replace("\r\n", "\n").split("\n")
    pat = re.compile("|".join(f"(?:{p})" for p in CHAPTER_PATTERNS), re.M)
    idx = [i for i, l in enumerate(lines) if pat.match(l)]

    chapters: List[Dict[str, Any]] = []
    if len(idx) >= 3:
        idx.append(len(lines))
        for k in range(len(idx) - 1):
            title = lines[idx[k]].strip()
            body = "\n".join(lines[idx[k] + 1: idx[k + 1]]).strip()
            if len(re.findall(r"[一-鿿]", body)) > 200:
                chapters.append({"n": len(chapters) + 1, "title": title, "text": body})
    if not chapters:                     # 兜底: 每 3000 字一段
        buf, cur = [], []
        cn = 0
        for l in lines:
            cur.append(l)
            cn += len(re.findall(r"[一-鿿]", l))
            if cn >= 3000:
                buf.append("\n".join(cur)); cur, cn = [], 0
        if cur:
            buf.append("\n".join(cur))
        # 整本没有换行时按行聚合只会得到 1 段 —— 退化为按字符定长切
        if len(buf) < 2 and text.strip():
            step = 4500
            buf = [text[i:i + step] for i in range(0, len(text), step)]
        chapters = [{"n": i + 1, "title": f"片段{i+1}", "text": t}
                    for i, t in enumerate(buf)]
    return chapters


def analyze(chapters: List[Dict[str, Any]], llm: Callable[[str], str],
            sample: int = 8, on_progress=None) -> Dict[str, Any]:
    """逐章抽结构 + 全局汇总。章数多时按均匀间隔抽样，避免烧钱。"""
    picks = chapters
    if len(chapters) > sample:
        step = len(chapters) / sample
        picks = [chapters[int(i * step)] for i in range(sample)]

    per: List[Dict[str, str]] = []
    for i, c in enumerate(picks):
        if on_progress:
            on_progress(f"拆解 {c['title']}（{i+1}/{len(picks)}）\n")
        out = llm(
            f"读下面这一章，按格式抽取，没有就写「无」，不要多余文字：\n"
            f"核心事件：（一句话）\n出场人物：（顿号分隔）\n冲突：（一句话）\n"
            f"爽点：（一句话）\n章末钩子：（一句话）\n"
            f"写作手法：（3 个关键词，如 短句/多对话/内心戏重）\n\n"
            f"{c['text'][:5000]}")
        per.append({"title": c["title"], "raw": out})

    digest = "\n\n".join(f"【{p['title']}】\n{p['raw']}" for p in per)
    summary = llm(
        f"下面是一本小说 {len(picks)} 个章节的结构抽取结果。请汇总成可复用的创作素材，"
        f"按以下小标题分节输出：\n"
        f"## 题材与定位\n## 世界观要素\n## 主要角色（姓名｜身份｜动机｜与主角关系）\n"
        f"## 爽点公式（这本书靠什么让读者爽，列 3-5 条）\n"
        f"## 节奏表（多少章一个大事件，钩子怎么留）\n"
        f"## 文风特征（句式、段落、对话占比、常用手法）\n"
        f"## 可借鉴与不可借鉴\n\n"
        f"要求：具体、可执行，不要空泛评价。直接输出，无前言。\n\n{digest[:12000]}")
    return {"chapters": len(chapters), "sampled": len(picks),
            "per_chapter": per, "summary": summary}


def apply_to_project(project, result: Dict[str, Any]) -> Dict[str, Any]:
    """把拆书结果落进项目 —— 这才是闭环（issue #5）。"""
    s = result.get("summary", "")
    project.write("teardown.md", s)
    project.write("teardown.json", json.dumps(result, ensure_ascii=False, indent=2))

    def section(name: str) -> str:
        m = re.search(rf"^##\s*{name}[^\n]*\n(.*?)(?=^##\s|\Z)", s, re.M | re.S)
        return m.group(1).strip() if m else ""

    applied = {}
    n = project.mem.index_document("world", "teardown", s)
    applied["memory_chunks"] = n
    for key, field in (("世界观要素", "world"), ("主要角色", "roles"),
                       ("爽点公式", "pleasure"), ("节奏表", "pacing"),
                       ("文风特征", "style")):
        v = section(key)
        if v:
            project.mem.add("world", f"teardown-{field}", f"拆书·{key}", v)
            applied[field] = len(v)

    # 没有角色档案时，直接用拆出来的角色表起草
    if not project.read("characters.md") and section("主要角色"):
        project.write("characters.md", "# 角色档案（由拆书生成，可继续编辑）\n\n"
                      + section("主要角色"))
        applied["characters_seeded"] = True
    # 知识库字段补上文风与爽点，写作时会被注入
    meta_fields = project.meta.setdefault("fields", {})
    kb = [section("爽点公式"), section("文风特征")]
    kb = "\n\n".join(x for x in kb if x)
    if kb:
        meta_fields["kb"] = (meta_fields.get("kb", "") + "\n\n" + kb).strip()
        project.save()
        applied["kb"] = len(kb)
    return applied
