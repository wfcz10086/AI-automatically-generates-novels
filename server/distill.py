"""提炼代替硬切。

—— 为什么要有这个文件 ——

全仓库扫出 171 处 `[:N]` 硬切。硬切的问题不是「限制长度」，是**它把尾巴直接
扔掉**：一章 4022 字的正文 `[:2000]` 之后，后一半再也召不回来，而下游拿到的
东西看起来是完整的，没人知道丢了什么。

规矩：
  1. **不许硬切**喂给模型的长文本
  2. 超长就**提炼**（保留信息，压缩篇幅），目标压缩比 3:1
  3. 提炼结果按内容哈希落盘缓存 —— 同一段只花一次钱
  4. 提炼失败才退回截断，且**必须打日志**

什么不用提炼：
  · 日志、预览、错误信息（`print(err[:400])` 这类）—— 本来就是给人看一眼的
  · 结构化字段的存储上限（`solves[:120]`）—— 那是模型输出的字段，不是输入
    的长文本；这类该做的是**放宽或校验**，不是提炼
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Optional

#: 目标压缩比。原文超过 目标字数×RATIO 才值得花一次调用去提炼；
#: 没超过就直接截断也丢不了多少（而且提炼本身有损）。
RATIO = 3

_CACHE_DIR: Optional[Path] = None


def bind_cache(d: Path) -> None:
    global _CACHE_DIR
    _CACHE_DIR = d
    d.mkdir(parents=True, exist_ok=True)


def _key(text: str, limit: int, what: str) -> str:
    h = hashlib.sha1(f"{what}|{limit}|{text}".encode("utf-8")).hexdigest()[:16]
    return h


def _cached(k: str) -> Optional[str]:
    if not _CACHE_DIR:
        return None
    f = _CACHE_DIR / f"{k}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("text")
    except Exception:
        return None


def _store(k: str, text: str, meta: dict) -> None:
    if not _CACHE_DIR:
        return
    try:
        (_CACHE_DIR / f"{k}.json").write_text(
            json.dumps({"text": text, **meta}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def p_distill(text: str, limit: int, what: str) -> str:
    return (
        f"把下面这段【{what}】压缩到 {limit} 字以内，**不许丢信息**。\n\n"
        f"压缩的办法：\n"
        f"- 合并重复与同义的表述；删掉修饰、铺陈、举例\n"
        f"- 人名、地名、数字、时间、因果关系、已经定死的事实**一个都不能少**\n"
        f"- 保留原文的条目结构（有编号就还用编号，有分栏就还分栏）\n"
        f"- 不写「以下是压缩后的内容」这类前言，直接输出\n\n"
        f"── 原文（{len(text)} 字）──\n{text}")


def distill(text: str, limit: int, what: str,
            ask: Optional[Callable[[str], str]] = None) -> str:
    """超长就提炼，不硬切。

    ask: 调模型的函数（收提示词、返回文本）。不给就退回带日志的截断。
    """
    t = text or ""
    if len(t) <= limit:
        return t

    # 只超一点点：提炼的损失大于收益，直接按句子边界收尾
    if len(t) < limit * 1.2 or ask is None:
        cut = _soft_cut(t, limit)
        print(f"  [提炼] {what}: {len(t)} → {len(cut)} 字"
              f"（{'超出不多，按句子收尾' if ask else '没有提炼器，按句子收尾'}）",
              flush=True)
        return cut

    k = _key(t, limit, what)
    hit = _cached(k)
    if hit:
        return hit

    try:
        out = (ask(p_distill(t, limit, what)) or "").strip()
    except Exception as e:
        out = ""
        print(f"  [提炼] {what} 失败({type(e).__name__})，退回按句子收尾", flush=True)
    if not out or len(out) > limit * 1.5:
        cut = _soft_cut(t, limit)
        print(f"  [提炼] {what}: 提炼没成（收到 {len(out)} 字），"
              f"退回 {len(cut)} 字", flush=True)
        return cut
    print(f"  [提炼] {what}: {len(t)} → {len(out)} 字"
          f"（压缩比 {len(t)/max(1,len(out)):.1f}:1）", flush=True)
    _store(k, out, {"what": what, "src_len": len(t), "limit": limit})
    return out


def _soft_cut(t: str, limit: int) -> str:
    """按句子边界收尾，不切在半句话上。"""
    if len(t) <= limit:
        return t
    head = t[:limit]
    m = list(re.finditer(r"[。！？…\n]", head))
    if m and m[-1].end() > limit * 0.6:
        return head[:m[-1].end()]
    return head
