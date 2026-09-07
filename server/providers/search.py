"""检索供应商 —— 与模型网关并列的一类 provider，可插拔。

加一家搜索源 = 加一个类 + 在 SEARCH_TYPES 注册一行 + providers.yaml 配一段。
框架只认 BaseSearch 接口，不关心底下是 SearXNG、Bing、还是内网知识库。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

JUNK_HOST = re.compile(
    r"(imagecompressor|11zon|pdf2go|onlineconvert|resize-image|freepik|shutterstock)", re.I)


class BaseSearch:
    id = "base"

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.endpoint = (cfg.get("endpoint") or "").rstrip("/")
        self.timeout = int(cfg.get("timeout") or 20)
        self.lang = cfg.get("lang") or "zh-CN"
        self._cache: Optional[Path] = None

    def bind_cache(self, d: Path) -> "BaseSearch":
        d.mkdir(parents=True, exist_ok=True)
        self._cache = d
        return self

    def available(self) -> bool:
        raise NotImplementedError

    def _fetch(self, query: str, k: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    # --- 通用: 缓存 + 清洗 ---
    def search(self, query: str, k: int = 6) -> List[Dict[str, Any]]:
        cp = None
        if self._cache:
            cp = self._cache / (hashlib.sha1(f"{self.id}|{query}|{k}".encode())
                                .hexdigest()[:16] + ".json")
            if cp.exists():
                try:
                    return json.loads(cp.read_text(encoding="utf-8"))
                except Exception:
                    pass
        try:
            raw = self._fetch(query, k * 3)
        except Exception as e:
            print(f"[search:{self.id}] {query!r} 失败: {e}")
            return []
        out = []
        for it in raw:
            url, content = it.get("url", ""), (it.get("content") or "").strip()
            if JUNK_HOST.search(url) or len(content) < 20:
                continue
            if re.search(r"[一-鿿]", query) and not re.search(
                    r"[一-鿿]", it.get("title", "") + content):
                continue
            out.append({"title": it.get("title", "")[:120], "url": url,
                        "content": content[:600], "engine": it.get("engine", self.id)})
            if len(out) >= k:
                break
        if cp:
            cp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out


class SearxNGSearch(BaseSearch):
    id = "searxng"

    def available(self) -> bool:
        try:
            return requests.get(self.endpoint, timeout=5).status_code == 200
        except Exception:
            return False

    def _fetch(self, query: str, k: int) -> List[Dict[str, Any]]:
        r = requests.get(f"{self.endpoint}/search",
                         params={"q": query, "format": "json", "language": self.lang},
                         timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("results", [])[:k]


class OpenSearchCompat(BaseSearch):
    """任意返回 {results:[{title,url,content}]} 的 HTTP 检索服务。"""
    id = "http_json"

    def available(self) -> bool:
        try:
            return requests.get(self.endpoint, timeout=5).status_code < 500
        except Exception:
            return False

    def _fetch(self, query: str, k: int) -> List[Dict[str, Any]]:
        params = dict(self.cfg.get("params") or {})
        params[self.cfg.get("query_param", "q")] = query
        headers = {}
        if self.cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {self.cfg['api_key']}"
        r = requests.get(self.endpoint, params=params, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        d = r.json()
        return (d.get("results") or d.get("data") or d.get("items") or [])[:k]


class NullSearch(BaseSearch):
    """未配置检索时的空实现 —— 让上层代码不必到处判空。"""
    id = "null"

    def available(self) -> bool:
        return False

    def _fetch(self, query: str, k: int) -> List[Dict[str, Any]]:
        return []


SEARCH_TYPES = {
    "searxng": SearxNGSearch,
    "http_json": OpenSearchCompat,
    "null": NullSearch,
}
