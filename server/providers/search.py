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

#: 结构性垃圾域名 —— 这类站点无论查什么都不可能是考据资料，零成本先滤掉，
#: 省得占着名额去消耗模型的判定。语义上的答非所问不在这里管（规则判不准），
#: 交给检索层的逐条 AI 过滤。
#: 名单来源：实测一次中文考据检索里，bing 返回的 10 条有 4 条是
#: 「TikTok - Make Your Day」和「抖音企业号怎么注册-百度经验」。
JUNK_HOST = re.compile(
    r"(imagecompressor|11zon|pdf2go|onlineconvert|resize-image|freepik|shutterstock"
    r"|tiktok\.|douyin\.|kuaishou\.|xiaohongshu\.|bilibili\.com/video"
    r"|jingyan\.baidu\.com|zhidao\.baidu\.com|wenku\.baidu\.com"
    r"|taobao\.|tmall\.|jd\.com|1688\.com|pinduoduo"
    r"|/(?:login|register|signup)(?:$|\?))", re.I)


class BaseSearch:
    id = "base"

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.endpoint = (cfg.get("endpoint") or "").rstrip("/")
        self.timeout = int(cfg.get("timeout") or 20)
        self.lang = cfg.get("lang") or "zh-CN"
        # 定向引擎。实例启用 85 家，实测只有 3 家真在服务（baidu/sogou 被
        # CAPTCHA 封、google cse 限流、wikipedia 无响应），而 bing 返回的
        # 大半是短视频与百科泛述。按质量排序、只打有效的那几家，比让实例
        # 用默认混合要干净得多。
        self.engines: List[str] = [str(x) for x in (cfg.get("engines") or []) if x]
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
        # engines= 参数在这版实例上不生效（指定 google 照样返回 bing+yandex），
        # 实际管用的是查询串前缀的 !bang 语法。
        q = " ".join(f"!{e}" for e in self.engines) + (" " if self.engines else "") + query
        r = requests.get(f"{self.endpoint}/search",
                         params={"q": q, "format": "json", "language": self.lang},
                         timeout=self.timeout)
        r.raise_for_status()
        res = r.json().get("results", [])
        if self.engines:      # 按配置里的引擎顺序排优先级
            rank = {e: i for i, e in enumerate(self.engines)}
            res.sort(key=lambda it: min(
                [rank.get(x, 99) for x in (it.get("engines") or [it.get("engine", "")])]
                or [99]))
        return res[:k]


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
