"""两类思考开关约定：开关型（可关）与档位型（关不掉，只能调档）。

起因：接入 GLM 网关时直接 400 ——「该模型始终会思考，不支持关闭思考，
请使用 low、high 或 max」。框架原来只会发 enable_thinking=false，
这一类网关全部接不进来。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.providers import openai_compat as oc  # noqa: E402


class _Resp:
    """假的流式响应：吐一条 content 就结束。"""

    status_code = 200
    encoding = None

    def iter_lines(self, decode_unicode=False):
        chunk = {"choices": [{"delta": {"content": "好"}}]}
        yield "data: " + json.dumps(chunk, ensure_ascii=False)
        yield "data: [DONE]"


@pytest.fixture
def captured(monkeypatch):
    """拦住真实请求，把发出去的 body 交出来 —— 测的是生产代码本身。"""
    box = {}

    def fake_post(url, **kw):
        box["url"] = url
        box["body"] = kw.get("json")
        return _Resp()

    monkeypatch.setattr(oc.requests, "post", fake_post)
    return box


def _run(cfg, thinking, captured):
    p = oc.OpenAICompatProvider(cfg)
    list(p.stream([{"role": "user", "content": "hi"}], thinking=thinking))
    return captured["body"]


def test_toggle_style_sends_enable_thinking(captured):
    b = _run({"base_url": "http://x/v1", "default_model": "m"}, False, captured)
    assert b["enable_thinking"] is False
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in b


def test_effort_style_never_sends_disable_flag(captured):
    """档位型网关收到 enable_thinking=false 会直接 400。"""
    cfg = {"base_url": "http://x/v1", "default_model": "glm",
           "thinking_style": "effort", "thinking_levels": {"off": "low", "on": "high"}}
    off = _run(cfg, False, captured)
    assert "enable_thinking" not in off and "chat_template_kwargs" not in off
    assert off["reasoning_effort"] == "low"
    on = _run(cfg, True, captured)
    assert "enable_thinking" not in on
    assert on["reasoning_effort"] == "high"


def test_effort_style_has_defaults(captured):
    """网关没写 thinking_levels 也要能用。"""
    cfg = {"base_url": "http://x/v1", "default_model": "m", "thinking_style": "effort"}
    assert _run(cfg, False, captured)["reasoning_effort"] == "low"
    assert _run(cfg, True, captured)["reasoning_effort"] == "high"


def test_utf8_decoding_is_pinned():
    """网关不带 charset 时 requests 退回 latin-1，中文整段变乱码
    （实测「明代一石米」变成 æä»£ä¸ç³ç±³）—— 错误响应与流式响应都要钉死。"""
    import inspect
    src = inspect.getsource(oc.OpenAICompatProvider.stream)
    assert src.count('resp.encoding = "utf-8"') >= 2


def test_yaml_on_off_keys_are_quoted():
    """YAML 1.1 把 on/off 当布尔值。

    写 {off: low, on: high} 解析出来是 {False:'low', True:'high'}，
    levels.get("on") 取不到值，只能碰运气落到默认档 —— 默认档恰好一样，
    所以这个 bug 不会报错，只会在改默认值那天悄悄生效。
    """
    import yaml
    from pathlib import Path
    raw = (Path(__file__).resolve().parents[2] / "config" / "providers.yaml"
           ).read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    for gid, gw in (cfg.get("gateways") or {}).items():
        lv = gw.get("thinking_levels")
        if lv:
            assert set(lv) <= {"off", "on"}, \
                f"{gid}.thinking_levels 的键被 YAML 解析成了 {list(lv)}，要加引号"
