"""提示词渲染引擎.

修掉老版的两个问题:
  1. 老版用链式 .replace('${x}', v) —— JS/Python 传字符串只替换第一次出现,
     模板里重复用同一个占位符时第二个会静默失效. 这里一律全局替换.
  2. 老版把上文原样怼进去, 没有长度控制, 长篇必然爆 context.
     这里按 token 预算分优先级裁剪.
"""
from __future__ import annotations

import re
from typing import Dict, Any, List, Tuple
from server.distill import soft

_VAR = re.compile(r"\$\{(\w+)\}")

# Qwen 系中文实测 1 汉字 ≈ 0.75 token, 即 1 token ≈ 1.33 字。
# 之前按 1 字 1.43 tok 高估近一倍, 导致预算 97k 只敢装 68k 字,
# 五层记忆的容量凭空少了一半。
CHARS_PER_TOKEN = 1.33


def est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) if text else 0


def render(template: str, ctx: Dict[str, Any], strict: bool = False) -> str:
    """全局替换 ${var}. 未知变量替换为空串并可选报警."""
    missing: List[str] = []

    def sub(m: re.Match) -> str:
        k = m.group(1)
        if k not in ctx:
            missing.append(k)
            return ""
        v = ctx[k]
        return v if isinstance(v, str) else str(v)

    out = _VAR.sub(sub, template)
    if missing and strict:
        raise KeyError(f"模板缺少变量: {sorted(set(missing))}")
    if missing:
        print(f"[prompt] 未提供的变量(已置空): {sorted(set(missing))}")
    return out


def budget(parts: List[Tuple[str, str, int]], max_tokens: int) -> Dict[str, str]:
    """按优先级分配上下文预算.

    parts: [(key, text, priority)]  priority 越小越重要, 优先保留
    超预算的低优先级内容从尾部截断并标注.
    """
    parts = sorted(parts, key=lambda x: x[2])
    used, out = 0, {}
    for key, text, _ in parts:
        if not text:
            out[key] = ""
            continue
        need = est_tokens(text)
        if used + need <= max_tokens:
            out[key] = text
            used += need
        else:
            room = max(0, max_tokens - used)
            keep = int(room * CHARS_PER_TOKEN)
            out[key] = ((soft(text, keep, key) + "\n…（因上下文预算收尾）")
                        if keep > 200 else "")
            used = max_tokens
    return out
