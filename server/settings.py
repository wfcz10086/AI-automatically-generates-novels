"""全局 + 项目级配置. 用户改 config/settings.yaml 即可统一调整所有项目."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "config" / "settings.yaml"          # 带注释的用户文件, 程序只读不写
LOCAL = ROOT / "config" / "settings.local.yaml"   # UI 保存写这里, 只存差异

DEFAULTS: Dict[str, Any] = {
    "generation": {"chapter_words_min": 2200, "chapter_words_max": 3000,
                   "chapter_words_tolerance": 0.25, "outline_batch": 10,
                   "max_tokens_draft": 8192, "max_tokens_plan": 8000, "critique_budget_chars": 46000,
                   "context_budget": "auto", "output_reserve": 8192,
                   "safety_margin": 4000,
                   "min_context_budget": 32000, "max_context_budget": 100000, "temperature_draft": 0.92,
                   "temperature_plan": 0.80},
    "limits": {"max_chapters": 500, "max_total_words": 2_000_000, "daily_call_budget": 0},
    "quality": {"audit_pass_score": 70, "max_rewrites": 1, "hard_fail_on_blacklist": True},
    "memory": {"enabled": True, "top_k": 40, "recent_chapters": 16, "recent_full": 14, "foreshadow_show": 24, "l2_every": 10,
               "index_chapters": True},
    "style_defaults": {"narration": "第三人称限制视角", "tense": "过去时", "extra": ""},
    "context_menus": [],
    "banned_global": [], "anti_ai_rules": [], "chapter_directives": [],
}


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read(p: Path) -> Dict[str, Any]:
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[settings] {p.name} 解析失败, 忽略: {e}")
        return {}


def load(project_overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """优先级: 项目覆盖 > settings.local.yaml > settings.yaml > 代码默认."""
    cfg = _merge(DEFAULTS, _read(PATH))
    cfg = _merge(cfg, _read(LOCAL))
    return _merge(cfg, project_overrides or {})


def _diff(base: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """只保留与基线不同的项 —— 覆盖层越小越好, 免得挡住以后基线的更新."""
    out: Dict[str, Any] = {}
    for k, v in (new or {}).items():
        b = base.get(k)
        if isinstance(v, dict) and isinstance(b, dict):
            d = _diff(b, v)
            if d:
                out[k] = d
        elif v != b:
            out[k] = v
    return out


def save(cfg: Dict[str, Any]) -> None:
    """UI 保存 —— 只写覆盖层, 绝不重写 settings.yaml。

    之前直接 yaml.safe_dump 回 settings.yaml, 结果把整份带注释的配置文件
    冲成了无注释的裸 YAML, 连 context_budget: auto 都被存成了 0。
    配置文件的注释是给用户读的, 属于资产, 不能被程序抹掉。
    """
    base = _merge(DEFAULTS, _read(PATH))
    over = _diff(base, cfg)
    LOCAL.parent.mkdir(parents=True, exist_ok=True)
    if over:
        LOCAL.write_text(
            "# 由「全局设置」页面自动写入，只存与 settings.yaml 的差异。\n"
            "# 想恢复默认删掉本文件即可；注释与文档在 settings.yaml 里。\n"
            + yaml.safe_dump(over, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
    elif LOCAL.exists():
        LOCAL.unlink()
