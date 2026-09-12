"""OpenAI 兼容供应商 (vLLM / 各类网关 / DeepSeek / 通义 / 302.AI / Ollama ...).

绝大多数国内外服务都走这个类, 差异靠 providers.yaml 里的字段声明吃掉.
"""
from __future__ import annotations

import json
import time
from typing import Iterator, List, Dict, Any

import requests

from .base import BaseProvider, Delta, ProviderError


class OpenAICompatProvider(BaseProvider):
    #: 发送阶段的重试次数（瞬时写超时用）
    SEND_RETRIES = 3
    #: 流式响应**块与块之间**允许的最长静默。两个 chunk 隔这么久就是挂了。
    READ_TIMEOUT = 120
    #: 单次调用的总时长上限。防「每隔 50 秒吐一个字」这类慢性挂起。
    CALL_BUDGET = 480

    id = "openai_compat"
    name = "OpenAI 兼容"

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def models(self) -> List[str]:
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=15)
            r.raise_for_status()
            return [m["id"] for m in r.json().get("data", [])]
        except Exception as e:  # 网关不可用不应该炸掉整个应用
            raise ProviderError(f"拉取模型列表失败: {e}") from e

    def stream(self, messages: List[Dict[str, str]], **kw) -> Iterator[Delta]:
        """流式生成. 关键点见 base.py 的模块注释.

        kw: model / temperature / max_tokens / thinking(bool)
        """
        self.last_usage = {}
        #: 上一次生成的收尾原因。"length" = 撞上输出上限被切断 ——
        #: 这是 API 本来就给的信号，接住它就不必逐个调用去猜 max_tokens。
        self.last_finish = ""
        thinking = kw.pop("thinking", False)
        body: Dict[str, Any] = {
            "model": kw.pop("model", self.cfg.get("default_model")),
            "messages": messages,
            "stream": True,
            "temperature": kw.pop("temperature", 0.85),
            "max_tokens": kw.pop("max_tokens", self.cfg.get("max_tokens", 8192)),
        }
        # 思考开关: 各家约定不同, 分两类。
        #   开关型(vLLM/Qwen 系): enable_thinking = true/false, 可以彻底关掉
        #   档位型(GLM 系):       thinking 只能调档 low/high/max, 关不掉 ——
        #                         传 enable_thinking=false 会直接 400
        #                         「该模型始终会思考，不支持关闭思考」
        # 网关用 thinking_style 声明自己是哪一类。
        # 聚合网关会同时服务多个模型族（gw4 上既有 glm 又有 qwen），而思考开关的
        # 形态是**按模型族**分的, 不是按网关分的:
        #   qwen 系支持 enable_thinking=false, 能彻底关掉；
        #   glm 系只能调档, 传 enable_thinking=false 直接 400。
        # 只配一个 thinking_style 必然有一半模型吃错参数 —— 实测 qwen 在配了
        # effort 的网关上, 一条 6.2 万字提示词要 109s 且白烧 4096 思考 token；
        # 换成 toggle 后 36s、思考 0。所以支持按模型名前缀覆盖。
        style = (self.cfg.get("thinking_style") or "toggle").lower()
        by_model = self.cfg.get("thinking_style_by_model") or {}
        _m = str(kw.get("model") or body.get("model") or "").lower()
        for pref, st in by_model.items():
            if _m.startswith(str(pref).lower()):
                style = str(st).lower()
                break
        if style == "effort":
            levels = self.cfg.get("thinking_levels") or {}
            body["reasoning_effort"] = (levels.get("on", "high") if thinking
                                        else levels.get("off", "low"))
        else:
            if not thinking:
                body["enable_thinking"] = False
                body["chat_template_kwargs"] = {"enable_thinking": False}
            else:
                body["enable_thinking"] = True
        body.update(kw)

        # 发送阶段的超时是**瞬时错误**：排纲的请求体有五万多字符，实测
        # 46 次成功里伴随 12 次 "The write operation timed out"，21% 的失败率，
        # 而每次失败都要整批重排（四到八分钟白跑）—— 守护重来必成，说明重试
        # 一次就够，不该把这个代价推到上层。
        # 连接超时给到 60s：大请求体的发送过程算在连接阶段里，20s 太紧。
        self.last_finish = ""
        last = None
        for attempt in range(self.SEND_RETRIES):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                    stream=True,
                    # (连接, **块与块之间**)。原来读超时给的是 600s ——
                    # 流式生成里两个 chunk 隔十分钟从来不是正常情况, 这个值
                    # 等于没有超时。实测挂过一次: 最后一个 chunk 停在 15:58:26,
                    # 连接一直 ESTABLISHED, 整条流水线干等, 而守护那一层的
                    # 设计是「活着但不涨章只报警不重启」(免得打断正在写的章),
                    # 于是没有任何人会来救它。
                    timeout=(60, self.READ_TIMEOUT),
                )
                break
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError) as e:
                last = e
                if attempt + 1 < self.SEND_RETRIES:
                    wait = 3 * (attempt + 1)
                    print(f"[provider] 发送失败（{type(e).__name__}），"
                          f"{wait}s 后重试 {attempt + 2}/{self.SEND_RETRIES}",
                          flush=True)
                    time.sleep(wait)
            except Exception as e:                 # 其余异常不重试
                raise ProviderError(f"连接失败: {e}") from e
        else:
            raise ProviderError(f"连接失败（重试 {self.SEND_RETRIES} 次）: {last}") from last

        if resp.status_code != 200:
            # requests 按 header 猜编码, 不少网关不带 charset 就退回 latin-1,
            # 中文报错全变成乱码（实测「该模型始终会思考」变成一串 è¯¥æ¨¡å）
            if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
                resp.encoding = "utf-8"
            raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        # 流式响应同样要钉死 UTF-8: iter_lines(decode_unicode=True) 用的是
        # resp.encoding, 网关不带 charset 时 requests 退回 latin-1, 中文正文
        # 会整段变成 æä»£ä¸ç³ç±³ 这样的乱码 —— 不是模型的问题, 是解码的问题
        if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
            resp.encoding = "utf-8"
        # 第二层: 单次调用的总时长上限。块间超时只管「一直没有下一块」,
        # 管不了「每隔 50 秒吐一个字」这种慢性挂起 —— 那种情况块间超时永远
        # 不触发, 而这一章能写到天亮。
        _t0 = time.time()
        for raw in resp.iter_lines(decode_unicode=True):
            if time.time() - _t0 > self.CALL_BUDGET:
                resp.close()
                raise ProviderError(
                    f"单次调用超过 {self.CALL_BUDGET}s 仍未结束（已出 "
                    f"{self.last_finish or '未完'}），判定挂起并中止")
            if not raw or not raw.startswith("data: "):
                continue
            payload = raw[6:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            fr = choices[0].get("finish_reason")
            if fr:
                self.last_finish = str(fr)
            d = self.adapt(choices[0].get("delta") or {})
            if d:
                yield d
            u = chunk.get("usage")
            if u:                       # vLLM/网关在末帧带 usage
                self.last_usage = {"prompt": u.get("prompt_tokens"),
                                   "completion": u.get("completion_tokens"),
                                   "total": u.get("total_tokens")}
