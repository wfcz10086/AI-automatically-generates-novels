"""插件注册表: 网关 / 内容类型 / 题材包 的加载与解析."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

import yaml

from .providers import PROVIDER_TYPES, BaseProvider
from .providers.search import SEARCH_TYPES, BaseSearch, NullSearch


def _load_dotenv() -> None:
    """读 .env 到环境变量. 真实地址与密钥只存在于 .env (已 gitignore)."""
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
PACKS = ROOT / "packs"
CONFIG = ROOT / "config"

_ENV_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: Any) -> Any:
    """展开 ${VAR} / ${VAR:-default}."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


class Registry:
    def __init__(self) -> None:
        self.reload()

    # ---------------- 网关 ----------------
    def reload(self) -> None:
        cfg = _expand(yaml.safe_load((CONFIG / "providers.yaml").read_text(encoding="utf-8")))
        # 未配置地址的网关直接跳过, 不让半配的网关污染下拉框
        self.gateways: Dict[str, Dict[str, Any]] = {
            k: v for k, v in cfg["gateways"].items() if (v.get("base_url") or "").strip()
        }
        if not self.gateways:
            print("[registry] 警告: 没有可用网关, 请照 .env.example 配置 .env")
        # 档位指向的网关若不可用, 自动落到第一个可用网关
        fallback = next(iter(self.gateways), None)
        self.profiles: Dict[str, Dict[str, Any]] = {}
        for name, prof in cfg["profiles"].items():
            prof = dict(prof)
            if prof.get("gateway") not in self.gateways:
                prof["gateway"] = fallback
            self.profiles[name] = prof
        self.default_profile: str = cfg.get("default_profile", "drafting")
        self._providers: Dict[str, BaseProvider] = {}

        sc = cfg.get("search") or {}
        self.search_cfg: Dict[str, Any] = sc.get("providers") or {}
        self.search_default: str = sc.get("default") or next(iter(self.search_cfg), "")
        self._searchers: Dict[str, BaseSearch] = {}

        self.types = self._load_packs(PACKS / "type")
        self.genres = self._load_packs(PACKS / "genre")
        self.styles = self._load_packs(PACKS / "style")
        common = PACKS / "common" / "novel-common.json"
        self.common: Dict[str, Any] = json.loads(common.read_text(encoding="utf-8")) if common.exists() else {}

    @staticmethod
    def _load_packs(d: Path) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        if not d.exists():
            return out
        for f in sorted(d.glob("*.json")):
            try:
                pack = json.loads(f.read_text(encoding="utf-8"))
                out[pack["id"]] = pack
            except Exception as e:
                print(f"[registry] 跳过损坏的包 {f.name}: {e}")
        return out

    def provider(self, gateway_id: str) -> BaseProvider:
        if gateway_id not in self._providers:
            cfg = self.gateways[gateway_id]
            cls = PROVIDER_TYPES[cfg.get("type", "openai_compat")]
            self._providers[gateway_id] = cls(cfg)
        return self._providers[gateway_id]

    def searcher(self, name: Optional[str] = None) -> BaseSearch:
        """取检索 provider。未配置或不可用时返回空实现, 上层无需判空。"""
        name = name or self.search_default
        if not name or name not in self.search_cfg:
            return NullSearch({})
        if name not in self._searchers:
            c = self.search_cfg[name]
            cls = SEARCH_TYPES.get(c.get("type", "searxng"), NullSearch)
            # 在这里就把缓存与计数目录绑好, 而不是等调用方自己 bind_cache ——
            # 漏绑的后果不是报错, 是**静默地既不缓存也不计数**, 按次计费的源上
            # 这等于白烧额度还查不出烧在哪。目录挂仓库级, 跨书共享。
            self._searchers[name] = cls(c).bind_cache(
                Path(__file__).resolve().parents[1] / ".cache" / "search")
        return self._searchers[name]

    def resolve(self, profile: Optional[str] = None, **override) -> tuple[BaseProvider, Dict[str, Any]]:
        """按档位解析出 (provider, 调用参数)."""
        name = profile or self.default_profile
        prof = dict(self.profiles.get(name) or self.profiles[self.default_profile])
        prof.update({k: v for k, v in override.items() if v is not None})
        gw = prof.pop("gateway")
        p = self.provider(gw)
        kw = {
            "model": prof.get("model") or self.gateways[gw].get("default_model"),
            "thinking": prof.get("thinking", False),
            "temperature": prof.get("temperature", 0.85),
            "max_tokens": prof.get("max_tokens") or self.gateways[gw].get("max_tokens", 8192),
        }
        return p, kw

    # ---------------- 前端要的清单 ----------------
    def catalog(self) -> Dict[str, Any]:
        return {
            "gateways": [
                {"id": k, "label": v.get("label", k), "model": v.get("default_model"),
                 "context_window": v.get("context_window")}
                for k, v in self.gateways.items()
            ],
            "profiles": list(self.profiles.keys()),
            # 按包声明的 order 排, 不按文件名字母序 —— 否则「动漫分镜」(anime)
            # 排在最前并成为新建项目的默认选中项, 而绝大多数用户是来写小说的。
            "types": [
                {"id": t["id"], "name": t["name"], "levels": [l["name"] for l in t["levels"]],
                 "exporters": t.get("exporters", []),
                 "defaultStyle": t.get("defaultStyle", "")}
                for t in sorted(self.types.values(),
                                key=lambda x: (x.get("order", 99), x["id"]))
            ],
            "genres": [{"id": g["id"], "name": g["name"], "menus": g.get("menus", []),
                        "historyMode": g.get("historyMode", ""),
                        "historyModeAmbiguous": bool(g.get("historyModeAmbiguous"))}
                       for g in self.genres.values()],
            "search": [{"id": k, "label": v.get("label", k), "type": v.get("type")}
                       for k, v in self.search_cfg.items()],
            "styles": [{"id": s["id"], "name": s["name"]} for s in self.styles.values()],
        }


registry = Registry()
