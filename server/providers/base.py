"""Provider 抽象层.

一个文件 = 一个供应商. 放进来即可用, 拔掉即卸载.

核心职责是把各家"OpenAI 兼容"接口的差异吃掉, 对上层只暴露 Delta(text, reasoning).
实测差异 (2026-09-05):
    qwen3.8-max / qwen3.8-flash (gether 网关)  -> delta.reasoning_content
    Qwen3.8-Flash-Next (自建 vLLM)             -> delta.reasoning      ← 非标准
    Qwen3.6-35B        (自建 vLLM)             -> delta.reasoning      ← 非标准
    多数模型                                    -> delta.content
Qwen3.x 在某些 prompt 下会把答案整个放进 reasoning 并包在 <answer></answer> 里,
content 全空 —— 老版 app.py 只读 delta.content, 因此前端一片空白.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, List, Dict, Any, Optional

# 思考字段的所有已知命名, 按优先级探测
#   reasoning_content  DeepSeek / 多数聚合网关 / Qwen 商用版
#   reasoning          vLLM 自建 (Qwen3.6-35B / Qwen3.8-Flash-Next) —— 非标准
#   thinking           Anthropic 兼容层 / 部分中转
#   thought            Gemini 兼容层
REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "thought",
                  "reasoning_text", "thinking_content")

# 正文字段的所有已知命名. OpenAI 系是 content, 但各家兼容层五花八门:
#   text        部分中转 / completions 风格
#   result      文心一言
#   output_text 部分网关
CONTENT_KEYS = ("content", "text", "result", "output_text", "delta_text")

_ANSWER_RE = re.compile(r"<answer>(.*?)(?:</answer>|$)", re.S)


@dataclass
class Delta:
    """流式增量的统一表示."""
    text: str = ""        # 正文
    reasoning: str = ""   # 思考过程 (前端折叠展示, 不进正文)

    def __bool__(self) -> bool:
        return bool(self.text or self.reasoning)


class ProviderError(RuntimeError):
    pass


class BaseProvider:
    id: str = "base"
    name: str = "Base"

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.base_url: str = cfg["base_url"].rstrip("/")
        self.api_key: str = cfg.get("api_key") or "EMPTY"
        # 允许在配置里声明该网关的思考字段名; 为空则自动探测
        self.reasoning_field: Optional[str] = cfg.get("reasoning_field")
        # 实际命中过的字段名, 供接入排查用
        self.seen_fields: set[str] = set()

    # --- 子类需要实现 -------------------------------------------------
    def stream(self, messages: List[Dict[str, str]], **kw) -> Iterator[Delta]:
        raise NotImplementedError

    def models(self) -> List[str]:
        raise NotImplementedError

    # --- 通用工具 -----------------------------------------------------
    @staticmethod
    def _flatten(v: Any) -> str:
        """把各种形状的 content 压成字符串.

        见过的形状:
          "文本"                                    多数厂商
          [{"type":"text","text":"…"}, …]           Anthropic 兼容层 / 部分网关
          [{"text":"…"}, …]                         Gemini 兼容层 parts
          {"text":"…"}                              个别中转
          ["a","b"]                                 少数把 token 拆成数组的实现
        """
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            for k in ("text", "content", "value"):
                if isinstance(v.get(k), str):
                    return v[k]
            return ""
        if isinstance(v, (list, tuple)):
            return "".join(BaseProvider._flatten(x) for x in v)
        return ""

    def adapt(self, delta: Dict[str, Any]) -> Delta:
        """把一个原始 delta dict 归一化成 Delta.

        字段名与形状都做兼容: 配置里显式声明了 reasoning_field 就优先用它,
        否则按已知命名依次探测; 正文同理. 都取不到时不报错, 返回空 Delta,
        由上层的空输出重试兜底 —— 接新模型时不会因为字段名对不上就整个哑掉.
        """
        if not isinstance(delta, dict):
            return Delta()

        # 有些兼容层把内容再包一层 message
        if "message" in delta and isinstance(delta["message"], dict):
            inner = delta["message"]
            delta = {**inner, **{k: v for k, v in delta.items() if k != "message"}}

        think = ""
        if self.reasoning_field:
            think = self._flatten(delta.get(self.reasoning_field))
        if not think:
            for k in REASONING_KEYS:
                think = self._flatten(delta.get(k))
                if think:
                    break

        text = ""
        for k in CONTENT_KEYS:
            text = self._flatten(delta.get(k))
            if text:
                break

        # 记录本次实际命中的字段名, 便于 /api/probe 报给用户
        if text or think:
            self.seen_fields.update(
                k for k in delta
                if delta.get(k) and k in set(REASONING_KEYS) | set(CONTENT_KEYS))
        return Delta(text=text, reasoning=think)

    @staticmethod
    def salvage(reasoning: str) -> str:
        """兜底: content 全空但 reasoning 有货时, 只从 <answer> 标签里取答案.

        绝不把整段思考当正文返回 —— 那会把"我们需要回答中文用户…"这种内部独白
        写进稿子里 (实测踩过). 取不到就返回空, 由上层关思考重试.
        """
        if not reasoning:
            return ""
        m = _ANSWER_RE.search(reasoning)
        return m.group(1).strip() if m else ""
