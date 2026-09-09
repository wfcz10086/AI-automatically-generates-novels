"""检索供应商 —— 与模型网关并列的一类 provider，可插拔。

加一家搜索源 = 加一个类 + 在 SEARCH_TYPES 注册一行 + providers.yaml 配一段。
框架只认 BaseSearch 接口，不关心底下是 SearXNG、Bing、还是内网知识库。
"""
from __future__ import annotations

import hashlib
import html
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
    # 题库/作业站：整站是选择题与答案，考据价值为零，却极易命中历史类关键词
    r"|shuashuati|zujuan|jyeoo|xkw\.com|xuekeda|21cnjy|doc88|docin|renrendoc"
    r"|/(?:login|register|signup)(?:$|\?))", re.I)


class BaseSearch:
    id = "base"
    #: 一次真实请求最多留存多少条。取宽一点，后续同式不同 k 的检索直接切片复用。
    CACHE_WIDTH = 12

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
        """绑定结果缓存目录。

        缓存**跨书共享**：同一个「宋代盐引怎么走」，第二本书不该再花一次
        检索额度。目录由 registry 统一指到仓库级 .cache/search，
        项目目录只留一份软引用。
        """
        d.mkdir(parents=True, exist_ok=True)
        self._cache = d
        self._stats = d.parent / "search_usage.json"
        return self

    # --- 计费次数：只统计真正打到外部的请求，缓存命中不计 ---
    _stats: Optional[Path] = None

    def usage(self) -> Dict[str, Any]:
        if not self._stats or not self._stats.exists():
            return {"calls": 0, "by_day": {}, "by_provider": {}}
        try:
            return json.loads(self._stats.read_text(encoding="utf-8"))
        except Exception:
            return {"calls": 0, "by_day": {}, "by_provider": {}}

    def _count(self, n: int = 1) -> None:
        if not self._stats:
            return
        import time as _t
        u = self.usage()
        day = _t.strftime("%Y-%m-%d")
        u["calls"] = int(u.get("calls", 0)) + n
        u.setdefault("by_day", {})[day] = int(u.get("by_day", {}).get(day, 0)) + n
        u.setdefault("by_provider", {})[self.id] = int(
            u.get("by_provider", {}).get(self.id, 0)) + n
        u["last"] = _t.strftime("%F %T")
        try:
            self._stats.write_text(json.dumps(u, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except Exception:
            pass

    def _log_query(self, query: str, hit: bool, n: int) -> None:
        """把检索式本身落一行盘。

        缓存文件名是查询的哈希, 光看目录**看不出查过什么** —— 想知道钱花在
        哪些检索式上、哪些查了等于没查(命中 0 条), 只能靠这个日志。
        写不进去就算了, 记账不该拖垮检索。
        """
        if not self._stats:
            return
        import time as _t
        line = json.dumps({"at": _t.strftime("%F %T"), "q": query,
                           "hit": hit, "n": n, "src": self.id}, ensure_ascii=False)
        try:
            with (self._stats.parent / "search_log.jsonl").open(
                    "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def available(self) -> bool:
        raise NotImplementedError

    def _fetch(self, query: str, k: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    # --- 通用: 缓存 + 清洗 ---
    def search(self, query: str, k: int = 6) -> List[Dict[str, Any]]:
        cp = None
        if self._cache:
            # 缓存键**不含 k**：同一条检索式要 5 条和要 6 条，在按次计费的源上
            # 是两次扣费、一模一样的内容。一次多取一点存下来，按需切片。
            cp = self._cache / (hashlib.sha1(f"{self.id}|{query}".encode())
                                .hexdigest()[:16] + ".json")
            if cp.exists():
                try:
                    got = json.loads(cp.read_text(encoding="utf-8"))
                    if len(got) >= k or len(got) >= self.CACHE_WIDTH:
                        self._log_query(query, True, len(got[:k]))
                        return got[:k]
                except Exception:
                    pass
        try:
            self._count()          # 缓存没命中, 这一次要真花额度
            raw = self._fetch(query, max(k * 3, self.CACHE_WIDTH))
        except Exception as e:
            print(f"[search:{self.id}] {query!r} 失败: {e}")
            self._log_query(query, False, -1)
            return []
        out = []
        for it in raw:
            url = it.get("url", "")
            content = html.unescape((it.get("content") or "")).strip()
            if JUNK_HOST.search(url) or len(content) < 20:
                continue
            if re.search(r"[一-鿿]", query) and not re.search(
                    r"[一-鿿]", it.get("title", "") + content):
                continue
            out.append({"title": html.unescape(it.get("title", ""))[:120], "url": url,
                        "content": content[:1200], "engine": it.get("engine", self.id)})
            if len(out) >= self.CACHE_WIDTH:
                break
        if cp:
            cp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        self._log_query(query, False, len(out))
        return out[:k]


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


class BochaSearch(BaseSearch):
    """博查 AI 搜索 —— 按次计费的中文搜索 API。

    选它的理由是实测：本地 SearXNG 名义启用 85 家引擎，实际只有 yandex 与
    bing 在服务（baidu/sogou 被 CAPTCHA 封、google cse 限流、wikipedia 零响应），
    而 bing 返回的一半是短视频与电商。同一条「北宋买扑盐引」检索，博查前三条
    是杭州市文旅局、国学网中国经济史论坛这类真能用的来源。

    按次计费，所以两件事必须做实：结果跨书共享缓存，真实请求逐次计数。
    """
    id = "bocha"
    ENDPOINT = "https://api.bochaai.com/v1/web-search"

    def available(self) -> bool:
        # 不拿一次真实检索去探活 —— 那是在烧额度。有钥匙就认为可用，
        # 真失败会在 search() 里被捕获并打日志。
        return bool(self.cfg.get("api_key") and
                    self.cfg["api_key"] not in ("EMPTY", "", None))

    def _fetch(self, query: str, k: int) -> List[Dict[str, Any]]:
        r = requests.post(self.endpoint or self.ENDPOINT,
                          headers={"Authorization": f"Bearer {self.cfg['api_key']}",
                                   "Content-Type": "application/json"},
                          json={"query": query, "summary": True,
                                "count": max(4, min(k, 20)),
                                "freshness": self.cfg.get("freshness") or "noLimit"},
                          timeout=self.timeout)
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 200:
            raise RuntimeError(f"博查返回 {d.get('code')}: {d.get('msg')}")
        out = []
        for v in (((d.get("data") or {}).get("webPages") or {}).get("value") or [])[:k]:
            # summary 比 snippet 长得多, 优先用它 —— 摘要质量的天花板就在这里
            body = (v.get("summary") or v.get("snippet") or "").strip()
            site = (v.get("siteName") or "").strip()
            date = (v.get("datePublished") or "")[:10]
            out.append({"title": v.get("name", ""), "url": v.get("url", ""),
                        "content": body,
                        "engine": f"bocha/{site}" if site else "bocha",
                        "published": date})
        return out


class NullSearch(BaseSearch):
    """未配置检索时的空实现 —— 让上层代码不必到处判空。"""
    id = "null"

    def available(self) -> bool:
        return False

    def _fetch(self, query: str, k: int) -> List[Dict[str, Any]]:
        return []


SEARCH_TYPES = {
    "bocha": BochaSearch,
    "searxng": SearxNGSearch,
    "http_json": OpenSearchCompat,
    "null": NullSearch,
}
