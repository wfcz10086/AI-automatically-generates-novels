"""发行版编译器 —— 把一本书按目标平台规格重新打包。

同一份内容, 付费平台要 6000 字/章的大章节奏, 番茄免费生态要 2600 字/章
的高频卡点。写作按创作节奏, 发行按平台规格, 两者解耦。

番茄规格（fanqie）:
  - 单章 2000-3200 字, 切点必须落在冲突/悬念处
  - 章末必须带钩(悬念/威胁/反转/未接来电), 平收章自动补 1-2 句
  - 每章有抓人的短标题
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List

SPECS = {
    "fanqie": {
        "label": "番茄免费",
        "chapter_words": (2000, 3200),
        "need_hook": True,
        "title_style": "短促抓人，6-10 字，可用悬念式（如「他慌了」「第一桶金」）",
    },
}


def split_points(text: str, lo: int, hi: int) -> List[int]:
    """按段落边界给出候选切点（字符偏移），供模型挑选。"""
    pts, acc = [], 0
    for m in re.finditer(r"\n+", text):
        acc = m.end()
        pts.append(acc)
    return [p for p in pts if lo * 0.7 < p]


def compile_chapter(n: int, title_hint: str, text: str, spec: Dict[str, Any],
                    llm: Callable[[str], str]) -> List[Dict[str, str]]:
    """把一个大章切成若干平台章。切点与补钩交给模型，切分由机械执行。"""
    lo, hi = spec["chapter_words"]
    cn = len(re.findall(r"[一-鿿]", text))
    n_parts = max(1, round(cn / ((lo + hi) / 2)))
    if n_parts == 1:
        parts = [text]
    else:
        # 让模型标注切点: 输出每段最后一句的原文引文, 机械定位切分
        prompt = (
            f"把下面这章小说切成 {n_parts} 段发布，每段 {lo}-{hi} 字。\n"
            f"切点必须落在冲突升级、悬念抛出或场景切换处——读者读完一段必须想点开下一段。\n"
            f"只输出 JSON（无围栏）：\n"
            f'{{"cuts":[{{"last_sentence":"第1段最后一句的原文（逐字引用，含标点）"}},…共{n_parts-1}个]}}\n\n'
            f"{text}")
        raw = llm(prompt)
        m = re.search(r"\{.*\}", raw or "", re.S)
        cuts = []
        if m:
            try:
                cuts = [c.get("last_sentence", "") for c in
                        json.loads(m.group(0)).get("cuts", [])]
            except Exception:
                cuts = []
        parts, rest = [], text
        for sent in cuts:
            key = sent.strip()[-18:]
            idx = rest.find(key)
            if idx == -1:
                continue
            end = idx + len(key)
            parts.append(rest[:end].strip())
            rest = rest[end:].strip()
        parts.append(rest)
        parts = [p for p in parts if len(re.findall(r"[一-鿿]", p)) > 300]
        # 模型切点定位失败的兜底: 按段落边界机械等分, 保证必切
        # (实测第 1 章 6417 字因引文匹配失败整章未切, 超标发布)
        need = n_parts - len(parts) + 1
        fixed = []
        for p_ in parts:
            cn_p = len(re.findall(r"[一-鿿]", p_))
            if cn_p > hi * 1.15 and need > 0:
                paras = p_.split("\n")
                half, acc, cut = cn_p / 2, 0, len(paras) // 2
                for i, para in enumerate(paras):
                    acc += len(re.findall(r"[一-鿿]", para))
                    if acc >= half:
                        cut = i + 1
                        break
                a_, b_ = "\n".join(paras[:cut]).strip(), "\n".join(paras[cut:]).strip()
                fixed += [x for x in (a_, b_) if x]
                need -= 1
            else:
                fixed.append(p_)
        parts = fixed or [text]

    # 标题 + 补钩，一次调用处理整章的所有分段
    seg_list = "\n".join(f"【段{i+1}·结尾 120 字】…{p[-120:]}" for i, p in enumerate(parts))
    prompt2 = (
        f"这是《大章：{title_hint}》切成 {len(parts)} 段后的各段结尾。为每段：\n"
        f"1. 起标题（{spec['title_style']}）\n"
        f"2. 判断结尾是否带钩（悬念/威胁/反转/新事件）。不带就写 1-2 句补钩文案，"
        f"衔接原文语气，禁止「才刚刚开始」这类万金油。\n"
        f"只输出 JSON：{{\"segs\":[{{\"title\":\"…\",\"hook\":\"补钩文案或空串\"}},…]}}\n\n"
        f"{seg_list}")
    raw2 = llm(prompt2)
    m2 = re.search(r"\{.*\}", raw2 or "", re.S)
    metas = []
    if m2:
        try:
            metas = json.loads(m2.group(0)).get("segs", [])
        except Exception:
            metas = []
    out = []
    for i, p in enumerate(parts):
        meta = metas[i] if i < len(metas) else {}
        hook = (meta.get("hook") or "").strip()
        body = p + ("\n\n" + hook if hook and spec["need_hook"] else "")
        out.append({"title": (meta.get("title") or f"{title_hint}·{i+1}").strip()[:16],
                    "text": body})
    return out
