"""分层记忆控制器 —— 长篇写作的核心。

问题: 写到第 300 章时, 模型窗口只有 11 万 token, 但已有素材远超这个量。
      塞什么、塞多少、谁先被砍, 必须是一套显式规则, 而不是散落在各处的拼接。

五层记忆, 每层职责与衰减率不同:

  L0 即时层   本章细纲                       权重最高, 永不裁剪
  L1 常驻层   世界观精要 + 主角卡             全书常驻, 压缩版
  L2 近程层   最近 N 章摘要                  滑动窗口, 原样保留
  L3 中程层   每 K 章的段摘要                分层压缩, 越远越粗
  L4 远程层   FTS5 按本章细纲检索召回         只取相关, 不按时间
  L5 约束层   题材红线 + 黑名单 + 未回收伏笔   必进, 但很短

预算分配: 每层按配比拿到 token 上限, 层内超额则按该层规则裁剪 (截断 / 丢弃最旧 /
          少召回几条)。配比在 config/settings.yaml 里可调, 用户能直接控制"记住什么"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .prompt_engine import est_tokens, CHARS_PER_TOKEN

# 默认配比 (占 context_budget 的比例). 合计 <= 1.0, 余量留给提示词本身
DEFAULT_LAYERS: Dict[str, Dict[str, Any]] = {
    "L0_outline":   {"label": "本章细纲", "ratio": 0.10, "hard": True},
    "L1_resident":  {"label": "世界观常驻", "ratio": 0.22, "hard": False},
    "L2_recent":    {"label": "最近章节", "ratio": 0.20, "hard": False},
    "L3_mid":       {"label": "段落摘要", "ratio": 0.16, "hard": False},
    "L4_recall":    {"label": "检索召回", "ratio": 0.20, "hard": False},
    "L5_constraint": {"label": "红线约束", "ratio": 0.08, "hard": True},
}


@dataclass
class LayerResult:
    key: str
    label: str
    text: str
    tokens: int
    cap: int
    truncated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "tokens": self.tokens,
                "cap": self.cap, "truncated": self.truncated,
                "chars": len(self.text), "preview": self.text[:160]}


class MemoryController:
    """把五层素材按预算装配成一份上下文, 并给出可视化的占用报告。"""

    def __init__(self, total_budget: int, layers: Optional[Dict[str, Any]] = None):
        self.total = total_budget
        self.layers = {**DEFAULT_LAYERS}
        for k, v in (layers or {}).items():           # 用户配比覆盖
            if k in self.layers and isinstance(v, (int, float)):
                self.layers[k]["ratio"] = float(v)
        self.results: List[LayerResult] = []

    def _cap(self, key: str) -> int:
        return int(self.total * self.layers[key]["ratio"])

    def _fit(self, key: str, text: str) -> LayerResult:
        cap = self._cap(key)
        lab = self.layers[key]["label"]
        text = text or ""
        tk = est_tokens(text)
        if tk <= cap or self.layers[key]["hard"]:
            # hard 层不裁剪, 但要记录是否已超额 (超了说明预算配比该调)
            return LayerResult(key, lab, text, tk, cap, truncated=tk > cap)
        keep = int(cap * CHARS_PER_TOKEN)
        cut = text[:keep].rstrip() + f"\n…（{lab}超预算，已截断）"
        return LayerResult(key, lab, cut, est_tokens(cut), cap, True)

    # ---------------- 装配 ----------------
    def assemble(self, *, outline: str, resident: str, recent: List[str],
                 mid: List[str], recall: List[Dict[str, Any]],
                 constraints: str) -> Dict[str, Any]:
        # 入参防呆 —— 变量遮蔽把 dict 列表传进 recent/mid 已经踩过两次,
        # 报在 "\n".join 深处很难定位, 这里直接报清楚。
        for nm, v in (("recent", recent), ("mid", mid)):
            bad = [type(x).__name__ for x in (v or []) if not isinstance(x, str)]
            if bad:
                raise TypeError(f"assemble({nm}=...) 需要 List[str]，收到 {bad[:3]}"
                                f"（多半是上游变量名被覆盖了）")
        self.results = []

        self.results.append(self._fit("L0_outline", outline))

        self.results.append(self._fit("L1_resident", resident))

        # L2: 最近章节 —— 超预算时丢最旧的, 而不是把每条都截断
        cap = self._cap("L2_recent")
        kept: List[str] = []
        used = 0
        for line in reversed(recent):               # 从最新往回收
            t = est_tokens(line)
            if used + t > cap:
                break
            kept.insert(0, line); used += t
        if not kept and recent:      # 一条都装不下时, 至少保留最新一条的截断版
            kept = [recent[-1][:int(cap * CHARS_PER_TOKEN)] + "…"]
            used = est_tokens(kept[0])
        self.results.append(LayerResult("L2_recent", self.layers["L2_recent"]["label"],
                                        "\n".join(kept), used, cap,
                                        truncated=len(kept) < len(recent)))

        # L3: 段摘要 —— 越远的越先丢
        cap = self._cap("L3_mid")
        kept, used = [], 0
        for blk in reversed(mid):
            t = est_tokens(blk)
            if used + t > cap:
                break
            kept.insert(0, blk); used += t
        if not kept and mid:
            kept = [mid[-1][:int(cap * CHARS_PER_TOKEN)] + "…"]
            used = est_tokens(kept[0])
        self.results.append(LayerResult("L3_mid", self.layers["L3_mid"]["label"],
                                        "\n\n".join(kept), used, cap,
                                        truncated=len(kept) < len(mid)))

        # L4: 检索召回 —— 超预算时少召回几条, 不截断单条 (截断会丢关键信息)
        cap = self._cap("L4_recall")
        lines, used = [], 0
        label = {"world": "世界观", "role": "角色", "plot": "往期剧情", "fore": "伏笔"}
        for h in recall:
            line = f"[{label.get(h['kind'], h['kind'])}] {h['title']}：{h['text'][:900]}"
            t = est_tokens(line)
            if used + t > cap:
                break
            lines.append(line); used += t
        self.results.append(LayerResult("L4_recall", self.layers["L4_recall"]["label"],
                                        "\n".join(lines), used, cap,
                                        truncated=len(lines) < len(recall)))

        self.results.append(self._fit("L5_constraint", constraints))

        # 余量回收: 各层配额按比例切死, 用不完也不外借 —— 实测 100k 预算只用到
        # 21.8%（L1 用 6.4k/上限 20k, L2 用 11.5k/上限 38k），而 L2「最近章节原文」
        # 恰恰是越多越好的那一层。把没人用的额度让给它, 把窗口吃满。
        spare = self.total - sum(r.tokens for r in self.results)
        if spare > 2000:
            for key, pool, joiner in (("L2_recent", recent, "\n"),
                                      ("L3_mid", mid, "\n\n")):
                i = next((j for j, r in enumerate(self.results) if r.key == key), None)
                if i is None or not self.results[i].truncated:
                    continue
                cur = self.results[i]
                cap2 = cur.cap + spare
                kept, used = [], 0
                for blk in reversed(pool):
                    t = est_tokens(blk)
                    if used + t > cap2:
                        break
                    kept.insert(0, blk)
                    used += t
                if used > cur.tokens:
                    self.results[i] = LayerResult(
                        key, self.layers[key]["label"], joiner.join(kept),
                        used, cap2, truncated=len(kept) < len(pool))
                    spare = self.total - sum(r.tokens for r in self.results)
                    if spare <= 2000:
                        break

        by = {r.key: r.text for r in self.results}
        return {
            "layers": by,
            "report": self.report(),
            "text": self.compose(by),
        }

    @staticmethod
    def compose(by: Dict[str, str]) -> str:
        blocks = [
            ("【世界观】", by.get("L1_resident")),
            ("【前情·段落摘要】", by.get("L3_mid")),
            ("【前情·最近章节】", by.get("L2_recent")),
            ("【相关记忆召回】", by.get("L4_recall")),
            ("【必守约束】", by.get("L5_constraint")),
        ]
        return "\n\n".join(f"{h}\n{b}" for h, b in blocks if b and b.strip())

    def report(self) -> Dict[str, Any]:
        used = sum(r.tokens for r in self.results)
        return {
            "total_budget": self.total,
            "used": used,
            "usage_pct": round(used / self.total * 100, 1) if self.total else 0,
            "layers": [r.to_dict() for r in self.results],
            "overflow": [r.label for r in self.results if r.truncated],
        }
