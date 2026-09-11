"""自动写作编排层 —— "真正能自动写小说" 在这里.

设计要点 (借鉴 x10086 skills/long-novel 的工程架构):
  * 外部档案替代 LLM 记忆: world_bible + characters + 滚动摘要, 不依赖长 context
  * 逐章 checkpoint: 任何时候中断都能续写
  * 写完即自审: 跑 evaluator, 不合格自动重写一次
  * 上下文预算: 每次组装按优先级裁剪, 绝不撑爆 window
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Iterator, Callable

from .registry import registry, ROOT
from .prompt_engine import render, budget, est_tokens
from .evaluator import audit, book_audit, window_audit
from . import dials as dl
from . import stagecraft as sc
from .retrieval import Retriever
from .prompt_compiler import (outline_required, outline_format_block, window_drift,
                             render_item,
                             compile_chapter_prompt, compile_outline_prompt,
                               to_plot_list)
from . import critic as critic_mod
from .settings import load as load_settings
from .memory import Memory
from .memory_ctl import MemoryController

PROJECTS = ROOT / "projects"
PROJECTS.mkdir(exist_ok=True)

# 架空题材的穿帮词: 实测生成 30 章后"宋律"出现 28 次、"大宋"20 次,
# 而自定的架空朝代"景朝"只有 2 次 —— 模型会稳定回落到训练数据里的真朝代。
MODE_RULES = {
    "alt": ("【架空硬约束，违反即作废】\n"
            "开头两行必须严格照抄下面的格式，把 X 换成你定的名字，"
            "括号里的说明文字不要抄进去：\n"
            "```\n国号：燕朝\n参考朝代：北宋\n```\n"
            "（国号是两字虚构名，不得以真朝代单字开头，不许写成「大宋景朝」这种真假混写；"
            "参考朝代仅内部参考，正文永不出现）\n"
            "其余要求：\n"
            "- 律法称「X律」、史书称「X史」，不得出现大宋、宋律、北宋等真实朝代词\n"
            "- 主场地点只指定一处，全书主线不换主场\n"
            "- 最后列「禁用词表」\n\n"
            "直接输出，无前言，不要复述本条要求。"),
    "real": ("【正统历史硬约束】\n"
             "1. 写的就是真实朝代，朝代名/官职/律法/纪年一律用真实名称，不要自造国号\n"
             "2. 开头两行照格式写（说明文字不要抄）：\n```\n朝代：北宋 仁宗 庆历年间\n"
             "起始年份：1043\n```\n"
             "3. 官职品级、俸禄、物价、度量衡必须真实；不确定的写模糊，不许编数字\n"
             "4. 主场地点只指定一处\n5. 最后列「易错点」\n\n直接输出，无前言。"),
    "modern": ("【当代现实约束】\n"
               "1. 写的是真实世界的具体年代，年份必须钉死，开头两行照格式写"
               "（说明文字不要抄）：\n```\n年代：2005 年 中国 东南沿海省会\n"
               "起始时间：2005 年 3 月\n```\n"
               "2. **晚于这个年份才出现的东西一律不得作为日常存在**"
               "（技术、产品、平台、政策、流行语都算）。主角若是重生者，"
               "可以在心里盘算或对话中预言它们，但周围的世界不能已经有\n"
               "3. 真实公司、平台、在世名人一律化名或模糊处理，不影射\n"
               "4. 物价、工资、房价、学制、通讯方式必须符合该年份\n"
               "5. 主场地点只指定一处\n6. 最后列「那年还没有」\n\n直接输出，无前言。"),
    "fanfic": ("【同人硬约束 —— 原作设定是最高法】\n"
               "1. 开头两行照格式写（说明文字不要抄）：\n```\n原作：龙珠\n"
               "切入点：魔人布欧篇 · 布欧被唤醒之时\n```\n"
               "2. **原作已确立的设定不得推翻**：人物姓名、能力体系与上限、"
               "势力关系、已发生事件的因果。要改的必须是**主角介入之后**的走向，"
               "不能改介入之前的既成事实\n"
               "3. 原作角色的性格与说话方式必须认得出来；崩人设是同人最大的雷\n"
               "4. 力量层级沿用原作标尺（原作里打不过的，不能因为主角来了就突然打得过，"
               "要给出可信的变强路径）\n"
               "5. 原作的专有名词用原作叫法，不要自己另起译名\n"
               "6. 最后列「原作红线」：本书绝不改动的原作设定清单\n\n直接输出，无前言。"),
    "mythos": ("【神话体系硬约束】\n"
               "1. 开头两行照格式写（说明文字不要抄）：\n```\n体系：洪荒（封神+山海经）\n"
               "起始节点：巫妖大战之前\n```\n"
               "2. 神话人物的**辈分、师承、法宝归属、既定结局**按公认设定来"
               "（如鸿钧道祖之下三清、女娲造人、十二祖巫、封神榜的归属），"
               "不得随意错配\n"
               "3. 各家演绎有差异时，**选定一版并全书统一**，在世界观里写明采用哪一版\n"
               "4. 境界层级要给出明确阶梯并全书一致（如凡人→真仙→金仙→太乙→大罗→准圣→圣人）\n"
               "5. 神话专名用通行写法，不要自造异体字\n"
               "6. 最后列「本书采用的体系版本与既定节点」\n\n直接输出，无前言。"),
    "multiworld": ("【多世界约束 —— 无限流/副本流】\n"
                   "1. 分清**主世界**与**副本世界**，开头两行照格式写"
                   "（说明文字不要抄）：\n```\n主世界：当代现实 · 轮回空间\n"
                   "副本规则：每个副本自成一套设定，进入即适用\n```\n"
                   "2. 主世界的常识**不适用于副本内**，反之亦然；"
                   "跨副本能带走的只有明确规定可带的（积分、道具、能力）\n"
                   "3. 每个副本必须在本卷设定里写清：世界基底（历史/现代/架空/虚构/同人）、"
                   "时代或世界锚点、通关条件、死亡规则\n"
                   "4. 轮回空间/系统的规则一旦定下不得中途改口，"
                   "兑换价目与积分结算要前后一致\n"
                   "5. 最后列「跨副本恒定规则」\n\n直接输出，无前言。"),
    "invented": ("【设定约束】\n1. 力量/规则体系必须自洽且可执行\n"
             "2. 主场地点只指定一处\n3. 最后列「禁忌」：本书绝不出现的东西\n\n"
             "直接输出，无前言。"),
}

REAL_DYNASTIES = ["大宋", "宋律", "宋朝", "北宋", "南宋", "大唐", "唐朝", "大明", "明朝",
                  "大清", "清朝", "大汉", "汉朝", "秦朝", "元朝", "民国", "大元",
                  "宋史", "唐律", "明律", "大周"]
FAKE_DYN_RE = re.compile(r"(?:虚构|架空)?(?:朝代|王朝|国号)[^\n。]{0,8}?[「\"'']?([一-鿿]{1,2}朝)")


def slugify(t: str) -> str:
    return re.sub(r"[^\w一-鿿-]+", "_", t).strip("_")[:60]


class Project:
    def __init__(self, slug: str):
        self.slug = slug
        self.dir = PROJECTS / slug
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "chapters").mkdir(exist_ok=True)
        (self.dir / "audit").mkdir(exist_ok=True)
        (self.dir / "l2_summary").mkdir(exist_ok=True)
        self.meta: Dict[str, Any] = self._load("project.json", {})
        self.state: Dict[str, Any] = self._load("state.json",
                                                {"current": 0, "done": [], "log": []})
        # 全局配置 + 本项目覆盖
        self.cfg: Dict[str, Any] = load_settings(self.meta.get("overrides"))
        self.mem = Memory(self.dir / "memory.db")

    # ---------- io ----------
    def _load(self, name: str, default):
        p = self.dir / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return default
        return default

    # 长跑进程会持有 meta 的内存副本几小时。期间用户在界面上改书名、改设定、
    # 改提示词覆盖 —— 下一次 save() 就把这些改动整份冲掉, 而且悄无声息。
    # 实测: 改完书名 30 秒后又变回旧名, tagline 直接消失。
    # 所以 meta 落盘前先合并磁盘上的最新版本, 只用内存值覆盖本进程真正改过的键。
    _META_OWNED = {"anchor", "prompt_overrides", "era", "history_mode"}

    def save(self):
        f = self.dir / "project.json"
        merged = dict(self.meta)
        try:
            disk = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            disk = {}
        if disk:
            for k, v in disk.items():
                # 磁盘上有、而本进程没主动改过的键, 以磁盘为准
                if k not in self._META_OWNED and self.meta.get(k) != v:
                    if k not in self._meta_touched:
                        merged[k] = v
            for k in disk:
                if k not in merged and k not in self._meta_touched:
                    merged[k] = disk[k]
        self.meta = merged
        f.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.dir / "state.json").write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def _meta_touched(self) -> set:
        """本进程主动改过的 meta 键 —— 只有这些才该覆盖磁盘。"""
        return getattr(self, "_touched", set()) | self._META_OWNED

    def touch_meta(self, *keys: str) -> None:
        """声明本进程要改这些 meta 键（保存时以内存值为准）。"""
        self._touched = getattr(self, "_touched", set()) | set(keys)

    def read(self, name: str) -> str:
        p = self.dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def write(self, name: str, text: str):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def chapter_path(self, n: int) -> str:
        return f"chapters/{n:03d}.md"

    def chapter(self, n: int) -> str:
        return self.read(self.chapter_path(n))

    # ---------- 派生 ----------
    @property
    def total_words(self) -> int:
        """全书字数。按「章数 + 目录 mtime」缓存 —— 工作台列表每次都要它，
        而它原本会把七本书上千个章节文件全读一遍（实测 /api/projects 耗时
        683ms），首屏因此长时间停在「加载中…」，用户看到的就是一片空白。"""
        done = self.state.get("done", [])
        try:
            stamp = f"{len(done)}:{int((self.dir / 'chapters').stat().st_mtime)}"
        except OSError:
            stamp = str(len(done))
        cache = self.state.get("_words_cache") or {}
        if cache.get("stamp") == stamp:
            return int(cache.get("value", 0))
        val = sum(len(re.findall(r"[一-鿿]", self.chapter(n))) for n in done)
        self.state["_words_cache"] = {"stamp": stamp, "value": val}
        try:
            self.save()
        except Exception:
            pass
        return val

    def board(self) -> str:
        done = self.state.get("done", [])
        tw = self.total_words
        tgt = self.meta.get("target_words", 0)
        lines = [
            f"# 《{self.meta.get('title','')}》项目看板", "",
            f"- **类型**: {self.meta.get('type_id')} / **题材**: {self.meta.get('genre_id')} / **文风**: {self.meta.get('style_id')}",
            f"- **进度**: {len(done)} / {self.meta.get('target_chapters', 0)} 章",
            f"- **字数**: {tw:,} / {tgt:,} 字 ({tw/tgt*100 if tgt else 0:.1f}%)",
            f"- **模型**: {self.meta.get('model')}",
            "", "## 质量", ]
        scores = []
        for n in done:
            a = self._load(f"audit/{n:03d}.json", None)
            if a:
                scores.append(a["score"])
        if scores:
            lines += [f"- 平均 AI 味得分: **{sum(scores)/len(scores):.1f}** / 100",
                      f"- 最低分章节: 第 {done[scores.index(min(scores))]} 章 ({min(scores)} 分)"]
        u = self.state.get("usage") or {}
        if u:
            lines += ["", "## 用量",
                      f"- 调用 {u.get('calls',0)} 次 / 累计 {u.get('total',0):,} token"
                      f"（入 {u.get('prompt',0):,} 出 {u.get('completion',0):,}）",
                      f"- 模型耗时 {u.get('elapsed_s',0)} 秒"]
            for k, v in (u.get("by_profile") or {}).items():
                lines.append(f"  - {k}: {v['calls']} 次 / "
                             f"{v['prompt']+v['completion']:,} token")
        lines += ["", "## 最近日志"] + [f"- {x}" for x in self.state.get("log", [])[-12:]]
        return "\n".join(lines)


# ---------------------------------------------------------------- 引擎
@dataclass
class GenResult:
    text: str
    reasoning: str = ""
    elapsed: float = 0.0
    chars: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)


# 全进程用量累计. 网关不返回 usage 时按中文 1 字≈1.4token 估算, 标记 estimated。
USAGE: Dict[str, Any] = {"calls": 0, "prompt": 0, "completion": 0,
                         "elapsed": 0.0, "by_profile": {}}


def _record(profile: str, prompt_chars: int, res: "GenResult") -> None:
    u = res.usage or {}
    pt = u.get("prompt") or int(prompt_chars / 0.7)
    ct = u.get("completion") or int((len(res.text) + len(res.reasoning)) / 0.7)
    USAGE["calls"] += 1
    USAGE["prompt"] += pt
    USAGE["completion"] += ct
    USAGE["elapsed"] += res.elapsed
    b = USAGE["by_profile"].setdefault(profile, {"calls": 0, "prompt": 0,
                                                 "completion": 0, "elapsed": 0.0})
    b["calls"] += 1; b["prompt"] += pt; b["completion"] += ct; b["elapsed"] += res.elapsed
    res.usage = {"prompt": pt, "completion": ct,
                 "estimated": not (u.get("prompt") or u.get("completion"))}


# ---------------------------------------------------------------- 调用追踪

#: 当前项目的追踪目录。Novelist 初始化时绑定 —— call() 是模块级函数，
#: 没有项目上下文，而追踪必须落到具体的书下面。
_TRACE_DIR: Optional[Path] = None
#: 追踪保留多少条。一本书跑完几千次调用，全留会撑爆磁盘（单条可达 50KB）。
TRACE_KEEP = 400
_trace_seq = [0]


#: 本书选定的模型。project.json 里一直有 model 字段, 前端也让人选,
#: 但 call() 走 registry.resolve(profile) 只认 providers.yaml 的档位和网关
#: default_model —— 书级 model 从来没生效过, 换模型只能去改全局 .env,
#: 而且改了会波及所有书。实测第 1-35 章 meta 写着 flash, 实际全是 max 写的。
_BOOK_MODEL: Optional[str] = None


def bind_model(m: Optional[str]) -> None:
    global _BOOK_MODEL
    _BOOK_MODEL = (m or "").strip() or None


def bind_trace(d: Optional[Path]) -> None:
    global _TRACE_DIR
    _TRACE_DIR = d
    if not d:
        return
    d.mkdir(parents=True, exist_ok=True)
    # 序号要接着已有的往下走。进程重启后从 1 重来的话，新记录会**覆盖**
    # 同号的旧记录 —— 追踪列表里既看不到新的（被排在中间），
    # 也丢了旧的，正好在最需要回溯的时候（崩溃重启后）失效。
    try:
        got = [int(f.name.split("_")[0]) for f in d.glob("*.json")
               if f.name.split("_")[0].isdigit()]
        _trace_seq[0] = max(got) if got else 0
    except Exception:
        _trace_seq[0] = 0


def _trace(profile: str, prompt: str, kw: Dict[str, Any], out: str,
           rsn: str, elapsed: float, usage: Dict[str, Any]) -> None:
    """把**实际发出去的**提示词与回复原样落盘。

    原来只有写正文那一步存 audit/NNN.prompt.txt，排纲、巡检、审阅、评审、
    英文修复……全都不留痕。出了问题只能靠猜「当时到底发了什么过去」——
    这一轮排查「巡检全零」「提示词自相矛盾」「替换静默失败」，
    每一次都得临时插桩重跑一遍才看得到。
    """
    if not _TRACE_DIR:
        return
    try:
        _trace_seq[0] += 1
        i = _trace_seq[0]
        rec = {
            "seq": i, "at": time.strftime("%F %T"), "profile": profile,
            "model": kw.get("model"), "thinking": kw.get("thinking"),
            "max_tokens": kw.get("max_tokens"), "elapsed": round(elapsed, 1),
            "prompt_chars": len(prompt), "out_chars": len(out),
            "reasoning_chars": len(rsn), "usage": usage,
            "prompt": prompt, "output": out,
        }
        f = _TRACE_DIR / f"{i:05d}_{profile}.json"
        f.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        # 滚动清理：只留最近 TRACE_KEEP 条
        old = sorted(_TRACE_DIR.glob("*.json"))
        for x in old[:-TRACE_KEEP]:
            x.unlink(missing_ok=True)
    except Exception:
        pass                                    # 追踪绝不能影响正常生成


#: 开着思考时输出上限放大的倍数。推理与答案共用同一个 max_tokens，
#: 不放大的话小预算的判定任务永远只吐出思考、答案被截在门外。
THINK_BUDGET_X = 3


#: 撞上输出上限时自动续写的轮数。**不准截断** —— 截断的产物比缺失更糟：
#: 它看起来是完整的，后面的环节会把半句话当成写好的内容接着用
#: （实测细纲里留下「第三天乖乖回来」「应二」，角色档案停在「**原声」）。
CONTINUE_ROUNDS = 3


def call(profile: str, prompt: str, on_delta: Optional[Callable[[str], None]] = None,
         system: str = "", max_tokens: Optional[int] = None,
         _retry: bool = True, _cont: int = 0,
         no_continue: bool = False) -> GenResult:
    """一次生成. 自动处理 reasoning/content 三种字段 + 空 content 兜底.

    实测坑: 开思考时模型可能把全部内容留在 reasoning 里 content 为空, 或思考
    吃光 max_tokens 导致正文被截断. 空输出会自动关思考重试一次.
    """
    provider, kw = registry.resolve(profile)
    if _BOOK_MODEL:
        kw["model"] = _BOOK_MODEL
    if not _retry:
        kw["thinking"] = False
    if max_tokens:
        kw["max_tokens"] = max_tokens
    # 思考 token 也算在 max_tokens 里 —— 关不掉思考的网关（GLM 系只能调档，
    # thinking_style: effort）上，一个 2500 预算的判定任务会被推理吃光，
    # 可见输出 0 字符，然后走「关思考重试」，等于这次判定白做一遍。
    # 实测：16384 的预算全进思考，content 为空。
    # 开着思考时把输出上限放大，给推理留出自己的地方，别挤掉答案。
    if kw.get("thinking"):
        gw = (registry.gateways.get(
            (registry.profiles.get(profile) or {}).get("gateway", "")) or {})
        # 网关级 thinking_style 之外，还要看按模型族的覆盖 —— 否则
        # 「网关是 toggle、但这个模型被覆盖成 effort」时预算不会放大。
        _st = gw.get("thinking_style")
        _m = str(kw.get("model") or gw.get("default_model") or "").lower()
        for _p, _v in (gw.get("thinking_style_by_model") or {}).items():
            if _m.startswith(str(_p).lower()):
                _st = _v; break
        if _st == "effort":
            ceiling = int(gw.get("max_tokens") or 8192)
            kw["max_tokens"] = min(ceiling, max(int(kw.get("max_tokens") or 2000)
                                                * THINK_BUDGET_X, 6000))
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    t0 = time.time()
    text, think = [], []
    for d in provider.stream(msgs, **kw):
        if d.text:
            text.append(d.text)
            if on_delta:
                on_delta(d.text)
        if d.reasoning:
            think.append(d.reasoning)
    out = "".join(text).strip()
    rsn = "".join(think)
    raw_usage = getattr(provider, "last_usage", {}) or {}
    if not out and rsn:          # ★ 兜底 1: 答案被 <answer> 包在 reasoning 里
        out = provider.salvage(rsn)
        if on_delta and out:
            on_delta(out)
    if not out and _retry:       # ★ 兜底 2: 关思考重试, 把 token 全给正文
        # 这条兜底救得了单次调用，却会**把双倍开销藏起来**：实测细纲审阅每一段
        # 都先烧光预算只产出思考、再靠这次重试拿结果，日志上只有一行「输出为空」，
        # 看不出这个档位在该网关上根本不该开思考。所以把代价打在明处。
        print(f"  [call] {profile} 输出为空（思考吃光了 {kw.get('max_tokens')} "
              f"预算、耗时 {time.time() - t0:.0f}s），关思考重试 —— "
              f"这一次等于白跑，考虑把该档位的 thinking 关掉", flush=True)
        return call(profile, prompt, on_delta, system, max_tokens, _retry=False)
    # ★ 兜底 3: 撞上输出上限就**接着写**，不接受半截产物。
    # 逐个调用去猜 max_tokens 是猜不完的（加一栏字段就得改一处预算，
    # 这个坑已经踩了三次：细纲批量、细纲审阅、角色档案）。
    # API 本来就给了 finish_reason=length 这个信号，接住它，从根上不截断。
    # drafting 例外: 正文的 max_tokens 是**按目标字数算出来的意图**
    # (cap = 目标字 × 0.75 × 1.25), 撞上它意味着「写够了, 停」, 不是被切断。
    # 实测第6章: 撞上 2625 tok 上限后续写, 最终 5655 字 —— 目标 2400-3200,
    # 超 77%。续写机制把字数上限整个废掉了。
    # 结构化产物(细纲/角色档案/评审 JSON)才需要续写, 那里截断是真的坏。
    if (out and _cont < CONTINUE_ROUNDS and profile != "drafting" and not no_continue
            and getattr(provider, "last_finish", "") == "length"):
        print(f"  [call] {profile} 撞上输出上限（{kw.get('max_tokens')} tok，"
              f"已出 {len(out)} 字），第 {_cont + 1} 次续写", flush=True)
        tail = out[-1200:]
        more = call(profile,
                    prompt + "\n\n【上次输出被上限切断了，下面是已经写出来的结尾】\n"
                    + tail + "\n\n请**从断口处接着写**：不要重复上面已有的内容，"
                    "不要重新开头，不要加任何说明，直接续写剩下的部分。",
                    on_delta, system, max_tokens, _retry=False, _cont=_cont + 1)
        if more.text:
            out = out + more.text
    _trace(profile, prompt, kw, out, rsn, time.time() - t0, dict(raw_usage))
    res = GenResult(text=out, reasoning=rsn, elapsed=time.time() - t0,
                    chars=len(out), usage=dict(raw_usage))
    _record(profile, sum(len(m["content"]) for m in msgs), res)
    return res


# 模型经常把提示词里的括号说明当模板抄进正文, 例如
#   国号：燕朝（一个两字虚构国号，不得以真朝代单字开头…）
_ECHO = re.compile(
    r"（[^（）]{0,60}(?:不得|不许|禁止|仅内部参考|说明文字|不要抄|违反即作废)[^（）]{0,60}）")


def _first_line(card: str) -> str:
    for ln in (card or "").splitlines():
        ln = ln.strip().lstrip("#").strip()
        if len(ln) > 4 and "：" in ln:
            return ln[:60]
    return (card or "")[:60]


def clean(text: str) -> str:
    """去掉模型爱加的前言/标题/代码围栏, 以及被复述的约束文字."""
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text.strip())
    text = re.sub(r"^(?:好的|以下是|下面是)[^\n]{0,40}[:：]\s*\n+", "", text)
    text = _ECHO.sub("", text)
    # 模型爱在正文末尾附一段自己的工作笔记（【本章末状态字段更新】
    # 【伏笔登记】【字数统计】之类）。那是给流水线看的, 不是给读者看的,
    # 混进成稿会直接印到书里。从这类标题起整段截掉。
    text = re.sub(
        r"\n+(?:---+\s*\n+)?[【\[]?\s*(?:本章末?)?(?:状态(?:字段)?更新|状态更新"
        r"|状态字段|伏笔登记|字数统计|本章统计|写作说明|作者note|备注说明)"
        r"[】\]]?[\s\S]*$", "", text)
    return text.strip()



# 内置提示词模板。抽成常量而不是埋在函数里, 前端「本书提示词」页才拿得到真身 ——
# 原来接口返回的是「内置：世界观圣经模板（含架空/正史硬约束）」这样一句话说明,
# 用户对着空白输入框根本不知道内置模板长什么样, 也就无从改起。
BUILTIN_PROMPTS = {
    "world_bible":
        "你是网文世界观设计师。为《${title}》写世界观圣经，2000 字以内，压缩、密集、可执行。\n\n"
        "【一句话故事】${premise}\n【背景】${background}\n\n【题材规范】\n${genre_rules}\n\n"
        # 铁律定的是主角的位置与金手指的用法, 世界观里的「主角身份与金手指」
        # 那一节必须照着它写。原来这一步看不见铁律, 于是写出「他的阶层天花板
        # 是商字的结构性屈辱」这种与「他是本地地头蛇」正相反的定位。
        "【本书铁律 —— 世界观必须与之一致，冲突时以铁律为准】\n${hard_rules}\n\n"
        "必须输出以下字段（用小标题分节）：\n"
        "朝代设定 / 地理与势力 / 政治军事经济 / 社会风貌与阶层 / 主角身份与金手指 / "
        "核心矛盾 / 力量或规则体系 / 关键道具与线索 / 禁用词表\n\n",
    # 铁律必须进人物卡。**踩过**: 铁律第一条写着「他是本地地头蛇, 不许把他
    # 写成缩着脖子过日子的人, 不许躲不许赔罪」, 可这一步的提示词里根本没有
    # 铁律 —— 生成出来的主角卡是「身份：生药铺老板 / 性格：隐忍 / 动机：
    # 从待宰肥羊进化为操盘手」, 三处全和铁律对着干。人物卡定了往后三百章的
    # 人设, 这一步走歪, 后面每一章都跟着歪。
    "characters": "基于世界观，为《${title}》设计角色档案。\n\n【世界观】\n${world_bible}\n\n"
        "【题材规范】\n${genre_rules}\n\n"
        "【本书铁律 —— 人物设定必须与之一致，冲突时以铁律为准】\n${hard_rules}\n\n"
        "输出 10-14 个角色，格式严格如下（每人一节，标题行必须是「### N. 姓名：某某」）：\n"
        "### 1. 姓名：某某\n"
        "**身份**：…\n**年龄**：…\n**外貌一句话**：…\n**性格三词**：…\n"
        "**核心动机**：…\n**与主角关系**：…\n"
        # 声音卡：原来只有一行「专属口头禅」，人物只有动作没有语感 ——
        # 分章独立生成时说话必然趋同，谁都在说同一种腔调的金句。
        "**自称**：（他管自己叫什么：武二／老夫／老娘／小的／洒家）\n"
        "**口头禅**：（反复说的一两句话或口癖，不超过 8 字）\n"
        "**语感**：（一句话说清他说话的样子：长短句、粗细、绕不绕弯、爱不爱反问）\n"
        "**原声样本**：（三句他会说的话，用 ｜ 分隔。要能一眼认出是他，不是别人）\n"
        "**禁用词**：（他绝不会用的词，用、分隔）\n"
        "**结局走向**：…\n\n"
        "【硬性约束】\n"
        "1. 必须有 4 位以上**戏份仅次于主角的核心配角**，各自有独立目标与故事线，"
        "不是主角的应声筒\n"
        "2. 至少 2 位女性角色有独立故事线，不围绕男主转\n"
        "3. 反派要有自洽逻辑，不能纯坏\n"
        "4. 若借用了知名作品的人物，其身份职业必须与原作一致，"
        "不得随意安排不符身份的官职\n",
}

# 角色档案的历史约束必须分模式。原来把「禁用真实历史人物」「不许出现大明大宋」
# 写死在通用模板里 —— 可 real 模式写的就是真实朝代，朱元璋、蓝玉、洪武年间
# 都是必需的，这两条在那种书里完全说反了。世界观那一步早就按 MODE_RULES 分了，
# 角色这一步漏了。
CHAR_MODE_RULES = {
    "alt": ("5. 口头禅与说话习惯里绝对不许出现真实朝代名（大宋/宋律/大唐/大明…），"
            "要用世界观里的虚构国号\n"
            "6. 【架空世界禁用真实历史人物】不得出现真实存在过的帝王将相文人"
            "（如赵构、蔡京、岳飞、诸葛亮、魏征…）。需要类似角色时另起名字，"
            "可保留原型气质但姓名必须虚构\n"),
    "real": ("5. 【正史模式】本书写的就是真实朝代：真实历史人物用真名、真身份，"
             "不要改姓改名或另起炉灶；虚构角色与真人同处一个官制体系\n"
             "6. 真人的性格与结局可以按剧情需要改写（这本来就是历史架空），但"
             "**出场时的身份必须与史实一致** —— 不得让某人担任他那一年根本不可能"
             "担任的职位，也不得让已死或未生的人出场\n"
             "7. 称谓、避讳、礼制按本朝规矩；其他朝代特有的官职、称呼、器物不得混入\n"),
    "modern": ("5. 当代现实：真实存在的公司、品牌、在世名人一律化名或模糊处理，"
               "避免直接影射\n"
               "6. 角色的年龄、学历、职业阶段必须与故事所处年份对得上\n"),
    "fanfic": ("5. 同人：**原作已有的角色一律沿用原名、原身份、原性格与原能力**，"
               "不得改姓改名或换设定；原作没有的角色可自创，但要和原作世界观相容\n"
               "6. 原作角色的强弱关系按原作标尺，不能为了捧主角而降智降战力\n"),
    "mythos": ("5. 神话体系：神话人物的辈分、师承、法宝归属按公认设定，"
               "不得随意错配（如把某法宝安在别人身上）\n"
               "6. 自创角色要说清在既有体系里的位置（哪一脉、什么境界、与谁相识）\n"),
    "multiworld": ("5. 分清常驻班底与副本角色：常驻班底跨副本延续，"
                   "档案要记清能力与积分；副本角色只在本副本内有效\n"
                   "6. 副本内若借用既有作品的角色，沿用原作设定，不得崩人设\n"),
    "invented": ("5. 虚构世界：不必挂靠真实历史，但力量体系、组织架构、地理格局"
                 "要自洽且前后一致\n"),
}

class Novelist:
    """把 ContentType + GenrePack + StylePack 组装成可执行流水线."""

    def __init__(self, project: Project):
        self.p = project
        m = project.meta
        self.type = registry.types[m["type_id"]]
        # 题材包/文风包是**默认值**，不是铁板。本书可以覆盖任意字段 ——
        # 实测同人包写着「打赢原作人物就是崩人设」「靠武力赢原作强者是第一大雷」，
        # 这对绝大多数同人是对的，可这一本要的恰恰是「打服武松」。
        # 没有覆盖口子的话，只能改包（伤别的书）或跟包对着写（模型两头听、写歪）。
        bind_trace(project.dir / "trace")
        bind_model(m.get("model"))
        self.genre = self._with_overrides(
            registry.genres.get(m.get("genre_id")) or {}, "genre")
        self.style = self._with_overrides(
            registry.styles.get(m.get("style_id")) or {}, "style")
        self.common = registry.common
        self.cfg = project.cfg
        self.g = self.cfg["generation"]
        self.q = self.cfg["quality"]
        self.mcfg = self.cfg["memory"]
        self._retriever: Optional[Retriever] = None
        self.g["context_budget"] = self._resolve_budget()

    def _resolve_budget(self) -> int:
        """context_budget=auto 时按当前网关窗口推导, 不再拍一个保守小数字.

        可用提示词预算 = 模型窗口 - 输出预留 - 安全余量
        Qwen3.8-Flash-Next 110k 窗口 -> 约 96k 可用.
        """
        lo = int(self.g.get("min_context_budget", 32000))
        hi = int(self.g.get("max_context_budget", 100000))
        v = self.g.get("context_budget")
        if isinstance(v, int) and v > 0:
            return max(lo, min(hi, v))

        gw = registry.gateways.get(registry.profiles["drafting"]["gateway"], {})
        win = int(gw.get("context_window") or 0)
        usable = win - int(self.g.get("output_reserve", 8192)) \
                     - int(self.g.get("safety_margin", 4000))
        if usable < lo:
            # 窗口撑不起下限: 仍按下限走, 并明确告警 —— 低于 32k 长篇一致性必崩
            print(f"[budget] 警告: 网关窗口 {win} 不足以支撑 {lo} 记忆体下限, "
                  f"请换用 ≥{lo + 12000} 窗口的模型")
            return lo
        return max(lo, min(hi, usable))

    # ---------- 规则文本 ----------
    @staticmethod
    def _items(v, cap: int = 6) -> list:
        """包字段既可能写成列表也可能写成一整句, 统一成列表再拼。

        直接 join 一个字符串会**逐字拆开**: 「熟悉感 × 意外感」变成
        「熟；悉；感； ；×」, 提示词里的题材规范整段成了乱码, 模型收到的是噪声。
        实测同人二创包的 corePleasure/cast 都是字符串, 已经这么喂了很久。
        字符串本身就是一条, 不切分 —— 按标点乱切会把一句话腰斩得更难读。
        """
        if not v:
            return []
        if isinstance(v, str):
            return [v.strip()]
        if isinstance(v, dict):
            v = list(v.values())
        return [str(x).strip() for x in list(v)[:cap] if str(x).strip()]

    def genre_rules(self, full: bool = False) -> str:
        g = self.genre
        if not g:
            return ""
        if full:
            return g.get("raw", "")[:6000]
        parts = [f"题材：{g.get('name')}"]
        for label, key, sep, cap in (("核心爽点：", "corePleasure", "；", 6),
                                     ("人物配置：", "cast", "；", 6)):
            got = self._items(g.get(key), cap)
            if got:
                parts.append(label + sep.join(got))
        if g.get("pacing"):
            parts.append("节奏要求：\n" + str(g["pacing"])[:600])
        pit = self._items(g.get("pitfalls"), 8)
        if pit:
            parts.append("必须避开的坑：\n- " + "\n- ".join(pit))
        bm = self._items(g.get("benchmarks"), 6)
        if bm:
            parts.append("对标作品：" + "；".join(bm))
        return "\n".join(parts)

    def style_rules(self) -> str:
        s = self.style
        if not s:
            return ""
        out = [f"平台文风：{s.get('name')}（{s.get('platform','')}）"]
        out += ["- " + r for r in self._items(s.get("rules"), 40)]
        ban = self._items(s.get("banned"), 60)
        if ban:
            out.append("禁止：" + "、".join(ban))
        sd = self.cfg.get("style_defaults", {})
        pref = [f"叙事视角：{sd.get('narration')}" if sd.get("narration") else "",
                f"时态：{sd.get('tense')}" if sd.get("tense") else "",
                sd.get("extra") or ""]
        pref = [x for x in pref if x]
        if pref:
            out.append("【全局写作偏好】" + "；".join(pref))
        return "\n".join(out)

    def common_rules(self) -> str:
        cats = self.common.get("categories", [])
        out = []
        for c in cats:
            out.append(f"[{c['category']}] 禁：" + "；".join(c["avoid"][:3]))
        return "\n".join(out)

    def hard_blacklist(self) -> List[str]:
        """验收用: 只有穿帮词（真朝代/真实人物/自审判定的 hard）即中即扣。
        「冷笑」「深吸一口气」这类套话由 CLICHE_PATTERNS 做密度检测,
        出现 1 次就扣 15 分是误伤(实测第 117 章因此被砸到 25 分)。"""
        a = self.world_anchor()
        out = list(a.get("forbidden") or []) + list(a.get("forbidden_people") or [])
        out += self.learned_rules().get("forbidden_terms", [])
        out += self.era_named_forbidden()
        return list(dict.fromkeys([w for w in out if w]))

    def era_named_forbidden(self) -> List[str]:
        """时代红线卡里**用引号点名**的禁用词, 自动进验收黑名单。

        实测: era_card 写着「严禁直接写出'纽约'等现代专有名词」, 连着加了五条
        同义红线, 而 hard_blacklist 里压根没有「纽约」—— 规则一直在累加,
        验收却一次都没查。红线卡是模型写的自然语言, 里面点名的词就是硬证据。
        """
        card = self.p.read("era_card.md")
        if not card:
            return []
        out: List[str] = []
        for line in card.splitlines():
            if not re.search(r"严禁|禁止|不得|不许", line):
                continue
            for m in re.finditer(r"[‘'「『\"“]([^’'」』\"”，。；：\n]{2,10})[’'」』\"”]", line):
                w = m.group(1).strip()
                # 排除「暗语/意象」这一侧的替代词 —— 那是要**写**的, 不是禁的
                # 同一句里常常既点名禁用词、又点名**替代写法** ——
                # 「严禁X，必须通过'梦境碎片'呈现」里的梦境碎片是要写的东西。
                # 实测不过滤会把三个替代词一起拉黑, 等于把出路也堵死。
                seg = line[max(0, m.start() - 16):m.start()]
                if re.search(r"转化为|转化成|替代|代之|写成|改成|呈现|体现|"
                             r"如：|例如|通过|以.{0,6}方式", seg):
                    continue
                # 禁用词是**专名**: 短、没有动词、不是一句描述。
                # 「旁人误解的疯话」这种带「的」的短语是写法说明, 不是要禁的词。
                if (w and not re.fullmatch(r"[0-9a-zA-Z]+", w)
                        and len(w) <= 6 and "的" not in w):
                    out.append(w)
        return out

    def asset_conflicts(self) -> str:
        """禁用词出现在常驻文档里 —— 提示词自己在打架, 必须当场调解。

        禁令写在红线卡, 而世界观／角色卡／总纲里明明白白写着那个词, 模型每章
        重读一遍这些文档, 当然照抄。实测「纽约」在提示词里出现 7 次(世界观 3、
        角色卡 1、总纲 1、上一章原文 2), 评审连着 4 章判违规 —— 不是模型不听话,
        是它同时收到「他是纽约黑帮」和「不许写纽约」两条指令。
        """
        bad = [w for w in self.hard_blacklist() if w]
        if not bad:
            return ""
        hits: Dict[str, List[str]] = {}
        for name, label in (("world_bible.md", "世界观"),
                            ("characters.md", "角色卡"),
                            ("outline.md", "总纲")):
            txt = self.p.read(name)
            for w in bad:
                if w and w in txt:
                    hits.setdefault(w, []).append(label)
        if not hits:
            return ""
        lines = ["\n⚠️ 【设定与红线打架·按这里处理】"]
        for w, where in hits.items():
            lines.append(f"　「{w}」写在{'、'.join(where)}里，但它是**禁用词**。"
                         f"那是设定的来源，不是准许你照抄的许可。")
        lines.append("　本章遇到这些东西：**只写它的样子、手感、作用和它在主角身上"
                     "留下的东西，绝不写出那个名字**。\n"
                     "　例：不写「纽约街头」，写「那座终年下雨、抬头看不见天的城」；"
                     "不写「格洛克」，写「那件冰凉的、一扣就响的铁物」。\n"
                     "　前几章里若已经写出过那个名字，那是**旧错**，本章不许跟着学。")
        return "\n".join(lines)

    def blacklist(self) -> List[str]:
        # 基底卡的检测词是这本书专属的穿帮词, 必须参与验收 —— 光写进提示词
        # 而不检测, 模型写错了没人发现
        bl = list(self.genre.get("clicheBlacklist", []))
        # 文风包 banned 是「反向提示词」（写作时少用的倾向词, 如「然后/坚定/温暖」),
        # 不是验收黑名单。实测把它塞进 audit 的即中即扣后, 6700 字的章因为一个
        # 「然后」被扣 15 分, 8 个日常词直接把规则分打到 0, 触发无谓重写。
        # banned 只进提示词引导(见 compile_chapter_prompt 的 negative), 不参与验收。
        bl += self.cfg.get("banned_global", []) or []
        from .evaluator import DEFAULT_TICS
        bl += DEFAULT_TICS                    # 冷启动就设防, 不等统计攒够
        lr = self.learned_rules()             # 自审学到的规则, 自动生效
        bl += lr.get("forbidden_terms", []) + lr.get("tics", [])
        bl += self.basis_words()              # 本书专属穿帮词（基底卡判出来的）
        return list(dict.fromkeys([b for b in bl if b]))

    # ---------- 上下文 ----------
    def base_ctx(self) -> Dict[str, Any]:
        m = self.p.meta
        ctx = {
            "title": m.get("title", ""),
            "target_chapters": m.get("target_chapters", 0),
            "target_words": m.get("target_words", 0),
            "genre_rules": self.genre_rules(),
            # 铁律进 base_ctx, 各阶段提示词都能引用。原来只有细纲和正文拿得到,
            # 世界观/人物卡这两步看不见 —— 而人设一旦定歪, 往后三百章跟着歪。
            "hard_rules": "\n".join(f"- {x}" for x in self.hard_rules()) or "（无）",
            "style_rules": self.style_rules(),
            "common_rules": self.common_rules(),
            "cliche_blacklist": "、".join(self.blacklist()),
            # 类型包(剧本/短剧/动漫)的提示词引用 roles/style/artstyle 这些变量,
            # base_ctx 原来不提供, 于是每一层都打印「未提供的变量(已置空)」——
            # 精心写好的格式规范里, 角色表和画风是空的。
            "style": self.style.get("name", "") or m.get("style_id", ""),
            "kb": ((m.get("fields", {}) or {}).get("kb", "")),
        }
        rost = self.roster()
        if rost:
            ctx["roles"] = "\n".join(
                f"{c['name']}：{_first_line(c['card'])}" for c in rost[:12])
        ctx.update({k: v for k, v in m.get("fields", {}).items() if v not in (None, "")})
        for k in ("roles", "artstyle", "logline", "theme", "scenes", "hook"):
            ctx.setdefault(k, "")
        return ctx

    # ---------- 统一检索 ----------
    def _ask_era(self, src: str) -> str:
        """问模型这是哪个朝代, 答案存进 meta 复用。

        考据卡跨书共享的键是「朝代|主题」—— 朝代认错, 整本书的考据都白查;
        朝代空着, 「盐引制度」在北宋和明代会被当成同一件事。所以宁可花一次
        调用也要问准。取不到就留空, 让检索退化成不带朝代的查询, 别乱填。
        """
        got = (self.p.meta.get("era") or "").strip()
        if got:
            return got
        if not (src or "").strip():
            return ""
        try:
            ans = clean(call("planning", 
                "下面是一部小说的设定。它发生在中国历史上的哪个朝代？\n"
                "只回答朝代名, 2-4 个字, 比如「北宋」「明」「唐」。\n"
                "架空世界或无法判断就回答「无」, 不要解释。\n\n"
                + src[:1200], max_tokens=20).text)[:6].strip()
        except Exception:
            return ""
        ans = re.sub(r"[^\u4e00-\u9fff]", "", ans)
        if not ans or ans in ("无", "架空", "无法判断"):
            return ""
        self.p.meta["era"] = ans
        self.p.save()
        return ans

    @property
    def retriever(self) -> Retriever:
        """内部记忆 + 外部搜索合一。搜索不是独立步骤, 是召回层的一半。"""
        if self._retriever is None:
            wb = self.p.read("world_bible.md")
            m = re.search(r"参考朝代[^\n]*?[:：]\s*([^\n，,。]{1,10})", wb) or \
                re.search(r"(?:朝代|时代)\s*[:：]\s*([^\n，,。]{1,10})", wb)
            era = (self.p.meta.get("era") or (m.group(1).strip() if m else ""))
            if not era:
                # 世界观还没生成时从正文里捞朝代。**不能回退到题材包名** ——
                # 「同人二创」不是时代，拿它当跨书考据库的键，
                # 同一件事在「北宋|…」和「同人二创|…」两个键下各存一份，
                # 共享库就白建了（实测 1110 张卡一张也命中不了）。
                src = wb or (self.p.meta.get("fields", {}) or {}).get("premise", "")
                # 朝代交给模型判, **别用正则猜**。两种猜法都实测栽过:
                #   单字裸匹配 —— 「元祐党籍碑」里的「元」被认成元朝,
                #     这本北宋书的考据键成了「元|…」, 共享库 1263 张卡全废;
                #   只认「北宋」「宋代」这类无歧义写法 —— 可这本的世界观通篇
                #     写的是「政和五年」「徽宗朝」「靖康」, 一次都没出现朝代名,
                #     于是又回退成了题材包名。
                # 年号到朝代的映射本来就是常识题, 模型一次就答对, 答完存进
                # meta 复用, 全书只花这一次。
                era = self._ask_era(src)
            if not era:
                era = self.genre.get("name", "")     # 实在捞不到才拿题材名兜底
            self._retriever = Retriever(
                self.p.mem, self.p.dir, era=era,
                enable_web=bool(self.mcfg.get("web_search", True)),
                summarize=lambda q: clean(call("polishing", q, max_tokens=800).text),
                topics=self.genre.get("research_topics"),
                plan=lambda q: clean(call("planning", q, max_tokens=500).text))
        return self._retriever

    def sanitize_facts(self, text: str) -> str:
        """架空模式下把参考资料里的真朝代名换成本书国号。

        考据卡会进 L1 常驻层, 里面满篇"宋代"等于把真朝代名喂回给模型 ——
        实测正文出现 28 次"宋律"就是这么来的。
        """
        if not text:
            return text
        a = self.world_anchor()
        if a.get("mode") != "alt" or not a.get("dynasty"):
            return text
        dyn = a["dynasty"][:-1] if a["dynasty"].endswith("朝") else a["dynasty"]
        for w in ("北宋", "南宋", "大宋", "宋朝", "宋代", "唐代", "唐朝", "明代",
                  "明朝", "清代", "清朝", "汉代", "元代"):
            text = text.replace(w, f"{dyn}朝")
        return re.sub(r"《宋史[^》]*》|《宋[^》]{0,4}》", f"《{dyn}史》", text)

    def ground(self, stage: str, extra=None, context: str = "") -> str:
        if not self.mcfg.get("web_search", True):
            return ""
        try:
            return self.retriever.ground(stage, extra=extra, context=context)
        except Exception as e:
            print(f"[ground] 背景落地跳过: {e}")
            return ""

    # 内置七种基底是**预设**, 不是穷举。恋爱国度、赛博废土、克苏鲁神话……
    # 永远有装不进去的新题材, 一路枚举下去只会越补越漏。所以真正的机制是:
    # 建项目时让模型读一遍设定, 自己判定这本书的世界基底并写出红线清单,
    # 存成 basis.md（用户可改）。内置七种只用作快速预设与兜底。
    BASIS_TYPES = ("real", "modern", "alt", "fanfic", "mythos", "multiworld", "invented")

    def basis_card(self, force: bool = False) -> str:
        """本书的世界基底卡 —— 模型判定，用户可改，约束层照此执行。"""
        if not force:
            cached = self.p.read("basis.md")
            if cached:
                return cached
        f = self.p.meta.get("fields", {}) or {}
        preset = (self.genre.get("historyMode") or "invented")
        prompt = (
            f"你在给一部小说定「世界基底」—— 也就是判断这本书的世界是什么性质，"
            f"从而定出哪些东西写了就算错。\n\n"
            f"【书名】{self.p.meta.get('title','')}\n"
            f"【题材】{self.genre.get('name','')}\n"
            f"【一句话故事】{f.get('premise','')}\n"
            f"【背景设定】{f.get('background','')}\n\n"
            f"先在下列类型里选一个最贴切的（选不出就选 other 并自己描述）：\n"
            f"  real       写真实历史朝代（用真名，校史实）\n"
            f"  modern     真实世界的具体年代（晚于该年的事物即穿帮）\n"
            f"  alt        架空世界（自造国号，出现真朝代名即穿帮）\n"
            f"  fanfic     同人二创（原作设定是最高法，崩人设即错）\n"
            f"  mythos     既有神话体系（辈分师承法宝按公认设定）\n"
            f"  multiworld 多世界/副本流（每个副本自带基底）\n"
            f"  invented   完全自创世界（只校内部自洽）\n"
            f"  other      以上都不贴切\n\n"
            f"严格按下面格式输出，不要多余文字：\n"
            f"类型：（上面八个之一）\n"
            f"锚点：（这本书钉死的参照物：某年某朝、某部原作的某个节点、"
            f"某个神话体系的某一版、或自创世界的核心规则。一句话）\n"
            f"红线：（写了就算错的东西，5-8 条，每条一行「- 」开头，"
            f"要具体到能一眼看出违规。例如「- 2005 年不能有智能手机作为日常物品」"
            f"「- 悟空的战力不能低于原作同期」「- 恋爱国度里不存在货币，"
            f"一切交易用好感度」）\n"
            f"可以放开：（本书**允许**自由发挥的地方，2-4 条，避免过度约束。"
            f"每条一行「- 」开头）\n"
            f"检测词：（写进正文即穿帮的具体词，逗号分隔，没有就写「无」）\n")
        r = call("planning", prompt, max_tokens=900)
        card = clean(r.text)
        if card:
            self.p.write("basis.md", card)
            self._log(f"世界基底卡 {len(card)} 字（预设 {preset}）")
        return card

    def basis_field(self, name: str) -> str:
        m = re.search(rf"^{name}：\s*(.*?)(?=\n[一-鿿]{{2,4}}：|\Z)",
                      self.basis_card(), re.S | re.M)
        return (m.group(1).strip() if m else "")

    def history_mode(self) -> str:
        """这本书的世界基底 —— 决定哪些红线成立。

        四种，不是三种。原来只有 real/alt/none，而 none 同时表示「当代现实」
        和「纯虚构世界」，这两者差得远：2005 年的都市重生有硬时代锚点
        （那年没有 4G、没有微信），玄幻大陆根本没有锚点。混为一谈的后果是
        都市重生书的穿帮词检测**完全关闭**。

          real       真实世界·历史 —— 朝代官职用真名，真实历史人物可出场（须符史实）
          modern     真实世界·当代 —— 有具体年代锚点，晚于该年的事物即穿帮；
                                      真实公司与在世名人须化名，避免影射
          alt        架空世界      —— 必须自造国号，出现真朝代名或真人即穿帮
          fanfic     同人          —— 基于既有作品，原作设定是最高法，崩人设即错
          mythos     神话体系      —— 洪荒/封神/希腊北欧，辈分师承法宝归属按公认设定
          multiworld 多世界        —— 无限流：主世界+副本，每个副本自带基底
          invented   纯虚构世界    —— 无真实时代参照，两条都不校

        映射由**题材包声明**（historyMode 字段），不再硬编码在引擎里 ——
        原来加一个新题材包还得回来改这个函数里的白名单。
        """
        m = (self.p.meta.get("history_mode") or "auto").lower()
        if m in self.BASIS_TYPES:
            return m                          # 用户显式指定, 最高优先
        if m == "none":                       # 老项目的遗留取值
            return "modern" if self.era_words() else "invented"
        declared = self.basis_field("类型").split()[0] if self.p.read("basis.md") else ""
        if declared in self.BASIS_TYPES:
            return declared                   # 模型判定的基底
        if declared == "other":
            return "custom"                   # 七种都不贴切, 红线全走基底卡
        return (self.genre.get("historyMode") or "invented").lower()

    # ---------- 提示词覆盖 ----------
    # 每本书可在前端改自己的提示词模板, 存 meta.prompt_overrides。
    # 可用变量与内置模板一致(${title} ${premise} ${background} ${genre_rules}...)。
    PROMPT_KEYS = {
        "world_bible": "世界观圣经生成",
        "characters": "角色档案生成",
        "outline": "总纲生成",
        "chapter_outline_extra": "分章细纲·追加指令",
        "content_extra": "正文·追加指令",
    }

    def prompt_override(self, key: str) -> str:
        return ((self.p.meta.get("prompt_overrides") or {}).get(key) or "").strip()

    # ---------- 世界观锚定 ----------
    def world_anchor(self) -> Dict[str, Any]:
        """从世界观里抽出必须钉死的硬设定, 并推导禁用词。

        架空题材如果自定了"景朝", 就必须禁掉"大宋/宋律/北宋"这类真朝代词,
        否则模型会一路写成同人。这些进 L5 硬约束层, 每章必带。
        """
        wb = self.p.read("world_bible.md")
        meta = self.p.meta
        anchor = dict(meta.get("anchor") or {})

        if not anchor.get("dynasty"):
            m = re.search(r"国号\s*[:：]\s*([一-鿿]{1,3}朝)", wb) or FAKE_DYN_RE.search(wb)
            cand = m.group(1) if m else ""
            if not cand:
                # 兜底: 从"朝代设定：大宋景朝"这类混写里剥出虚构部分
                m2 = re.search(r"朝代设定\s*[:：]\s*([一-鿿]{2,6}朝)", wb)
                cand = m2.group(1) if m2 else ""
            # 逐层剥掉真朝代前缀: "大宋景朝" -> "宋景朝" -> "景朝"
            for _ in range(3):
                for real in ("大宋", "北宋", "南宋", "大唐", "大明", "大清", "大汉", "大元",
                             "宋", "唐", "明", "清", "汉", "元", "秦", "晋", "隋"):
                    if cand.startswith(real) and len(cand) > len(real) + 1:
                        cand = cand[len(real):]
                        break
                else:
                    break
            if cand and cand not in REAL_DYNASTIES and cand.endswith("朝"):
                anchor["dynasty"] = cand
        if not anchor.get("main_place"):
            places = Counter(re.findall(r"([一-鿿]{2}(?:县|州|府|城|镇))", wb))
            if places:
                anchor["main_place"] = places.most_common(1)[0][0]

        mode = self.history_mode()
        anchor["mode"] = mode
        forbidden = list(anchor.get("forbidden") or [])
        # 只有架空模式才禁真朝代名; 正统历史里「宋朝」「宋律」本来就是正确写法
        if mode == "alt" and anchor.get("dynasty"):
            forbidden += [w for w in REAL_DYNASTIES if w not in forbidden]
        elif mode != "alt":
            anchor.pop("dynasty", None)
        forbidden += self.learned_rules().get("forbidden_terms", [])
        anchor["forbidden"] = list(dict.fromkeys(forbidden))
        if mode == "alt":
            from .evaluator import REAL_PEOPLE
            anchor["forbidden_people"] = REAL_PEOPLE
        return anchor

    def alias_pair(self) -> Optional[tuple]:
        """返回 (对外身份, 本名) —— 供状态抽取与约束共用。"""
        cards = self.roster()
        if not cards:
            return None
        head = cards[0]["card"]
        m = re.search(r"姓名\s*[:：]\s*([^\n（(]{1,8})(?:[（(]\s*(?:原名|本名|真名)?\s*[:：]?\s*([^）)]{1,8})[）)])?", head)
        if not m:
            return None
        a, b = m.group(1).strip(), (m.group(2) or "").strip()
        if not b or a == b:
            return None
        title = self.p.meta.get("title", "")
        # 书名里出现的那个是对外身份
        return (b, a) if b in title else (a, b)

    def protagonist_alias(self) -> str:
        """主角本名与对外身份不一致时（穿越/重生/马甲/化名）必须钉死称谓规则。

        实测: 花名册第一条是「林远」, 但书里对外身份是「西门庆」,
        没有这条约束后文会两个名字乱用。
        """
        pair = self.alias_pair()
        if not pair:
            return ""
        outer, inner = pair
        return (f"【称谓锚定】主角对外身份是「{outer}」，本名/前世名是「{inner}」。"
                f"叙述与他人称呼一律用「{outer}」；只有主角内心独白、"
                f"或明确回忆前世时才可出现「{inner}」，且不得让旁人叫出这个名字。")

    # ---------- 角色花名册 ----------
    # 两本通用台账。引擎只认「资源」和「力量/地位」两个槽位, 具体叫什么、
    # 记什么由题材包填 —— 都市记资金和职位, 修仙记灵石和境界, 历史记钱粮和官职。
    # 题材包没声明就用这里的通用默认值, 任何题材都不会缺台账。
    DEFAULT_LEDGERS = {
        "resource": {"label": "资源账",
                     "hint": "主角方可支配资源（钱财/物资/积分等）的收支与存量"},
        "power": {"label": "实力与地位",
                  "hint": "实力层级、身份地位、关键持有物、伤势状态"},
    }

    def ledger_spec(self) -> Dict[str, Dict[str, str]]:
        """本书两本台账的叫法与记账口径。"""
        spec = {k: dict(v) for k, v in self.DEFAULT_LEDGERS.items()}
        for slot, cfg in (self.genre.get("ledgers") or {}).items():
            if slot in spec and isinstance(cfg, dict):
                spec[slot].update({k: v for k, v in cfg.items()
                                   if k in ("label", "hint") and v})
        return spec

    def anachronism_check(self) -> bool:
        """要不要查「这个年代还没有的东西」。

        判据是**有没有时代锚点**，不是模式名。原来写死 mode in (real, alt)，
        于是 2005 年的都市重生书完全不查 —— 可「那年没有 4G、没有微信」
        和「洪武朝没有玉米」是同一类错误，只是参照点不同。
        """
        # 有检测词就查, 与模式无关。「恋爱国度」被判成 invented, 可它自己列出了
        # 人民币/支付宝/微信这些写了即穿帮的词 —— 按模式名开关就会把它们漏掉。
        if self.basis_words():
            return True
        return self.history_mode() in (
            "real", "alt", "modern", "fanfic", "mythos", "multiworld")

    def basis_words(self) -> List[str]:
        """基底卡里「写进正文即穿帮」的具体词。"""
        raw = self.basis_field("检测词")
        if not raw or raw.strip() in ("无", "None", "-"):
            return []
        return [w.strip() for w in re.split(r"[,，、;；\n]", raw)
                if 1 < len(w.strip()) <= 12][:30]

    # 本书资产（总纲/世界观/角色档案/时代卡/基底卡）体量有限且**每一段都要紧**：
    # 总纲的后半段是终局、爽点节奏表与伏笔总账。实测分卷生成只喂了总纲前 1600 字,
    # 于是排卷的时候连「登基」两个字都没看见, 全靠猜。这些小资产整份传, 只对
    # 能无限增长的东西（正文、召回命中）设上限。
    ASSET_CAP = 24000

    def asset(self, name: str, cap: int = 0) -> str:
        """读取本书资产。默认整份返回, 只在超过硬上限时才截。"""
        t = self.p.read(name)
        lim = cap or self.ASSET_CAP
        return t if len(t) <= lim else t[:lim]

    @staticmethod
    def condense(text: str, cap: int, head_ratio: float = 0.55) -> str:
        """超长文本要**压缩**，不是截断。

        截断等于把后半段直接扔了：实测结构化抽取只读章节前 4000 字，
        8683 字的章后 4683 字里的伏笔、角色状态、资金变动全部丢失；
        FTS5 索引只存前 1500 字，「写到 300 章也能找回第 30 章埋的线」
        对后半章根本不成立。

        这里保留首尾两头（开头交代场景、结尾落钩子，都是信息密度最高的地方），
        中段按段落均匀抽样，并明确标注省略了多少 —— 让模型知道自己看的是节选。
        """
        if len(text) <= cap:
            return text
        head_n = int(cap * head_ratio)
        tail_n = cap - head_n - 40
        paras = [x for x in text[head_n:len(text) - tail_n].split("\n") if x.strip()]
        mid = ""
        if paras:
            step = max(1, len(paras) // 6)
            mid = "\n".join(paras[::step][:6])[:max(0, cap // 6)]
        return (text[:head_n]
                + f"\n\n……〔中段节选，原文另有约 {len(text) - cap} 字〕\n" + mid
                + "\n\n" + text[len(text) - tail_n:])

    def era_hint(self) -> str:
        """本书时代背景的单一真相源。

        原来 era_card / gate / 规则校验各算各的（有的读 background，有的读
        meta['era_hint'] 这个根本不存在的字段），于是守门模型不知道本书就发生
        在洪武朝，把「洪武二十三年」判成穿帮词，全书 42 处合法用法被判违规。
        """
        f = self.p.meta.get("fields", {}) or {}
        return (str(f.get("background", "")) + " " + str(f.get("premise", ""))).strip()

    def era_brief(self, limit: int = 300) -> str:
        """给提示词用的短版时代背景。

        era_hint() 必须返回全文 —— 时代锚点常常写在背景的中后段（本书的
        时间轴就从第 300 字才开始），截断会让年号年份整个抽取不到，
        时代红线卡随之缺位。需要短文本的地方显式调这个方法。
        """
        return self.era_hint()[:limit]

    # 年号纪年（政和五年 / 宣和二年 / 洪武二十三年）与公元年（1115 / 1115年 /（1115））。
    # 原来用 [一-鿿]{2,8}(?:年|朝) 硬扫, 贪婪匹配会把「下始知能打的不是朝」这种
    # 半截句子当成朝代名; 而公元年只认「1115 年」, 写成「政和五年（1115）」就抓不到。
    _ERA_CN = re.compile(
        r"(?:[一-鿿]{2}(?=[元零一二三四五六七八九十百]{1,4}年))"
        r"[元零一二三四五六七八九十百]{1,4}年")
    _ERA_AD = re.compile(r"(?<![0-9])(1[0-9]{3}|[7-9][0-9]{2})(?:\s*年|(?=[）)]))")

    def era_words(self) -> set:
        """从时代背景里抽出年号/公元年 —— 这些词永远不能进禁用表。"""
        txt = self.era_hint()
        out = set()
        for w in self._ERA_CN.findall(txt):
            out.add(w)
            out.add(re.sub(r"年$", "", w))
        for y in self._ERA_AD.findall(txt):
            out.add(y)
            out.add(y + "年")
        # 直接读盘, 不能走 world_anchor() —— 它会回头调 learned_rules(), 而
        # learned_rules() 正是本方法的调用者, 形成无限递归
        a = self.p._load("world_anchor.json", {}) or {}
        for key in ("dynasty", "era"):
            if a.get(key):
                out.add(str(a[key]))
        return {w for w in out if len(w) >= 2}

    def roster(self) -> List[Dict[str, str]]:
        """把 characters.md 解析成结构化角色卡, 供逐章定向注入。"""
        cached = self.p._load("roster.json", None)
        if cached:
            return cached
        txt = self.p.read("characters.md")
        cards: List[Dict[str, str]] = []
        # 角色标题有两种写法, 都要认: 「### 1. 姓名：陈九四」和「### 1. 陈九四：抄写小吏」。
        # 只认「姓名」会在第二种格式上切不开 —— 整个文件变成一张卡, 花名册只剩 1 人,
        # 于是命名注册表失效(新配角可以重名)、配角卡不注入、戏份垄断误报。
        parts = re.split(r"\n(?=#{1,4}\s*\d*\.?\s*姓名)", txt)
        if len(parts) < 2:
            parts = re.split(r"\n(?=#{2,4}\s+\d+\s*[.、]\s*\S)", txt)
        for blk in parts:
            blk = blk.strip()
            if len(blk) < 40:
                continue
            head = blk.splitlines()[0]
            m = re.search(r"姓名\s*[:：]\s*([^（(｜|，,\n]{1,8})", head)
            if not m:
                # 「### 3. 朱棣：燕王/未来成祖」——取序号后、冒号前的那段做名字
                m = re.search(r"#{1,4}\s*\d+\s*[.、]\s*([^:：（(\n/]{1,8})", head)
            if not m:
                m = re.search(r"[#\d.\s]*([一-鿿]{2,6})", head)
            if not m:
                continue
            name = m.group(1).strip()
            # 标题也有「职务：姓名」的反向写法(### 8. 锦衣卫都指挥使：毛骧),
            # 冒号前是官职时取冒号后的真名, 否则花名册里躺的是「沈万三后人」这种伪名字
            if re.search(r"(?:使|后人|领袖|之女|之妻|遗老|皇帝|太子|王妃)$", name):
                m2 = re.search(r"[:：]\s*([一-鿿]{2,4})\s*(?:$|[/、，,（(])", head)
                if m2:
                    name = m2.group(1).strip()
            if not re.search(r"[一-鿿]", name) or name in ("身份", "年龄", "外貌", "性格"):
                continue
            cards.append({"name": name, "card": blk[:700]})
        if cards:
            # 花名册首条用「对外身份」做名字, 否则模型会把本名当主名来写
            m = re.search(r"姓名\s*[:：]\s*([^\n（(]{1,8})[（(]\s*([^）)]{1,8})[）)]",
                          cards[0]["card"])
            if m:
                a, b = m.group(1).strip(), m.group(2).strip()
                title = self.p.meta.get("title", "")
                cards[0]["name"] = b if (b in title and a not in title) else a
            self.p.write("roster.json", json.dumps(cards, ensure_ascii=False, indent=2))
        return cards

    def live_roster(self) -> List[Dict[str, str]]:
        """带「现在的身份」的花名册。

        角色档案是开书时一次写死的。写到第 125 章主角已统兵一方，注入的卡片还写着
        「应天府户部老仓未入流抄写吏」—— 模型照着过时的卡写，人物就会往回缩。
        这里把逐章抽出的身份变更贴在卡片最前面，档案本身不动（它记的是出身）。
        """
        ident = self.p.state.get("identity", {}) or {}
        out = []
        for c in self.roster():
            cur = ident.get(c["name"])
            card = c["card"]
            if cur:
                card = (f"**当前身份（第{cur['at']}章起，以此为准）**：{cur['now']}\n"
                        + card)
            out.append({"name": c["name"], "card": card})
        return out

    def cast_for(self, chapter_outline: str) -> str:
        """只注入本章会出场的角色卡 —— 全量塞进去既费预算又让模型抓瞎。"""
        cards = self.live_roster()
        if not cards:
            return ""
        hit = [c for c in cards if c["name"] and c["name"] in chapter_outline]
        # 主角永远带上
        if cards and cards[0] not in hit:
            hit.insert(0, cards[0])
        if len(hit) < 3:                       # 出场太少时补几个近期活跃角色
            for c in cards:
                if c not in hit:
                    hit.append(c)
                if len(hit) >= 4:
                    break
        # 档案里设计了、正文却一直没登场的角色 —— 补位时优先选它们。
        # 实测: 花名册解析坏掉的 76 章里, 苏婉儿(盐商之女/地下银行家)出场 0 次,
        # 角色档案白写。补位如果只按顺序拿, 永远轮不到排在后面的人。
        idle = [x for x in self.idle_cast()
                if x not in (self.learned_rules().get("drop_roles") or [])]
        if idle and len(hit) < 5:
            for c in cards:
                if c["name"] in idle and c not in hit:
                    hit.append(c)
                    if len(hit) >= 5:
                        break
        return "\n\n".join(f"【{c['name']}】{c['card']}" for c in hit[:6])

    def idle_cast(self, scan: int = 40) -> List[str]:
        """档案里有、最近 scan 章却从没出现过的角色名（按档案顺序）。"""
        done = sorted(self.p.state.get("done", []))[-scan:]
        if not done:
            return []
        seen = "".join(self.p.chapter(n) for n in done)
        return [c["name"] for c in self.roster()[1:]
                if c["name"] and c["name"] not in seen]

    # ---------- 口癖抑制 ----------
    def stale_cast(self, n: int, gap: int = 8) -> List[str]:
        """出场过、但已连续 gap 章没再出现的角色。

        与 idle_cast()（档案里有、从未出现）不同：这些是读者认识的人，
        突然消失比从未登场更伤 —— 尤其主角对他们还有未了的情感债。
        """
        done = sorted(self.p.state.get("done", []))
        if len(done) < gap + 2:
            return []
        recent = "".join(self.p.chapter(i) for i in done[-gap:])
        earlier = "".join(self.p.chapter(i) for i in done[:-gap])
        out = []
        for c in self.roster()[1:]:
            nm = c["name"]
            if not nm or nm in recent:
                continue
            hits = earlier.count(nm)
            if hits >= 5:                      # 出场够多才算「读者记得」
                out.append(f"{nm}（此前出现 {hits} 次，已 {gap} 章未提）")
        return out[:5]

    # ---------------- 阶段骨架 · 张力 · 承诺 ----------------
    # 这三样是「种子发散」的骨: 总纲(种子)先长成阶段骨架, 骨架再派生功能位与人,
    # 每一批细纲都长在骨架上, 而不是只看前一批的摘要往下接。

    def _ask_planner(self, q: str, cap: int = 4000) -> str:
        return clean(call("planning", q, max_tokens=cap).text)

    def _with_overrides(self, pack: Dict[str, Any], kind: str) -> Dict[str, Any]:
        """把本书的 pack_overrides 盖在包上。

        meta.pack_overrides = {"genre": {...}, "style": {...}}
          · 给值      → 整个字段替换（列表整体换掉，不做逐项合并：
                        逐项合并的话「删掉某一条规则」表达不出来）
          · 给 null   → 删掉这个字段
        包本身不动，别的书不受影响。
        """
        ov = ((self.p.meta.get("pack_overrides") or {}).get(kind) or {})
        if not isinstance(ov, dict) or not ov:
            return pack
        out = dict(pack)
        for k, v in ov.items():
            if v is None:
                out.pop(k, None)
            else:
                out[k] = v
        return out

    #: 决定全书走向的输入。这些一变，下游的总纲/骨架/支线/阶梯/细纲全都过期。
    # model 也算种子: 换了写手, 前面那批资产就不是这个写手的东西了。
    # 实测同一条提示词, 换个模型出来的文风、密度、常识差距比改旋钮还大
    # (有的模型甚至一句正文都不返回), 混着往下长等于两个人合写一本书。
    _SEED_KEYS = ("hard_rules", "fields", "dials", "extra_ladders",
                  "pack_overrides", "target_chapters", "target_words", "model")
    #: 依赖这些输入的下游资产，按生成顺序排列。
    #: 世界观与角色档案同样是从 premise 长出来的 —— 漏了它们，
    #: premise 改了却还拿旧世界观去写新总纲，等于只改了一半。
    _DERIVED = ("world_bible.md", "characters.md", "roster.json", "era_card.md",
                "basis.md", "outline.md", "volumes.json", "stages.json",
                "threads.json", "ladders.json", "chapter_outlines.json")

    def seed_stamp(self) -> str:
        import hashlib
        seed = {k: self.p.meta.get(k) for k in self._SEED_KEYS}
        # 题材/文风包**渲染后的文本**也要进指纹, 不只是本书 meta。
        # 踩过: 修好「包字段是整句时被逐字拆开」这个 bug 之后, 题材规范从
        # 「核心爽点：熟；悉；感」变成了正常一句话 —— 可总纲是用乱码规范生成的,
        # 指纹没变, 于是坏种子原地留着往下长。包本身改了也是同理。
        # 盖渲染结果而不是包文件, 是因为真正喂给模型的就是这段文本。
        seed["_rules"] = self.genre_rules() + "\n" + self.style_rules()
        raw = json.dumps(seed, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def stale_assets(self) -> List[str]:
        """种子变了、却还是旧版生成的下游资产。

        实测踩过：改完铁律（武功终点、加了枪）只删了骨架/支线/阶梯，
        **留下了 outline.md 想省一次生成** —— 总纲还是旧设定，
        阶梯是新的，两份资产打架，细纲照着总纲写，
        于是「打服武松」和那把枪在 128 章里一次没出现。
        这不该靠人记性。
        """
        want = self.seed_stamp()
        got = (self.p.state.get("seed_stamp") or "")
        if got == want:
            return []
        return [f for f in self._DERIVED if (self.p.dir / f).exists()]

    def mark_seed(self) -> None:
        st = self.p.state
        st["seed_stamp"] = self.seed_stamp()
        self.p.save()

    def hard_rules(self) -> List[str]:
        """本书铁律 —— 每一章都必须成立的设定约束。

        与题材纪律、文风纪律不同，这些是**这一本书特有**、且违反一次就穿帮的
        东西：金手指的用法与代价、某样资源的死数、某个能力的边界。
        写在设定文档里没用 —— 设定只在开书那几次被完整读过，写到第两百章
        早没人记得。所以它们和红线一样进约束层，排纲与写正文两处都带。
        """
        return [str(x).strip() for x in (self.p.meta.get("hard_rules") or [])
                if str(x).strip()][:12]

    def extra_ladders(self) -> List[Dict[str, str]]:
        """本书额外的成长线。

        题材包的 power 槽常把两件事塞在一起（同人包写的是「战力与身份」），
        模型只会挑一半去排阶梯 —— 实测某书 8 级里 5 级是商业地位，
        武功排到第 3 级就停，而武功恰恰是这本书点名要兑现的线。
        点名的线要能独立成槽，不跟别人挤。
        """
        out = []
        for x in (self.p.meta.get("extra_ladders") or [])[:3]:
            if isinstance(x, dict) and x.get("key") and x.get("label"):
                out.append({"key": str(x["key"])[:16], "label": str(x["label"])[:12],
                            "hint": str(x.get("hint") or "")[:160]})
        return out

    def dials(self) -> Dict[str, int]:
        """本书的两个旋钮：本书设置优先于全局默认。"""
        return dl.normalize({**(self.cfg.get("dials") or {}),
                             **(self.p.meta.get("dials") or {})})

    def name_aliases(self) -> Dict[str, str]:
        """主角别名 -> 本名。不合一的话主角会被判定全书没出过场。"""
        pair = self.alias_pair() or []
        if len(pair) < 2:
            return {}
        real = sc.canon_name(pair[0])
        return {sc.canon_name(pair[1]): real, str(pair[1]): real}

    def stages(self, rebuild: bool = False) -> List[Dict[str, Any]]:
        cached = self.p._load("stages.json", [])
        if cached and not rebuild:
            return cached
        outline = self.asset("outline.md")
        if not outline.strip():
            return []
        total = int(self.p.meta.get("target_chapters") or 0) or 100
        got = sc.build_stages(outline=outline, total_chapters=total,
                              title=self.p.meta.get("title", ""), genre=self.genre,
                              roster=[c["name"] for c in self.roster()],
                              ask=self._ask_planner)
        if got:
            self.p.write("stages.json", json.dumps(got, ensure_ascii=False, indent=2))
            self._log(f"阶段骨架 {len(got)} 段")
        return got

    def tensions(self, rebuild: bool = False) -> List[Dict[str, Any]]:
        st = self.p.state
        if st.get("tensions") and not rebuild:
            return st["tensions"]
        got = sc.build_tensions(outline=self.asset("outline.md"),
                                characters=self.p.read("characters.md"),
                                title=self.p.meta.get("title", ""), ask=self._ask_planner)
        if got:
            st["tensions"] = got
            self.p.save()
        return got

    def promises(self, rebuild: bool = False) -> List[Dict[str, Any]]:
        st = self.p.state
        if st.get("promises") and not rebuild:
            return st["promises"]
        got = sc.build_promises(outline=self.asset("outline.md"),
                                title=self.p.meta.get("title", ""), ask=self._ask_planner)
        # 铁律点名的东西**强制进承诺清单**，不靠模型从总纲里抽。
        # 承诺清单是从总纲抽的，总纲也是模型写的 —— 总纲写歪了，承诺跟着歪，
        # 整条链没有一处会发现「这跟用户要的不一样」。实测：premise 里写明
        # 「一把手枪一百二十发，最后保命手段」，总纲没把它当承诺，
        # 承诺清单里没有它，于是 346 章一发没开、开篇钩子当场被晾。
        base = len(got)
        for i, r in enumerate(self.hard_rules()):
            # 铁律也要有兑现判据，否则 unfulfilled() 会把它跳过 ——
            # 专门为「一百二十发一发没开」建的检测器，反倒对铁律不生效。
            # 铁律自己就是判据：它说必须发生什么，那就是必须发生什么。
            got.append({"id": 900 + i, "kind": "铁律", "text": r[:120],
                        "done_when": r[:120], "done_at": 0,
                        "keywords": [], "last_advanced": 0})
        if got:
            st["promises"] = got
            self.p.save()
            if len(got) > base:
                self._log(f"承诺清单 {base} 条 + 铁律强制 {len(got) - base} 条")
        return got

    def ladders(self, rebuild: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        """三条随进度演进的线：力量 / 爽点 / 人设。

        题材包的 power 槽原来只有记账口径（境界怎么写），没有「第几章该到第几级」，
        也没人检查 —— 实测主角的功夫写在总纲里，第 125 章之后 200 多章没再出现。
        """
        cached = self.p._load("ladders.json", {})
        if cached and not rebuild:
            return cached
        outline = self.asset("outline.md")
        if not outline.strip():
            return {}
        total = int(self.p.meta.get("target_chapters") or 0) or 100
        got = sc.build_ladders(outline=outline, total_chapters=total,
                               title=self.p.meta.get("title", ""), genre=self.genre,
                               extra_kinds=self.extra_ladders(),
                               ask=self._ask_planner)
        if got:
            self.p.write("ladders.json", json.dumps(got, ensure_ascii=False, indent=2))
            self._log("阶梯：" + "／".join(f"{k} {len(v)} 级" for k, v in got.items()))
        return got

    def threads(self, rebuild: bool = False) -> List[Dict[str, Any]]:
        """支线。主线管方向，支线管密度 —— 全书只有一条线在走就会干巴。"""
        cached = self.p._load("threads.json", [])
        if cached and not rebuild:
            return cached
        outline = self.asset("outline.md")
        if not outline.strip():
            return []
        total = int(self.p.meta.get("target_chapters") or 0) or 100
        got = sc.build_threads(outline=outline, stages=self.stages(),
                               total_chapters=total, title=self.p.meta.get("title", ""),
                               roster=[c["name"] for c in self.roster()],
                               ask=self._ask_planner)
        if got:
            self.p.write("threads.json", json.dumps(got, ensure_ascii=False, indent=2))
            # 支线的 org 字段就是组织台账的生命线 —— 原来 orgs 一直是空的,
            # 于是共主角级的势力(梁山)可以在卷末静默蒸发, 没人报警。
            st = self.p.state
            orgs = st.setdefault("orgs", {})
            for x in got:
                if x.get("org"):
                    # 形状必须与 _extract 写的一致：{at, state}。
                    # 两个生产者各写各的形状，消费方一取 v['at'] 就 KeyError ——
                    # 而这条路径在**写正文**时才走到，排纲全程不报错，
                    # 等于埋到动笔那天才炸。
                    orgs.setdefault(x["org"], {
                        "at": int(x["span"][0]), "state": f"（支线「{x['name']}」）在册",
                        "thread": x["name"], "span": x["span"]})
            self.p.save()
            self._log(f"支线 {len(got)} 条：" + "、".join(x["name"] for x in got))
        return got

    def outline_cast(self, before: Optional[int] = None) -> Dict[str, List[int]]:
        return sc.cast_appearances(self.p._load("chapter_outlines.json", {}),
                                   before=before, aliases=self.name_aliases())

    def stale_cast_planned(self, start: int, gap: int = 25,
                           min_hits: int = 5) -> List[str]:
        """排纲阶段的断线检测。

        stale_cast() 读的是**正文**, 排纲时一章正文都还没有, 所以整个排纲过程
        没有任何东西在看「谁掉线了」—— 实测 346 章排完, 主角最大的情感债
        出场 2 章后消失, 开局关键配角断线 206 章。
        """
        app = self.outline_cast(before=start)
        out = []
        for nm, cs in app.items():
            if len(cs) >= min_hits and start - cs[-1] > gap:
                out.append((start - cs[-1], f"{nm}（已排 {len(cs)} 章有戏，"
                                            f"第 {cs[-1]} 章后再没出现，断 {start - cs[-1]} 章）"))
        return [x for _, x in sorted(out, reverse=True)[:5]]

    def standby_cast(self) -> List[str]:
        """备选角色: 阶段骨架点了名、花名册里还没有的人。

        白名单原来是二元的(在册/不在册), 于是总纲承诺过的人只要开书那次没被
        写进 characters.md, 全书就再也不会出场 —— 实测一部水浒同人里,
        108 将除武松、鲁智深外全部零出场, 因为花名册只登记了 14 人。
        """
        return [nm for nm, _ in sc.unregistered(self.stages(),
                                                [c["name"] for c in self.roster()],
                                                self.name_aliases())][:24]

    _EN_OK = {"cpu", "dna", "gdp", "app", "kpi", "ceo", "cto"}

    def english_hits(self, t: str) -> List[str]:
        return [w for w in re.findall(r"[A-Za-z]{3,}", t)
                if w.lower() not in self._EN_OK]

    def fix_english(self, n: int, text: str, a: Dict[str, Any],
                    target: int, on_delta=None):
        """英文残留定点修复。

        这类错读者一眼就看见, 而且正确形态是语义判断（husband 该是「夫君」
        还是「官人」要看语境）, 所以不做机械替换, 让模型改; 但只准它改这几个词,
        字数变化超过 15% 就判定它顺手重写了, 弃用。
        """
        en = self.english_hits(text)
        if not en:
            return text, a
        prompt = (f"下面这章正文里混进了英文单词：{'、'.join(dict.fromkeys(en))}。\n"
                  f"请换成符合本书语境的中文，**其余一字不改**：不重写、不调段落、"
                  f"不改标点、不增删情节。\n直接输出修改后的完整正文，无前言。\n\n{text}")
        try:
            t2 = self.normalize_body(clean(call("polishing", prompt, on_delta,
                                                max_tokens=8192).text))
        except Exception as e:
            self._log(f"第{n}章英文修复失败: {str(e)[:40]}")
            return text, a
        c1 = len(re.findall(r"[一-鿿]", text))
        c2 = len(re.findall(r"[一-鿿]", t2))
        if not t2 or self.english_hits(t2) or abs(c2 - c1) > c1 * 0.15:
            self._log(f"第{n}章英文修复无效（{en}），保留原稿")
            return text, a
        a2 = audit(t2, extra_blacklist=self.hard_blacklist(), target_words=target,
                   check_modern=self.anachronism_check())
        self._log(f"第{n}章英文残留已改：{'、'.join(dict.fromkeys(en))}")
        return t2, a2

    def normalize_body(self, text: str) -> str:
        """成稿规范化 —— 确定性的格式问题，不该靠模型自觉，也不该靠人工校对。

        这几类错一旦写进成稿，读者一眼就看得见，而它们的正确形态是**唯一确定**的：
          · 正文里的 markdown 标题：章节正文是纯文本，`# 新官上任` 是格式漏出来的
          · 引号风格：一本书要么全用弯引号，要么全用直角引号，不能第 20 章突然换
        所以直接在落盘前改掉，而不是报个 issue 等模型下次注意。

        英文残留（husband / Discount）不在这里处理 —— 换成什么词是语义判断，
        猜错了比留着更糟，交给评审驱动的重写。
        """
        # 去掉正文里的 markdown 标题, 保留其文字内容（往往是小节名，有意义）
        text = re.sub(r"^#{1,6}\s+(\S.*)$", r"\1", text, flags=re.M)
        # 引号统一到全书主流风格
        style = self.quote_style()
        if style == "curly":
            text = (text.replace(chr(0x300C), chr(0x201C))
                        .replace(chr(0x300D), chr(0x201D)))
        elif style == "corner":
            text = (text.replace(chr(0x201C), chr(0x300C))
                        .replace(chr(0x201D), chr(0x300D)))
        return text

    def quote_style(self) -> str:
        """全书主流引号风格（前若干章说了算，后面的向它看齐）。"""
        done = sorted(self.p.state.get("done", []))[:12]
        c = k = 0
        for i in done:
            t = self.p.chapter(i)
            c += t.count(chr(0x201C))
            k += t.count(chr(0x300C))
        if c + k < 20:
            return ""
        return "curly" if c > k else "corner"

    def signature_guard(self) -> str:
        """本书自己长出来的高频动作 —— 人设标志用滥就成了口癖。

        「手指虚拨」是主角算账的招牌动作, 也是要在末章对账时回响的记忆点,
        所以不能进禁用表; 但实测 20 章里出现 40 次(7.8/万字), 每次决策都用,
        节奏就平了。这里只做提醒与限频, 不做硬禁。
        """
        done = sorted(self.p.state.get("done", []))[-12:]
        if len(done) < 4:
            return ""
        txt = "".join(self.p.chapter(i) for i in done)
        cn = max(1, len(re.findall(r"[一-鿿]", txt)))
        hot = []
        for m, name in ((r"[手指]{1,2}(?:在[^，。]{0,8})?虚拨", "手指虚拨"),
                        (r"眼皮(?:又)?抬了一下", "眼皮抬一下"),
                        (r"嘴角(?:勾起|扯出|挑了)", "嘴角动作"),
                        (r"喉结(?:滚|动)了", "喉结滚动")):
            c = len(re.findall(m, txt))
            if c / (cn / 10000) > 4:
                hot.append(f"{name}（近{len(done)}章 {c} 次）")
        if not hot:
            return ""
        return ("【标志动作已过密】" + "；".join(hot)
                + " —— 这些是人设记忆点不是禁用词，但本章最多用 1 次，"
                  "其余场合换同义表达（指头在袖里数 / 心里排了个次序 / "
                  "心里过了一遍账 / 眼神在账页上顿住）。")

    def tic_guard(self, n: Optional[int] = None) -> str:
        """把已写章节的问题反馈给下一章 —— 全书 + 邻章窗口双重视角。

        单章体检看不出问题 (1-2 次不触发阈值), 30 章连起来"嘴角勾起"43 次;
        而窗口视角能抓到"这三章都在同一场冲突里绕""开场和上一章雷同"这类
        全书统计也看不出的毛病。两者的结论都必须回流到生成端。
        """
        done = sorted(self.p.state.get("done", []))
        if len(done) < 3:
            return ""
        lines: List[str] = []

        chs = {i: self.p.chapter(i) for i in done[-40:]}
        tics = [t for t in book_audit(chs).get("tics", []) if t["count"] >= 6]
        if tics:
            lines.append("全书已用滥、本章禁止再出现的表达："
                         + "、".join(f"{t['tic']}({t['count']}次)" for t in tics[:10]))

        # 邻章窗口: 拿最近写完的一章做中心, 看它和前几章贴在一起有什么毛病
        last = done[-1]
        w = window_audit(chs, last, span=3,
                         outlines=self.p._load("chapter_outlines.json", {}))
        for i in w.get("issues", []):
            if i["type"] in ("与邻章重复度过高", "开场与邻章雷同"):
                lines.append(f"上一章已被判「{i['type']}」——本章必须换一种开场方式与场景切入点。")
            elif i["type"] == "剧情疑似原地踏步":
                lines.append("最近几章被判「原地踏步」——本章必须推进主线，"
                             "引入新地点/新人物/新矛盾，不许在同一场冲突里绕。")
            elif i["type"] == "角色断线":
                lines.append(f"注意角色连续性：{i['detail']}")
        return "\n".join(dict.fromkeys(lines))

    # ---------- 分层记忆装配 ----------
    #: 约束档位。约束堆到二十几块之后, 模型的注意力全花在合规上, 写出来的东西
    #: 「没错, 但也没意思」。而真正非有不可的其实只有三类:
    #:   剧情连续 / 不超纲 / 战斗力不崩塌
    #: minimal 只留这三类(外加命名注册表, 因为两个角色同名会让正文当场精神分裂),
    #: 其余全关。normal 是老行为。档位可写在项目 meta 或文风包里, 对所有书通用。
    MINIMAL_CONS = {"canon", "timeline", "roles", "terms", "power", "ledger",
                    "orgs", "names"}

    def con_level(self) -> str:
        return (self.p.meta.get("constraint_level")
                or self.style.get("constraintLevel") or "normal").lower()

    def con_on(self, key: str) -> bool:
        """这一块约束在当前档位下要不要发。"""
        lv = self.con_level()
        if lv == "minimal":
            return key in self.MINIMAL_CONS
        if lv == "off":
            return False
        return True

    def build_context(self, n: int, chapter_outline: str) -> Dict[str, Any]:
        """五层记忆 → 一份带预算报告的上下文. 见 memory_ctl.py."""
        mc = MemoryController(self.g["context_budget"], self.mcfg.get("layers"))

        resident = self.p.read("world_bible.md")
        brief = self.p.read("research_brief.md")
        if brief:
            resident += "\n\n【考据卡·硬事实，写到相关内容必须照此写】\n" + brief
        cast = self.cast_for(chapter_outline)
        if cast:
            resident += "\n\n【本章出场角色档案】\n" + cast
        roles_all = self.p.read("characters.md")
        if roles_all:
            # 这一块的唯一用途是「勿引入名单外新角色」—— 名单就够了。
            # 原来把 13 张整卡(4300+字)再塞一遍, 而出场角色的整卡 cast_for()
            # 已经给过, 等于每章为一句禁令花几千 token。
            import re as _re
            _names = _re.findall(r"^#{2,4}\s*\d+\.\s*姓名[:：]\s*(\S+)", roles_all, _re.M)
            if _names:
                resident += ("\n\n【本书全部角色名单（不得引入名单外的具名新角色；"
                             "名单内未出场者本章不必写）】\n" + "、".join(_names))
            else:
                resident += "\n\n【全部角色档案（备查，勿引入未列出的新角色）】\n" + roles_all
        graw = (self.genre.get("raw") or "")[:8000]
        if graw:
            resident += "\n\n【题材写作规范（完整版）】\n" + graw

        sm = self.p.state.get("summaries", {})
        k = self.mcfg.get("recent_chapters", 8)
        recent = [f"第{i}章：{sm[str(i)]}" for i in range(max(1, n - k), n) if str(i) in sm]
        # 只给一句话摘要, 模型接不住前文的文风、称谓、场景细节。
        # 窗口有近 10 万 token 而实测只用了 20%, 完全装得下最近几章原文。
        # 单章越长, 带的原文章数要越少, 否则 L2 必然溢出(实测 5750 字的书带 4 章
        # 原文直接把 30% 配额撑爆)
        full_k = int(self.mcfg.get("recent_full", 2) or 0)
        for i in range(max(1, n - full_k), n):
            body = self.p.chapter(i)
            if body:
                recent.append(f"\n———— 第{i}章 正文（承接文风与细节）————\n{body}")

        mid = [f.read_text(encoding="utf-8")
               for f in sorted((self.p.dir / "l2_summary").glob("*.md"))]

        recall, retr_info = [], {}
        if self.mcfg.get("enabled", True):
            try:
                rr = self.retriever.recall(chapter_outline, k=self.mcfg.get("top_k", 6))
                recall, retr_info = rr["items"], rr
            except Exception as e:            # 外部搜索挂了不能拖垮写作
                print(f"[retrieval] 降级为纯内部召回: {e}")
                recall = self.p.mem.search(chapter_outline, k=self.mcfg.get("top_k", 6))

        cons = []
        anchor = self.world_anchor()
        if anchor.get("mode") == "alt" and anchor.get("dynasty") and self.con_on("anchor"):
            cons.append(f"【朝代锚定·架空】本书朝代只叫「{anchor['dynasty']}」。"
                        f"绝对禁止出现真实朝代词：{'、'.join(anchor['forbidden'][:14])}。"
                        f"律法称「{anchor['dynasty']}律」，史书称「{anchor['dynasty']}史」。"
                        f"也不得出现真实历史人物（赵构、蔡京、岳飞、苏轼…），"
                        f"需要类似角色请用虚构姓名。")
        elif anchor.get("mode") == "real" and self.con_on("anchor"):
            cons.append("【史实锚定·正统历史】本书写的就是真实朝代，朝代名、官职、律法、"
                        "纪年一律用真实名称，不要自造国号。凡涉及具体年份、官职品级、"
                        "物价、器物，必须与背景资料一致；资料没有的宁可写模糊，不许编数字。")
        if anchor.get("main_place") and self.con_on("anchor"):
            cons.append(f"【主场锚定】主角常驻地是「{anchor['main_place']}」，"
                        f"不得随意把主场换到别的县城；确需异地必须写明行程。")
        cn = self.canon()
        if cn and self.con_on("canon"):
            recent_facts = cn[-40:]
            cons.append("【已确立的不可逆事实，绝对不得推翻】\n" + "；".join(
                f"{c['subject']}{c['fact']}(第{c['chapter']}章)" for c in recent_facts))
        era = self.era_card()
        if era and self.con_on("era"):
            cons.append("【时代红线·写进正文即穿帮】\n" + self.condense(era, 3000))
        guide = self.p.read("style_guide.md")
        if guide and self.con_on("guide"):
            cons.append("【本书写作守则·自审沉淀】\n" + self.condense(guide, 3000))
        alias = self.protagonist_alias()
        if alias and self.con_on("alias"):
            cons.append(alias)
        # 命名注册表 —— 实测第 12 章模型给骨科主任起名「赵德海」, 与主角死对头
        # (学生会副主席)同名, 正文当场精神分裂。新配角必须避开已用姓名。
        used_names = {c["name"] for c in self.roster()}
        used_names |= {c.get("subject", "") for c in self.canon()
                       if re.fullmatch(r"[一-鿿]{2,4}", c.get("subject", ""))}
        surnames = {n[0] for n in used_names if len(n) >= 2}
        if used_names and self.con_on("names"):
            cons.append("【命名注册表】已有角色："
                        + "、".join(sorted(used_names)[:24])
                        + "。新出现的任何配角（医生/官员/路人）绝不能与上述姓名"
                          "相同或高度相似；同姓可以，但名字必须完全不同。")
        lr = self.learned_rules()
        # 基底卡的红线 —— 模型为这本书判出来的, 比内置的七种预设更贴身
        red = self.basis_field("红线")
        if red and self.con_on("basis"):
            cons.append("【世界基底红线·写了即错】\n" + self.condense(red, 2000))
        loose = self.basis_field("可以放开")
        if loose and self.con_on("basis"):
            cons.append("【本书可以放开的地方】" + loose.replace("\n", " ")[:200])
        # 数字锁单独成块。canon 里其实记了「蒋家报价一千二百贯」, 但它混在 80 条
        # 散文体事实里, 模型扫过去不会逐条比对数字。数字要单拎出来、短、可扫。
        tm = self.p.state.get("terms", {}) or {}
        if tm and self.con_on("terms"):
            hot_tm = sorted(tm.items(), key=lambda kv: -kv[1].get("at", 0))[:12]
            cons.append("【数字锁·已定死的数字，不得改口】"
                        + "；".join(f"{k}={v['value']}（第{v['at']}章定）"
                                   for k, v in hot_tm)
                        + "。若剧情确需改动，必须在正文里明写「改标/重议/毁约」"
                        "并交代原因，不得静默换个数字。")
        spec = self.ledger_spec()
        pw = self.p.state.get("power", {}) or {}
        if pw and self.con_on("power"):
            hot = sorted(pw.items(), key=lambda kv: -kv[1].get("at", 0))[:10]
            cons.append(f"【{spec['power']['label']}·只进不退，不得凭空跃升或倒退】"
                        + "；".join(f"{nm}（第{v['at']}章）{v['state']}"
                                   for nm, v in hot))
        orgs = self.p.state.get("orgs", {}) or {}
        if orgs and self.con_on("orgs"):
            # 变量名别用 recent —— 本函数上面的 recent 是「最近章节原文」,
            # 覆盖掉会让记忆装配拿到势力元组而不是字符串（这类遮蔽栽过两次）
            hot_orgs = sorted((kv for kv in orgs.items() if isinstance(kv[1], dict)),
                              key=lambda kv: -int(kv[1].get("at") or 0))[:8]
            # 台账条目是模型抽出来的，字段随时可能缺 —— 用 .get 兜住，
            # 缺一个字段不该让整章写作崩掉
            cons.append("【势力现状·不得与此冲突】" + "；".join(
                f"{nm}（第{v.get('at', '?')}章）{v.get('state', '')}"
                for nm, v in hot_orgs))
        # 牌市模式下这条是**错的约束** —— 它强迫手上没牌的人回来刷存在感。
        # 实测原作里洪承畴消失 781 章、王承恩 1047 章都没问题, 因为那段时间
        # 他们的牌确实不值钱; 硬拉回来给「一句状态交代」只会稀释掉他重新登场时的分量。
        if (lr.get("must_appear") and self.style.get("threadDriver") != "cards"
                and self.con_on("stale")):
            cons.append("【断线角色必须回归】" + "、".join(lr["must_appear"][:5])
                        + " —— 接下来几章内安排他们出场并有实质戏份。")
        # 自审看到「档案里有、正文没出现」就判成废弃, 可那多半是还没轮到出场 ——
        # 朱允炆(皇太孙)要到靖难卷才登场, 被判废弃后就再也进不来了。而且 cast_for
        # 正在优先补位这些没登场的角色, 两个机制会在同一份提示词里打架
        # (实测苏婉儿同时出现在「本章出场角色」和「已废弃角色，不得再提」里)。
        # drop_roles 本意是清理写着写着冒出来的一次性路人, 不该动主创角色。
        # 判据用花名册而不是大纲文本 —— 大纲里写的是「皇太孙」, 名字「朱允炆」
        # 根本搜不到, 于是靖难卷的关键人物在第 100 章前就被判了死刑。
        designed = {c["name"] for c in self.roster()}
        drops = [r for r in (lr.get("drop_roles") or []) if r and r not in designed]
        late = [r for r in (lr.get("drop_roles") or []) if r in designed]
        if drops and self.con_on("drop"):
            cons.append("【已废弃角色，不得再提】" + "、".join(drops[:6]))
        if late and self.con_on("drop"):
            cons.append("【档案里设计了却迟迟未登场】" + "、".join(late[:6])
                        + " —— 他们不是废弃角色，是还没轮到；有合适时机就安排登场。")
        # 开篇去重 —— 自检报告「连续两章寅时三刻开篇」, 光靠事后判雷同没用,
        # 必须把上几章的开篇原样给模型看, 让它主动避开。
        done = sorted(self.p.state.get("done", []))
        heads = []
        for i in done[-3:]:
            first = next((ln.strip() for ln in self.p.chapter(i).splitlines()
                          if ln.strip()), "")
            if first:
                heads.append(f"第{i}章：{first[:36]}")
        if heads and self.con_on("opening"):
            cons.append("【开篇必须换花样】前几章是这样开场的——" + "；".join(heads)
                        + "。本章开篇的时间词、地点、句式、视角都不得与之雷同，"
                        "换一种切入方式（如直接对白、动作特写、他人视角）。")
        tg = self.tic_guard()
        if tg and self.con_on("tics"):
            cons.append("【口癖抑制】" + tg)
        sg = self.signature_guard()
        if sg and self.con_on("tics"):
            cons.append(sg)
        if self.con_on("quota"):
            cons.append("【配角配额】本章除主角外至少让 2 个配角有独立台词与动作，"
                        "配角不能只当背景板；不得给已知人物随意安排与其身份不符的官职。")
        # 登场过又长期消失的角色 —— 不是「从未出现」而是「出现完就没了」。
        # 实测潘金莲第 8 章被买回、第 9 章还在炉边坐着, 之后十几章一句没提,
        # 而主角明明欠着她一笔良心债（「义字那一档，他拨了三回」）, 人物就悬在半空。
        stale = self.stale_cast(n) if self.style.get("threadDriver") != "cards" else []
        if stale and self.con_on("stale"):
            cons.append("【出场过又断线的角色】" + "；".join(stale)
                        + " —— 他们出场过就消失了，读者还记得。"
                          "不必强行安排大戏，但要给一句状态交代（在做什么、什么处境）。")
        bl = self.blacklist()
        # 两类区别对待: 穿帮词是硬禁, 用滥的表达是限频 —— 一律硬禁会误伤正常写作
        hard = [w for w in bl if w in set(REAL_DYNASTIES) | set(
            self.learned_rules().get("forbidden_terms", [])[:0] or [])]
        anchor_forb = set(anchor.get("forbidden") or [])
        hard = [w for w in bl if w in anchor_forb]
        soft = [w for w in bl if w not in anchor_forb]
        if hard and self.con_on("blacklist"):
            cons.append("【绝不能出现】" + "、".join(hard[:20]))
        if soft and self.con_on("blacklist"):
            cons.append("【已被用滥的表达，本章最多出现 1 次，含变体】"
                        + "、".join(soft[:24]))
        # 分层注入 —— 之前永远取最早 6 条(FIFO), 而最早那几条恰恰是早期抽取
        # 不准的垃圾, 于是模型永远看不到真正该收的新伏笔, 回收率卡在 4%。
        pend = self.p.mem.pending_foreshadow() if self.con_on("foreshadow") else []
        if pend:
            cap = int(self.mcfg.get("foreshadow_show", 24))
            urgent = [f for f in pend if n - f["planted"] >= 30][:max(4, cap // 3)]
            active = [f for f in pend if n - f["planted"] < 12][-max(6, cap // 3):]
            midway = [f for f in pend if 12 <= n - f["planted"] < 30][:max(4, cap // 3)]
            show = list({f["id"]: f for f in urgent + active + midway}.values())
            if show:
                cons.append("【未回收伏笔】" + "；".join(
                    f"第{f['planted']}章「{f['text'][:36]}」" for f in show))
            if urgent:
                cons.append(f"【伏笔告急】埋了 30 章以上还没兑现，本章或接下来几章"
                            f"必须给个交代：" + "；".join(f"「{f['text'][:34]}」" for f in urgent))

        # 资金台账 —— 数值状态必须跨章衔接。实测第 37 章冒出 3500 万,
        # 第 38 章又缩回「两百多万本金」, 因为没人跟踪账目, 模型随手编数。
        ledger = self.p.state.get("ledger", {})
        if ledger and self.con_on("ledger"):
            last3 = sorted(ledger.items(), key=lambda kv: int(kv[0]))[-3:]
            cons.append(f"【{spec['resource']['label']}·数额必须与此衔接】" + "；".join(
                f"第{k}章：{v}" for k, v in last3)
                + "。本章出现的任何金额必须与上述期末规模连续，大额增减必须写明"
                  "来源或去向；禁止无由来的数量级跳变。")
        roles = self.p.state.get("roles", {})
        if roles and self.con_on("roles"):
            latest_roles = sorted(roles.items(), key=lambda kv: -kv[1].get("at", 0))[:8]
            cons.append("【角色当前状态·不得凭空改变】" + "；".join(
                f"{k}（第{v['at']}章）{v['state']}" for k, v in latest_roles))
        tl = self.p.state.get("timeline", {})
        if tl and self.con_on("timeline"):
            last = [f"第{k}章:{v}" for k, v in sorted(tl.items(), key=lambda x: int(x[0]))[-4:] if v]
            if last:
                cons.append("【时间线】" + "；".join(last)
                            + "。本章必须明确交代距上一章过了多久，不得时间跳跃无说明。")

        out = mc.assemble(outline=chapter_outline, resident=resident, recent=recent,
                          mid=mid, recall=recall, constraints="\n".join(cons))
        out["retrieval"] = {k: retr_info.get(k) for k in ("internal", "external", "needs")}
        return out

    def factions(self, rebuild: bool = False) -> List[Dict[str, Any]]:
        """会自己往前走的势力表 —— 横向扩散的燃料。

        误读/牌市/但是链都挂在主角身上，主角不动世界就不动。而原作里最耐读的
        一部分恰恰是「主角不在场时世界自己在走」：完颜宗构党争、大金父慈子孝、
        赵构在金陵一口气取四百武进士…… 主角回头一看，世界变了。
        """
        cached = self.p._load("factions.json", [])
        if cached and not rebuild:
            return cached
        try:
            fs = sc.build_factions(self.asset("outline.md"),
                                   [c["name"] for c in self.roster()],
                                   self.p.meta.get("title", ""),
                                   self._ask_planner)
        except Exception as e:
            print(f"[factions] 生成失败: {e}")
            return cached or []
        if fs:
            self.p.write("factions.json", json.dumps(fs, ensure_ascii=False, indent=2))
            self._log("势力 %d 家：%s" % (len(fs), "、".join(f["name"] for f in fs)))
        return fs

    @staticmethod
    def _same_move(a: str, b: str) -> bool:
        """两次解法算不算同一路数 —— 字面二元组重合过半就算。

        模型即使被要求「同一路数用同样的说法」也会飘（「肉身硬抗法器」→
        「以肉身硬接法器」）。全等匹配会让作废算子永远不触发，所以做模糊归并。
        """
        ga = {a[i:i + 2] for i in range(len(a) - 1)}
        gb = {b[i:i + 2] for i in range(len(b) - 1)}
        if not ga or not gb:
            return a == b
        return len(ga & gb) / min(len(ga), len(gb)) >= 0.5

    def stale_method(self, n: int) -> str:
        """作废算子（纵向）: 反复奏效的那一招该过期了。

        误读管「一变多」、代价管「一变二」、自转管横向，这一条管**纵向**：
        让时间在主角身上留下痕迹，而不是能力一路叠加。频率由引擎层 obsolete 定。
        """
        cfg = self.style.get("obsolete") or {}
        after = int(cfg.get("afterUses") or 0)
        if not after:
            return ""
        ms = [m for m in (self.p.state.get("methods") or []) if isinstance(m, dict)]
        if not ms:
            return ""
        expired = self.p.state.setdefault("expired_methods", [])
        cooldown = int(cfg.get("cooldown") or 12)
        # 刚下过作废令就别接着下, 否则每一批细纲都在写主角失手
        if any(n - int(e.get("at") or 0) < cooldown for e in expired):
            return ""
        groups: List[List[Dict[str, Any]]] = []
        for m in ms:
            for g in groups:
                if self._same_move(g[0]["method"], m.get("method", "")):
                    g.append(m); break
            else:
                groups.append([m])
        for g in sorted(groups, key=lambda g: -len(g)):
            if len(g) < after:
                continue
            name = g[0]["method"]
            if any(self._same_move(name, e.get("method", "")) for e in expired):
                continue
            if len(expired) >= int(cfg.get("cap") or 2) * 99:   # cap 留给单批条数
                break
            expired.append({"at": n, "method": name})
            self.p.save()
            self._log(f"作废令: 「{name}」已连奏效 {len(g)} 次, 本批细纲要让它失灵一次")
            return sc.obsolete_prompt(name, g[-int(after):], n)
        return ""

    def reshell(self, n: int) -> str:
        """换壳算子（重启）: 卷末收走主角借来的位置, 下一卷换赛道。"""
        cfg = self.style.get("reshell") or {}
        tail = int(cfg.get("tailChapters") or 0)
        if not tail:
            return ""
        cur = self.volume_of(n)
        if not cur or int(cur.get("index") or 1) < int(cfg.get("minVolume") or 1):
            return ""
        left = int(cur["end"]) - n + 1
        if left > tail or left <= 0:
            return ""
        who = (self.alias_pair() or [None])[0] or (
            self.roster()[0]["name"] if self.roster() else "")
        ident = (self.p.state.get("identity") or {}).get(who) or {}
        shell = ident.get("now") or ""
        if not shell:
            rl = (self.p.state.get("roles") or {}).get(who) or {}
            shell = str(rl.get("state") or "")[:60]
        self._log(f"换壳令: 第{cur['index']}卷还剩 {left} 章, 卷末要收走「{shell or '当前位置'}」")
        return sc.reshell_prompt(cur, shell, left, n)

    def milestone_ctx(self, n: int) -> str:
        """本章所属里程碑的合同 + 左右节 —— 排纲的主供料。

        这是树式分解的全部收益落地点: 与全书长度无关的几千字, 既有「从哪来」
        (本节进口), 也有「必须走到哪」(本节出口)和「下一节从哪开始」——
        伏笔有方向了(上一本 496 条埋、197 条没人收, 就是因为没有方向)。
        """
        f = self.p.dir / "tree.json"
        if not f.exists():
            return ""
        try:
            import server.tree as tr
            nodes = {k: tr.Node.from_dict(v) for k, v in
                     json.loads(f.read_text(encoding="utf-8")).items()}
        except Exception as e:
            self._log(f"[tree] 读取失败: {e}")
            return ""
        ms = sorted((x for x in nodes.values() if x.id != "R"),
                    key=lambda x: x.start)
        cur = next((m for m in ms if m.start <= n <= m.end), None)
        if not cur:
            return ""
        i = ms.index(cur)
        left = ms[i - 1] if i > 0 else None
        right = ms[i + 1] if i + 1 < len(ms) else None
        out = [f"🧭【本节里程碑·第{cur.start}-{cur.end}章「{cur.title}」】",
               f"　本节解决：{cur.solves}",
               f"　但是（本节的解法必须生出这个新问题）：{cur.exposes}",
               (f"　本节关键错算：{cur.line}" if cur.line else ""),
               f"　进这节时：{cur.entry.brief(400)}",
               f"　出这节时必须是：{cur.exit.brief(400)}"]
        if left:
            out.append(f"　上一节「{left.title}」刚解决了：{left.solves[:40]}")
        if right:
            out.append(f"　下一节「{right.title}」要解决的是：{right.solves[:40]} —— "
                       f"本节埋的伏笔往这个方向埋。")
        pos = n - cur.start + 1
        total = cur.end - cur.start + 1
        out.append(f"　本章是本节的第 {pos}/{total} 章"
                   + ("，**该开始收口对齐出口账本了**。" if pos > total * 0.7 else "。"))
        return "\n".join(x for x in out if x)

    def world_turn(self, n: int) -> str:
        """每隔几章让各势力各走一步 —— 完全不管主角在干什么。

        结果存进 state["world_turns"]，并作为下一批排纲的燃料。
        频率由文风包的 worldTurn.every 决定；没配就不跑。
        """
        cfg = self.style.get("worldTurn") or {}
        every = int(cfg.get("every") or 0)
        if not every:
            return ""
        turns = self.p.state.setdefault("world_turns", {})
        last = max((int(k) for k in turns), default=0)
        if n - last < every and turns:
            return turns.get(str(last), "")
        fs = self.factions()
        if not fs:
            return ""
        clock = self.basis_field("锚点") or ""
        try:
            r = call("planning", sc.world_turn_prompt(fs, n, clock[:160],
                                                     f"{n - last} 章" if last else ""),
                     max_tokens=int(self.g.get("max_tokens_plan") or 8000))
            txt = clean(r.text)
        except Exception as e:
            print(f"[world_turn] 跳过: {e}")
            return turns.get(str(last), "")
        if not txt:
            return turns.get(str(last), "")
        turns[str(n)] = txt[:2600]
        for f in fs:
            f["last_turn"] = n
        self.p.write("factions.json", json.dumps(fs, ensure_ascii=False, indent=2))
        self._log(f"世界回合 @第{n}章：各势力各走一步（{len(fs)} 家）")
        return turns[str(n)]

    def live_misreads(self, n: int, cap: int = 5) -> str:
        """还没被戳破的误读 —— 当**燃料**用，不是当禁令用。

        现有 build_context 塞了二十多个约束块，几乎全是「不得／禁止／必须衔接」。
        禁令能防倒退，不能产生推进。误读正相反：它是这类书主要的情节发生器
        （一个动作 × N 个误读者 = N 条新支线），所以要放在提示词**前部**，
        用「所以接下来会发生什么」的语气，而不是塞进「必守约束」里。
        """
        def _at(m, dflt: int = 0) -> int:
            # 台账是模型抽出来的, 字段随时可能缺或脏。这里炸掉会**打断整章写作**,
            # 而它只是用来排个序 —— 兜住, 别让一条烂数据毁掉一章。
            try:
                return int(m.get("at") or dflt)
            except (TypeError, ValueError):
                return dflt

        mis = [m for m in (self.p.state.get("misreads") or [])
               if isinstance(m, dict) and not m.get("closed_at") and m.get("concludes")]
        if not mis:
            return ""
        mis = sorted(mis, key=lambda m: -_at(m))[:cap]
        lines = []
        for m in mis:
            age = n - _at(m, n)
            # 阈值原来写死 25 章 —— 而原著误读极少活过 5 章。25 章才提醒,
            # 等于默认允许挂 24 章, 提醒来得比病晚太多。
            _old = int((self.style.get("misreadLifecycle") or {}).get("上限") or 25)
            tag = "（埋了 %d 章了，该结账了）" % age if age >= _old else ""
            # 缺字段的条目别印成「凭「」…于是「」」这种残句 —— 有什么写什么
            bits = []
            # 台账字段是模型写的, 长起来没边(实测五条燃料 5.8k 字, 是细纲硬约束
            # 块的五倍)。燃料要的是「谁信着什么错」这一句, 不是把当时的场景复述一遍。
            _c = lambda v, cap=60: (v[:cap] + "…") if len(v) > cap else v
            if m.get("because"):
                bits.append(f"凭「{_c(m['because'])}」")
            bits.append(f"认定「{_c(m.get('concludes',''))}」")
            if m.get("acts"):
                bits.append(f"于是「{_c(m['acts'])}」")
            lines.append(f"· {m.get('who') or '有人'}：" + "，".join(bits) + tag)
        # 实测原著误读寿命 1-3 章、极少超过 5, 靠「行动撞墙」戳破而不是口头解释。
        # 原来这里写的是「本章也不必让他们知道」—— 等于鼓励一直挂着,
        # 结果生成书的误会平均活 4.7 章、最长 23 章, 挂成了没人管的死账。
        lc = self.style.get("misreadLifecycle") or {}
        cap_ch = int(lc.get("上限") or 0)
        tail = ""
        if cap_ch:
            tail = ("\n\n⚠ 误会不是用来一直挂着的悬念：每一条挂上账后 "
                    f"{'-'.join(str(x) for x in (lc.get('发酵章数') or [1, 3]))} 章内"
                    f"必须**促成一个错误行动**，{cap_ch} 章之内必须撞墙戳破。\n"
                    "　戳破靠" + str(lc.get("戳破方式") or "行动撞墙") + "，不靠谁开口解释。\n"
                    "　上面标了「该结账了」的，本批就结掉。")
        return ("这些人现在都**信着一个错的东西**，而且正照着它行动。\n"
                + "\n".join(lines) + tail
                + "\n他们各自的下一步，会把局面推到主角没打算去的地方。"
                  "本章要么让其中一条继续发酵，要么让一条被当众戳破。")

    def window_feedback(self, n: int) -> str:
        """最近一个窗口的文体漂移，写成给下一章的两条纠偏指令。

        「约束抢配额」的解法：结构件八条全压在每一章上，模型必然拆东墙补西墙
        （实测与原作真章的平均相对差 27%）；只留常驻三条又会塌（39%）；
        常驻三条 + 本方法按实测漂移动态补两条 = **16%**。
        窗口长度与指标区间都在文风包的 windowFeedback 里，包没配就返回空串。
        """
        wf = (self.style.get("windowFeedback") or {})
        w = int(wf.get("window") or 0)
        if not w:
            return ""
        done = sorted(x for x in self.p.state.get("done", []) if x < n)
        if len(done) < max(3, w // 3):      # 刚开书没样本，别拿两三章的噪声去纠偏
            return ""
        texts = [self.p.chapter(i) for i in done[-w:]]
        try:
            return window_drift([t for t in texts if t], self.style)
        except Exception as e:              # 度量出问题不该拖垮写作
            print(f"[window] 漂移计算跳过: {e}")
            return ""

    def prev_summary(self, n: int, k: int | None = None) -> str:
        k = k or self.mcfg.get("recent_chapters", 3)
        """最近 k 章摘要 + 所属 L2 段摘要 —— 长篇控 context 的关键."""
        out = []
        l2 = sorted((self.p.dir / "l2_summary").glob("*.md"))
        if l2:
            out.append("【前情大纲】\n" + "\n".join(f.read_text(encoding="utf-8") for f in l2[-2:]))
        sm = self.p.state.get("summaries", {})
        recent = [f"第{i}章：{sm[str(i)]}" for i in range(max(1, n - k), n) if str(i) in sm]
        if recent:
            out.append("【最近章节】\n" + "\n".join(recent))
        return "\n\n".join(out)

    def recall(self, query: str) -> str:
        """多记忆索引召回: 从世界观/角色/往期剧情/伏笔里找回相关片段.

        这是长篇写到几百章后仍能保持一致性的关键 —— 最近摘要覆盖不到的远期细节
        (第 30 章埋的伏笔、只出场过一次的配角) 只能靠检索找回来.
        """
        if not self.mcfg.get("enabled", True):
            return ""
        hits = self.p.mem.search(query, k=self.mcfg.get("top_k", 6))
        if not hits:
            return ""
        label = {"world": "世界观", "role": "角色", "plot": "往期剧情", "fore": "伏笔"}
        lines = [f"[{label.get(h['kind'], h['kind'])}] {h['title']}: {h['text'][:300]}"
                 for h in hits]
        pend = self.p.mem.pending_foreshadow()
        if pend:
            lines.append("[未回收伏笔] " + "；".join(
                f"第{f['planted']}章「{f['text'][:40]}」" for f in pend[:5]))
        return "\n".join(lines)

    def reindex(self) -> Dict[str, int]:
        """把世界观/角色档案重新灌进记忆索引."""
        wb, ch = self.p.read("world_bible.md"), self.p.read("characters.md")
        if wb:
            self.p.mem.index_document("world", "world_bible", wb)
        if ch:
            self.p.mem.index_document("role", "characters", ch)
        return self.p.mem.stats()

    def builtin_prompt(self, key: str) -> str:
        """内置模板全文（按本书历史模式拼上对应硬约束）。"""
        t = BUILTIN_PROMPTS.get(key, "")
        mode = self.history_mode()
        # 七种预设都不贴切时(mode == custom), 规则来自模型为本书判出的基底卡 ——
        # 恋爱国度、赛博废土、克苏鲁…… 靠枚举永远补不完, 这条兜底才是通用机制
        if mode == "custom":
            red = self.basis_field("红线")
            anchor = self.basis_field("锚点")
            extra = ("【本书世界基底】" + anchor + "\n【硬红线，写了即错】\n" + red
                     + "\n\n直接输出，无前言。") if red else MODE_RULES["invented"]
            return t + extra
        if key == "world_bible":
            t += MODE_RULES.get(mode, MODE_RULES["invented"])
        elif key == "characters":
            t += CHAR_MODE_RULES.get(mode, CHAR_MODE_RULES["invented"])
            t += "直接输出，无前言。"
        return t

    def facts_block(self, bg: str, scope: str = "器物、官制、物价、风俗",
                    limit: int = 6000) -> str:
        """把检索资料拼成提示词块，按历史模式给出正确的使用说明。

        「资料里的朝代名不得出现在成稿里」只对架空成立。real 模式写的就是真实
        朝代，检索回来的「洪武」「都察院」正是要写进去的东西；写死这句话等于把
        考据结果扔了。这类模式相关的措辞必须收在一处，散在各步骤里迟早又漏一个。
        """
        if not bg:
            return ""
        if self.history_mode() == "alt":
            note = (f"只用来保证{scope}合理，"
                    f"资料里的朝代名与专有名词一律不得出现在成稿里")
        elif self.history_mode() == "real":
            note = (f"本书写的就是这个朝代，资料里的年号、官职、地名、{scope}"
                    f"请直接采用；拿不准的宁可写模糊，不要编造数字")
        else:
            note = f"用来保证{scope}贴近现实，别照抄原文措辞"
        return f"\n\n【现实参考资料 —— {note}】\n" + bg[:limit]

    # ---------- 步骤 ----------
    def step_naming(self, on_delta=None) -> Dict[str, Any]:
        """起书名与写简介 —— 平台上决定点击率的两样东西。

        「西门庆的生意经」这种名字信息量够但太素：读者扫过书城列表时，
        三秒内要知道「谁 + 逆什么境 + 爽在哪」。简介同理，番茄的转化几乎
        全靠前三行。所以这一步单独做，且给多个候选让人挑。
        """
        f = self.p.meta.get("fields", {}) or {}
        style_name = self.style.get("name", "")
        prompt = (
            f"你是网文平台的编辑，专门给书起名、写简介。\n\n"
            f"【题材】{self.genre.get('name','')} · {style_name}\n"
            f"【一句话故事】{f.get('premise','')}\n"
            f"【背景与主线】{self.era_brief(700)}\n"
            f"【当前书名】{self.p.meta.get('title','')}（可能太素，需要更好的）\n\n"
            f"先想清楚：读者在书城列表里扫过去，**三秒内**要看懂"
            f"「主角是谁、逆的什么境、爽在哪」。\n\n"
            f"输出（严格照格式，不要多余文字）：\n"
            f"书名候选：（5 个，一行一个「- 」开头。要求：\n"
            f"  · 8 字以内，读者一眼知道是什么故事\n"
            f"  · 至少 2 个带钩子（身份反差 / 悬念 / 冲突），"
            f"至少 1 个走「大俗大雅」路线\n"
            f"  · 不要「之」字堆砌，不要「重生之XX的XX人生」这种烂大街句式\n"
            f"  · 每个后面用括号注一句为什么这么起）\n"
            f"一句话简介：（30 字以内，用来做书城的副标题。"
            f"必须点出身份反差与最大的那个悬念）\n"
            f"平台简介：（150-220 字，分 3 段，每段 1-2 句：\n"
            f"  第一段：开局的死局 —— 主角眼下最要命的麻烦是什么\n"
            f"  第二段：他凭什么破局 —— 金手指与打法，要具体\n"
            f"  第三段：钩子 —— 抛出一个读者非想知道不可的问题，"
            f"以问句或悬念句收尾。\n"
            f"  忌讳：不要剧透终局、不要「一场惊天阴谋」这类空话、"
            f"不要形容词堆砌）\n"
            f"标签：（6-8 个平台标签，顿号分隔，如：历史同人、种田经商、"
            f"权谋、扮猪吃虎）\n")
        r = call("planning", prompt, on_delta, max_tokens=1500)
        card = clean(r.text)
        if card:
            self.p.write("naming.md", card)
            self._log(f"书名与简介 {len(card)} 字")
        return {"text": card}

    def step_world_bible(self, on_delta=None) -> str:
        ctx = self.base_ctx()
        ov = self.prompt_override("world_bible")
        prompt = render(ov or self.builtin_prompt("world_bible"), ctx)
        bg = self.sanitize_facts(
            self.ground("world", context=ctx.get("premise", "") + "\n" + ctx.get("background", "")))
        prompt += self.facts_block(bg)
        r = call("planning", prompt, on_delta, max_tokens=int(self.g.get("max_tokens_outline") or 8000))
        wb = clean(r.text)
        self.p.write("world_bible.md", wb)
        n = self.p.mem.index_document("world", "world_bible", wb)
        self._log(f"世界观 {len(wb)} 字 / {r.elapsed:.1f}s / 入索引 {n} 条")
        return wb

    def step_characters(self, on_delta=None) -> str:
        ctx = self.base_ctx()
        ctx["world_bible"] = self.p.read("world_bible.md")
        ov = self.prompt_override("characters")
        prompt = render(ov or self.builtin_prompt("characters"), ctx)
        bg = self.sanitize_facts(self.ground("cast", context=self.asset("world_bible.md")))
        prompt += self.facts_block(bg, scope="姓名、称谓、职业、阶层、女性处境",
                                   limit=5000)
        # 声音卡把每张角色卡从 6 栏加到 12 栏，4000 的上限装不下十来个角色 ——
        # 实测最后一个角色停在「**原声」三个字上，缺了三栏。
        # 加了字段就得同步提预算，这是同一个坑第三次（细纲批量、审阅、这里）。
        r = call("planning", prompt, on_delta,
                 max_tokens=int(self.g.get("max_tokens_outline") or 8000))
        ch = clean(r.text)
        ch = self._complete_characters(ch, prompt, on_delta)
        self.p.write("characters.md", ch)
        n = self.p.mem.index_document("role", "characters", ch)
        self._log(f"角色档案 {len(ch)} 字 / {r.elapsed:.1f}s / 入索引 {n} 条")
        return ch

    #: 每张角色卡必须齐的栏目。缺栏 = 被截断（模型不会写一半就换人）。
    _CARD_FIELDS = ("身份", "核心动机", "与主角关系", "自称", "口头禅",
                    "语感", "原声样本", "禁用词", "结局走向")

    def _card_gaps(self, text: str) -> List[str]:
        """哪几个角色的卡不完整。"""
        blocks = re.split(r"\n(?=#{2,4}\s*\d+\s*[.、]\s*姓名)", text or "")
        out = []
        for b in blocks[1:] if len(blocks) > 1 else blocks:
            m = re.search(r"姓名\s*[:：]\s*([^\n]+)", b)
            if not m:
                continue
            lack = [f for f in self._CARD_FIELDS
                    if not re.search(rf"\*?\*?{f}\*?\*?\s*[:：]\s*\S", b)]
            if lack:
                out.append(f"{m.group(1).strip()[:12]}（缺 {'、'.join(lack)}）")
        return out

    def _complete_characters(self, ch: str, prompt: str, on_delta=None) -> str:
        """截断了就续写，不整篇重来。

        整篇重生成会把已经写好的九张卡也换掉（实测那九张质量很好），
        而且再截一次的概率一样大。只补缺的那几张，接在后面。
        """
        gaps = self._card_gaps(ch)
        if not gaps:
            return ch
        self._log(f"角色档案不完整：{'；'.join(gaps[:3])} —— 续写补齐")
        tail = ch.rstrip()
        # 截断的那一张整段丢掉，从它的标题处截断，让模型重写这一张
        m = list(re.finditer(r"\n(?=#{2,4}\s*\d+\s*[.、]\s*姓名)", tail))
        if m:
            tail = tail[:m[-1].start()].rstrip()
        ask = (prompt + "\n\n【已经写好的部分，不要重复】\n" + self.condense(tail, 4000)
               + f"\n\n上面还差 {len(gaps)} 个角色（含被截断的那个）。"
               f"**只补这几个**，从「### N. 姓名：」开始接着写，"
               f"每张卡十二栏一栏不能少，不要重写前面已有的角色。")
        try:
            more = clean(call("planning", ask, on_delta,
                              max_tokens=int(self.g.get("max_tokens_outline") or 8000)).text)
        except Exception as e:
            self._log(f"角色档案续写失败: {e}")
            return ch
        merged = tail + "\n\n" + more.strip()
        if self._card_gaps(merged):
            self._log(f"续写后仍不完整：{'；'.join(self._card_gaps(merged)[:2])}")
        return merged

    def fix_scale(self, text: str) -> str:
        """纠正总纲里模型自己编的体量数字。

        提示词里明明传了「全书 231 章 / 约 60 万字」，模型照样在抬头写
        「体量：约 180 万字」。这个数字会被分卷与节奏表当依据，一错全错。
        文本层直接改写成真实值，比指望模型不写错可靠。
        """
        tw = int(self.p.meta.get("target_words") or 0)
        tc = int(self.p.meta.get("target_chapters") or 0)
        if not tw:
            return text
        want_w = f"{tw // 10000} 万字" if tw >= 10000 else f"{tw} 字"
        fixed, n = re.subn(r"(?:约\s*)?\d+(?:\.\d+)?\s*万字", "约 " + want_w, text)
        if tc:
            fixed, n2 = re.subn(r"全书\s*\d{2,4}\s*章", f"全书 {tc} 章", fixed)
            n += n2
        if n:
            self._log(f"总纲体量已校正为 {want_w}/{tc}章（模型写错 {n} 处）")
        return fixed

    def step_outline(self, on_delta=None) -> str:
        lvl = self.type["levels"][0]
        ctx = self.base_ctx()
        ctx["world_bible"] = self.p.read("world_bible.md")
        ctx["characters"] = self.p.read("characters.md")
        ov = self.prompt_override("outline")
        prompt = render(ov or lvl["prompt"], ctx)
        # 总纲用的是**类型包里的模板**, 不在 BUILTIN_PROMPTS 里 —— 于是铁律和
        # 开篇硬指标都进不来。实测: 铁律要求「第1章亮枪、前3章当街打死一个人」,
        # 总纲把卷一写成《死局》, 情感曲线「恐惧→算计→侥幸」, 全文没有一处
        # 「第一枪/打死/当街」。总纲定的是分卷骨架, 它不炸, 细纲再努力也白搭。
        # 不改类型包(那会影响所有书), 在这一步补。
        extra = [x for x in (
            "\n\n【本书铁律 —— 分卷与开篇必须与之一致，冲突时以铁律为准】\n"
            + "\n".join(f"- {r_}" for r_ in self.hard_rules()) if self.hard_rules() else "",
            "\n\n" + dl.opening_spec(self.dials(), 1),
        ) if x and str(x).strip()]
        prompt += "".join(extra)
        r = call("planning", prompt, on_delta, max_tokens=int(self.g.get("max_tokens_outline") or 8000))
        ol = self.fix_scale(clean(r.text))
        self.p.write("outline.md", ol)
        self._log(f"总纲 {len(ol)} 字 / {r.elapsed:.1f}s")
        return ol

    def step_volumes(self, on_delta=None) -> List[Dict[str, Any]]:
        """把全书拆成卷 —— 192 章直接从总纲跳到分章，中层节奏必然散。

        每卷给出：卷名 / 起止章 / 本卷主线 / 本卷高潮 / 卷末钩子 / 本卷主要出场角色。
        写分章细纲时只带所属卷的信息，而不是把整本总纲怼进去。
        """
        cached = self.p._load("volumes.json", None)
        if cached:
            return cached
        total = self.p.meta.get("target_chapters", 0)
        per = max(20, min(50, total // max(3, round(total / 40)) if total else 40))
        n_vol = max(2, round(total / per)) if total else 4
        anchor = self.world_anchor()
        # 「但是链」：每卷两栏 —— 这一卷解决什么、解决之后**新长出**什么问题。
        # 硬约束 volume[i].exposes == volume[i+1].solves。加上这条之后，
        # 「主角越来越强」这种结构在物理上就写不出来了：每一卷必须先把上一卷的
        # 解法作废掉。这是防止长篇写到一百章开始原地打转的唯一结构性手段。
        chain = bool(self.style.get("volumeChain"))
        chain_fmt = ("本卷解决：（这一卷把什么问题解决掉了，一句话）\n"
                     "解决之后暴露：（正因为用这个办法解决了，**新长出**什么问题？"
                     "一句话。必须是上一栏的解法本身带来的后果，不是另起炉灶的新麻烦）\n"
                     "\n⚠ 硬约束：**第 i 卷的「解决之后暴露」必须原样成为第 i+1 卷的"
                     "「本卷解决」**。照抄那一句，不许换说法。\n"
                     "  这意味着每一卷的解法都要让上一卷的解法失效 —— "
                     "不许写成能力叠加（更强的功法、更大的地盘、更多的兵）。\n"
                     "  例：活命→没名分｜名分→没钱没地｜钱和地→得罪整个士大夫阶级｜"
                     "府兵火器→天下三分打不动｜技术碾压→天下变大了｜"
                     "走出去→没有意识形态撑不住\n") if chain else ""
        prompt = (
            f"为《{self.p.meta.get('title','')}》做分卷。全书 {total} 章，分 {n_vol} 卷。\n\n"
            f"#总纲\n{self.p.read('outline.md')[:4000]}\n\n"
            f"#可用角色\n{'、'.join(c['name'] for c in self.roster()) or '未定'}\n\n"
            f"#题材节奏要求\n{self.genre_rules()[:800]}\n\n"
            + (f"#锚定\n朝代只叫「{anchor['dynasty']}」\n\n" if anchor.get("dynasty") else "")
            + f"每卷严格按此格式，卷之间用一行 ###fenge 分隔：\n"
              f"卷名：…\n章节范围：第X章-第Y章\n本卷主线：…\n"
              f"本卷高潮：（具体事件）\n卷末钩子：…\n主要出场：（3-6 个角色名）\n"
              f"实力/地位变化：（主角从什么状态到什么状态）\n"
            + (chain_fmt if chain_fmt else "")
            + f"\n要求：卷与卷之间要有明显的格局升级，不能原地打转。直接输出，无前言。")
        r = call("planning", prompt, on_delta, max_tokens=int(self.g.get("max_tokens_outline") or 8000))
        vols: List[Dict[str, Any]] = []
        cur = 1
        for blk in [clean(x) for x in re.split(r"###fenge", r.text) if x.strip()]:
            m = re.search(r"章节范围[^\d]*(\d+)\D+(\d+)", blk)
            a, b = (int(m.group(1)), int(m.group(2))) if m else (cur, cur + per - 1)
            name = (re.search(r"卷名\s*[:：]\s*(.+)", blk) or [None, f"第{len(vols)+1}卷"])[1]
            fld = lambda k: (re.search(rf"^{k}\s*[:：]\s*(.+)$", blk, re.M) or [None, ""])[1].strip()
            vols.append({"index": len(vols) + 1, "name": str(name).strip()[:30],
                         "start": a, "end": b, "text": blk,
                         "solves": fld("本卷解决")[:80],
                         "exposes": fld("解决之后暴露")[:80]})
            cur = b + 1
        if chain and len(vols) > 1:
            bad = self._check_volume_chain(vols)
            if bad:
                # 只告警不拒收：纯字面判断会误伤合法的语义链条
                # （「没有意识形态撑不住」→「造一套天理出来」一个字都不重合）
                self._log("⚠ 但是链可能没咬合（字面判断，可能误报）：" + "；".join(bad[:3]))
        if vols:
            self.p.write("volumes.json", json.dumps(vols, ensure_ascii=False, indent=2))
            self.p.mem.index_document("world", "volumes", r.text)
        self._log(f"分卷 {len(vols)} 卷 / {r.elapsed:.1f}s")
        return vols

    @staticmethod
    def _check_volume_chain(vols: List[Dict[str, Any]]) -> List[str]:
        """校验「但是链」是否咬合：第 i 卷暴露的问题，必须是第 i+1 卷要解决的。

        不做字符串全等 —— 模型总会换个说法。用**重合系数**（交集 / 较短那个集合）
        而不是交并比：拿真实的链条标定过，
            他没有名分→拿到名分      交并比 0.29  重合系数 0.50
            没钱没地→拿到钱和地       0.33 / 0.67
            得罪整个士大夫阶级→被士大夫阶级反扑  0.42 / 0.62
            他没有名分→北伐中原收复燕云  0.00 / 0.00
        交并比在「该咬合」那一侧只有 0.29~0.42，离噪声太近；重合系数是 0.50~0.67，
        与「不该咬合」的 0.00 完全分开，取 0.40 作阈。

        **这只是告警，不是拒收。** 纯字面判断必然误伤合法的语义链条 ——
        原作里「没有意识形态撑不住」→「造一套天理出来」是真链条，却一个字都不重合。
        所以这里只写日志给人看，不阻断生成。
        """
        def sim(a: str, b: str) -> float:
            sa = {c for c in a if '\u4e00' <= c <= '\u9fff'}
            sb = {c for c in b if '\u4e00' <= c <= '\u9fff'}
            if not sa or not sb:
                return 0.0
            return len(sa & sb) / min(len(sa), len(sb))

        bad = []
        for i in range(len(vols) - 1):
            e, nxt = vols[i].get("exposes", ""), vols[i + 1].get("solves", "")
            if not e or not nxt:
                bad.append(f"第{i+1}→{i+2}卷：缺「解决之后暴露」或「本卷解决」")
            elif sim(e, nxt) < 0.40:
                bad.append(f"第{i+1}卷暴露「{e[:22]}」，第{i+2}卷却去解决"
                           f"「{nxt[:22]}」—— 另起炉灶了")
        return bad

    def volume_of(self, n: int) -> Dict[str, Any]:
        for v in (self.p._load("volumes.json", []) or []):
            if v["start"] <= n <= v["end"]:
                return v
        return {}

    # 细纲生成的输入预算。理论上 128k 窗口能塞 60k tokens，但**网关对单次
    # 请求体有写入耐受上限** —— 实测排到第 160 章时已排细纲累积到 9.6 万字符，
    # 连同总纲与卷一起发出去约 66k tok，连续 7 次 write operation timed out。
    # 上下文装得下 ≠ 一次发得过去。留到 40k 字符（约 30k tok）稳定。
    OUTLINE_INPUT_CHARS = 40000

    @staticmethod
    def clean_outline(t: str) -> str:
        """清掉细纲里混进来的系统标记与分隔符。

        喂给模型的分隔符会被它当成格式学走：实测「—— 第N章 ——」原样出现在
        15 章的细纲正文开头。标记是给流水线看的，不该进产物。
        """
        t = re.sub(r"^\s*(?:\[\[CH\d+\]\]|——\s*第\d+章\s*——|###fenge|#{3,})\s*$",
                   "", t, flags=re.M)
        # 章标题前的 markdown 井号（「### 第298章雪里的弓」）
        t = re.sub(r"^[ \t]*#{1,6}[ \t]*(?=第\s*\d{1,4}\s*章)", "", t, flags=re.M)
        # 模型爱给整批加一个 markdown 大标题, 而分段切开后它就落在首章头上 ——
        # 实测「# 《大宋奸商西门庆》第161-178章细纲」被当成第 161 章的正文存了进去。
        t = re.sub(r"^\s*#{1,6}\s*《?[^\n]{0,40}?》?\s*第\s*\d+\s*[-—~至]\s*\d+\s*"
                   r"章[^\n]{0,12}\s*$", "", t, flags=re.M)
        # 字段行前面粘的小段说明：模型偶尔写成「出处补正，出场角色：…」，
        # 行首锚定的守卫一查就判缺字段。前缀短、且以逗号顿号收尾的，直接削掉。
        t = re.sub(r"^[ \t]*[^\n：:]{1,12}[，,、]\s*(?=(?:承接|出场角色|爽点|章末钩子)"
                   r"\s*[:：])", "", t, flags=re.M)
        # 申报行是给流水线看的手续, 登记完就不该留在细纲产物里
        t = re.sub(r"^\s*新角色\s*[:：].*$", "", t, flags=re.M)
        # 模型跟人说话的话不该留在产物里 —— 实测混进细纲的有
        # 「（如需继续 106-110 章，请续批。）」「注：」「原件拍照不存在的年代」
        # 这类既破叙事墙、又会被下一批当成格式学走。
        t = re.sub(r"[（(]?\s*(?:如需继续|请续批|如需补充|以上为|以上是|待续)"
                   r"[^\n]{0,40}[）)]?\s*$", "", t, flags=re.M)
        t = re.sub(r"^\s*注\s*[:：][^\n]*$", "", t, flags=re.M)
        t = re.sub(r"[^\n]{0,12}不存在的年代[^\n]{0,12}", "", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()

    @staticmethod
    def _bigrams(s: str) -> set:
        z = re.sub(r"[^一-鿿]", "", s or "")
        return {z[i:i + 2] for i in range(len(z) - 1)}

    def link_check(self, limit: int = 40, thresh: float = 0.05) -> List[Dict[str, Any]]:
        """逐章核对「承接」有没有真的接住上一章的「章末钩子」。

        字面重合率**只做零成本预筛**，不当判据：实测重合率为 0 的五对里有三对
        其实接得好好的，只是换了说法（钩子「女真人起了国号叫金，辽的盐路断了」→
        承接「泊码头探市，辽金开战风声入耳」）。接没接住是语义问题，
        用字面判会把改写判成断裂。

        所以：先用二元组重合率挑出最可疑的若干对（便宜、可全量跑），
        再把这些对交给模型判定，模型只回断没断、怎么断的。
        """
        co = self.p._load("chapter_outlines.json", {})
        ks = sorted(int(k) for k in co)
        if len(ks) < 2:
            return []

        def field(n: int, k: str) -> str:
            m = re.search(rf"^\s*{k}\s*[:：]\s*(.+)$", str(co.get(str(n), "")), re.M)
            return m.group(1).strip() if m else ""

        cand = []
        for n in ks[1:]:
            hook, link = field(n - 1, "章末钩子"), field(n, "承接")
            if not hook or not link:
                cand.append((0.0, n, hook, link))
                continue
            kh = self._bigrams(hook)
            ov = len(kh & self._bigrams(link)) / max(1, len(kh))
            if ov < thresh:
                cand.append((ov, n, hook, link))
        cand.sort()
        cand = cand[:limit]
        if not cand:
            return []

        listing = "\n\n".join(
            f"{i+1}. 第{n-1}章钩子：{h[:110]}\n   第{n}章承接：{l[:110]}"
            for i, (_, n, h, l) in enumerate(cand))
        prompt = (
            "下面是一部长篇作品里若干**相邻两章的接缝**：上一章的「章末钩子」"
            "与下一章的「承接」。\n\n"
            "逐对判断：下一章**是不是真的接住了**上一章的钩子？\n"
            "换个说法叙述同一件事**算接住**（钩子写「女真人起了国号叫金，辽的盐路断了」，"
            "承接写「泊码头探市，辽金开战风声入耳」，这是接住了）。\n"
            "只有这四种才算断：\n"
            "① 钩子被晾着不管，下一章另起一摊事；\n"
            "② 承接只复述钩子的字面，没真的处理它；\n"
            "③ 时间接不上（上一章深夜、下一章突然开春却没交代）；\n"
            "④ 人在哪接不上（上一章人在东京，下一章凭空回到阳谷）。\n\n"
            f"{listing}\n\n"
            '只输出 JSON，不要代码围栏：{"breaks":[{"i":1,"why":"哪一种断法，一句话"}]}\n'
            "接住了的不要列。宁可漏报，不要把改写判成断裂。")
        try:
            data = sc.parse_json(clean(call("judging", prompt, max_tokens=2500).text),
                                 "breaks")
        except Exception as e:
            self._log(f"接缝核对跳过: {e}")
            return []
        out = []
        for b in (data.get("breaks") or [])[:limit]:
            try:
                i = int(b.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(cand):
                ov, n, h, l = cand[i]
                out.append({"ch": n, "prev": n - 1, "overlap": round(ov, 3),
                            "why": str(b.get("why") or "")[:120],
                            "hook": h[:100], "link": l[:100]})
        self._log(f"接缝核对：预筛 {len(cand)} 对 → 模型判定真断 {len(out)} 对")
        return sorted(out, key=lambda x: x["ch"])

    #: 章末钩子的四类。轮换是硬要求 —— 实测某书 86 章里「话出口」占 51%、
    #: 「物件出现」占 35%，两类吃掉 86%，而「局面翻」只有 2 章。
    #: 钩子类型单一，读者翻页的理由每章都一样。
    HOOK_KINDS = [
        ("人到", r"来了|进城|到了|登门|上门|找上|寻来|回城"),
        ("物现", r"信|帖|条|文书|包袱|匣|印|契|单|册|物件|东西"),
        ("话出", r"说|问|撂下|留话|低声|一字一顿|开口|补一句|冷笑道"),
        ("局翻", r"翻|反|倒|变了|不见了|空的|换了|没了|死了|走水|塌"),
    ]

    #: 硬指标判定用的同义写法。**词法匹配认不出同义词**是它的根本局限:
    #: 铁律写「打死」, 正文写「一枪毙命」, 严格比对就永远报缺, 而这条警告
    #: 会每一批都占一个纠偏名额。这张表不求全, 只覆盖铁律里最常出现的几类
    #: 动作(杀、开枪、露底牌), 覆盖不到的宁可漏报也别误报。
    _SYN = {
        "打死": ("打死", "毙命", "击杀", "杀死", "杀了", "毙了", "当场死",
                 "一枪撂倒", "断气"),
        "开枪": ("开枪", "扣动扳机", "扣扳机", "拔枪", "枪响", "一枪"),
        "见血": ("见血", "血", "伤口", "尸"),
    }

    def _said(self, kw: str, text: str) -> bool:
        for forms in self._SYN.values():
            if kw in forms:
                return any(f in text for f in forms)
        return kw in text

    def _rule_deadlines(self) -> List[tuple]:
        """铁律里「前 N 章之内必须 X」这类**带截止的硬指标**。

        返回 (截止章号, 原文要求, 用来判定兑现的关键词)。判定用词法:
        从要求里挑动词性的短词, 只要这些词在窗口内一次都没出现, 就基本可以
        断定没兑现 —— 宁可漏报, 不要误报把真账挤掉。
        """
        out = []
        try:
            rules = self.hard_rules()
        except Exception:
            return []          # 读不到铁律就不查, 别把套路扫描拖下水
        for r in rules:
            for m in re.finditer(r"前\s*(\d{1,3})\s*章(?:之内|以内|内)?[^。；]{0,4}"
                                 r"(必须|一定要|务必)([^。；]{4,60})", r):
                due, need = int(m.group(1)), m.group(3).strip()
                # **先剥前缀再筛长度**, 顺序反了会把整半句丢掉:
                # 「当场打死一个人」7 字先被长度筛掉, 剥完只剩「开第一枪」,
                # 于是「有没有死人」这半个指标根本没在查。
                # 取**动作核心**, 不是整句。踩过三次:
                #   「开第一枪」原样去搜, 正文写的是「拔枪…扣动扳机」, 搜不到;
                #   「当场打死一个仗势欺人的泼皮」11 字超长被丢掉;
                #   「（泼皮、恶奴、打手）」是括号里的**可选项**——打死其中
                #     任一种就算数, 我却当成三个都必须, 于是永远缺两个。
                alts = re.findall(r"[（(]([^）)]{2,40})[）)]", need)
                bare = re.sub(r"[（(][^）)]*[）)]", "", need)
                kws = []
                for w in re.split(r"[、，,和及]|并且|然后", bare):
                    # 前缀要**循环剥**: 「而且要当着一条街的人打」只剥一层
                    # 剩「要当着…」, 取前两字就成了「要当」这种垃圾词。
                    w = w.strip()
                    while True:
                        w2 = re.sub(r"^(?:当场|立刻|马上|真的|亲手|而且|"
                                    r"并且|还要|要|须|必须|应)", "", w)
                        if w2 == w:
                            break
                        w = w2
                    w = re.sub(r"第[一二三四五六七八九十]|一个人|一个|一次|一回", "", w)
                    w = re.sub(r"[「」【】。]", "", w).strip()
                    if len(w) >= 2:
                        kws.append((w,) if len(w) == 2 else (w[:2],))
                for grp in alts:      # 括号内是「满足其一即可」
                    opts = tuple(x.strip() for x in re.split(r"[、，,或]", grp)
                                 if 2 <= len(x.strip()) <= 6)
                    if opts:
                        kws.append(opts)
                kws = [g for g in dict.fromkeys(kws) if g][:4]
                if due and kws:
                    out.append((due, need, kws))
        return out[:3]

    def _finite_carriers(self) -> List[str]:
        """铁律里点了名、又声明不可再生的**物件本身**(枪、丹炉、疫苗…)。

        与 _finite_units 一组: 那个查数量, 这个查这东西还在不在。
        """
        # 物件名和「不可再生」这句话**常常不在同一条铁律里**: 实测
        # 【沙漠之鹰·全书最大的底气】是第一条, 「一百二十发只减不增」是
        # 第二条。要求同条出现就一个也提不到。分开判断: 只要有任一条声明了
        # 不可再生, 就把所有铁律标题里的物件名收进来。
        try:
            rules = self.hard_rules()
        except Exception:
            return []
        if not any(re.search(r"只减不增|不可再生|补不了|造不出|独一无二", r)
                   for r in rules):
            return []
        names = []
        for r in rules:
            for m in re.finditer(r"【([^】·]{2,10})", r):
                nm = m.group(1).strip()
                if nm and nm not in names:
                    names.append(nm)
        return names[:2]

    def _finite_total(self, unit: str) -> int:
        """铁律里声明的这个单位一共有多少个（「一百二十发」→ 120）。

        用来给序号对账: 第几发 + 还剩几发 = 总数。读不出来就返回 0 不查,
        宁可不报也别拿错的总数报一片假账。
        """
        CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}

        def cn2int(t: str) -> int:
            if t.isdigit():
                return int(t)
            n, cur = 0, 0
            for ch in t:
                if ch in CN:
                    cur = CN[ch]
                elif ch == "十":
                    cur = (cur or 1) * 10
                    n, cur = n + cur, 0
                elif ch == "百":
                    cur = (cur or 1) * 100
                    n, cur = n + cur, 0
                elif ch == "千":
                    cur = (cur or 1) * 1000
                    n, cur = n + cur, 0
            return n + cur

        try:
            rules = self.hard_rules()
        except Exception:
            return 0
        best = 0
        pat = re.compile(rf"(\d{{1,4}}|[零一二两三四五六七八九十百千]+)\s*{unit}")
        for r in rules:
            for m in pat.finditer(r):
                v = cn2int(m.group(1))
                # 取最大的那个: 铁律里既会写总数也会写「第一发」「一把」,
                # 总数必然是其中最大的。
                if 1 < v <= 100000:
                    best = max(best, v)
        return best

    def _finite_units(self) -> List[str]:
        """铁律里声明了「只减不增／不可再生」的计量单位。

        从铁律原文里读, 不写死 —— 换一本书可能是丹药、箭矢、灵石、疫苗。
        没声明的单位不查: 钱粮兵马本来就该涨, 拿同一把尺子量会误报一片。
        """
        units, pat = [], re.compile(r"(\d+|[一二三四五六七八九十百千万几多]+)\s*"
                                    r"([发枚颗粒支张片瓶]|块|把)")
        try:
            rules = self.hard_rules()
        except Exception:
            return []          # 读不到铁律就不查, 别把整个套路扫描拖下水
        # 数量和「不可再生」这句话**常常不在同一条铁律里** —— _finite_carriers
        # 早就踩过并修了这个坑, 这里当初漏了, 于是本书整条弹药账从来没被查过:
        #   第2条【沙漠之鹰·开局就砸场】…一百二十发子弹…      有「发」无「只减不增」
        #   第3条 子弹只减不增…（这是第几发、还剩多少）        有「只减不增」无数字
        # 要求同条出现就一个单位也提不到, 实测 _finite_units() 返回空,
        # 于是子弹从第104章的105发跳回第145章的119发, 一声没吭。
        if not any(re.search(r"只减不增|不可再生|用一.{0,2}少一|补不了|造不出", r)
                   for r in rules):
            return []
        for r in rules:
            for m in pat.finditer(r):
                if m.group(2) not in units:
                    units.append(m.group(2))
        return units[:3]

    def outline_finite_check(self, name_chapters: bool = True) -> List[str]:
        """有限资源台账的**硬矛盾**检查（零模型调用, 每批都跑）。

        原本这段长在 outline_patterns 里, 而 outline_patterns 只有每 25 章的
        outline_selfcheck 消费, 还要先经模型改写成 ≤4 条 —— 两道都会吃掉它:
          · 节奏: 第145章的错账要等到第175章才轮到自审, 中间 30 章照错;
          · 丢失: 实测「126-150：3 个模式 → 3 条纠偏」, 出来的全是钩子和章名,
                  弹药那两条一条没进。
        风格类的问题(钩子雷同、章名一个模子)等 25 章不要紧, **事实性矛盾不行**:
        子弹从 105 发涨回 119 发是穿帮, 读者当场就看出来。所以拆出来走
        outline_repairs 那条路 —— 每批都算、原文并进纠偏, 不经模型改写。
        """
        co = self.p._load("chapter_outlines.json", {})
        if not co:
            return []
        out: List[str] = []
        # 结构化结果留给 _ledger_jobs 用。**不许再让它回读拼好的消息串**:
        # 消息里为了不刷屏只列几条, 回读就只看得到那几条 —— 实测消息取的是
        # 最早四条(145/197/200/249), 新冒出来的第257章排不进去, 于是既没进
        # 纠偏也没派工单, 悄悄错着。
        self._finite_bad: List[Dict[str, Any]] = []
        # 铁律里点名「只减不增」的资源, 数字涨回去就是穿帮。
        # 实测子弹账走成 118→117→116→**119**→1→119→116→1, 还写出过
        # 「第 121 发」(总共才 120 发)。铁律白纸黑字要求「每次开枪当场记账」,
        # 可没有任何东西在核对这个数 —— 台账管的是事件, 不管数量。
        # 只查铁律自己声明了不可再生的单位, 别去管钱粮那类本来就该涨的。
        for unit in self._finite_units():
            # **只认存量, 不认分项和序数**。裸抓「N发」会把
            #   「子弹剩余115发，其中1发已暴露原理，114发是最后的威慑」
            # 读成 115→1→114 而报「涨回去了」, 也会把「第121发」这种序数
            # 当成存量。噪声大的检测器比没有更糟 —— 一条假账挤掉一条真账。
            # 一章里只取第一个存量数, 后面的分项一律不看。
            stock = re.compile(rf"(?:剩余|还剩|仅剩|尚有|只剩|剩下|剩)\s*"
                               rf"(\d{{1,4}})\s*{unit}")
            seen = []
            for n in sorted(int(x) for x in co if str(x).isdigit()):
                mm = stock.search(str(co[str(n)]))
                if mm:
                    seen.append((n, int(mm.group(1))))
            # 「最后一发」被反复用也是账没记住 —— 数字检测抓不到它, 因为
            # 根本没写存量。实测第 63、65、140、197 章各来了一次「最后一发」,
            # 每次危机都是最后一发, 等于子弹永远打不完, 铁律要的
            # 「越来越不舍得」就架空了。
            LAST = re.compile(rf"最后(?:一|1)\s*{unit}")
            lasts = [n for n in sorted(int(x) for x in co if str(x).isdigit())
                     if LAST.search(str(co[str(n)]))]
            if len(lasts) >= 2:
                out.append(
                    f"「最后一{unit}」出现了 {len(lasts)} 次"
                    f"（第 {'、'.join(map(str, lasts[:6]))} 章）—— "
                    f"每次危机都是最后一{unit}, 等于永远用不完。"
                    f"要么写清具体还剩几{unit}, 要么就别再说「最后一{unit}」")
            # 序数那一栏也要对账: 「第N发」+「剩M发」必须等于总数。
            # 存量单调不代表账对 —— 实测存量 119→117→…→105 一路单调,
            # 序号却写成「第47发, 剩112发」(第47章)、「第50发, 剩111发」
            # (第50章): **章号漏进了序号栏**; 还有「第108发, 剩108发」
            # (第68章): 剩余数漏进了序号栏。读者一眼就看出来。
            total = self._finite_total(unit)
            if total:
                PAIR = re.compile(rf"第\s*(\d{{1,4}})\s*{unit}[^)）]{{0,12}}?"
                                  rf"剩\s*(?:余\s*)?(\d{{1,4}})\s*{unit}")
                mism = []
                for n in sorted(int(x) for x in co if str(x).isdigit()):
                    for m in PAIR.finditer(str(co[str(n)])):
                        o, rem = int(m.group(1)), int(m.group(2))
                        if o + rem != total:
                            mism.append((n, o, rem, total - rem))
                if mism:
                    # 点名旧章号**只能给重排工单看**。并进纠偏会让模型跑去重写
                    # 那几章 —— 代码里早有明文: 已落盘的章在这条回路里不会重排,
                    # 写了也执行不了。实测点名第29/32/38章之后, 连着两批各多吐
                    # 十几章(含第1、22、38、67章), 全被越界丢弃, 白烧 token。
                    where = ("；".join(f"第{n}章写「第{o}{unit}、剩{r}{unit}」"
                                      f"（该是第{c}{unit}）"
                                      for n, o, r, c in mism[-4:]) + "。"
                             ) if name_chapters else ""
                    out.append(
                        f"「第几{unit}」和「还剩几{unit}」对不上账（共 {total}{unit}）："
                        + where
                        + f"序号是**已经用掉的第几{unit}**，"
                          f"不是章号也不是剩余数：第几{unit} + 还剩几{unit} = {total}")
            # 跟**历史最小值**比, 不是跟前一项比。只减不增意味着任何一章的
            # 存量都不能高过此前出现过的最低值 —— 相邻比对只抓得到第一处断裂,
            # 之后所有数都以那个错值为基准: 实测 105(第104章) → 119(第145章)
            # 报了, 可后面 119→118→118→107 每一步都在减, 于是第249章的 107
            # 一路放过, 而它比第104章的 105 还高, 台账照样对不上。
            # 记下**违规当时的地板**。给模型的门槛必须就是实际判据用的那个数,
            # 否则两边说的不是一回事: 早先消息写「最后一个对的数是第104章的
            # 105发」, 而判据用的是历史最小值(第254章的 101) —— 第266章写
            # 「第18发、剩102发」(18+102=120 完全正确, 也小于 105)照样被判违规,
            # 模型照着 105 改永远改不对。
            bad, floor = [], None
            for n, v in seen:
                if floor is not None and v > floor:
                    bad.append((n, v, floor))
                else:
                    floor = v if floor is None else min(floor, v)
            if bad:
                # **把上一个有效存量的准数写进来**。只说「接着上一个数往下减」,
                # 模型并不知道那个数是几 —— 实测第174章被点名后确实提了子弹,
                # 却写成「只剩一百多发」: 知道该提, 不敢给准数, 用模糊量词绕开。
                # 一并堵掉「一百多／几十／若干」这类写法, 否则检测器抓不到它
                # (只匹配确切数字), 下一批照样蒙混。
                # 纠偏里的锚点要用**全书当前的地板**, 不是最后一处违规当时的。
                # 两者是两回事: 工单修第 N 章, 门槛是第 N 章之前那一刻的地板;
                # 而纠偏说的是「下一批从哪个数接着往下减」—— 那必须是已经写到
                # 的最低点。实测第254章违规时地板 101, 可 286-299 章已经一路
                # 正确减到 95, 若还报 101, 自审就会推出「本批第一次开枪锚定
                # 第20发、剩100发」, 把台账**倒着写回去**。
                low = floor
                low_n = max((n for n, v in seen if v == low), default=0)
                anchor = (f"第{low_n}章的 {low}{unit}" if low_n
                          else f"{low}{unit}")
                for n, v, fl in bad:
                    fn = next((m for m, x in seen if x == fl), 0)
                    self._finite_bad.append({"ch": n, "v": v, "unit": unit,
                                             "ok_n": fn, "ok_v": fl})
                where = ("；".join(f"第{n}章写成 {v}{unit}" for n, v, _ in bad[-4:])
                         + "。") if name_chapters else ""
                out.append(
                    f"「{unit}」这类不可再生的东西数字涨回去了："
                    + where
                    + f"最后一个对的数是{anchor} —— "
                      f"接下来这一批必须从这个数接着往下减，不许重新起数。"
                      f"而且**必须写确切数字**：「一百多{unit}」「几十{unit}」"
                      f"「所剩无几」都不算记账，铁律要的是「这是第几{unit}、"
                      f"还剩多少{unit}」")
        return out

    def outline_patterns(self, start: int, end: int) -> List[str]:
        """扫这一批**自己写出来的**东西有什么重复套路。

        零成本的确定性预筛，不下判断 —— 判断交给模型。
        状态回路（谁断线、哪条支线超期、赢法分布）管的是**写什么**，
        这里管的是**怎么写**：钩子类型、重场落点、报信人、章名句式。
        实测一整批 25 章的钩子有一半是「某人说了一句」，
        而没有任何东西看得见这件事。
        """
        import collections
        co = self.p._load("chapter_outlines.json", {})
        ks = [n for n in range(start, end + 1) if str(n) in co]
        if len(ks) < 6:
            return []

        def fld(n: int, k: str) -> str:
            m = re.search(rf"^\s*{k}\s*[:：]\s*(.+)$", str(co[str(n)]), re.M)
            return m.group(1).strip() if m else ""

        out = []
        hooks = [fld(n, "章末钩子") for n in ks]
        kinds = collections.Counter()
        for h in hooks:
            for lab, pat in self.HOOK_KINDS:
                if re.search(pat, h):
                    kinds[lab] += 1
                    break
        if kinds:
            top, cnt = kinds.most_common(1)[0]
            if cnt / len(ks) >= 0.45:
                cold = [k for k, _ in self.HOOK_KINDS if kinds.get(k, 0) <= 1]
                out.append(f"章末钩子有 {cnt}/{len(ks)} 章是「{top}」型"
                           f"（{int(cnt * 100 / len(ks))}%）"
                           + (f"，「{'、'.join(cold)}」几乎没用过" if cold else ""))
        # 爽点写成了**评语**而不是事件。光靠提示词压不住 —— 字段说明里
        # 已经写了「不是点评这一章的写法」并给了正反例, 实测 20 章仍然
        # 20 章全是「展现了主角的冷静」「爽点在于智斗升级带来的压迫感」,
        # 连点名禁掉的「虚惊一场」都原样写回来。原因是提示词三万多字,
        # 字段说明被读丢了, 而总纲自己就用评语腔写着「第一幕爽点密度」。
        # 词法层面的毛病就用词法查, 查到了进纠偏清单交给排纲回路重写。
        PUNDIT = re.compile(
            r"展现了|体现了|凸显|彰显|刻画了|渲染|烘托|形成(?:了)?张力|"
            r"爽点在于|紧张感|压迫感|代入感|环环相扣|张弛有度|层层递进|"
            r"虚惊一场|既.{0,12}又.{0,12}(?:了|感)|"
            r"(?:为|给).{0,10}埋下(?:了)?伏笔")
        pun = [n for n in ks if PUNDIT.search(fld(n, "爽点"))]
        if len(pun) >= max(2, len(ks) // 3):
            out.append(
                f"爽点有 {len(pun)}/{len(ks)} 章写成了**对写法的点评**而不是事件"
                f"（第 {'、'.join(map(str, pun[:6]))} 章）—— "
                f"爽点要写「谁在什么场合被怎样了 / 主角拿到了什么」，"
                f"能直接当一场戏来写；写不出就写「无」，不许拿"
                f"「展现了…」「紧张感拉满」这类评语凑数")
        # 钩子开头四字重复 —— 同一个人反复当报信的
        heads = collections.Counter(h[:4] for h in hooks if len(h) >= 4)
        rep = [f"「{k}…」{v} 次" for k, v in heads.most_common(3) if v >= 3]
        if rep:
            out.append("钩子开头重复：" + "；".join(rep))
        # 重场老落在同一拍
        beats = collections.Counter(fld(n, "重场") for n in ks if fld(n, "重场"))
        if beats:
            b, c = beats.most_common(1)[0]
            if c / len(ks) >= 0.5:
                out.append(f"重场有 {c}/{len(ks)} 章落在「{b}」—— 轻重节奏成了固定套路")
        # 铁律里的**截止指标**有没有兑现。这是今晚同一个病的又一次:
        # 铁律白纸黑字写着「前 15 章之内必须开第一枪、当场打死一个人」,
        # 实际前 20 章一个人没死, 第一次真开枪在第 38 章还只打伤 ——
        # 规则进了提示词, 没有任何东西验证它。
        # 开篇不炸就没有后面: 平台按前三章的留存给推荐, 而这条恰恰是
        # 全书最贵的一条硬指标。
        for due, need, kws in self._rule_deadlines():
            if start > due:            # 已经扫过的窗口不重复报
                continue
            span = [n for n in ks if n <= due]
            if len(span) < min(due, 3):
                continue               # 还没排到那儿, 不算违约
            # **逐条报缺**, 不是一刀切。实测第 1 章确实「拔枪…扣动扳机」了,
            # 只是没打死人 —— 报成「一次都没出现」会让人以为整条没做,
            # 而真正缺的只有「打死」那半个。
            body = "".join(str(co[str(n)]) for n in span)
            miss = [g for g in kws
                    if not any(self._said(k, body) for k in g)]
            # **补上了就别再念**。窗口(第1-3章)是固定的, 过期之后哪怕后面
            # 补做了, 这条也会每一批都报一次, 永远占着纠偏名额 ——
            # 实测第 29 章已经「一枪毙命」, 检测器还在说「第1-3章缺打死」。
            # 指标的目的是让事情发生, 发生了就该闭嘴。
            if miss:
                later = "".join(str(v) for k, v in co.items()
                                if str(k).isdigit() and int(k) > due)
                miss = [g for g in miss
                        if not any(self._said(k, later) for k in g)]
            if miss and len(miss) == len(kws):
                out.append(
                    f"⚠ 铁律硬指标**到期完全未兑现**：「{need}」—— "
                    f"第 1-{due} 章一次都没出现。这是开篇最贵的一条："
                    f"读者按前几章决定追不追。接下来这一批必须补上")
            elif miss:
                out.append(
                    f"⚠ 铁律硬指标**只做了一半**：「{need}」—— "
                    f"第 1-{due} 章里缺的是「{'、'.join('/'.join(g) for g in miss)}」。"
                    f"做了的那半不用重做，缺的这半接下来这一批补上")
        # 关键物件被写死了(报废/销毁/送走/沉河), 后面又拿出来用。
        # 实测: 第161章「枪身锈蚀、扳机卡死、彻底成了一根废铁」, 第173章
        # 「武松验看后确认报废, 将枪投入河中」, 第176章却「深夜从地窖取枪」,
        # 第178章还「试射一发」。这种复活不报错, 读者却一眼看得出来。
        GONE = re.compile(r"报废|废铁|成了一根废|扔进|投入河|沉入|销毁|毁了|"
                          r"再也(?:开|用)不了|彻底不能用")
        BACK = re.compile(r"捞|取回|寻回|修好|复得|换了一把|另一把|备用")
        USE = re.compile(r"取出|掏出|开枪|试射|装填|擦拭|按在.{0,4}枪")
        for item in self._finite_carriers():
            gone = back = 0
            revived = []
            for n in sorted(int(x) for x in co if str(x).isdigit()):
                body = str(co[str(n)])
                near = [body[max(0, m.start() - 40):m.start() + 40]
                        for m in re.finditer(item, body)]
                if not near:
                    continue
                seg = "".join(near)
                if GONE.search(seg):
                    gone = n
                if BACK.search(seg):
                    back = n
                if gone and n > gone and back < gone and USE.search(seg):
                    revived.append(n)
            if revived:
                out.append(
                    f"「{item}」在第{gone}章已经写死（报废／送走／沉了），"
                    f"第 {'、'.join(map(str, revived[:4]))} 章却又拿出来用 —— "
                    f"要么把它彻底当没有，要么在复活的那一章明写它是怎么回来的"
                    f"（谁捞的、谁修的、哪来的备用），不许悄悄复活")
        # 有限资源的硬矛盾另有一条**每批都跑**的通道(见 outline_finite_check),
        # 这里也带上, 让每 25 章的自审同样看得见。
        out.extend(self.outline_finite_check())
        # 章名字数单一
        names = [(re.search(r"第\d+章\s*(.+)", str(co[str(n)]).splitlines()[0])
                  or [None, ""])[1].strip() for n in ks]
        lens = collections.Counter(len(x) for x in names if x)
        if lens:
            l, c = lens.most_common(1)[0]
            if c / len(ks) >= 0.6:
                # 只报「占比高」不够: 实测上一批 60/60 是 2 字, 纠偏说「2 字
                # 不超过 25 个」, 下一批就 5/5 全变 3 字 —— 单调性没解决,
                # 只是换了个长度。模型执行配额时倾向**取一个值全用**。
                # 所以把跨批的连跑长度也算进来, 让它看见自己在来回摆。
                allk = sorted(int(x) for x in co if str(x).isdigit()
                              and int(x) <= end)
                seq = [len((re.search(r"第\d+章\s*(.+)",
                                     str(co[str(n)]).splitlines()[0])
                            or [None, ""])[1].strip()) for n in allk]
                run = best = 1
                for a, b in zip(seq, seq[1:]):
                    run = run + 1 if a == b else 1
                    best = max(best, run)
                tail = ""
                if best >= 8:
                    tail = (f"；而且全书出现过连续 {best} 章同字数的段落 —— "
                            f"不要把配额理解成「换一个长度全用」，"
                            f"要 2/3/4/5 字**交替**出现，同一字数不许连排 3 章以上")
                out.append(f"章名有 {c}/{len(ks)} 个是 {l} 字，长短一个模子{tail}")
        return out

    def outline_recap(self, upto: int, limit: int = 3000) -> str:
        """到此为止的**剧情概要** —— 每批重写一次。

        与「每章一句话」分工：
          · 一句话是**存储**：一章一条，永不丢，可查（第 47 章发生了什么）
          · 概要是**视图**：把它们读成一段连贯的故事，模型顺着往下接更顺

        关键是概要每次都从「上一版概要 + 全部一句话」**重新生成**，
        不是自己迭代自己 —— 自迭代会复利式失真：第 100 章那次重写丢了
        一个细节，之后永远找不回来，而且没人知道丢了什么。
        从一句话重生成，丢失就不累积。
        """
        co = self.p._load("chapter_outlines.json", {})
        ks = sorted(k for k in (int(x) for x in co) if k <= upto)
        if len(ks) < 8:
            return ""
        lines = []
        for k in ks:
            s = str(co[str(k)])
            mo = re.search(r"^\s*一句话\s*[:：]\s*(.+)$", s, re.M)
            head = (re.search(r"第\d+章\s*(.+)", s.splitlines()[0]) or [None, ""])[1]
            lines.append(f"{k}.{head.strip()[:12]}｜"
                         + (mo.group(1).strip()[:56] if mo else ""))
        prev = str(self.p.state.get("outline_recap") or "")
        prompt = (
            f"下面是一部长篇作品第 1-{upto} 章的**逐章一句话**，"
            f"以及上一版的剧情概要。\n\n"
            + (f"【上一版概要（写到第 {self.p.state.get('recap_at', 0)} 章）】\n{prev}\n\n"
               if prev else "")
            + f"【逐章一句话】\n{self.condense(chr(10).join(lines), 24000)}\n\n"
            f"请重写一份**到第 {upto} 章为止的剧情概要**，{limit} 字以内：\n"
            f"- 按卷或按阶段分段，每段说清这一段发生了什么、局面怎么变的\n"
            f"- 保留**后面还要用到的东西**：埋下没收的线、欠着没还的账、"
            f"现在谁在哪、手里有什么\n"
            f"- 不要写成流水账，也不要写成评论；写成「一个人给另一个人讲到这儿的故事」\n"
            f"- 上一版里有、这一版该保留的，不要漏掉；"
            f"上一版里没有但一句话里有的重要转折，要补进去\n"
            f"直接输出，无前言。")
        try:
            out = clean(call("polishing", prompt,
                             max_tokens=int(self.g.get("max_tokens_outline") or 8000)).text)
        except Exception as e:
            self._log(f"剧情概要跳过: {e}")
            return prev
        if len(out) < 200:
            return prev
        st = self.p.state
        st["outline_recap"], st["recap_at"] = out[:limit * 2], upto
        self.p.save()
        self._log(f"剧情概要更新至第 {upto} 章（{len(out)} 字）")
        return out

    def outline_selfcheck(self, start: int, end: int) -> List[str]:
        """看完自己刚写的一批，给下一批写几条针对性的纠偏指令。

        这是**排纲阶段的自审回路**。step_reflect 只管正文，排纲一直没有 ——
        于是同一个套路能重复两百章而无人发现：状态回路知道「谁断线了」，
        不知道「钩子写法都一个样」。

        确定性扫描出模式（零成本），纠偏指令交给模型写 —— 因为
        「怎么改才不生硬」是判断题。指令要具体到能照着做，
        不要「注意多样性」这种没法执行的话。
        """
        pats = self.outline_patterns(start, end)
        if not pats:
            return []
        co = self.p._load("chapter_outlines.json", {})
        def _f(n: int, k: str, cap: int) -> str:
            return (re.search(rf"^\s*{k}\s*[:：]\s*(.+)$", str(co[str(n)]), re.M)
                    or [None, ""])[1][:cap]

        # 样本必须**带上被扫出问题的那个字段**。原先只给章名和钩子, 于是
        # 机器报「爽点写成了评语」, 自审的模型却看不到一条爽点原文,
        # 只能写出「注意爽点要具体」这种照着做不了的话。
        sample = "\n".join(
            f"第{n}章 {str(co[str(n)]).splitlines()[0][:24]}"
            f"｜钩子：{_f(n, '章末钩子', 60)}"
            f"｜爽点：{_f(n, '爽点', 60)}"
            for n in range(start, end + 1) if str(n) in co)
        prompt = (
            "你在给一部长篇的排纲做**写法自审**。下面是刚排好的一批章节，"
            "以及机器扫出来的重复模式。\n\n"
            f"【扫出来的模式】\n" + "\n".join(f"- {x}" for x in pats) +
            f"\n\n【这一批的章名、钩子与爽点】\n{self.condense(sample, 5000)}\n\n"
            "请给下一批写 2-4 条**纠偏指令**。要求：\n"
            "- 具体到能照着做：说清「改成什么」，不是「注意多样性」这种没法执行的话\n"
            "- 带数量：比如「本批至少 5 章的钩子改用『局面翻』型："
            "东西不见了／人换了／账对不上／说好的事变了」\n"
            "- 只针对扫出来的模式，不要泛泛而谈写作技巧\n"
            # 实测三条纠偏里有一条是「已排好的章名需立即返工第171-185中至少
            # 8 章」—— 这条在排纲回路里**做不到**: 已经落盘的章不会被重排,
            # 指令白写, 还占掉了一个名额(清单只并前三条)。
            "- **只能要求下一批怎么写, 不许要求返工已经排好的章**："
            "已落盘的章在这条回路里不会重排，写了也执行不了，"
            "还会挤掉一条能执行的\n"
            "- 扫出来几个模式就尽量各给一条，别把名额都压在同一个模式上\n"
            "每行一条，直接输出，无前言。")
        try:
            out = clean(call("polishing", prompt, max_tokens=800).text)
        except Exception as e:
            self._log(f"排纲自审跳过: {e}")
            return []
        tips = [re.sub(r"^[-•*\d.、\s]+", "", x).strip()
                for x in out.splitlines() if len(x.strip()) > 8][:4]
        if tips:
            st = self.p.state
            st["outline_guide"] = tips
            self.p.save()
            self._log(f"排纲自审 {start}-{end}：{len(pats)} 个模式 → {len(tips)} 条纠偏")
        return tips

    def _ledger_jobs(self) -> List[Dict[str, Any]]:
        """把台账矛盾落成**重排工单**（outline_repairs 的第 ⑤ 类）。

        这一类只能靠重排修: 纠偏单只能要求「下一批怎么写」, 而错的数已经落盘
        —— 实测第145章写成 119发(第104章还剩 105发), 连着三批纠偏都没能改掉:
        模型读到上下文里白纸黑字的 119, 只会接着往下写 118, 让它跳回 105 反倒
        是制造新矛盾。**往后追加修不好已经写错的账**, 得回去改那一章本身。
        检测器报得出来却没有任何东西能把它修回去 —— 又一次「有生产者没消费者」,
        所以落成工单交给 replan.py。
        """
        jobs: List[Dict[str, Any]] = []
        # **读结构化结果, 不回读消息串**。消息为了不刷屏只列最近几条, 回读就
        # 只看得到那几条 —— 实测消息取最早四条(145/197/200/249), 新冒出来的
        # 第257章排不进去, 于是既没进纠偏也没派工单, 悄悄错着。
        self.outline_finite_check()          # 只为算出 _finite_bad
        for b in getattr(self, "_finite_bad", [])[:12]:
            unit, ok_n, ok_v = b["unit"], b["ok_n"], b["ok_v"]
            if not ok_n:
                continue
            jobs.append({
                "kind": "台账对不上", "chapters": [b["ch"]],
                "demand": f"这一章把「{unit}」的存量写成了 {b['v']}{unit}，"
                          f"可第 {ok_n} 章就只剩 {ok_v}{unit} 了 —— "
                          f"只减不增，这一章的数**必须不大于 {ok_v}**。"
                          f"重排时**剧情一律不动**，只把这个数改对：按这一章"
                          f"和第 {ok_n} 章之间实际开过几枪往下减，"
                          f"并写清「这是第几{unit}、还剩多少{unit}」"})
        return jobs

    def outline_repairs(self, upto: int = 0) -> List[Dict[str, Any]]:
        """把各检测器的结论落成「哪几章要重排、为什么」。

        检测器只报警不修，等于「有生产者没消费者」—— 报出来的断线躺在日志里，
        没有任何东西能把它变回一段有戏的细纲。这里把结论翻译成可执行的重排单：
        每条给出要重排的章号区间与**具体要求**（不是「写好点」，是
        「这一段必须让梁山线露面并推进到某个节点」）。
        """
        co = self.p._load("chapter_outlines.json", {})
        if not co:
            return []
        nums = sorted(int(k) for k in co)
        upto = upto or nums[-1]
        al = self.name_aliases()
        hero = (self.alias_pair() or [""])[0]
        jobs: List[Dict[str, Any]] = []

        def window(last: int, hi: int, want: int = 3) -> List[int]:
            """在断裂区间里挑几章来重排 —— 挑断点之后、均匀分布的那几章。"""
            lo = max(min(nums), last + 1)
            hi = min(hi, upto)
            pool = [n for n in nums if lo <= n <= hi]
            if not pool:
                return []
            step = max(1, len(pool) // want)
            return pool[::step][:want]

        # ① 支线断线
        thr = self.threads()
        if thr:
            sc.thread_last_seen(thr, co, al, protagonist=hero)
            for x in thr:
                end = min(upto, x["span"][1])
                last = x.get("last_touched") or x["span"][0]
                if end - last < x["cadence"]:
                    continue
                chs = window(last, end)
                if not chs:
                    continue
                who = "、".join(sc.thread_owners(x, hero, al, thr)) or x.get("org", "")
                jobs.append({
                    "kind": "支线断线", "chapters": chs,
                    "demand": f"「{x['name']}」这条{x['kind']}从第 {last} 章之后就没再露面，"
                              f"到第 {end} 章断了 {end - last} 章（它每 {x['cadence']} 章"
                              f"至少该露一次）。承载它的是：{who}。"
                              f"这几章里必须让这条线真的往前走一步"
                              + (f"，它的关键节点是：{' → '.join(x['beats'])}"
                                 if x.get("beats") else "")})

        app = self.outline_cast()

        # ①b 戏份很重、却不在任何阶段功能位上的人 —— 骨架不知道他为什么存在，
        #     于是他只会一直做同一件事（实测某配角 8 章全是采买记账）。
        assigned = {sc.canon_name(nm, al)
                    for s in self.stages()
                    for v in (s.get("roles") or {}).values() for nm in v}
        for nm, cs in app.items():
            if len(cs) >= 8 and sc.canon_name(nm, al) not in assigned and nm != hero:
                chs = window(cs[-1] - 1, upto, want=2)
                if chs:
                    jobs.append({"kind": "有戏无位", "chapters": chs,
                                 "demand": f"「{nm}」已出场 {len(cs)} 章（第 {cs[0]}-{cs[-1]} 章），"
                                           f"戏份不轻，却不在任何阶段的功能位上 —— "
                                           f"骨架不知道他为什么存在，他就只会一直做同一件事。"
                                           f"这几章里给他一件**与他此前做的事不同类**的戏："
                                           f"让他挡一次、给一次、付一次代价、或与主角有一次分歧"})

        # ①c 支线露面方式重复
        for x in sc.thread_repetitive(thr):
            m2 = re.search(r"^([^：]+)", x)
            nm = m2.group(1) if m2 else ""
            tt = next((y for y in thr if y["name"] == nm), None)
            if tt:
                chs = window(tt.get("last_touched") or tt["span"][0],
                             min(upto, tt["span"][1]), want=2)
                if chs:
                    jobs.append({"kind": "支线写法重复", "chapters": chs,
                                 "demand": f"{x}。这几章里换一种方式推进它：换场景、"
                                           f"换视角人物、换事件类型、换它与主线咬合的方式"})

        # ①d 赢法单一 / 阶段没有挫败 —— 对手再强也救不回「读者早知道他会怎么赢」
        _modes = self.p.state.get("resolution_modes") or []
        for x in sc.mode_monotony(_modes) + sc.mode_unused(_modes):
            chs = window(upto - 20, upto, want=2)
            if chs:
                jobs.append({"kind": "赢法单一", "chapters": chs,
                             "demand": f"{x}。这几章里让主角换一种赢法："
                                       f"力破／借势／交易／收心／忍退／破局，挑一种没怎么用过的"})
        for x in sc.setback_missing(self.stages(), self.p.state.get("setbacks") or [], upto,
                                    quota=max(dl.derived(self.dials())["setback_quota"],
                                              int(self.genre.get("setbackQuota") or 1))):
            m3 = re.search(r"第(\d+)-(\d+)章", x)
            if m3:
                chs = window(int(m3.group(1)), min(int(m3.group(2)), upto), want=2)
                if chs:
                    jobs.append({"kind": "阶段无挫败", "chapters": chs,
                                 "demand": f"{x}。这几章里补一次主角自己判断错、"
                                           f"付出收不回来的代价、且不是靠看家本领翻盘的失手"})

        # ② 张力被静默消解
        for s in sc.silent_resolution(self.tensions(), app, upto, aliases=al):
            m = re.search(r"末次出场第 (\d+) 章", s)
            last = int(m.group(1)) if m else 0
            chs = window(last, upto)
            if chs:
                jobs.append({"kind": "张力静默消解", "chapters": chs,
                             # 只说「让人重新出现」是**做不到的要求**: 实测主角
                             # 正在辽东, 潘金莲在千里外的阳谷, 连报三批都被跳过,
                             # 模型没错 —— 硬把人拽到场才是崩。给它一条远程也能
                             # 执行的路: 书信、传话、物件、传闻同样是明写的事件。
                             "demand": f"{s}。张力只能被明写的事件推动，"
                                       f"不许靠一方消失来消解 —— 这几章里必须让这笔账"
                                       f"再响一次：两人在同一地就当面撞上；"
                                       f"隔着千里就用书信、带话的人、一件对方的东西、"
                                       f"或一条传到耳朵里的消息推进它。"
                                       f"关键是**明写这笔账现在压到什么程度**，"
                                       f"不是非得让人到场"})

        # ③ 阶梯停滞
        lad = self.ladders()
        for s in sc.ladder_stalled(lad, upto):
            m = re.search(r"末次推进第 (\d+) 章", s)
            last = int(m.group(1)) if m else 0
            chs = window(last, upto, want=2)
            if chs:
                jobs.append({"kind": "阶梯停滞", "chapters": chs,
                             "demand": f"{s}。这几章里必须让它实质往前走一级，"
                                       f"并且写出可验证的表现，不能只说「变强了」"})

        # ④ 承诺挨饿。先拿已排好的细纲回填「末次推进」—— 巡检是后加的，
        #    之前排的章节一条记录都没有，不回填就会把「没记录」当成「饿着」，
        #    12 条承诺全部误报且全指向同样两章。
        proms = self.promises()
        sc.promise_last_seen(proms, co)
        st = self.p.state
        st["promises"] = proms
        self.p.save()
        for s in sc.starving(proms, upto):
            m = re.search(r"末次推进第 (\d+) 章", s)
            last = int(m.group(1)) if m else 0
            chs = window(last, upto, want=2)
            if chs:
                jobs.append({"kind": "承诺挨饿", "chapters": chs,
                             "demand": f"总纲承诺久未兑现：{s}。这几章里必须推进它"})
        # ⑤ 台账对不上（见 _ledger_jobs 的说明）
        try:
            jobs.extend(self._ledger_jobs())
        except Exception as e:
            self._log(f"台账工单跳过: {e}")
        # 落在同一批章节上的合并成一条 —— 否则同几章被重排多次，后一次覆盖前一次
        return sc.merge_repairs(jobs)

    def replan_outline(self, chapters: List[int], demand: str,
                       on_delta=None) -> int:
        """定点重排指定的几章 —— 前后不动，只换这几章的内容。

        重排一章会牵动它前后的衔接，所以必须把两头钉死：前一章的结尾状态与
        后一章的开头都原样给模型看，让它重排的内容**接得住两头**。
        章数与章号一律不变，只换内容。
        """
        co = self.p._load("chapter_outlines.json", {})
        chapters = [n for n in sorted(set(chapters)) if str(n) in co]
        if not chapters:
            return 0
        nums = sorted(int(k) for k in co)

        def neighbour(n: int, step: int) -> str:
            i = n + step
            while i in nums:
                if str(i) in co and i not in chapters:
                    return f"第{i}章：\n{self.condense(co[str(i)], 700)}"
                i += step
            return "（无）"

        cur = "\n\n".join(f"[[原第{n}章]]\n{co[str(n)]}" for n in chapters)
        stage = sc.stage_of(self.stages(), chapters[0])
        blocks = [
            f"【要重排的章节】第 {'、'.join(map(str, chapters))} 章",
            f"【必须解决的问题】\n{demand}",
            f"【前一章（不动，你重排的内容要接得住它）】\n{neighbour(chapters[0], -1)}",
            f"【后一章（不动，你重排的内容要交得回它）】\n{neighbour(chapters[-1], 1)}",
        ]
        if stage:
            blocks.append(sc.stage_brief(stage, chapters[0]))
        tb = sc.thread_brief(self.threads(), chapters[0],
                             self.style.get("threadDriver") or "cadence")
        if tb:
            blocks.append(tb)
        blocks.append(f"【这几章现在的内容（要被替换掉）】\n{cur}")
        cr = self.cfg.get("character_rules") or []
        if cr:
            blocks.append("【人物纪律】\n" + "\n".join(f"- {r}" for r in cr))
        prompt = (
            f"你在给长篇作品《{self.p.meta.get('title','')}》做**定点重排**：\n"
            f"只重写下面这几章的细纲，前后章节一律不动。\n\n"
            + "\n\n".join(blocks) +
            f"\n\n要求：\n"
            f"- 章号与章数一律不变，还是这 {len(chapters)} 章\n"
            f"- 保留原来这几章**对主线的推进**，不要把主线剧情删掉 —— "
            f"是在原有骨架上把缺的那条线补进去，不是另起炉灶\n"
            f"- 开头接得住前一章，结尾交得回后一章\n"
            f"- 章节名不要与全书已用过的重复\n\n"
            f"每章按下面格式输出，章与章之间用一行 ###fenge 分隔：\n"
            + outline_format_block(6, 0, self.style) +
            f"\n⚠ {len(outline_required(self.style))} 个字段一个都不能少 —— 缺字段的章不予采用。")
        r = call("planning", prompt, on_delta, max_tokens=int(self.g.get("max_tokens_outline") or 8000))
        parts = [clean(x) for x in re.split(r"###fenge", r.text) if x.strip()]
        done = 0
        for part in parts:
            m = re.search(r"第\s*(\d{1,4})\s*章", part[:60])
            if not m:
                continue
            idx = int(m.group(1))
            if idx not in chapters:      # 越界的丢掉, 不许它顺手改别的章
                continue
            body = self.clean_outline(part)
            lack = [f for f in outline_required(self.style)
                    if not re.search(rf"^\s*{f}\s*[:：]\s*\S", body, re.M)]
            if lack:                     # 残缺的不许换上去, 原稿还在
                self._log(f"第{idx}章重排结果缺 {'、'.join(lack)}，不予采用")
                continue
            co[str(idx)] = body
            done += 1
        if done:
            self.register_new_cast(parts)
            self.p.write("chapter_outlines.json",
                         json.dumps(co, ensure_ascii=False, indent=2))
            self._log(f"定点重排 {done}/{len(chapters)} 章：{demand[:40]}")
        return done

    _OUT_EN_OK = {"cpu", "dna", "gdp", "app", "kpi", "ceo", "cto"}

    def outline_english(self, text: str) -> List[str]:
        return [w for w in re.findall(r"[A-Za-z]{2,}", text or "")
                if w.lower() not in self._OUT_EN_OK]

    def fix_outline_english(self, chapters: List[int]) -> int:
        """把细纲里混进的英文单词换成中文。

        正文阶段早有这道硬闸（fix_english），排纲阶段一直没有 —— 于是细纲里
        留着「藏在 ship 的旧档里」「从档房 deepest 的柜里」「谈了三round」
        这类词，写正文时模型照着细纲写，还会把它当成本书的用词习惯学去。

        **只问替换词，不让它重写整章**。让模型输出「修改后的完整细纲」看着
        省事，实测它只回了修好的那一句（976 字变 88 字），字数守卫一挡就整章
        跳过，等于没修。判断题（换成什么）交给模型，替换这个确定性动作交给
        代码 —— 这样既不可能丢内容，也不可能顺手重写。
        """
        co = self.p._load("chapter_outlines.json", {})
        done = 0
        for n in chapters:
            raw = str(co.get(str(n)) or "")
            en = list(dict.fromkeys(self.outline_english(raw)))
            if not en:
                continue
            ctx = "\n".join(
                f"- {w}：…{(re.search(r'.{0,40}' + re.escape(w) + r'.{0,40}', raw) or [''])[0]}…"
                if re.search(r'.{0,40}' + re.escape(w) + r'.{0,40}', raw) else f"- {w}"
                for w in en)
            prompt = (
                f"下面是一部中文小说的章节细纲，里面混进了英文单词。"
                f"请给出每个词在**该上下文里**应该换成的中文。\n\n{ctx}\n\n"
                f"每行一条，严格格式：英文=中文\n"
                f"中文要贴合上下文语气与句式，能直接替换进去读得通；"
                f"不要解释，不要输出别的。")
            try:
                out = clean(call("polishing", prompt, max_tokens=400).text)
            except Exception as e:
                self._log(f"第{n}章英文修复失败: {e}")
                continue
            fixed, hit = raw, 0
            for line in out.splitlines():
                if "=" not in line:
                    continue
                lhs, zh = line.split("=", 1)
                # 左边**不要求精确相等**：模型时不时加装饰（`**cases**=案例`、
                # 「- cases = 案例」），精确匹配一挡就整章跳过，日志上还写着
                # 「未能全换」，看着像模型没给对，其实是校验太死。
                # 只要左边包含某个待换词，就认这一条。
                key = next((x for x in en if x in lhs), None)
                zh = zh.strip().strip("*`「」\"' 　")
                if key and zh and not re.search(r"[A-Za-z]", zh) and len(zh) <= 12:
                    fixed = fixed.replace(key, zh)
                    hit += 1
            # 只有一个词要换时，模型常常不带等号、直接回一个词（实测回了「游骑」）。
            # 这种回法信息是全的，没道理因为格式不合就整章跳过。
            if not hit and len(en) == 1:
                cand = clean(out).strip().strip("*`「」\"' 　。")
                if cand and not re.search(r"[A-Za-z]", cand) and len(cand) <= 12:
                    fixed = fixed.replace(en[0], cand)
                    hit = 1
            # 换完把英文原来占位留下的空格收掉：「给钱就 卖」→「给钱就卖」
            fixed = re.sub(r"(?<=[一-鿿])[ \t]+(?=[一-鿿，。、；：！？」）])", "", fixed)
            if not hit or self.outline_english(fixed):
                # 连模型原话一起记，否则只看「未能全换」查不出是模型没给
                # 还是校验挡了
                self._log(f"第{n}章英文残留未能全换：{self.outline_english(fixed)[:4]}"
                          f"｜模型原话 {out[:80]!r}")
                continue
            co[str(n)] = fixed
            done += 1
        if done:
            self.p.write("chapter_outlines.json",
                         json.dumps(co, ensure_ascii=False, indent=2))
            self._log(f"细纲英文残留修复 {done} 章")
        return done

    def outline_sweep(self, start: int, end: int) -> Dict[str, int]:
        """一批细纲排完后的连续性巡检 —— 排纲阶段的**生产者**。

        原来 step_chapter_outlines 会读 pending_foreshadow() 拼进约束，可全书
        没有任何地方在排纲阶段调用过 add_foreshadow()：伏笔只在**正文**写完后由
        _extract() 落库。于是纯排纲跑到 346 章，伏笔表 0 行，那段「未回收伏笔」
        永远是空字符串 —— 有消费者没有生产者。

        这里把生产端补上。一批一次调用，四个产出：埋了哪些伏笔、兑现了哪些、
        推进了哪些总纲承诺、碰了哪些关系张力。全部写回状态，下一批直接吃。
        """
        # 先补上之前失败的批次 —— 欠着的账越积越久，越难判断
        pend = list(self.p.state.get("pending_sweeps") or [])
        if pend and [start, end] not in pend:
            st = self.p.state
            st["pending_sweeps"] = []
            self.p.save()
            for a, b in pend[:3]:
                self._log(f"补跑巡检 {a}-{b}")
                self.outline_sweep(int(a), int(b))

        co = self.p._load("chapter_outlines.json", {})
        body = "\n\n".join(f"[第{n}章]\n{co[str(n)]}"
                            for n in range(start, end + 1) if str(n) in co)
        if not body.strip():
            return {}
        pend = self.p.mem.pending_foreshadow()
        # 「埋了多久」要用 **end** 算, 不是 start。补跑的宽区间巡检
        # (比如一次扫 1-60 章) start=1, `start - planted` 恒为负, 于是
        # 「埋了 20 章以上」那一档全空, 只剩「最后 10 条」—— 也就是这一批
        # 刚埋下的钩子。最该查回收的老伏笔(第 8 章埋的石灰, 第 9 章就踩到了)
        # 压根没进候选, 模型只能报「回收 0」, 而这个 0 看上去像是剧情问题。
        cands = ([x for x in pend if end - x["planted"] >= 20][:8]
                 + [x for x in pend if end - x["planted"] < 20][-10:])
        cands = list({f["id"]: f for f in cands}.values())
        proms = self.promises()
        tens = self.tensions()

        blocks = [f"【本批细纲：第{start}-{end}章】\n{self.condense(body, 20000)}"]
        if cands:
            blocks.append("【尚未兑现的伏笔】\n" + "\n".join(
                f"{i+1}. （第{f['planted']}章埋）{f['text'][:50]}"
                for i, f in enumerate(cands)))
        if proms:
            blocks.append("【总纲承诺清单（带兑现判据的，要分清「推进」与「兑现」）】\n"
                          + "\n".join(
                f"P{p['id']}. [{p.get('kind','')}] {p['text'][:60]}"
                + (f"｜兑现判据：{p['done_when'][:60]}"
                   f"{'（已兑现于第%d章）' % p['done_at'] if p.get('done_at') else ''}"
                   if p.get("done_when") else "")
                for p in proms))
        if tens:
            blocks.append("【关系张力】\n" + "\n".join(
                f"T{i+1}. {' ↔ '.join(x['between'])}：{x['about'][:50]}"
                for i, x in enumerate(tens)))
        thr = self.threads()
        live = sc.active_threads(thr, end)
        if live:
            blocks.append("【本批区间活着的支线】\n" + "\n".join(
                f"S{x['id']}. {x['name']}（{x['kind']}｜{'、'.join(x['owner']) or x.get('org','')}）"
                for x in live))
        lad = self.ladders()
        if lad:
            lab = {k["key"]: k["label"] for k in sc.LADDER_KINDS}
            blocks.append("【三条线当前应处的位置】\n" + "\n".join(
                f"{k}（{lab.get(k, k)}）：{(sc.ladder_rung(v, end) or {}).get('stage', '')}"
                for k, v in lad.items()))
        prompt = (
            "你在给一部长篇作品做**排纲阶段的连续性记账**。下面是刚排好的一批细纲，"
            "以及全书当前的伏笔／承诺／张力清单。\n\n"
            + "\n\n".join(blocks) +
            "\n\n请判断四件事，只输出 JSON，不要代码围栏：\n"
            '{"plant":[{"ch":163,"text":"某处埋下的悬念，一句话"}],'
            '"resolve":[2,5],"advanced":[1,4],"fulfilled":[{"id":3,"ch":88}],"touched":[1],"ledger":{"资源":"子弹:118发；现银:三百二十贯","力量":"横练小成，能接三十招","身份":"西门庆·阳谷县生药铺主人","势力":"县衙:县尉主事，与主角互扣","条款":"茶坊分成:一成","时间":"政和五年冬"},"ladder":["power"],"modes":["outwit"],"setbacks":[{"ch":88,"what":"押错了船期，赔掉半年脚费"}],"threads":[{"id":1,"how":"一句话说清这条线这次是怎么露的面：""在什么场合、由谁带出、发生了什么事"}]}\n'
            "- plant：本批**新埋下**的悬念/伏笔（最多 6 条，写清是哪一章埋的）\n"
            "- resolve：本批**明确兑现或解开**的伏笔编号。只是提到、只是继续铺垫、"
            "只是相关，都不算\n"
            "- advanced：本批**实质推进**了的承诺编号（P 后面的数字）。"
            "只是提了一嘴不算，要真往前走了一步\n"
            "- fulfilled：本批里**兑现判据真的发生了**的承诺，给 {id, ch}。"
            "推进≠兑现：判据写「必须开枪」，那点验一百次弹单也不算兑现，"
            "只有真开了那一枪才算。没有就给空数组\n"
            "- touched：本批**正面碰到**的张力编号（T 后面的数字）。"
            "双方同框、或一方为此付出代价、或明写了它的进展\n"
            "- ledger：本批结束时的**状态快照**，只记**变了的**：\n"
            "    资源=可数的东西现在是多少（钱、子弹、船、人手…写「名目:数值」）\n"
            "    力量=实力／地位现在到哪一档（含伤势）\n"
            "    身份=主角与主要人物此刻的身份与所在地\n"
            "    势力=组织的掌事者／规模／立场有什么结构性变化\n"
            "    条款=本批立下的、后文要当规矩守的数量约定"
            "（几成干股／几日为限／月息几分…）\n"
            "    时间=本批结束时是什么时候（年号年月或相对时间）\n"
            "  没变的不要写。这些会原样喂给下一批，写错一条错一路。\n"
            "- ladder：本批**实质推进**了的线（power／pleasure／persona）。"
            "只是维持现状不算，要看得出比上一批往前走了\n"
            "- modes：本批**关键冲突主角是靠哪几种赢法赢的**，从 "
            + "／".join(sc.MODE_KEYS) + " 里挑，最多 3 个。"
            "赢法定义见下。没有关键冲突就给空数组\n"
            "- setbacks：本批里主角**自己判断错、付出收不回来的代价、"
            "且不是靠看家本领翻盘**的失手。被打一下又立刻用老办法赢回来不算。"
            "每条给 ch（第几章）与 what（一句话）。没有就给空数组\n"
            "- threads：本批**真的推进**了的支线，每条给出编号与 how。"
            "该支线的人或组织有实际戏份才算，只被提一句不算。"
            "how 要写清「这次是怎么露的面」——下一批要靠它避免重复写法\n"
            "拿不准就不填，宁缺毋滥。\n\n【七种赢法】\n" + sc.mode_menu())
        try:
            # 1200 装不下六个字段的完整 JSON —— 实测返回停在半个字符串上，
            # 解析失败后静默记成全零，一半批次的状态就这么丢了。
            txt = clean(call("polishing", prompt, max_tokens=3000).text)
            data = sc.parse_json(txt, ("plant", "resolve", "advanced", "threads"))
        except Exception as e:
            # 巡检失败不能就这么算了 —— 那一批的伏笔、承诺、张力、支线全部
            # 无人记账，而且再也不会有人回头补（实测 153-169 批因为一次写超时
            # 整批状态丢失）。记下来，下一批开头先补跑。
            st = self.p.state
            pend = st.setdefault("pending_sweeps", [])
            if [start, end] not in pend:
                pend.append([start, end])
                st["pending_sweeps"] = pend[-8:]
                self.p.save()
            self._log(f"细纲巡检跳过（已记入待补）: {e}")
            return {}
        if not data:
            # 解析不出来要吭声。静默返回零和「本批确实没埋伏笔」长得一模一样，
            # 而后者几乎不可能发生 —— 分不清就永远发现不了链路断了。
            self._log(f"细纲巡检 {start}-{end}：JSON 解析失败，模型原话 "
                      f"{(txt or '')[:120]}")
            return {}

        got = {"plant": 0, "resolve": 0, "advanced": 0, "touched": 0}
        for f in (data.get("plant") or [])[:6]:
            txt = str((f or {}).get("text") or "").strip()
            if len(txt) < 4:
                continue
            try:
                ch = int((f or {}).get("ch") or start)
            except (TypeError, ValueError):
                ch = start
            self.p.mem.add_foreshadow(max(start, min(end, ch)), txt[:60])
            got["plant"] += 1
        for i in (data.get("resolve") or [])[:6]:
            try:
                c = cands[int(i) - 1]
            except (ValueError, TypeError, IndexError):
                continue
            self.p.mem.resolve_foreshadow(c["id"], end)
            got["resolve"] += 1
        by_id = {p["id"]: p for p in proms}
        led = data.get("ledger") or {}
        if isinstance(led, dict) and led:
            st4 = self.p.state
            snap = st4.setdefault("outline_state", {})
            for k, v in led.items():
                if not str(v).strip():
                    continue
                snap[str(k)[:8]] = {"at": end, "v": str(v)[:160]}
            self.p.save()
        got["ledger"] = len([1 for v in led.values() if str(v).strip()]) if isinstance(led, dict) else 0

        got["fulfilled"] = 0
        for f in (data.get("fulfilled") or [])[:6]:
            if not isinstance(f, dict):
                continue
            try:
                pr = by_id.get(int(f.get("id")))
                ch = int(f.get("ch") or end)
            except (TypeError, ValueError):
                continue
            if pr and not pr.get("done_at"):
                pr["done_at"] = max(start, min(end, ch))
                got["fulfilled"] += 1
        for i in (data.get("advanced") or [])[:10]:
            try:
                pr = by_id.get(int(i))
            except (ValueError, TypeError):
                continue
            if pr:
                pr["last_advanced"] = end
                got["advanced"] += 1
        for i in (data.get("touched") or [])[:8]:
            try:
                tens[int(i) - 1]["last_touched"] = end
                got["touched"] += 1
            except (ValueError, TypeError, IndexError):
                continue
        got["ladder"] = 0
        for k in (data.get("ladder") or [])[:3]:
            rungs = lad.get(str(k))
            if not rungs:
                continue
            cur = sc.ladder_rung(rungs, end)
            if cur:
                cur["reached"] = end
                got["ladder"] += 1
        if got["ladder"]:
            self.p.write("ladders.json", json.dumps(lad, ensure_ascii=False, indent=2))
        modes = [str(x) for x in (data.get("modes") or []) if str(x) in sc.MODE_KEYS][:3]
        if modes:
            st2 = self.p.state
            st2["resolution_modes"] = ((st2.get("resolution_modes") or []) + modes)[-24:]
            self.p.save()
        got["modes"] = len(modes)
        got["setbacks"] = 0
        for s in (data.get("setbacks") or [])[:3]:
            if not isinstance(s, dict) or not str(s.get("what") or "").strip():
                continue
            try:
                ch = int(s.get("ch") or start)
            except (TypeError, ValueError):
                ch = start
            st3 = self.p.state
            lst = st3.setdefault("setbacks", [])
            lst.append({"ch": max(start, min(end, ch)),
                        "what": str(s["what"])[:120]})
            st3["setbacks"] = lst[-40:]
            self.p.save()
            got["setbacks"] += 1

        got["threads"] = 0
        by_tid = {x["id"]: x for x in thr}
        for item in (data.get("threads") or [])[:8]:
            # 兼容两种回法: 光给编号, 或给 {id, how}
            how = ""
            if isinstance(item, dict):
                raw_id, how = item.get("id"), str(item.get("how") or "")[:60]
            else:
                raw_id = item
            try:
                x = by_tid.get(int(raw_id))
            except (ValueError, TypeError):
                continue
            if not x:
                continue
            x["last_touched"] = end
            if how:
                # 只留最近三次 —— 再多既占提示词又没有判别价值
                x["recent_how"] = ((x.get("recent_how") or []) + [how])[-3:]
            got["threads"] += 1
        if got["threads"]:
            self.p.write("threads.json", json.dumps(thr, ensure_ascii=False, indent=2))
        st = self.p.state
        st["promises"], st["tensions"] = proms, tens
        self.p.save()
        self._log(f"细纲巡检 {start}-{end}：埋伏笔 {got['plant']}／回收 {got['resolve']}"
                  f"／推进承诺 {got['advanced']}／触及张力 {got['touched']}"
                  f"／推进阶梯 {got['ladder']}／推进支线 {got['threads']}"
                  f"／赢法 {got.get('modes', 0)}／挫败 {got.get('setbacks', 0)}"
                  f"／兑现 {got.get('fulfilled', 0)}／台账 {got.get('ledger', 0)}")
        return got

    _DECLARE = re.compile(r"^\s*新角色\s*[:：]\s*(.+)$", re.M)

    def register_new_cast(self, texts: List[str]) -> List[str]:
        """把本批申报的新角色登记进花名册。

        白名单挡住凭空造人是对的（否则满地跑龙套、重名、写完就忘），但原来
        只有禁令没有手续 —— 于是需要新人时模型只能硬用旧人，或者干脆绕开剧情。
        这里补上正规通道：申报 → 查重 → 建档 → 下一批自动可用。
        """
        known = {sc.canon_name(c["name"]) for c in self.roster()}
        known |= set(self.name_aliases())
        new: List[str] = []
        seen: set = set()
        for txt in texts:
            for m in self._DECLARE.finditer(txt or ""):
                f = [x.strip() for x in re.split(r"[|｜]", m.group(1))]
                nm = sc.canon_name(f[0] if f else "")
                if not nm or len(nm) > 8 or nm in known or nm in seen:
                    continue
                if len(f) < 4 or not f[3]:      # 挂靠是硬要求, 不挂靠不予登记
                    self._log(f"新角色「{nm}」未写挂靠对象，不予登记")
                    continue
                seen.add(nm)
                new.append(f"### 姓名：{nm}\n"
                           f"身份：{f[1][:40]}\n"
                           f"由来：{f[2][:60]}\n"
                           f"挂靠：{f[3][:40]}\n"
                           f"备注：排纲阶段申报登记\n")
        if not new:
            return []
        cur = self.p.read("characters.md")
        self.p.write("characters.md", cur.rstrip() + "\n\n" + "\n".join(new))
        self.p.write("roster.json", "null")      # 让 roster() 重新解析
        names = [n.splitlines()[0].split("：")[-1] for n in new]
        self._log(f"登记新角色 {len(names)} 人：{'、'.join(names)}")
        return names

    def outline_digest(self, before: int, full_span: int = 0,
                       limit: int = 0) -> str:
        """已排好的细纲喂给下一批：**每章一句话保底，余额给最近几章完整版**。

        两档，顺序也是这个：
          1. 先给**每一章**一句话 —— 一章不漏。这是底线，不是可选项。
          2. 剩下的预算，从最近的章往回给完整细纲。

        原来反着来：先划一段最近的给完整版，剩下的才轮到压缩行，装不下就丢。
        实测排到第 346 章，345 章前情只覆盖 134 章、**丢了 211 章**，
        而预算 40000 字符只用掉 25150 —— 前两百多章在模型眼里根本不存在，
        它凭什么接得住那时候埋的线。

        一句话按 60 字算，346 章也只要两万字符，全书永远装得下；
        完整版是锦上添花，不是保底项。
        """
        limit = limit or self.OUTLINE_INPUT_CHARS
        co = self.p._load("chapter_outlines.json", {}) or {}
        keys = sorted(k for k in (int(x) for x in co) if k < before)
        if not keys:
            return ""

        def one_line(k: int) -> str:
            """一章一句话。

            **优先用模型自己写的那句**（细纲的「一句话」字段）——
            边写边压，它比机械抽取更清楚这一章的重点在哪，
            也不依赖「重场」标得准不准。
            没有那一栏的（这条字段之前排的章）才回退到抽取。
            """
            s = str(co[str(k)])
            head = (re.search(r"第\d+章\s*(.+)", s.splitlines()[0])
                    or [None, ""])[1].strip()[:14]
            mo = re.search(r"^\s*一句话\s*[:：]\s*(.+)$", s, re.M)
            if mo and len(mo.group(1).strip()) >= 8:
                return f"{k}.{head}｜{mo.group(1).strip()[:56]}"
            core = ""
            mb = re.search(r"重场\s*[:：]\s*剧情\s*(\d)", s)
            if mb:
                mm = re.search(rf"剧情{mb.group(1)}\s*[:：]\s*(.+)", s)
                core = mm.group(1).strip() if mm else ""
            if not core:
                mm = (re.search(r"核心事件[：:]\s*(.+)", s)
                      or re.search(r"剧情1[：:]\s*(.+)", s))
                core = mm.group(1).strip() if mm else s[:60]
            return f"{k}.{head}｜{core[:52]}"

        lines = [one_line(k) for k in keys]
        used = sum(len(x) for x in lines) + len(lines)
        # 保底装不下才砍，且从最老砍起（最近的更要紧）
        while lines and used > limit * 0.8:
            used -= len(lines[0]) + 1
            lines.pop(0)

        # 余额给最近几章的完整细纲
        span = full_span or max(self.outline_batch(), 12)
        full, budget = [], limit - used
        for k in reversed(keys[-span:]):
            body = f"[[CH{k}]]\n{str(co[str(k)]).strip()}"
            if len(body) > budget:
                break
            full.insert(0, body)
            budget -= len(body)

        recap = str(self.p.state.get("outline_recap") or "")
        out = []
        if recap:
            out.append(f"【到第 {self.p.state.get('recap_at', 0)} 章为止的剧情概要"
                       f"（先读这个，把故事读顺）】\n{recap}")
        if lines:
            # 按卷分组加小标题。三百多行平铺，模型多半只看头尾；
            # 同样的内容分成七八段带标题，读起来是结构而不是流水账。
            vols = self.p._load("volumes.json", []) or []
            kept = keys[len(keys) - len(lines):]
            body, i = [], 0
            for v in vols:
                a, b = int(v.get("start", 0)), int(v.get("end", 0))
                grp = [lines[j] for j, k in enumerate(kept) if a <= k <= b]
                if grp:
                    body.append(f"── {v.get('name','')}（第{a}-{b}章）──\n"
                                + "\n".join(grp))
                    i += len(grp)
            if i < len(lines):                 # 没落进任何一卷的
                inv = {k for v in vols
                       for k in range(int(v.get("start", 0)), int(v.get("end", 0)) + 1)}
                rest = [lines[j] for j, k in enumerate(kept) if k not in inv]
                if rest:
                    body.append("── 其余 ──\n" + "\n".join(rest))
            out.append(f"【前面每一章一句话（共 {len(lines)} 章，一章不漏）】\n"
                       + "\n\n".join(body if body else lines))
        if full:
            out.append("【最近各章（完整细纲，本批要接住的就是这些）】\n"
                       + "\n\n".join(full))
        return "\n\n".join(out)

    @staticmethod
    def split_outline(text: str, want: int = 0) -> List[str]:
        """把一批细纲切成单章 —— 分隔符靠不住，得有兜底。

        约定的分隔符是 ###fenge，但模型时不时整批不输出它。原来直接
        re.split 一刀切，切不开就返回一整块，然后被当成**第一章**存进
        chapter_outlines.json —— 实测存进去两条：一条 7659 字里裹着 11 章，
        一条 6004 字里裹着 8 章。后面的批次以为这些章已排好，接着往下排，
        于是同一段剧情既在第 53 章里，又在第 54-63 章里各存了一份。

        兜底判据是**切出来的块数明显少于要的章数**，这时改按「第N章」
        标题行重切。标题行是四种内容类型共用的格式，比分隔符可靠得多。
        """
        def by_heading(s: str) -> List[str]:
            heads = list(re.finditer(r"^[ \t]*(?:#{1,6}[ \t]*|[-*·]\s*)?第\s*\d{1,4}\s*章",
                                     s, re.M))
            if len(heads) < 2:
                return [s]
            segs = []
            for i, m in enumerate(heads):
                end = heads[i + 1].start() if i + 1 < len(heads) else len(s)
                seg = clean(s[m.start():end])
                if seg.strip():
                    segs.append(seg)
            return segs or [s]

        parts = [clean(x) for x in re.split(r"###fenge|^\s*#{3,}\s*$", text or "",
                                            flags=re.M) if x.strip()]
        # 块数够阈值也不能直接信 —— 模型常常只输出**部分**分隔符，块数看着够，
        # 其中一块里还裹着十几章（实测一条 16740 字的「第298章」里有 14 章，
        # 一条 6588 字的「第87章」里有 8 章）。逐块再按标题行拆一次。
        out: List[str] = []
        for seg in parts:
            out.extend(by_heading(seg))
        return out or by_heading(text or "")

    #: 细纲与正文的健康比例。1:4 意味着一章 2700 字的正文配约 675 字细纲 ——
    #: 每条剧情一句话。比这密，写正文就退化成扩写；比这稀，写手没有抓手。
    OUTLINE_RATIO = 4

    def outline_cap(self) -> int:
        """单章细纲的字数上限，按正文目标推出来，不写死。"""
        return max(320, min(900, int(self.target_words() / self.OUTLINE_RATIO)))

    def outline_bloat(self, window: int = 40) -> List[str]:
        """细纲发胖检测 —— 一路涨一倍没人管，是实测踩过的坑。"""
        co = self.p._load("chapter_outlines.json", {})
        ks = sorted(int(k) for k in co)
        if len(ks) < window * 2:
            return []
        cap = self.outline_cap()
        recent = [len(str(co[str(n)])) for n in ks[-window:]]
        avg = sum(recent) // len(recent)
        if avg <= cap * 1.35:
            return []
        first = [len(str(co[str(n)])) for n in ks[:window]]
        return [f"最近 {window} 章细纲均 {avg} 字，上限 {cap} 字"
                f"（开篇 {window} 章是 {sum(first)//len(first)} 字）—— "
                f"细纲发胖，写正文会退化成扩写"]

    @staticmethod
    def _dup_of(body: str, outlines: Dict[str, Any], idx: int,
                thr: float = 0.82) -> int:
        """这一章的「一句话」是不是和某个已排章节撞了 —— 撞了返回那一章的章号。

        实测第 114-117 章被整段复制成第 122-125 章, 字段齐全、长度正常,
        完整性守卫放行。重复剧情不报错, 只会让读者读到同一段演两遍。
        """
        import difflib
        m = re.search(r"^\s*一句话\s*[:：]\s*(.+)$", body, re.M)
        one = (m.group(1).strip() if m else "")[:120]
        if len(one) < 16:
            return 0
        for k, v in outlines.items():
            if not str(k).isdigit() or int(k) == idx:
                continue
            m2 = re.search(r"^\s*一句话\s*[:：]\s*(.+)$", str(v), re.M)
            if not m2:
                continue
            if difflib.SequenceMatcher(
                    None, one, m2.group(1).strip()[:120]).ratio() >= thr:
                return int(k)
        return 0

    def outline_lead(self) -> int:
        """细纲最多允许领先正文多少章。0 = 不限（老行为）。

        排纲一次排完全书是「剧情不连续」的根源：实测某书 346 章细纲在正文只写了
        6 章时两小时内全部排完 —— 后一批细纲的输入只能是前一批细纲，永远不可能是
        正文，因为那时正文还不存在。正文一偏离，后面三百多章细纲全部失效，
        而且没有任何东西知道这件事。

        滚动排纲让每一批都能看见**已经写出来的正文**（见 step_chapter_outlines 里
        的「最近几章正文原文」块）。
        """
        return max(0, int(self.g.get("outline_lead") or 0))

    def outline_stop_at(self) -> int:
        """本轮排纲允许排到第几章为止。没设领先上限就返回 0（不设限）。"""
        lead = self.outline_lead()
        if not lead:
            return 0
        return int(self.p.state.get("current") or 0) + lead

    def outline_batch(self, want: int = 0) -> int:
        """一批排多少章细纲 —— 按输出上限算，不是拍一个 10。

        10 章太少：写第 5 章时模型只知道 1-10 章要发生什么，第 30 章的伏笔
        无从铺起，批与批之间的节奏也接不上。批量的真实约束是**输出 token 上限**：
        单章细纲约 456 字 ≈ 342 tok，8192 的上限能装 20 出头，留两成余量取 20。
        """
        # 单章字数**按实测算，不写死**：格式一改（比如新增「承接」字段），
        # 写死的 480 就低估了，批量不跟着降，每批最后一章必被截断 ——
        # 实测加了承接字段后单章从 456 涨到 515+，18 章的批量正好超出上限。
        co = self.p._load("chapter_outlines.json", {})
        got = [len(str(v)) for v in co.values() if len(str(v)) > 200]
        per = 480
        if len(got) >= 6:
            # 按**均值**留一成五的余量，不是按分位数。一批的总长是求和，
            # 会向 n×均值收敛，不会 n 章全撞上最长的那种；按 85 分位估
            # 等于按最坏情况给每一章配额，实测把批量从 14 压到 10，白扔容量。
            per = max(420, int(sum(got) / len(got) * 1.15))
        cap = int(self.g.get("max_tokens_outline")
                  or self.g.get("max_tokens_draft", 8192))
        fit = max(4, int(cap * 0.8 / (per * 0.75)))   # 留两成余量
        # 上限不只受输出预算约束，还要夹一个硬上限：把输出预算提到 2 万之后，
        # 反推出来的批量涨到 46 章，单批要跑八分钟 —— 一次失败整批重来，
        # 而且批内后半段的章离「前面各章」已经隔了四十多章，质量掉。
        # 输出余量该留给**不截断**（call() 的自动续写），不是拿去一次塞更多章。
        hard = int(self.g.get("outline_batch_max") or 25)
        cfg = int(self.g.get("outline_batch") or 0)
        return max(4, min(want or cfg or fit, fit, hard))

    def step_outline_review(self, on_delta=None) -> Dict[str, Any]:
        """细纲审阅 —— 全书细纲排完之后、动笔之前的一道关。

        逐章写的时候看不出整体毛病：节奏是不是章章高潮、伏笔埋了没人收、
        同一个桥段换个人名又演一遍、某个配角连着五十章没露面、卷末钩子接不上
        下一卷开头。这些只有把全书细纲摊开才看得见，而且**现在改一行字，
        比写完二十万字再返工便宜一百倍**。

        分段读再汇总，不一次吞：160 章细纲 9.7 万字符，一次发出去 33k tok
        模型直接返回空。分段还有个好处 —— 每段能看到剧情点细节，
        一次吞就只能压成一行，反而看不出重复桥段。
        """
        co = self.p._load("chapter_outlines.json", {}) or {}
        if len(co) < 5:
            return {"error": "细纲太少，先生成细纲"}
        keys = sorted(int(k) for k in co)
        vols = self.p._load("volumes.json", []) or []
        vol_map = "\n".join(f"{v.get('start')}-{v.get('end')} {v.get('name','')}"
                            for v in vols)
        outline = self.asset("outline.md")[:3000]

        CHECKS = (
            "1. **推进**（最要紧的一条）：逐章看，这一章**结束时的局面**与"
            "**开始时的局面**相比，具体变了什么？主角手里多了/少了什么、"
            "敌我态势挪了几寸、哪条线往前走了一格？\n"
            "   只是「又赢了一场」「又记了一笔账」「又查清一件事」而局面没动的，"
            "就是**原地打转** —— 哪怕它有爽点、有钩子、写得好看。\n"
            "   连着三章局面没实质变化的，点名报出来，说清这三章各自「本该推进"
            "什么却没推」。\n"
            "2. **承接**（第二要紧）：逐章核对第 N 章的「承接」是不是**真的接住**"
            "第 N-1 章的「章末钩子」。\n"
            "   四种断法都要报：① 钩子被晾着不管，下一章另起一摊事；"
            "② 承接只是复述钩子的字面，没真的处理它；"
            "③ 时间接不上（上一章深夜、下一章开春却没交代）；"
            "④ 人在哪接不上（上一章人在东京，下一章凭空回到阳谷）。\n"
            "   报的时候写清是哪两章之间断的。\n"
            "3. **节奏**：是不是章章高潮（读者会疲）或连着三章没事发生"
            "（读者会跑）？每 4-6 章该有小高潮，卷末该有大高潮\n"
            "4. **伏笔**：哪些埋了没人收？哪些埋下三章就兑现（没有煎熬）？"
            "哪些超过 40 章才收（读者早忘了）？\n"
            "5. **重复**：有没有同一个桥段换个人名又演一遍"
            "（又一次公堂对质、又一次半夜递话、又一次账本翻案）？"
            "主角的**赢法**是不是一直是同一种？\n"
            "6. **人物**：谁连着二十章没露面？谁从头到尾只是背景板？"
            "主角是不是每章都在赢、没真正吃过亏？\n"
            "7. **接缝**：卷末钩子接不接得上下一卷开头？跨卷的线有没有断？\n"
            "8. **兑现**：总纲承诺的东西（终局、爽点节奏表、伏笔总账），"
            "细纲里有没有对应章节去落实？\n")
        JSON_FMT = ('只输出 JSON，不要代码围栏，不要在 JSON 之后追加说明：\n'
                    '{"issues":[{"kind":"推进|承接|节奏|伏笔|重复|人物|接缝|兑现",'
                    '"where":"第X-Y章","what":"问题是什么",'
                    '"fix":"具体怎么改，改哪一章的哪一点"}],'
                    '"strong":["写得好的地方"]}\n'
                    "没问题就给空数组。")

        def parse(txt: str) -> Dict[str, Any]:
            raw = re.sub(r"^```[a-z]*\s*|\s*```$", "", clean(txt).strip(), flags=re.M)
            for m in re.finditer(r"\{", raw):
                depth, end = 0, None
                for i in range(m.start(), len(raw)):
                    if raw[i] == "{":
                        depth += 1
                    elif raw[i] == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if not end:
                    continue
                try:
                    cand = json.loads(raw[m.start():end])
                except Exception:
                    continue
                if isinstance(cand, dict) and "issues" in cand:
                    return cand
            return {}

        # 分段: 每段约 18000 字符, 能看到剧情点细节
        segs, cur, cur_len = [], [], 0
        for k in keys:
            t = str(co[str(k)]).strip()
            if cur and cur_len + len(t) > 18000:
                segs.append(cur)
                cur, cur_len = [], 0
            cur.append((k, t))
            cur_len += len(t)
        if cur:
            segs.append(cur)

        part = self.p._load("outline_review.partial.json", {}) or {}
        issues = list(part.get("issues") or [])
        strong = list(part.get("strong") or [])
        done_upto = int(part.get("done_upto") or 0)
        if done_upto:
            self._log(f"细纲审阅：接上次进度，已审到第 {done_upto} 章")
        for i, seg in enumerate(segs, 1):
            lo, hi = seg[0][0], seg[-1][0]
            if hi <= done_upto:                 # 这一段上次已审过
                continue
            body = "\n\n".join(t for _, t in seg)
            p = (f"你是网文主编，在动笔之前审读《{self.p.meta.get('title','')}》的细纲。\n\n"
                 f"【分卷】\n{vol_map}\n\n【总纲】\n{outline}\n\n"
                 f"【本段细纲：第 {lo}-{hi} 章】\n{body}\n\n"
                 f"请看这八件事（只报本段内看得出的问题）。"
                 f"**推进与承接是重点，逐章核，宁可多报**：\n{CHECKS}\n{JSON_FMT}")
            try:
                d = parse(call("judging", p, on_delta, max_tokens=int(self.g.get("max_tokens_outline") or 8000)).text)
            except Exception as e:
                self._log(f"细纲审阅第{i}段失败: {str(e)[:40]}")
                continue
            got = d.get("issues") or []
            issues += got
            strong += d.get("strong") or []
            self._log(f"细纲审阅 第{lo}-{hi}章 → {len(got)} 条")
            # 每段落盘一次。分段跑十几分钟，中途超时或被停就全丢了 ——
            # 实测跑到 5/6 段报出 25 条问题，进程一停，报告一个字都没留下。
            self.p.write("outline_review.partial.json", json.dumps(
                {"done_upto": hi, "issues": issues, "strong": strong},
                ensure_ascii=False, indent=2))

        # 汇总一轮: 去重、剔除误报、补上跨段才看得见的问题
        verdict = ""
        if issues:
            one_line = []
            for k in keys:
                t = str(co[str(k)])
                m = re.search(r"核心事件[：:]\s*(.+)", t) or re.search(r"剧情1[：:]\s*(.+)", t)
                one_line.append(f"{k}. {t.splitlines()[0][:20]}｜{(m.group(1) if m else t)[:50]}")
            p = (f"下面是分段审读《{self.p.meta.get('title','')}》细纲得到的问题清单，"
                 f"以及全书一行一章的梗概。\n\n"
                 f"【全书梗概】\n" + "\n".join(one_line)[:20000] + "\n\n"
                 f"【分段初判】\n" + json.dumps({"issues": issues}, ensure_ascii=False)[:12000] +
                 f"\n\n请：① 去重合并；② 剔除误报（分段时看不到全局，"
                 f"有些「伏笔没收」其实后面收了）；③ 补上**跨段才看得见**的问题"
                 f"（跨卷断线、全书节奏、某人物长期缺席）；④ 按严重程度排序，最多 12 条。\n"
                 f"另给一句 verdict 总评。\n\n"
                 '只输出 JSON：{"verdict":"…","issues":[…],"strong":[…]}')
            try:
                d = parse(call("judging", p, on_delta, max_tokens=3000).text)
                if d.get("issues") is not None:
                    issues = d["issues"]
                    strong = d.get("strong") or strong
                    verdict = d.get("verdict", "")
            except Exception as e:
                self._log(f"细纲审阅汇总失败: {str(e)[:40]}")

        lines = [f"# 细纲审阅（第 {keys[0]}-{keys[-1]} 章，共 {len(keys)} 章）", ""]
        if verdict:
            lines += [f"**总评**：{verdict}", ""]
        if strong:
            lines += ["## 写得好的地方"] + [f"- {x}" for x in dict.fromkeys(strong)] + [""]
        if issues:
            lines += ["## 要改的地方", ""]
            for i, x in enumerate(issues, 1):
                lines.append(f"### {i}. [{x.get('kind','')}] {x.get('where','')}")
                lines.append(f"- 问题：{x.get('what','')}")
                lines.append(f"- 怎么改：{x.get('fix','')}")
                lines.append("")
        else:
            lines.append("## 未发现结构性问题")
        self.p.write("outline_review.md", "\n".join(lines))
        (self.p.dir / "outline_review.partial.json").unlink(missing_ok=True)
        self._log(f"细纲审阅完成 {len(keys)} 章 → {len(issues)} 条问题")
        return {"verdict": verdict, "issues": issues, "strong": strong}

    @staticmethod
    def _title_norm(t: str) -> str:
        return re.sub(r"[\s，,。.！!？?、·「」『』“”\"']+", "", t or "")

    def _title_of(self, body: str) -> str:
        m = re.search(r"第\s*\d+\s*章\s*(.+)", body or "")
        return (m.group(1).strip() if m else "").split("\n")[0]

    def polish_titles(self, ids: List[int], on_delta=None) -> int:
        """标题选优：程序执法大罗罗的章名规矩，不合格的一章重起 5 个候选再挑。

        规矩全部来自 2833 个原作标题的统计(见文风包·章标题模板库), 但原来只写在
        提示词里当**建议** —— 实测 165 章里 16 章重复(10%), 书名本身被当标题用了
        4 次。建议没人执法就等于没有:
          · 不许用书名当章名
          · 不许与非相邻章重复(相邻重复=同题连章, 是原作手法, 放行)
          · 长度 4~16 字
        """
        co = self.p._load("chapter_outlines.json", {})
        book = self._title_norm(self.p.meta.get("title", ""))
        # 全书标题账: 归一化标题 → 章号列表
        ledger: Dict[str, List[int]] = {}
        for k, v in co.items():
            if str(k).isdigit():
                ledger.setdefault(self._title_norm(self._title_of(v)), []).append(int(k))
        fixed = 0
        for n in ids:
            body = co.get(str(n)) or ""
            t = self._title_of(body)
            tn = self._title_norm(t)
            dup_at = [x for x in ledger.get(tn, []) if abs(x - n) > 1 and x != n]
            bad = (not t or len(t) < 4 or len(t) > 16
                   or (book and book in tn) or bool(dup_at))
            if not bad:
                continue
            why = ("与书名相同" if book and book in tn else
                   f"与第{dup_at[0]}章重复" if dup_at else "长度不合规")
            spec = sc.sc_title_examples(self.style)
            hint = re.search(r"^\s*一句话\s*[:：]\s*(.+)$", body, re.M)
            recent = [self._title_of(co.get(str(i), "")) for i in
                      range(max(1, n - 30), n) if co.get(str(i))]
            # 原作的招牌起法: 标题往往是**章内有人真的说出的一句话**。
            # 《如果金兵不肯退走呢？》就是秦桧在章末问的原话, 主角答不上。
            hook = re.search(r"^\s*章末钩子\s*[:：]\s*(.+)$", body, re.M)
            prompt = (f"给这一章重起标题。原标题「{t}」不能用（{why}）。\n"
                      f"本章内容：{hint.group(1)[:80] if hint else body[:120]}\n"
                      + (f"章末钩子：{hook.group(1)[:60]}\n" if hook else "")
                      + f"{spec}\n"
                      f"优先从钩子或章内台词里拿那句最扎人的话当标题——"
                      f"标题是有人开口, 不是内容概括。\n"
                      f"最近已用（不许重复）：{'、'.join(x for x in recent[-24:] if x)}\n"
                      f"给 5 个候选，一行一个，只要标题本身。")
            try:
                r = call("polishing", prompt, on_delta, max_tokens=200)
                cands = [x.strip().lstrip("12345.、- ") for x in
                         (r.text or "").splitlines() if x.strip()]
            except Exception as e:
                self._log(f"  标题重起失败(第{n}章): {e}")
                continue
            for cand in cands:
                cn2 = self._title_norm(cand)
                if (4 <= len(cand) <= 16 and cn2 and cn2 not in ledger
                        and not (book and book in cn2)):
                    co[str(n)] = re.sub(r"^(第\s*\d+\s*章\s*).*",
                                        lambda m: m.group(1) + cand,
                                        body, count=1, flags=re.M)
                    ledger.setdefault(cn2, []).append(n)
                    self._log(f"  标题选优: 第{n}章「{t}」({why}) → 「{cand}」")
                    fixed += 1
                    break
        if fixed:
            self.p.write("chapter_outlines.json",
                         json.dumps(co, ensure_ascii=False, indent=2))
        return fixed

    def step_chapter_outlines(self, start: int, count: int, on_delta=None) -> List[str]:
        """分批生成章节细纲。批量由 outline_batch() 按输出上限算。"""
        count = self.outline_batch(count)
        ctx = self.base_ctx()
        ctx.update({
            "outline": self.p.read("outline.md"),
            "world_bible": self.p.read("world_bible.md"),
            "characters": self.p.read("characters.md"),
            "prev_summary": self.prev_summary(start),
            "range": f"{start}-{start + count - 1}",
            "parent": "", "parent_title": f"第 {start}-{start+count-1} 章",
            "count": count,
        })
        anchor = self.world_anchor()
        cons = []
        if anchor.get("dynasty"):
            cons.append(f"朝代只叫「{anchor['dynasty']}」，禁用：{'、'.join(anchor['forbidden'][:10])}")
        # 时代红线卡原来只在**写正文**时注入，排纲阶段完全看不见 ——
        # 于是细纲里会冒出差着一百二十年的年号（政和年间写成「嘉熙年前」），
        # 等写正文时才发现，那一章的剧情已经按错的年代排好了。
        era = self.asset("era_card.md", cap=3000)
        if era:
            cons.append("【时代红线卡 —— 排纲就要守，别等写正文才发现穿帮】\n" + era)
        cons.append("纪年只用本书时代真实存在的年号或纪年方式；拿不准就写"
                    "「某年春」「入冬前」这类相对时间，**绝不许编一个年号**，"
                    "更不许用本朝之后才有的年号。")
        if anchor.get("main_place"):
            cons.append(f"主场固定在「{anchor['main_place']}」")
        cons.append("每章主角之外必须有 2 个以上配角有独立戏份")

        # 「种子发散」的约束层: 这一批长在阶段骨架上, 而不是只接着上一批往下写。
        stages = self.stages()
        cur_stage = sc.stage_of(stages, start)
        if cur_stage:
            cons.append(sc.stage_brief(cur_stage, start))
            vac = sc.vacancies(cur_stage)
            if vac:
                cons.append(f"本阶段「{'、'.join(vac)}」还没有人担当 —— "
                            f"本批要么让已有角色补上这一位，要么按下面的手续登记新人")
        tb = sc.tension_brief(self.tensions(), start)
        if tb:
            cons.append(tb)
        pb = sc.promise_brief(self.promises(), start)
        if pb:
            cons.append(pb)
        hr = self.hard_rules()
        if hr:
            cons.append("【本书铁律（违反一次就穿帮，每章都要成立）】\n"
                        + "\n".join(f"- {x}" for x in hr))
        snap = self.p.state.get("outline_state") or {}
        if snap:
            # 台账是模型从细纲里抽出来的，会抽错。措辞不能写死成铁律 ——
            # 抽错一条就把错误固化成规矩，后面每一批都照着错的写。
            # 说清「谁记的、哪一章记的」，并允许本批剧情明确改变它。
            cons.append("【此刻的账（从前面的细纲里抽的，可能有误）："
                        "本批不得**无缘无故**与它冲突；"
                        "确实要变，就在剧情里写明怎么变的】\n"
                        + "\n".join(
                            f"- {k}（第{v.get('at','?')}章）：{v.get('v','')}"
                            for k, v in snap.items()))
        guide = self.p.state.get("outline_guide") or []
        if guide:
            cons.append("【上一批排出来的毛病，本批必须纠正】\n"
                        + "\n".join(f"- {x}" for x in guide))
        cons.append(dl.brief(self.dials()))
        # 开篇硬指标 —— 只在排最前面几章时注入, 强度跟着爽度走。
        _open = dl.opening_spec(self.dials(), start)
        if _open:
            cons.append(_open)
        st_ = self.p.state
        mb = sc.mode_brief(st_.get("resolution_modes") or [], start)
        if mb:
            cons.append(mb)
        # 配额由狂野度驱动；题材包的 setbackQuota 作为下限（正剧不许太顺）
        _q = max(dl.derived(self.dials())["setback_quota"],
                 int(self.genre.get("setbackQuota") or 1))
        sb = sc.setback_brief(cur_stage, st_.get("setbacks") or [], start, quota=_q)
        if sb:
            cons.append(sb)
        thr = self.threads()
        tbrief = sc.thread_brief(thr, start,
                                 self.style.get("threadDriver") or "cadence")
        if tbrief:
            cons.append(tbrief)
        due = sc.thread_overdue(thr, start)
        if due:
            cons.append("【下面这些支线已经超过自己的节奏没露面，本批必须让它们各推进一步】\n"
                        + "\n".join(f"- {x}" for x in due))
        lad = self.ladders()
        lb = sc.ladder_brief(lad, start, self.extra_ladders())
        if lb:
            cons.append(lb)
        stalled = sc.ladder_stalled(lad, start)
        if stalled:
            cons.append("【下面这几条线已经停太久，本批必须让它往前走一步】\n"
                        + "\n".join(f"- {x}" for x in stalled))
        gone = self.stale_cast_planned(start)
        if gone:
            cons.append("【以下角色已经断线，本批择机让他们重新入场或明写其去向，"
                        "不许当作不存在】\n" + "\n".join(f"- {g}" for g in gone))
        if self.prompt_override("chapter_outline_extra"):
            cons.append(self.prompt_override("chapter_outline_extra"))
        cons.append(self.genre_rules()[:800])
        # 这一批要写的章号**不一定连续**：前面批次有章被完整性守卫丢掉，
        # 就留下了洞。而前情里的「每章一句话」清单是按现有章号排的，
        # 洞在清单里看不出来（66 直接跳到 69），模型以为那两章早写过了，
        # 于是从最大章号往下接，洞永远补不上 —— 实测缺口从 2 个滚到 8 个。
        # 所以要把「哪几号是洞」明明白白列出来。
        co_now = self.p._load("chapter_outlines.json", {}) or {}
        want = [n for n in range(start, start + count)
                if len(str(co_now.get(str(n), ""))) < 200]
        holes = [n for n in want if n < max((int(k) for k in co_now), default=0)]
        if holes:
            cons.append(f"⚠ 本批**必须补上这几章**：第 {'、'.join(map(str, holes))} 章。"
                        f"它们是前面批次漏掉的洞 —— 前情的一句话清单里没有它们，"
                        f"不是因为不用写，是因为还没写。"
                        f"补的时候要接住它们前后两章（第 {holes[0]-1} 章与第 {holes[-1]+1} 章）。")
        if want and want != list(range(start, start + count)):
            cons.append(f"本批要写的章号是：{'、'.join(map(str, want))}"
                        f"（不是连续的，按这个号写，别自己顺着往下编）")

        vol = self.volume_of(start)
        if not vol:
            self.step_volumes()
            vol = self.volume_of(start)
        # 活跃误读放在约束块**最前面**，语气是「接下来会发生什么」而不是「不许怎样」。
        # 排纲这一步最需要它：一个动作 × N 个误读者 = N 条新支线，
        # 不用另外设计情节，只要老实结算每个人会怎么误会。
        lm = self.live_misreads(start)
        if lm:
            cons.insert(0, "🔥【正在发酵的误会·本批的情节燃料】\n" + lm)
        mc = self.milestone_ctx(start)
        if mc:
            cons.insert(0, mc)
        # 世界自转 —— 横向扩散。放在最前面, 让排纲先看见「世界自己变成了什么样」,
        # 再决定主角撞上哪一条。没有这一块, 各势力就只是背景板。
        wt = self.world_turn(start)
        if wt:
            cons.insert(0, "🌍【世界自转·主角不在场时各家各走了一步】\n" + wt
                        + "\n本批要让主角**撞上**其中至少一条，"
                          "而不是把它们当背景交代掉。")
        # 作废与换壳 —— 五算子里的纵向两条。放最前面, 它们决定这一批的骨架:
        # 主角靠什么过关这件事本身要发生变化, 而不是永远靠同一招越用越熟。
        # 推进燃料配比 —— 实测两本原著: 账目变动 35% > 新误读 30% >
        # 旧误读发酵 15% > 外部事件 15% > 新人物 5%。不给这一条, 模型会章章
        # 靠「又来了个新人物」「又有人误会了」推进, 账目那一格永远不动。
        af = self.style.get("advanceFuel") or {}
        if af.get("指令"):
            pct = "、".join(f"{k} {int(v*100)}%" for k, v in af.items()
                           if isinstance(v, float) and k != "承接率")
            cons.insert(0, "⚙【这一批的推进燃料配比·从原著实测】\n"
                        + (f"　{pct}\n" if pct else "")
                        + "　" + str(af["指令"])
                        + (f"\n　另: 原著 {int(af['承接率']*100)}% 的章开头明确接住上一章"
                           "结尾那条信息, 本批也要做到。" if af.get("承接率") else ""))
        sm = self.stale_method(start)
        if sm:
            cons.insert(0, "⛔" + sm)
        rs = self.reshell(start)
        if rs:
            cons.insert(0, "🔄" + rs)

        # 排纲必须看见**已经写出来的正文**，不是只看自己上一批排的细纲。
        # 只喂摘要接不住文风、称谓、和正文里临时长出来的东西 —— 实测第 2 章细纲
        # 写「何九叔验出武大郎体内有弹头」，正文改成了「弹头从青石板缝里抠出来」，
        # 而线索表照旧按细纲往下排，后面三个节拍全建在没发生的事实上。
        recent_full = ""
        done_before = sorted(x for x in self.p.state.get("done", []) if x < start)
        for i in done_before[-2:]:
            body = self.p.chapter(i)
            if body:
                recent_full += (f"\n———— 第{i}章 正文（**以这个为准**，"
                                f"与细纲冲突时按正文往下接）————\n{body[:3000]}\n")
        if recent_full:
            cons.append("【最近几章的正文原文】" + recent_full)

        outline_ctx = self.asset("outline.md")            # 分卷必须看见终局与节奏表
        if vol:
            outline_ctx = (f"【本卷：{vol['name']}（第{vol['start']}-{vol['end']}章）】\n"
                           f"{vol['text']}\n\n【全书总纲摘要】\n" + outline_ctx)
        # 已排好的细纲压成一行一章喂回去 —— 不给的话，这一批不知道上一批
        # 埋了什么、铺到哪一步，接缝处必然重复或断裂。
        prior = self.outline_digest(start)
        if prior:
            outline_ctx += ("\n\n【前面各章已排好的内容（一行一章，本批必须接住）】\n"
                            + prior)
            cons.append(f"本批章节属于「{vol['name']}」，必须服务于本卷主线与卷末钩子，"
                        f"不得越出本卷进度")
        prompt = compile_outline_prompt(
            title=self.p.meta.get("title", ""), start=start, count=count,
            genre_line=f"{self.genre.get('name','')}/{self.style.get('name','')}".strip("/"),
            world_digest=self.asset("world_bible.md"),
            roster_names=[c["name"] for c in self.roster()] or ["主角"],
            standby_names=self.standby_cast(),
            outline=outline_ctx,
            prev_summary=self.prev_summary(start),
            constraints="\n".join(cons),
            character_rules=self.cfg.get("character_rules") or [],
            used_titles=[m.group(1).strip()[:20] for m in
                         (re.search(r"第\s*\d+\s*章\s*(.+)", v)
                          for v in self.p._load("chapter_outlines.json", {}).values())
                         if m],
            outline_cap=self.outline_cap(),
            plots_per_chapter=max(3, int(
                self.target_words() / (int(self.style.get("blockWords") or 500) * 0.8))),
            style_pack=self.style)
        # 细纲生成之前也要召回已确立的事实 —— 否则会写出自相矛盾的剧情。
        # 实测: 第 25 章把玉佩指向皇子赵琰, 第 33 章又说是东平府通判赵家之物,
        # 因为细纲生成压根没接记忆层, 模型看不到前面已经定死的结论。
        seed = self.asset("outline.md") + "\n" + self.prev_summary(start, k=6)
        established = ""
        try:
            hits = self.p.mem.search(seed, k=8)
            if hits:
                established = "\n".join(
                    f"- {h['title']}：{h['text'][:220]}" for h in hits)
        except Exception as e:
            print(f"[outline] 事实召回跳过: {e}")
        pend = self.p.mem.pending_foreshadow()
        if pend:
            established += "\n【未回收伏笔，本批可择机兑现】" + "；".join(
                f"第{f['planted']}章「{f['text'][:36]}」" for f in pend[:8])
        if established:
            cons.append("【已确立的事实，不得推翻或给出不同结论】\n" + established[:2500])

        bg = self.sanitize_facts(self.ground("plot", context=self.asset("outline.md")))
        # 再查一轮「剧情素材」—— plot 那轮查的是写得对不对(器物称谓物价),
        # 这一轮查的是接下来能写什么: 真实发生过的事、行内真实的做局手法、
        # 制度上真实的漏洞。虚构不出来的东西, 现实里有现成的。
        drive_ctx = (self.asset("outline.md")[:1500] + "\n\n【本批要排的章节范围】"
                     + f"第 {start}-{start + count - 1} 章\n"
                     + (vol.get("text", "")[:800] if vol else ""))
        drive = self.sanitize_facts(self.ground("drive", context=drive_ctx))
        if drive:
            bg = (bg + "\n\n【可用作剧情素材的真实内容（不是查证，是拿来用）】\n"
                  + drive) if bg else drive
        if bg:
            prompt += ("\n\n【现实参考资料 —— 本批剧情涉及的器物、行程、礼俗须符合下列常识；"
                       "资料里的朝代名不得出现在成稿里】\n" + bg[:5000])
        r = call("planning", prompt, on_delta,
                 max_tokens=int(self.g.get("max_tokens_outline") or 8000))
        parts = self.split_outline(r.text, count)
        outlines = self.p._load("chapter_outlines.json", {})
        # 章号以**正文里写的**为准, 不能按顺序硬编号。实测要它排 37-54,
        # 它排出的是「第75章…第87章」, 而 str(start+i) 把这些内容存成了
        # 第 37-50 章 —— 键和内容对不上, 写正文时按第 37 章取到的是第 75 章的剧情。
        end = start + count - 1
        kept, drift, out_of_range, truncated, dupes = 0, [], [], [], []
        for i, part in enumerate(parts):
            m = re.search(r"第\s*(\d{1,4})\s*章", part[:60])
            idx = int(m.group(1)) if m else start + i
            if m and idx != start + i:
                drift.append((start + i, idx))
            if not (start <= idx <= end):
                out_of_range.append(idx)
                continue                      # 越界的丢掉, 下一轮重排
            body = self.clean_outline(part)
            # 完整性守卫：一批的最后一章常被输出上限切掉半句，而截断的章往往
            # 有四五百字，光按长度筛（<200 字）根本拦不住 —— 细纲里就留下
            # 「第三天乖乖回来」「应二」这种断句，后面的批次还当它排好了去接。
            # 判据取自**字段契约**（prompt_compiler.OUTLINE_REQUIRED），
            # 与提示词同源：守卫自带一份副本一定会漂移，实测松松地查个「钩子」
            # 就被「剧情6：钩子：…」蒙混过关，整批十章的爽点全丢了。
            lack = [f for f in outline_required(self.style)
                    if not re.search(rf"^\s*{f}\s*[:：]\s*\S", body, re.M)]
            if lack:
                truncated.append(idx)
                continue
            # 与已排章节撞车的丢掉。实测第 114-117 章被**整段复制**成了
            # 第 122-125 章(偏移正好 8, 其中两对一字不差), 而所有字段都齐,
            # 完整性守卫放行, 日志一切正常 —— 读者读到的是同一段剧情演两遍。
            # 比对「一句话」就够: 它是整章的压缩, 换个说法也压不出同样的句子。
            twin = self._dup_of(body, outlines, idx)
            if twin:
                dupes.append((idx, twin))
                continue
            outlines[str(idx)] = body
            kept += 1
        self.register_new_cast(parts)
        # 章末钩子逐章入伏笔库。钩子本来就是「明写的待兑现项」，可原来只有巡检
        # 从细纲里**猜**伏笔，钩子这个现成的字段反而没人管 —— 实测 346 章里
        # 六处接缝断裂全是「钩子被晾着」，包括开篇那把枪的悬念。
        for idx in range(start, end + 1):
            body = outlines.get(str(idx)) or ""
            m = re.search(r"^\s*章末钩子\s*[:：]\s*(.+)$", body, re.M)
            if m and len(m.group(1).strip()) > 6:
                try:
                    self.p.mem.add_foreshadow(idx, "【钩子】" + m.group(1).strip()[:56])
                except Exception:
                    pass
        self.p.write("chapter_outlines.json", json.dumps(outlines, ensure_ascii=False, indent=2))
        try:
            self.polish_titles(list(range(start, end + 1)), on_delta)
        except Exception as e:
            self._log(f"  标题选优跳过: {e}")
        note = ""
        if truncated:
            note += f"，丢弃残缺 {len(truncated)} 章（{truncated[:12]}）"
        if dupes:
            note += ("，丢弃与旧章重复 " + str(len(dupes)) + " 章（"
                     + "、".join(f"{a}≈{b}" for a, b in dupes[:6]) + "）")
        if out_of_range:
            note += f"，丢弃越界 {len(out_of_range)} 章（{out_of_range[:4]}）"
        if drift and not note:
            note = f"，章号偏移 {len(drift)} 处（如 {drift[0][0]}→{drift[0][1]}）"
        self._log(f"细纲 {start}-{end} 收 {kept} 章 / {r.elapsed:.1f}s{note}")
        if kept:
            bad = [n for n in range(start, end + 1)
                   if str(n) in outlines and self.outline_english(outlines[str(n)])]
            if bad:
                self.fix_outline_english(bad)
            # 巡检／自审／概要都不便宜（概要一次要吐三千多字），批次小了之后
            # 每批只多五章，状态和套路都还没变，每批重跑一遍纯属浪费。
            # 改成按「距上次跑过多少章」触发，与批量大小解耦。
            st0 = self.p.state
            for key, cfg, fn, span in (
                    ("swept_at", "sweep_every", self.outline_sweep, True),
                    ("selfchecked_at", "selfcheck_every", self.outline_selfcheck, True),
                    ("recapped_at", "recap_every", self.outline_recap, False)):
                every = int(self.g.get(cfg) or 1)
                last = int(st0.get(key) or 0)
                if end - last < every and end < int(
                        self.p.meta.get("target_chapters") or 0):
                    continue
                try:
                    fn(last + 1, end) if span else fn(end)
                except Exception as e:
                    self._log(f"{cfg} 跳过: {e}")
                st0 = self.p.state
                st0[key] = end
                self.p.save()

            # 巡检把「赢法单一／阶段没挫败／支线写法重复／承诺挨饿」记进了台账,
            # 可 outline_repairs() **只有手动脚本 replan.py 在调用** —— 排纲跑
            # 的时候从没人问过它, 这些检测全在空转。又一次「有生产者没消费者」。
            # 它是纯确定性的(零模型调用), 每批算一次不花钱, 结论并进纠偏清单
            # 直接喂给下一批。
            # 只并「换个写法」类的要求, 不并「重排第 X 章」—— 已经排好的章
            # 在这条回路里没法回头改, 说了也做不到, 反而挤占提示词。
            try:
                jobs = self.outline_repairs(end)
            except Exception as e:
                jobs = []
                self._log(f"重排单跳过: {e}")
            # demand 里的「这几章」指的是**检测器标记的旧章号**(那批已经排完),
            # 直接并进纠偏, 模型读到「第 1-57 章里要怎样」会正确地判断与本批
            # 无关而忽略 —— 实测重排单进了提示词两批, 王婆/赵若锦一次没出现。
            # 并进来时要把指向改成「接下来这一批」。
            def _retarget(s: str) -> str:
                s = re.sub(r"这几章里", "接下来这一批里", s)
                s = re.sub(r"这几章", "接下来这一批", s)
                return s

            # 「台账对不上」是**重排单专用**, 不并进纠偏: 它要求改的是已经落盘
            # 的那一章, 下一批做不到; 而纠偏单里已经有 outline_finite_check()
            # 直接塞的同一件事(要求下一批接着往下减)。并进来只会重复占名额。
            fresh = [f"{j['kind']}（**在接下来这一批里解决**）："
                     f"{_retarget(j['demand'])}" for j in jobs
                     if j.get("demand") and j["kind"] != "台账对不上"][:3]
            # 有限资源的硬矛盾**插在最前面**。它和重排单一样是零成本确定性检测,
            # 但优先级更高: 风格问题(钩子雷同)晚 25 章再改无所谓, 子弹从 105 发
            # 涨回 119 发是当场穿帮。原本它只挂在 outline_patterns 上, 而那条路
            # 每 25 章才走一次、还要先经模型改写成 ≤4 条 —— 实测「126-150：3 个
            # 模式 → 3 条纠偏」出来的全是钩子和章名, 弹药那两条一条没进。
            try:
                hard = self.outline_finite_check(name_chapters=False)[:2]
            except Exception as e:
                hard = []
                self._log(f"台账核对跳过: {e}")
            fresh = [f"台账对不上（**在接下来这一批里解决**）：{h}"
                     for h in hard] + fresh
            if fresh:
                st0 = self.p.state
                guide = [g for g in (st0.get("outline_guide") or [])][:3]
                st0["outline_guide"] = guide + fresh
                self.p.save()
                kinds = [j["kind"] for j in jobs if j["kind"] != "台账对不上"]
                self._log(f"重排单并入纠偏 {len(fresh)} 条："
                          + "；".join(["台账对不上"] * len(hard) + kinds[:3]))
        return parts

    def reconcile_outline(self, n: int, co: str) -> str:
        """细纲定稿（写正文前的最后一步）：拿上一章**实际正文**对表。

        细纲是批量排的（草稿），正文会越线 —— 实测第1章细纲止于「铁钉对峙」，
        正文把仗打完了；第2章细纲还等着接对峙。这不是细纲过期的老病，
        是它的镜像：正文超纲。批量排纲改不了这个，只有写前对表能。
        改完回写 chapter_outlines.json —— 后续排纲/评审看到的是定稿。
        """
        if n <= 1 or not co:
            return co
        prev = self.p.chapter(n - 1)
        if not prev:
            return co
        tail = prev[-900:]
        prompt = (
            f"下面是长篇小说第 {n} 章的细纲草稿，和第 {n-1} 章**实际写出来的结尾**。\n"
            f"草稿是提前排的，正文可能已经越过或改变了它的假设。请对表修订：\n"
            f"- 「承接」栏改成接住下面这个实际结尾（从它的**结果**起笔，不许重演）\n"
            f"- 正文里**已经发生过**的事，从剧情条里删掉；空出的篇幅让后面的剧情条展开\n"
            f"- 其余栏目（视角/标题/账目/误读/代价/钩子）没冲突就**原样保留**\n"
            f"- 保持原有栏目格式输出完整细纲，无前言\n\n"
            f"── 第 {n-1} 章实际结尾 ──\n{tail}\n\n"
            f"── 第 {n} 章细纲草稿 ──\n{co}")
        try:
            r = call("planning", prompt, max_tokens=2500)
            new_co = clean(r.text)
        except Exception as e:
            self._log(f"  细纲对表跳过(第{n}章): {e}")
            return co
        need = outline_required(self.style)
        have = sum(1 for f in need
                   if re.search(rf"^\s*{f}\s*[:：]", new_co, re.M))
        if len(new_co) < len(co) * 0.5 or have < max(1, len(need) - 2):
            self._log(f"  细纲对表产物残缺(第{n}章, 栏目{have}/{len(need)}), 保留草稿")
            return co
        outlines = self.p._load("chapter_outlines.json", {})
        outlines[str(n)] = new_co
        self.p.write("chapter_outlines.json",
                     json.dumps(outlines, ensure_ascii=False, indent=2))
        self._log(f"  细纲定稿: 第{n}章已按第{n-1}章实际正文对表")
        return new_co

    def step_chapter(self, n: int, on_delta=None, retry_on_low: int | None = None) -> Dict[str, Any]:
        self._check_budget()
        retry_on_low = retry_on_low if retry_on_low is not None else self.q["audit_pass_score"]
        outlines = self.p._load("chapter_outlines.json", {})
        co = outlines.get(str(n), "")
        if not co:
            raise RuntimeError(f"第 {n} 章没有细纲, 先跑 step_chapter_outlines")

        target = self.target_words()          # 验收线：文风包规格，不随补偿浮动
        ask = self.ask_words()                # 写进提示词的要价：可高于验收线
        # 最末一层就是「成品层」(小说=正文, 剧本=剧本页, 短剧=分镜台本, 动漫=分镜表)。
        # 原来写死 ("content","page","shot") 三个 id, 动漫的 storyboard 不在里面,
        # 直接 IndexError —— 四个内容类型里有一个从来跑不起来。
        lvl = self.type["levels"][-1]

        co = self.reconcile_outline(n, co)
        ctx = self.base_ctx()
        asm = self.build_context(n, co)     # ★ 五层记忆 + 预算分配
        L = asm["layers"]
        ctx.update({
            "chapter_outline": L["L0_outline"],
            "world_bible": L["L1_resident"],
            "characters": "",               # 已并入常驻层, 避免重复占预算
            # L1 已进世界观速览、L5 单独作为「必守约束」传给编译器, 这里都要排除,
            # 否则同一块内容在提示词里出现两遍（实测白烧掉上万 token）
            "prev_summary": MemoryController.compose(
                {k: v for k, v in L.items()
                 if k not in ("L1_resident", "L5_constraint")}),
            "index": n, "target_words": ask,
        })
        self.p.write(f"audit/{n:03d}.ctx.json",
                     json.dumps(asm["report"], ensure_ascii=False, indent=2))

        # 用编译器产出网文作者实战格式的提示词 (块状标记 / 人物卡出场标注 /
        # 正负词库 / 字数标记 / 编号剧情), 而不是长段落描述式提示词。
        st = self.style
        f = self.p.meta.get("fields", {})
        rost = self.roster()
        prompt = compile_chapter_prompt(
            title=self.p.meta.get("title", ""), index=n, target_words=ask,
            genre_line=f"{self.genre.get('name','')}/{st.get('name','')}".strip("/"),
            manner=st.get("manner") or "口语化，节奏明快",
            background=f.get("background", ""),
            # 装配层(MemoryController)已按预算裁剪, 编译层不得再截断 ——
            # 实测这里的 [:4000] 把 13k tok 的常驻层砍到 3k, 最终提示词只剩
            # 装配量的 27%, 五层记忆白装
            world_digest=L["L1_resident"],
            alias_rule=self.protagonist_alias(),
            # 题材包若有自己的爽点结构(如同人「用他认的道理将死他」)，
            # 覆盖文风包的通用版本 —— 越贴题材越管用
            style_pack=({**st, "pleasureBeats": self.genre["pleasureBeats"]}
                        if self.genre.get("pleasureBeats") else st),
            # 里程碑合同正文也要看 —— 只喂排纲的话, 正文只知道本章剧情,
            # 不知道本节要走到哪、下一节从哪接(实测第1章提示词 🧭 出现 0 次)。
            extra_directive=((self.prompt_override("content_extra") or "")
                             + self.asset_conflicts()
                             + (("\n" + self.milestone_ctx(n)) if self.milestone_ctx(n) else "")),
            global_rules=self.cfg.get("anti_ai_rules") or [],
            directives=self.cfg.get("chapter_directives") or [],
            character_rules=((self.cfg.get("character_rules") or [])
                             + ([("【本书铁律，违反一次就穿帮】\n"
                                  + "\n".join(f"- {x}" for x in self.hard_rules()))]
                                if self.hard_rules() else [])
                             + [dl.brief(self.dials())]),
            roster=rost, protagonist=((self.alias_pair() or [None])[0]
                                      or (rost[0]["name"] if rost else "")),
            relations=f.get("relationships", ""),
            mainline=self.asset("outline.md"),
            chapter_outline=co,
            positive=st.get("positive", []),
            negative=self.blacklist(),
            constraints=L.get("L5_constraint", ""),
            memory=ctx.get("prev_summary", ""),
            block_words=int(st.get("blockWords") or 500),
            window_feedback=self.window_feedback(n),
            fuel=self.live_misreads(n),
        )
        # 非小说类型: 成品的形态是剧本页/分镜表, 不是网文段落。类型包里写好的格式
        # 规范(Fountain 场头、【镜N】景别|时长)必须真的发给模型 —— 原来算出 lvl
        # 之后就再没用过, 剧本和分镜全都按小说正文的格式在生成。
        if self.p.meta.get("type_id") != "novel":
            ctx["constraints"] = L.get("L5_constraint", "")
            prompt = self.compose_typed_prompt(lvl, ctx, n, co)
        self.p.write(f"audit/{n:03d}.prompt.txt", prompt)
        # 按目标字数推导 max_tokens 硬上限。之前按 1 字≈1.43 token 估算, 对 Qwen
        # 中文严重高估(实际约 0.75 token/汉字), 于是上限根本不起作用, 章章超长。
        cap = min(self.g["max_tokens_draft"], int(ask * 0.75 * 1.25))
        r = call("drafting", prompt, on_delta, max_tokens=cap)
        # 去掉模型输出里的【字数标记】—— 它只是写作时的计数脚手架, 不进成稿
        r.text = re.sub(r"【字数标记[^】]*】\s*", "", r.text)
        text = clean(r.text)

        a = audit(text, extra_blacklist=self.hard_blacklist(), target_words=target,
                  check_modern=self.anachronism_check())
        pos = st.get("positive", [])
        if pos:
            used = [w for w in pos if w in text]
            a["positive_hits"] = len(used)
            a["positive_samples"] = used[:12]
            if len(used) < max(2, len(pos) // 25):
                a["issues"].append({"level": "low", "type": "网感不足",
                                    "detail": f"正向词库仅命中 {len(used)} 个"})
                a["score"] = max(0, a["score"] - 3)

        # 硬闸门: 严重偏短必须补, 与分数无关。
        # 分数机制表达不了「这条绝对不行」—— 实测只写到目标 47% 的章仍得 82 分,
        # 高于合格线, 于是一路短下去。而且这里要的是「扩写」不是「重写」:
        # 原文是好的, 缺的是量, 让模型重写一遍反而会把写好的东西弄丢。
        # 地板取文风包声明的下限（番茄 2200-3000 就是 2200），没声明才退回 0.85×目标。
        # 原来写 0.7×目标，2600 的目标下 1857 字刚好擦线过关 —— 而 1857 字的章
        # 在番茄上就是不合格的短章。规格是文风包说了算，不是拍一个比例。
        cw = self.style.get("chapterWords")
        floor = (int(cw[0]) if isinstance(cw, (list, tuple)) and len(cw) == 2
                 else int(target * 0.85))
        # 扩到达标为止, 最多两轮。原来只扩一轮就收工, 实测第 19 章
        # 1117 → 1727 字仍差 473 字照样落盘 —— 19 章里有 8 章卡在地板下。
        # 一轮补不满是常态: 模型对「缺 1400 字」的响应通常只补一半。
        # 扩写用的文风块：调子 + 常驻结构件 + 本章的窗口漂移。
        # 补出来的字占最终篇幅的三到五成，不能让它是文风盲的。
        grow_bits = []
        if self.style.get("主调"):
            grow_bits.append("── 补进去的文字必须是这个调子 ──\n" + self.style["主调"])
        _si = [x for x in ((self.style.get("structuralItems") or {}).get("items") or [])
               if x.get("resident", True)]
        if _si:
            grow_bits.append(
                "── 补的时候优先补这几样（原文缺什么补什么，不要平均加水）──"
                + "".join(render_item(x) for x in _si))
        _wf = self.window_feedback(n)
        if _wf:
            grow_bits.append("── 这几章的文体漂移，扩写时顺手补上 ──\n" + _wf)
        grow_style = ("\n" + "\n\n".join(grow_bits) + "\n\n") if grow_bits else ""

        for round_ in (1, 2):
            was = a["stats"]["cn"]
            if was >= floor or not text:
                break
            # 扩写这一步原来**一条文风要求都没有** —— 而实测每一章都写不够、
            # 每一章都要扩 30~90%（第7章 1365→2440，56% 的字是扩写写的）。
            # 结果是：正文那一遍精心给的结构件管住了前半截，后半截全漏，
            # 叹号密度被稀释到原作的 1/30。扩写必须带上同一套调子。
            grow = (f"下面这一章只有 {was} 字，目标 {target} 字，缺 {target - was} 字。\n"
                    f"请在**不改变任何已有情节与结局**的前提下扩写到 {target} 字左右"
                    f"（**上限 {int(target * 1.15)} 字，超了同样算不合格**）：\n"
                    f"- 把一笔带过的关键场景演出来（对话、动作、交锋的来回）\n"
                    f"- 给已出场的配角补上反应与小动作\n"
                    f"- 补足做局/算账/谈判的具体过程，让读者跟得上推理\n"
                    f"- 不要加新人物、新地点、新情节线，不要写心理总结与环境铺陈\n"
                    f"- 不要在正文末尾附任何状态更新、伏笔登记、字数统计\n"
                    + (f"⚠️ 这是第二轮扩写，上一轮只补到 {was} 字仍不达标（下限 {floor} 字），"
                       f"这次必须写够，把每一场戏都完整演出来。\n" if round_ == 2 else "")
                    + grow_style
                    + f"禁用套话：{'、'.join(self.blacklist()[:40])}\n"
                    f"直接输出扩写后的完整正文，无前言。\n\n{text}")
            # 上限只写在提示词里没人执法 —— 实测第10章 2066→3683 字,
            # 超上限(2800×1.15=3220) 15%。用 max_tokens 把天花板做实,
            # 并禁续写(续写会把天花板整个废掉, 正文那边刚踩过)。
            _cap3 = int(target * 1.15 * 0.75 * 1.1)
            r3 = call("polishing", grow, on_delta, max_tokens=_cap3,
                      no_continue=True)
            t3 = clean(r3.text)
            cn3 = len(re.findall(r"[一-鿿]", t3))
            if not t3 or cn3 <= was * 1.05:
                self._log(f"第{n}章第{round_}轮扩写无效（{cn3} 字），保留 {was} 字")
                break
            a = audit(t3, extra_blacklist=self.hard_blacklist(),
                      target_words=target, check_modern=self.anachronism_check())
            text = t3
            a["expanded"] = True
            tag = "达标" if cn3 >= floor else f"仍差 {floor - cn3} 字"
            self._log(f"第{n}章扩写第{round_}轮 {was} → {cn3} 字（{tag}）")

        # 不合格自动重写一次 (只做一轮, 避免无限循环烧钱)
        if a["score"] < retry_on_low and text:
            probs = "；".join(f"{i['type']}{i.get('samples','')}" for i in a["issues"][:6])
            over_note = ""
            if a["stats"]["cn"] > target * 1.15:
                over_note = (f"另外字数严重超标（{a['stats']['cn']}/{target}），必须压缩到 "
                             f"{target} 字左右，删掉旁枝末节与重复铺陈，保留主线与爽点。\n")
            fix = (f"下面这章 AI 味检测不合格（{a['score']}分）。问题：{probs}\n"
                   f"{over_note}"
                   f"禁用套话：{'、'.join(self.blacklist())}\n"
                   f"请重写，保持剧情完全不变，只改语言：去掉套话与 AI 腔，"
                   f"句式长短交错，段落节奏有变化，字数保持 {target} 字左右。"
                   f"直接输出正文，无前言。\n\n{text}")
            r2 = call("polishing", fix, on_delta, max_tokens=8192)
            t2 = clean(r2.text)
            a2 = audit(t2, extra_blacklist=self.blacklist(), target_words=target)
            # 长度守卫 —— 重写只为改语言, 内容大幅缩水说明模型把剧情写丢了。
            # 实测新书第 5 章原稿正常, 重写只剩 1231 字(目标 5750)却因为分数高被采纳。
            too_short = len(re.findall(r"[一-鿿]", t2)) < max(
                int(target * 0.6), int(a["stats"]["cn"] * 0.6))
            if t2 and a2["score"] > a["score"] and not too_short:
                text, a = t2, a2
                a["rewritten"] = True
            elif t2 and too_short:
                self._log(f"第{n}章重写结果过短({len(re.findall(chr(0x4e00)+'-'+chr(0x9fff), t2))}字)，弃用")

        text = self.normalize_body(text)     # 落盘前统一格式，别让格式错进成稿
        # 英文残留必须当场改掉, 不能靠分数管 —— 实测第 5 章「若是都头 private
        # 自己动手」检测器报了 high, 但全章 85 分高于合格线, 重写闸门不触发,
        # 于是这个一眼可见的低级错就落盘了。换成什么词是语义判断, 所以定点重写。
        text, a = self.fix_english(n, text, a, target, on_delta)
        self.p.write(self.p.chapter_path(n), text)
        a["target_words"] = target      # 交付率要用: 没有它就算不出模型少写了多少
        self.p.write(f"audit/{n:03d}.json", json.dumps(a, ensure_ascii=False, indent=2))

        # 结构化抽取: 摘要 + 伏笔 + 角色状态 + 时间推进 —— 合并成一次调用
        one = self._extract_state(n, text)
        self.p.state.setdefault("summaries", {})[str(n)] = one
        if self.mcfg.get("index_chapters", True):
            self.p.mem.add("plot", f"ch{n}", f"第{n}章", one + "\n" + text)

        if n not in self.p.state["done"]:
            self.p.state["done"].append(n)
        self.p.state["current"] = n
        # 逐章评审: 让模型真读一遍; 评审发现的问题直接驱动重写
        crit = {}
        if self.q.get("critique_every_chapter", True):
            try:
                crit = self.step_critique(n, text)
                a["critique"] = {k: crit.get(k) for k in
                                 ("overall", "scores", "issues", "contradictions", "tics")}
                # 评审不合格 → 用评审的具体发现当重写指令(而不是只报黑名单词)
                fix_note = self._critique_to_note(crit)
                if (crit.get("overall") or 100) < self.q["audit_pass_score"] and fix_note:
                    r3 = call("polishing",
                        f"下面这章被主编批了，问题如下，逐条改掉。剧情主线不变，"
                        f"字数保持 {target} 字左右。直接输出正文，无前言。\n\n"
                        f"【主编意见】\n{fix_note}\n\n【原稿】\n{text}",
                        on_delta, max_tokens=cap)
                    t3 = re.sub(r"【字数标记[^】]*】\s*", "", clean(r3.text))
                    if t3 and len(re.findall(r"[一-鿿]", t3)) >= target * 0.6:
                        c3 = self.step_critique(n, t3)
                        if (c3.get("overall") or 0) > (crit.get("overall") or 0):
                            text, crit = t3, c3
                            a["critique"] = {k: c3.get(k) for k in
                                             ("overall", "scores", "issues",
                                              "contradictions", "tics")}
                            a["critique_rewritten"] = True
                            self.p.write(self.p.chapter_path(n), text)
            except Exception as e:
                self._log(f"评审失败(不阻塞写作): {e}")

        # 窗口体检: 本章 + 前 3 章贴在一起看
        done_now = sorted(set(self.p.state.get("done", [])) | {n})
        wchs = {i: self.p.chapter(i) for i in done_now[-8:]}
        w = window_audit(wchs, n, span=3,
                         outlines=self.p._load("chapter_outlines.json", {}))
        a["window"] = {"score": w.get("score"), "issues": w.get("issues", [])}
        # 复读/雷同是硬伤, 抓到必须重写, 不能只记录在案
        # (实测第 8/9 章开场逐字复读, 窗口体检报了「开场与邻章雷同」但没人处理)
        dup_issues = [i for i in w.get("issues", [])
                      if i["type"] in ("与邻章重复度过高", "开场与邻章雷同")]
        if dup_issues and text:
            prev_head = ""
            for i in sorted(done_now[-4:-1], reverse=True):
                ph = self.p.chapter(i).strip()[:120]
                if ph:
                    prev_head = ph
                    break
            rdup = call("polishing",
                f"这一章的开场与上一章雷同（上一章开头：{prev_head}）。\n"
                f"重写本章的前 300 字：换一个完全不同的切入点（换场景/换人物视角/"
                f"换事件），正文其余部分原样保留。直接输出完整的这一章，无前言。\n\n"
                f"{text}", on_delta, max_tokens=cap)
            t4 = re.sub(r"【字数标记[^】]*】\s*", "", clean(rdup.text))
            if t4 and len(re.findall(r"[一-鿿]", t4)) >= len(re.findall(r"[一-鿿]", text)) * 0.8:
                text = t4
                self.p.write(self.p.chapter_path(n), text)
                a["dedup_rewritten"] = True
                self._log(f"第{n}章开场雷同已强制重写")
        a["target_words"] = target      # 交付率要用: 没有它就算不出模型少写了多少
        self.p.write(f"audit/{n:03d}.json", json.dumps(a, ensure_ascii=False, indent=2))

        rep = asm["report"]
        if crit.get("overall") is not None:
            a["score"] = min(a["score"], int(crit["overall"]))   # 取严
        rt = asm.get("retrieval") or {}
        self._log(f"第{n}章 {a['stats']['cn']}字 单章{a['score']} "
                  f"评审{crit.get('overall','-')} 窗口{a['window']['score']} / {r.elapsed:.1f}s"
                  + (f" / 召回 内{rt.get('internal',0)}+外{rt.get('external',0)}"
                     + (f"({'/'.join(rt.get('needs') or [])})" if rt.get("needs") else "")
                     if rt else "")
                  + f" / 记忆 {rep['used']}tok({rep['usage_pct']}%)"
                  + (f" 溢出:{','.join(rep['overflow'])}" if rep["overflow"] else "")
                  + ("（已重写）" if a.get("rewritten") else ""))
        self.p.state["usage"] = self._usage_snapshot()
        self.p.save()
        self.p.write("PROJECT_BOARD.md", self.p.board())

        # 每 10 章压一次 L2 摘要
        every = self.mcfg.get("l2_every", 10)
        if n % every == 0:
            self._l2(n - every + 1, n)
        ref = int(self.q.get("reflect_every", 5) or 0)
        if ref and n % ref == 0:
            try:
                self.step_reflect()
            except Exception as e:
                self._log(f"自审失败(不阻塞写作): {e}")
        heal = int(self.q.get("heal_every", 5) or 0)
        if heal and n % heal == 0:
            try:
                self.step_heal()
            except Exception as e:
                self._log(f"自愈失败(不阻塞写作): {e}")
        sc = int(self.q.get("selfcheck_every", 20) or 0)
        if sc and n % sc == 0:
            try:
                self.step_selfcheck()
            except Exception as e:
                self._log(f"系统自检失败(不阻塞写作): {e}")
        return {"chapter": n, "chars": a["stats"]["cn"], "score": a["score"],
                "elapsed": r.elapsed, "rewritten": a.get("rewritten", False)}

    # ---------------- 自我改进循环 ----------------
    def step_reflect(self, on_delta=None, sample: int = 3) -> Dict[str, Any]:
        """写几章就自己读一遍、批一遍，把结论沉淀成本书的写作守则。

        单纯把审查分数打出来没用 —— 必须把"哪里不好、下次怎么写"变成
        可执行的守则文本，注入后续每一章。这是全书质量能爬坡的唯一机制。
        """
        done = sorted(self.p.state.get("done", []))
        if len(done) < 3:
            return {"skipped": "章节太少"}

        chs = {n: self.p.chapter(n) for n in done}
        names = [c["name"] for c in self.roster()] or []
        anchor = self.world_anchor()
        forb = [w for w in (anchor.get("forbidden") or []) if w not in set(names)]
        ba = book_audit(chs, characters=names, forbidden_terms=forb,
                        forbidden_people=anchor.get("forbidden_people"),
                        protagonist=names[0] if names else "")
        # 全书分会被前期旧账永久拖住, 再给一个「最近 20 章」的趋势分,
        # 让自审看得见改进, 而不是每次都看到同一个分数
        recent_ids = sorted(chs)[-20:]
        ba_recent = book_audit({i: chs[i] for i in recent_ids}, characters=names,
                               forbidden_terms=forb,
                               forbidden_people=anchor.get("forbidden_people"),
                               protagonist=names[0] if names else "")
        ba["recent_score"] = ba_recent["score"]
        ba["recent_range"] = f"{recent_ids[0]}-{recent_ids[-1]}" if recent_ids else ""
        wa = window_audit(chs, done[-1], span=3,
                          outlines=self.p._load("chapter_outlines.json", {}))

        # 抽样最近几章正文给 critic 读 —— 光看指标看不出"写得好不好"
        picks = done[-sample:]
        excerpt = "\n\n".join(
            f"—— 第{n}章（节选）——\n{chs[n][:1800]}" for n in picks)

        prev_guide = self.p.read("style_guide.md")
        problems = json.dumps(ba.get("issues", []) + wa.get("issues", []),
                              ensure_ascii=False)[:2500]

        rules_now = self.p._load("rules.json", {})
        prompt = (
            f"你是网文主编，正在审读《{self.p.meta.get('title','')}》。\n\n"
            f"【机器体检结论】\n全书 {ba['score']}/100，近章窗口 {wa.get('score')}/100\n"
            f"{problems}\n\n"
            f"【最近 {len(picks)} 章正文节选】\n{excerpt}\n\n"
            + (f"【上一版写作守则】\n{prev_guide[:1500]}\n\n" if prev_guide else "")
            + "请输出**更新后的本书写作守则**，直接给后续章节的作者看。要求：\n"
              "1. 只写可执行的具体指令，不要评价、不要鼓励、不要空话\n"
              "2. 每条指令都要能被检查（写什么/不写什么/写多少）\n"
              "3. 优先解决体检里的高危问题，并针对节选里读到的实际毛病补充\n"
              "4. 保留上一版里仍然有效的条目，去掉已经解决的\n"
              "5. 12 条以内，每条一行，以「-」开头\n\n"
              "写完守则后，另起一行输出 ===RULES=== ，再输出一段 JSON（不要代码围栏），"
              "把守则里**能被程序自动检查**的部分抽出来，供检测器强制执行：\n"
              '{"forbidden_terms":["本书绝不能出现的词，如穿帮的朝代名/现代词"],'
              '"tics":["被用滥、应加入禁用的表达"],'
              '"must_appear":["接下来几章必须回归的断线角色"],'
              '"drop_roles":["建了档但一直没登场、建议删除的角色"]}\n'
              "每项最多 8 条，没有就给空数组。只抽**确定无疑**的，宁缺毋滥。\n"
              + (f"（已有规则，不要重复：{json.dumps(rules_now, ensure_ascii=False)[:600]}）\n"
                 if rules_now else "")
              + "先输出守则，再输出 ===RULES=== 与 JSON。")
        r = call("judging", prompt, on_delta, max_tokens=int(self.g.get("max_tokens_outline") or 8000))
        body = clean(r.text)
        guide, _, rules_raw = body.partition("===RULES===")
        guide = guide.strip()
        self._merge_rules(rules_raw)
        if guide:
            self.p.write("style_guide.md", guide)
            hist = self.p._load("reflect_log.json", [])
            hist.append({"at_chapter": done[-1], "book_score": ba["score"],
                         "window_score": wa.get("score"),
                         "issues": [i["type"] for i in ba.get("issues", [])],
                         "guide_chars": len(guide)})
            self.p.write("reflect_log.json", json.dumps(hist, ensure_ascii=False, indent=2))
        self._log(f"自审@第{done[-1]}章 全书{ba['score']}(近{ba.get('recent_score')}) "
                  f"窗口{wa.get('score')} "
                  f"→ 守则 {len(guide)} 字")
        return {"book": ba, "window": wa, "guide": guide}

    def target_words(self) -> int:
        """单章**验收线**。文风包声明了就用它 —— 番茄短章 2200-3000 与都市爽文
        5000-6500 是两种节奏, 不该被同一个全局默认值压平。"""
        cw = self.style.get("chapterWords")
        if isinstance(cw, (list, tuple)) and len(cw) == 2:
            return (int(cw[0]) + int(cw[1])) // 2
        return (self.g["chapter_words_min"] + self.g["chapter_words_max"]) // 2

    def ask_words(self) -> int:
        """写进提示词的**要价**，可以高于验收线。

        换网关后实测连续几章只写到 47-74%，提示词里写 2600 也没用。与其每章
        重写(贵)，不如按 验收线/交付率 去要，指望它交回验收线的量。
        但「要多少」和「验收线」必须分开 —— 否则模型老老实实交了 2600 字，
        反而会被按 4453 的标准判成「严重偏离」，越修越乱。
        """
        base = self.target_words()
        r = self.delivery_rate()
        if r and r < 0.9:
            return min(int(base / max(0.55, r)), base * 2)
        return base

    def delivery_rate(self, span: int = 6) -> float:
        """最近几章「实写字数 / 当时要求字数」的中位数。不足 3 章不补偿。"""
        done = sorted(self.p.state.get("done", []))[-span:]
        if len(done) < 3:
            return 0.0
        rates = []
        for n in done:
            a = self.p._load(f"audit/{n:03d}.json", {}) or {}
            cn = (a.get("stats") or {}).get("cn") or 0
            tw = a.get("target_words") or 0
            if cn and tw:
                rates.append(cn / tw)
        if len(rates) < 3:
            return 0.0
        rates.sort()
        return rates[len(rates) // 2]

    # ---------------- 时代红线卡 ----------------
    def era_card(self) -> str:
        """「这个年代还没有什么」的负面清单 —— 一次生成全书复用。

        实测都市重生 2005 的设定自洽只有 40 分, 全是时代错位:
        微服务(2014+)、菜鸟驿站(2013)、支付宝个人转账(当时要网银盾)。
        ground() 查的是「该年份有什么」, 防穿帮需要的是「还没有什么」。
        """
        cached = self.p.read("era_card.md")
        if cached:
            return cached
        f = self.p.meta.get("fields", {})
        era_hint = self.era_hint()
        # 支持公元年与朝代纪年 —— 实测「洪武二十三年」提取不到, 历史书 era 卡整个缺位
        m = self._ERA_AD.search(era_hint)
        era = m.group(1) + "年" if m else ""
        if not era:
            m2 = re.search(r"((?:洪武|建文|永乐|洪熙|宣德|正统|景泰|天顺|成化|弘治|正德|"
                           r"嘉靖|隆庆|万历|天启|崇祯|贞观|开元|天宝|康熙|雍正|乾隆|"
                           r"元和|绍兴|靖康|熙宁|庆历)[元一二三四五六七八九十]*年?)", era_hint)
            if m2:
                era = m2.group(1)
        if not era:
            return ""
        # 先检索实锚, 再让模型汇成卡片
        facts = ""
        try:
            sx = registry.searcher()
            if sx.available():
                hits = []
                qs = ((f"{era} 官制 物价 俸禄", f"{era} 火器 军备 水平",
                       f"明代 称谓 礼制 避讳")
                      if (self.history_mode() == "real" and not re.match(r"\d{4}", era))
                      else (f"{era} 智能手机 移动支付 普及情况",
                            f"{era} 互联网 主流网站 技术",
                            f"{era} 物价 工资 收入水平"))
                for q in qs:
                    hits += sx.bind_cache(self.p.dir / "research").search(q, k=3)
                facts = "\n".join(f"- {h['title']}: {h['content'][:200]}" for h in hits[:9])
        except Exception:
            pass
        if self.history_mode() == "real" and not re.match(r"\d{4}", era):
            ask = (f"为一部设定在明朝{era}（或所写朝代年号对应时期）的历史小说做"
                   f"「时代红线卡」，防止穿帮。\n"
                   + (f"检索参考：\n{facts}\n\n" if facts else "")
                   + f"输出三部分，条目化、每条一行、带依据：\n"
                   f"## {era}还没有（写进正文即穿帮）\n"
                   f"该时期尚未出现的制度/官职/作物/器物/称谓/技术（如内阁、军机处、"
                   f"玉米红薯辣椒、某类火器），注明实际出现年代\n"
                   f"## {era}的真实水位\n"
                   f"物价（一石米/一两银购买力）/俸禄（七品官月俸）/度量衡/主要货币/"
                   f"火器与军备的真实水平边界\n"
                   f"## 称谓与礼制红线\n"
                   f"对皇帝太子藩王的称谓/自称/避讳；常见错用（如「大人」滥用、"
                   f"「奴才」明代不用）\n"
                   f"只写确定的，拿不准的不写。直接输出，无前言。")
        else:
            ask = (f"为一部设定在{era}的中国都市小说做「时代红线卡」，防止写出时代错位。\n"
                   + (f"检索到的参考：\n{facts}\n\n" if facts else "")
                   + f"输出两部分，条目化、每条一行、带具体年份：\n"
                   f"## {era}还没有（写进正文即穿帮）\n"
                   f"技术产品/平台/服务/流行语各列几条，注明它们实际出现的年份\n"
                   f"## {era}的真实水位\n"
                   f"普通人月薪/房价/一顿饭价格/主流手机与网络/通讯方式/支付方式\n"
                   f"只写确定的，拿不准的不写。直接输出，无前言。")
        r = call("judging", ask, max_tokens=1400)
        card = clean(r.text)
        if card:
            self.p.write("era_card.md", card)
            self.p.mem.index_document("fact", "era_card", card)
            self._log(f"时代红线卡 {len(card)} 字")
        return card

    # ---------------- 逐章评审 ----------------
    def canon(self) -> List[Dict[str, Any]]:
        return self.p._load("canon.json", [])


    def compose_typed_prompt(self, lvl: Dict[str, Any], ctx: Dict[str, Any],
                             n: int, co: str) -> str:
        """非小说类型的成品提示词 = 类型包的格式规范 + 全流水线共享的纪律。

        共享部分(记忆、角色卡、禁用词、去 AI 味纪律)对剧本分镜同样有用,
        只有「怎么排版」这件事各类型不同, 所以格式交给类型包, 其余照旧。
        """
        rost = self.roster()
        c2 = dict(ctx)
        c2.setdefault("roles", "\n".join(
            f"{c['name']}：{_first_line(c['card'])}" for c in rost[:12]))
        c2.setdefault("index", n)
        c2.setdefault("chapter_outline", co)
        for k in ("logline", "theme", "scenes", "artstyle", "hook", "style"):
            c2.setdefault(k, (self.p.meta.get("fields", {}) or {}).get(k, "")
                          or (self.style.get("name", "") if k == "style" else ""))
        seg = [render(self.prompt_override("content") or lvl["prompt"], c2)]
        cons = ctx.get("constraints") or ""
        if cons:
            seg.append("\n【必守约束】\n" + cons)
        rules = (self.cfg.get("anti_ai_rules") or [])
        if rules:
            seg.append("\n【写作纪律】\n" + "\n".join(f"- {r}" for r in rules))
        bl = self.blacklist()[:40]
        if bl:
            seg.append("\n【禁用套话】" + "、".join(bl))
        if self.style.get("rules"):
            seg.append("\n【文风纪律】\n"
                       + "\n".join(f"- {r}" for r in self.style["rules"][:8]))
        return "\n".join(seg)

    def step_critique(self, n: int, text: Optional[str] = None,
                      on_delta=None) -> Dict[str, Any]:
        """让模型真读这一章并打分, 同时抽出不可逆事实锁进 canon。

        统计指标测不出「叙述拐杖」「官职凭空升级」「死了的人又活了」这类问题,
        必须有人真读。上下文走索引 + 压缩, 64k 足够覆盖全书而不是只看四五章。
        """
        text = text if text is not None else self.p.chapter(n)
        if not text:
            return {"skipped": "没有正文"}
        done = sorted(self.p.state.get("done", []))
        prev = [self.p.chapter(i) for i in done if i < n][-1:]
        digests = [f.read_text(encoding="utf-8")
                   for f in sorted((self.p.dir / "l2_summary").glob("*.md"))]
        try:
            recalled = self.p.mem.search(text[:1500], k=20)
        except Exception:
            recalled = []
        tl = self.p.state.get("timeline", {})
        timeline = [f"第{k}章:{v}" for k, v in
                    sorted(tl.items(), key=lambda x: int(x[0]))[-12:] if v]

        budget = int(self.cfg["generation"].get("critique_budget_chars") or 46000)
        n_pass = max(1, min(3, int(self.q.get("critique_passes", 2) or 2)))
        world_plus = (self.p.read("world_bible.md")
                      + ("\n\n【时代红线】\n" + self.p.read("era_card.md")
                         if self.p.read("era_card.md") else ""))
        outline_txt = self.p._load("chapter_outlines.json", {}).get(str(n), "")

        # 多遍读: 每遍换一个焦点, 一遍读不出所有问题
        merged: Dict[str, Any] = {"scores": {}, "issues": [], "contradictions": [],
                                  "new_facts": [], "tics": []}
        elapsed = 0.0
        for pi in range(n_pass):
            spec = critic_mod.PASSES[pi]
            real = self.p.meta.get("history_mode") == "real"
            prompt = critic_mod.build_prompt(
                title=self.p.meta.get("title", ""), n=n, text=text, prev_texts=prev,
                world=world_plus, roster=self.p.read("characters.md"),
                canon=self.canon(), outline=outline_txt,
                budget_chars=budget, recalled=recalled, digests=digests,
                roles=self.p.state.get("roles", {}), timeline=timeline,
                # 维度三来源: 通遍固有的 + 真实历史专属的 + **本书文风包声明的**。
                # 少了第三项, 评审就在用通用网文的尺子量一套换过的文风。
                dims_override=(spec["dims"]
                               + (critic_mod.REAL_DIMS if pi == 0 and real else [])
                               + critic_mod.style_dims(self.style, spec["name"])),
                pass_name=spec["name"], real_mode=real,
                era_hint=self.era_brief(400) if real else "")
            # 2000 装不下 15 个维度 + 6 条带原文引证的问题 + new_facts + tics,
            # 实测 30 章里 17 章被截断。截断后 JSON 仍可能解析成功但缺 scores。
            cap = int(self.g.get("max_tokens_critique") or 4000)

            def _read_once() -> Dict[str, Any]:
                rr = call("judging", prompt, on_delta, max_tokens=cap)
                return rr.elapsed, critic_mod.parse(clean(rr.text))

            el, one = _read_once()
            elapsed += el
            # 判据是「这一遍有没有交回分数」, 不是「有没有解析出东西」——
            # 原来只查 `not one`, 于是「解析成功但 scores 为空」这一路
            # 完全静默。而 overall 是存活遍数的平均, 少一遍就等于换了把尺子,
            # 章与章的分数不再可比(第1章按15维、第4章按8维)。
            if not (one or {}).get("scores"):
                self._log(f"  评审第{n}章「{spec['name']}」这遍没交回分数, 重试一次")
                el, one = _read_once()
                elapsed += el
            if not (one or {}).get("scores"):
                self._log(f"  ⚠ 评审第{n}章「{spec['name']}」两次都没交回分数, "
                          f"本章缺 {len(spec['dims'])}+ 个维度, 分数偏高不可比")
                merged.setdefault("failed_passes", []).append(spec["name"])
                continue
            got = one.get("scores") or {}
            want = {d[0] for d in spec["dims"]}
            miss = want - set(got)
            if miss:
                self._log(f"  评审第{n}章「{spec['name']}」漏评 {len(miss)} 维: "
                          f"{'、'.join(sorted(miss))}")
                merged.setdefault("missing_dims", []).extend(sorted(miss))
            merged["scores"].update(got)
            for k in ("issues", "contradictions", "tics"):
                merged[k] += one.get(k) or []
            if pi == 0:                      # 事实抽取只做一遍, 避免重复入账
                merged["new_facts"] = one.get("new_facts") or []
        vals = [v for v in merged["scores"].values() if isinstance(v, (int, float))]
        if not vals:
            return {"error": "评审未返回可解析结果"}
        merged["overall"] = round(sum(vals) / len(vals))
        merged["passes"] = n_pass
        d = merged

        class _R:                            # 兼容后面的日志字段
            pass
        r = _R(); r.elapsed = elapsed

        # 本章被判出矛盾, 本章的「不可逆事实」就不许固化 ——
        # 正文本身是错的, 从错正文抽出来的事实必然是错的。实测第5章:
        # 正文把第4章骑马跑掉的散修乙写死了, 评审报了 4 条矛盾, 可
        # 「散修乙已死亡, 尸体藏于迷离林」照样进了 canon, 和第4章那条
        # 「逼退散修乙, 故意放其去报信」并排躺着 —— 往后每章都被这条毒害。
        # 这就是上一本「金钟罩已坏却生效」那类硬伤的制造机制。
        bad = d.get("contradictions") or []
        if bad:
            self._log(f"  第{n}章有 {len(bad)} 条矛盾, 本章 "
                      f"{len(d.get('new_facts') or [])} 条新事实**不入台账**"
                      f"（重写通过后再收）")
            added = 0
        else:
            cn, added = critic_mod.merge_canon(self.canon(), d.get("new_facts"), n)
            if added:
                self.p.write("canon.json", json.dumps(cn, ensure_ascii=False, indent=2))
        self.p.write(f"audit/{n:03d}.critique.json",
                     json.dumps(d, ensure_ascii=False, indent=2))
        self._log(f"评审第{n}章 {d.get('overall')}分 "
                  f"问题{len(d.get('issues') or [])} 矛盾{len(d.get('contradictions') or [])} "
                  f"新事实+{added} / {r.elapsed:.1f}s")
        return d

    @staticmethod
    def _critique_to_note(crit: Dict[str, Any]) -> str:
        """把评审的结构化发现编译成可执行的重写指令。"""
        lines = []
        for i in (crit.get("issues") or [])[:6]:
            if i.get("severity") in ("high", "mid"):
                lines.append(f"- [{i.get('dim')}] {i.get('what')}"
                             + (f"（原文：{str(i.get('evidence'))[:60]}）"
                                if i.get("evidence") else ""))
        for c in (crit.get("contradictions") or [])[:3]:
            lines.append(f"- [违背既定事实] {c.get('fact')}"
                         + (f"（原文：{str(c.get('evidence'))[:60]}）"
                            if c.get("evidence") else ""))
        if crit.get("tics"):
            lines.append("- [换掉这些套路] " + "；".join(crit["tics"][:4]))
        return "\n".join(lines)

    # ---------------- 章节重写 ----------------
    def rewrite_chapter(self, n: int, mode: str = "polish", note: str = "",
                        on_delta=None) -> Dict[str, Any]:
        """三种重写模式（取自 x10086 novel-writing skill 的实践）。

        polish  剧情完全不动，只改语言、节奏、对白
        replace 整章重写，剧情可调，但必须衔接前后章
        fork    从本章分叉出另一条线，旧稿保留为 .alt
        旧稿一律先备份到 .ckpt/，永不覆盖丢失。
        """
        old = self.p.chapter(n)
        if not old:
            raise RuntimeError(f"第 {n} 章没有正文")
        ck = self.p.dir / ".ckpt"
        ck.mkdir(exist_ok=True)
        ver = len(list(ck.glob(f"{n:03d}_v*.md"))) + 1
        (ck / f"{n:03d}_v{ver}.md").write_text(old, encoding="utf-8")

        co = self.p._load("chapter_outlines.json", {}).get(str(n), "")
        asm = self.build_context(n, co)
        L = asm["layers"]
        target = self.target_words()
        common = (f"【本章细纲】\n{co}\n\n【必守约束】\n{L.get('L5_constraint','')}\n\n"
                  f"【前情】\n{L.get('L2_recent','')}\n")

        if mode == "polish":
            prompt = (f"下面这一章剧情不动，只改语言。\n{common}\n"
                      f"改写要求：{note or '句式长短交错，去掉套话与 AI 腔，对白更有性格，节奏更紧'}\n"
                      f"字数保持 {target} 字左右。剧情、人物、事件顺序一律不得改变。"
                      f"直接输出正文，无前言。\n\n{old}")
            profile = "polishing"
        elif mode == "replace":
            prompt = (f"重写《{self.p.meta.get('title','')}》第 {n} 章。\n{common}\n"
                      f"重写方向：{note or '按细纲重新组织，加强冲突与钩子'}\n"
                      f"必须与前后章衔接。字数 {target} 字左右。直接输出正文，无前言。\n\n"
                      f"【原稿供参考，可大幅改动】\n{self.condense(old, 9000)}")
            profile = "drafting"
        elif mode == "fork":
            (self.p.dir / f"chapters/{n:03d}.alt.md").write_text(old, encoding="utf-8")
            prompt = (f"从第 {n} 章分叉出另一种走向。\n{common}\n"
                      f"分叉方向：{note or '让本章的关键选择走向相反的结果'}\n"
                      f"字数 {target} 字左右。直接输出正文，无前言。\n\n"
                      f"【原线供对照】\n{self.condense(old, 9000)}")
            profile = "drafting"
        else:
            raise ValueError(f"未知模式 {mode}，可选 polish/replace/fork")

        r = call(profile, prompt, on_delta, max_tokens=self.g["max_tokens_draft"])
        new = re.sub(r"【字数标记[^】]*】\s*", "", clean(r.text))
        if not new or len(new) < len(old) * 0.4:
            return {"ok": False, "msg": "重写结果过短，已保留原稿",
                    "backup": f".ckpt/{n:03d}_v{ver}.md"}

        self.p.write(self.p.chapter_path(n), new)
        # 验收只用硬黑名单 —— 用全量套话表判分是黑名单分层前的残留,
        # 实测把 97 分的返修稿记成 0 分
        a = audit(new, extra_blacklist=self.hard_blacklist(), target_words=target)
        a["target_words"] = target      # 交付率要用: 没有它就算不出模型少写了多少
        self.p.write(f"audit/{n:03d}.json", json.dumps(a, ensure_ascii=False, indent=2))
        self.p.state.setdefault("summaries", {})[str(n)] = self._extract_state(n, new)
        self.p.mem.add("plot", f"ch{n}", f"第{n}章", new)

        # 剧情有变时提示下游受影响的章节
        affected = []
        if mode in ("replace", "fork"):
            done = sorted(self.p.state.get("done", []))
            affected = [x for x in done if x > n][:5]
        log = self.p._load("edit_log.json", [])
        log.append({"chapter": n, "mode": mode, "note": note, "backup": f"{n:03d}_v{ver}.md",
                    "score_before": None, "score_after": a["score"]})
        self.p.write("edit_log.json", json.dumps(log, ensure_ascii=False, indent=2))
        self._log(f"第{n}章 {mode} 重写 → {a['stats']['cn']}字 得分{a['score']}"
                  + (f"，可能影响后续 {affected}" if affected else ""))
        return {"ok": True, "mode": mode, "score": a["score"],
                "chars": a["stats"]["cn"], "backup": f".ckpt/{n:03d}_v{ver}.md",
                "affected": affected}

    def repair_violations(self, limit: int = 10, dry: bool = False,
                          on_delta=None) -> Dict[str, Any]:
        """按当前规则批量返修旧章 —— 规则是后来长出来的，前面的章享受不到。

        实测: 第 20 章才加的禁用词，改不了 1-19 章，于是全书分被存量永久拖住
        （全书 52 而最近 20 章 74）。这里找出违规最重的章节做 polish 重写，
        剧情不动只改语言，旧稿照例备份到 .ckpt/。
        """
        done = sorted(self.p.state.get("done", []))
        bl = self.blacklist()
        target = self.target_words()
        ranked = []
        for n in done:
            t = self.p.chapter(n)
            if not t:
                continue
            a = audit(t, extra_blacklist=bl, target_words=target,
                      check_modern=self.anachronism_check())
            hits = {w: t.count(w) for w in bl if w in t}
            if a["score"] < self.q["audit_pass_score"] or hits:
                ranked.append({"n": n, "score": a["score"],
                               "violations": sum(hits.values()), "hits": hits})
        ranked.sort(key=lambda x: (-x["violations"], x["score"]))
        picked = ranked[:limit]
        if dry:
            return {"candidates": ranked, "would_repair": [x["n"] for x in picked]}

        fixed = []
        for item in picked:
            n = item["n"]
            note = ("重点清除这些违规表达：" + "、".join(list(item["hits"])[:8])
                    if item["hits"] else "去掉套话与 AI 腔，句式长短交错")
            try:
                r = self.rewrite_chapter(n, mode="polish", note=note, on_delta=on_delta)
                fixed.append({"n": n, "before": item["score"],
                              "after": r.get("score"), "ok": r.get("ok")})
            except Exception as e:
                fixed.append({"n": n, "error": str(e)[:120]})
        self._log(f"批量返修 {len(fixed)} 章: " +
                  "、".join(f"{f['n']}({f.get('before')}→{f.get('after')})" for f in fixed))
        return {"repaired": fixed, "remaining": len(ranked) - len(picked)}

    def _extract_state(self, n: int, text: str) -> str:
        """一次调用抽出: 本章摘要 / 新埋与回收的伏笔 / 角色状态变化 / 时间推进。

        没有这层, 人物会瞬移、伤口会自愈、伏笔埋了永不回收、时间线会错乱 ——
        这些都是单章读起来没问题、连起来一定崩的东西。
        """
        names = [c["name"] for c in self.roster()][:14]
        alias = self.alias_pair()
        alias_note = (f"注意：「{alias[0]}」与「{alias[1]}」是同一个人，"
                      f"角色状态一律记在「{alias[0]}」名下。\n" if alias else "")
        spec = self.ledger_spec()
        prompt = (
            f"读下面这一章，抽取结构化信息。已知角色：{'、'.join(names) or '未知'}\n"
            f"{alias_note}\n"
            f"严格按下面格式输出，没有内容的写「无」，不要任何多余文字：\n"
            f"摘要：（60 字以内一句话概括本章发生了什么）\n"
            f"时间：（本章相对上一章过了多久，如「当天下午」「三日后」）\n"
            f"埋伏笔：（**只记真正的悬念**：被刻意隐藏、以后必须专门写一段来解开的东西。\n"
            f"  算：身份秘密、来历不明的物证与短信、没说破的承诺、可疑的神秘人物、"
            f"被打断的话、未解的警告、莫名消失的东西。本章若出现神秘人/匿名消息/"
            f"未兑现的威胁，必须登记。\n"
            f"  不算：人物性格（如「某人贪婪」）、当前处境（如「某人心态转变」）、"
            f"已经讲明白的事、单纯的剧情推进。\n"
            f"  最多 2 条，宁可写「无」也不要凑数）\n"
            f"收伏笔：（本章解开了之前埋的哪些线索，分号分隔）\n"
            f"角色状态：（格式 姓名=所在地/身体状态/关键持有物，分号分隔，只列本章出场的）\n"
            f"{spec['power']['label']}：（格式 姓名=当前值，分号分隔。"
            f"要记的是：{spec['power']['hint']}。"
            f"**只填当前实际状态，不要把这句说明里的举例或箭头抄进去**；"
            f"只记本章发生变化的，没变化的不用列。没有就写「无」）\n"
            f"{spec['resource']['label']}：（主角方本章的" + spec["resource"]["hint"] + "，"
            f"格式如「期初约X；本章+A(来源)-B(去向)；期末约Y」。没有就写「无变动」）\n"
            # 角色档案是开书时写死的。写到 125 章, 主角已经统兵一方, 注入的卡片
            # 还写着「未入流抄写吏」—— 模型照着过时的卡写, 人物就会往回缩。
            f"身份变更：（谁的身份/职位/势力归属/核心能力发生了**不可逆的变化**，"
            f"格式 姓名=新身份，分号分隔。升官、掌兵、拜师、入门、叛出、"
            f"继位、失势都算；情绪波动、临时处境不算。没有就写「无」）\n"
            # 组织架构同理: 门派/军团/商号/朝堂派系是推动剧情的实体, 一直没人记账,
            # 于是同一个门派的掌门、堂口、实力在不同章节各写各的。
            # 数字条款是最容易前后打架的东西: 竞标底价从第 10 章的八百贯，
            # 到第 14 章变一千、第 15 章变五百 —— 因为「底价」既不是不可逆事实
            # （不是死亡升迁背叛），也不是主角方的收支，三本台账全接不住。
            f"数字约定：（本章**定下或引用**的规则性数字，格式 事项=数值，分号分隔。"
            f"包括：竞标底价/标的价、对手报价、合同期限与分成比例、利率、违约金、"
            f"约定的交付日期与数量、悬赏金额。**只记被当成规则来遵守的数字**，"
            f"不记随口提到的价钱。没有就写「无」）\n"
            f"势力变动：（本章涉及的组织/门派/军团/商号/朝堂派系发生了什么结构性"
            f"变化，格式 势力名=当前掌事者/规模或实力/立场，分号分隔。"
            f"新建、易主、合并、覆灭、结盟、反目都算。没有就写「无」）\n"
            # 误读是这类书最主要的情节发生器：一个动作 × N 个误读者 = N 条新支线。
            # 它必须被记账，否则埋下去就没人管了 —— 和伏笔一样，只是伏笔记的是
            # 「藏起来的真相」，误读记的是「别人信了的假解释」。
            f"误读：（本章有谁**推断错了**？格式 谁|他凭什么这么想|他得出的错误结论|"
            f"他因此做了什么，多条用分号分隔，最多 2 条。\n"
            f"  只记**当事人自己信以为真、并据此行动**的错误判断；"
            f"读者已经知道是错的、而人物也知道自己在猜，那不算。\n"
            f"  没有就写「无」）\n"
            f"收误读：（本章有谁**发现自己之前想错了**？写清是谁的哪个误会被戳破了，"
            f"分号分隔。没有就写「无」）\n"
            # 作废算子的原料: 不记下主角每章靠什么过关, 就没法知道哪一招该过期。
            # 长篇中期原地打转的根子不是敌人不够强, 是主角的解法永远有效。
            f"解法：（主角本章**靠什么**过的关？格式 招数|解决了什么，"
            f"招数写成 4-12 字的路数名，同一路数每次要用**同样的说法**"
            f"（例如一直写「肉身硬抗法器」，不要一次写「铜皮挡剑」一次写"
            f"「硬接一击」）。主角没解决什么就写「无」）\n\n"
            f"{self.condense(text, 9000)}")
        r = call("polishing", prompt, max_tokens=600)
        out = clean(r.text)

        def field(k: str) -> str:
            m = re.search(rf"^{k}\s*[:：]\s*(.*)$", out, re.M)
            v = (m.group(1).strip() if m else "")
            return "" if v in ("无", "None", "-") else v

        summary = field("摘要") or out.splitlines()[0][:60]
        st = self.p.state
        st.setdefault("timeline", {})[str(n)] = field("时间")

        for f in [x.strip() for x in re.split(r"[;；]", field("埋伏笔")) if len(x.strip()) > 3][:2]:
            self.p.mem.add_foreshadow(n, f[:60])
        self._resolve_foreshadow(n, text)

        led = field(spec["resource"]["label"]) or field("资金账")
        if led and "无变动" not in led:
            st.setdefault("ledger", {})[str(n)] = led[:160]
        # 力量/地位账: 修为境界、官职兵权、段位战绩…… 槽位通用, 叫法由题材包定
        for item in [x.strip() for x in
                     re.split(r"[;；]", field(spec["power"]["label"])) if "=" in x]:
            name, _, val = item.partition("=")
            name, val = name.strip(), val.strip()[:70]
            if alias and name == alias[1]:
                name = alias[0]
            if name and val:
                st.setdefault("power", {})[name] = {"at": n, "state": val}
        roles = st.setdefault("roles", {})
        for item in [x.strip() for x in re.split(r"[;；]", field("角色状态")) if "=" in x]:
            name, _, val = item.partition("=")
            name = name.strip()
            if alias and name == alias[1]:      # 别名归一, 免得主角被记成两个人
                name = alias[0]
            if name:
                roles[name] = {"at": n, "state": val.strip()[:80]}
        if alias and alias[1] in roles and alias[0] in roles:
            roles.pop(alias[1], None)

        # 身份变更写进花名册, 让后续章节拿到的是「现在的他」而不是第 1 章的他
        for item in [x.strip() for x in re.split(r"[;；]", field("身份变更")) if "=" in x]:
            name, _, val = item.partition("=")
            name, val = name.strip(), val.strip()[:60]
            if alias and name == alias[1]:
                name = alias[0]
            if name and val:
                st.setdefault("identity", {})[name] = {"at": n, "now": val}

        # 解法台账 —— 作废算子的原料。
        _m = field("解法")
        if _m and "|" in _m:
            _mm, _, _sv = _m.partition("|")
            _mm = _mm.strip()[:24]
            if _mm:
                st.setdefault("methods", []).append(
                    {"at": n, "method": _mm, "solved": _sv.strip()[:60]})

        # 误读台账 —— 一个动作 × N 个误读者 = N 条新支线, 是这类书主要的情节发生器。
        # 记的是「别人信了的假解释」, 与伏笔(藏起来的真相)互补。
        mis = st.setdefault("misreads", [])
        for item in [x.strip() for x in re.split(r"[;；]", field("误读")) if "|" in x][:2]:
            parts = [y.strip() for y in item.split("|")]
            while len(parts) < 4:
                parts.append("")
            who, because, concludes, acts = parts[:4]
            if alias and who == alias[1]:
                who = alias[0]
            if who and concludes:
                mis.append({"at": n, "who": who[:20], "because": because[:80],
                            "concludes": concludes[:90], "acts": acts[:80],
                            "closed_at": 0})
        closed = field("收误读")
        if closed:
            for m in mis:
                if not m["closed_at"] and (m["who"] in closed or m["concludes"][:10] in closed):
                    m["closed_at"] = n
        if len(mis) > 60:                       # 只留最近的, 老的早就结完了
            st["misreads"] = [m for m in mis if not m["closed_at"]][-40:] + \
                             [m for m in mis if m["closed_at"]][-20:]

        # 数字条款台账: 一旦定下就不许改口, 除非正文明写「改标/重议/毁约」
        terms = st.setdefault("terms", {})
        for item in [x.strip() for x in
                     re.split(r"[;；]", field("数字约定")) if "=" in x]:
            k, _, v = item.partition("=")
            k, v = k.strip()[:24], v.strip()[:60]
            if k and v:
                old_v = terms.get(k)
                rec = {"at": n, "value": v}
                if old_v and old_v.get("value") != v:
                    rec["was"] = f"第{old_v['at']}章={old_v['value']}"
                terms[k] = rec
        orgs = st.setdefault("orgs", {})
        for item in [x.strip() for x in re.split(r"[;；]", field("势力变动")) if "=" in x]:
            name, _, val = item.partition("=")
            name, val = name.strip()[:20], val.strip()[:90]
            if name and val:
                orgs[name] = {"at": n, "state": val}
        self.p.save()
        return summary.replace("\n", " ")

    def step_selfcheck(self, on_delta=None) -> Dict[str, Any]:
        """系统级自检 —— 区分「内容问题」与「系统问题」。

        自审守则只能改内容。但有些毛病守则治不了:
          * 检测器漏检 (「瞳孔骤缩」不在黑名单里, 写了 9 次没人管)
          * 检测器误报 (「西门府」被当成行政区, 天天报主场漂移)
          * 约束没被遵守 (称谓规则埋在末尾, 模型压根不看)
          * 配额设错 (10 条剧情写 2600 字, 必然超字数)
        这些要改代码或配置。让系统自己识别出来写成待办, 而不是靠人一章章读。
        """
        done = sorted(self.p.state.get("done", []))
        if len(done) < 5:
            return {"skipped": "章节太少"}
        chs = {n: self.p.chapter(n) for n in done}
        names = [c["name"] for c in self.roster()]
        anchor = self.world_anchor()
        ba = book_audit(chs, characters=names, forbidden_terms=anchor.get("forbidden"),
                        forbidden_people=anchor.get("forbidden_people"),
                        protagonist=names[0] if names else "")
        per = []
        for n in done[-8:]:
            a = self.p._load(f"audit/{n:03d}.json", {})
            if a:
                per.append({"n": n, "score": a.get("score"),
                            "cn": (a.get("stats") or {}).get("cn"),
                            "issues": [i["type"] for i in a.get("issues", [])]})
        cn = self.canon()
        if cn:
            recent_facts = cn[-40:]
            cons.append("【已确立的不可逆事实，绝对不得推翻】\n" + "；".join(
                f"{c['subject']}{c['fact']}(第{c['chapter']}章)" for c in recent_facts))
        era = self.era_card()
        if era:
            cons.append("【时代红线·写进正文即穿帮】\n" + self.condense(era, 3000))
        guide = self.p.read("style_guide.md")
        rules = self.learned_rules()
        target = self.target_words()

        prompt = (
            "你是这套 AI 写作系统的架构师，正在做系统级自检。\n\n"
            f"【目标单章字数】{target}\n"
            f"【逐章检测结果】{json.dumps(per, ensure_ascii=False)}\n\n"
            f"【全书体检】总分 {ba['score']}；最近 {ba.get('recent_range')} 章 "
            f"{ba.get('recent_score')} 分（分差说明前期旧账，重点看后者的趋势）\n问题："
            f"{json.dumps([{'type': i['type'], 'detail': str(i.get('detail'))[:160]} for i in ba['issues']], ensure_ascii=False)}\n\n"
            f"【当前机器规则】{json.dumps(rules, ensure_ascii=False)[:800]}\n\n"
            f"【当前写作守则】\n{guide[:1200]}\n\n"
            f"【最近一章正文节选】\n{chs[done[-1]][:2000]}\n\n"
            "请判断：哪些问题**靠改提示词/守则解决不了**，是系统本身的缺陷？只看这四类：\n"
            "1. 漏检 —— 正文里明显有问题但检测器没报出来\n"
            "2. 误报 —— 检测器报了但其实不是问题\n"
            "3. 约束失效 —— 约束写了但模型明显没遵守（说明位置或写法有问题）\n"
            "4. 配额错误 —— 字数/条数/预算之类的参数设得不合理\n\n"
            "严格输出 JSON（不要代码围栏，没有就给空数组）：\n"
            '{"issues":[{"kind":"漏检|误报|约束失效|配额错误","what":"一句话说清现象",'
            '"evidence":"引用具体证据","fix":"建议怎么改（改哪个模块/参数）"}]}\n'
            "只报**证据确凿**的，最多 5 条。纯内容问题（写得不好看、人物不立体）不要报。")
        r = call("judging", prompt, on_delta, max_tokens=1600)
        m = re.search(r"\{.*\}", clean(r.text), re.S)
        found = []
        if m:
            try:
                found = (json.loads(m.group(0)).get("issues") or [])[:5]
            except Exception:
                pass
        if found:
            log = self.p._load("system_issues.json", [])
            log.append({"at_chapter": done[-1], "book_score": ba["score"], "issues": found})
            self.p.write("system_issues.json", json.dumps(log, ensure_ascii=False, indent=2))
            md = [f"# 系统级待办（自动生成）\n\n> 由 step_selfcheck 产出，"
                  f"这些是**改提示词解决不了**、需要动代码或配置的问题。\n"]
            for e in log[-6:]:
                md.append(f"\n## @第{e['at_chapter']}章（全书 {e['book_score']} 分）\n")
                for i in e["issues"]:
                    md.append(f"- **[{i.get('kind')}]** {i.get('what')}\n"
                              f"  - 证据：{i.get('evidence')}\n"
                              f"  - 建议：{i.get('fix')}\n")
            self.p.write("SYSTEM_ISSUES.md", "".join(md))
        self._log(f"系统自检@第{done[-1]}章 → {len(found)} 条系统级问题")
        return {"issues": found, "book_score": ba["score"]}

    # 规则守门 —— 判断交给模型，正则只做廉价预筛。
    # 之前用一堆正则启发式（拟声词/感官名词/词频…），结果先误杀「脸色铁青」这种
    # 真套话，又漏掉「蔡知县」这种角色称呼，来回调都调不准。这类判断本就需要
    # 理解上下文，应该让模型做。
    _CHEAP_REJECT = re.compile(
        r"[（(].*?[）)]"                                # 带括号的条件说明
        r"|作为|用作|无铺垫|不得|禁止|应当|时候|场景|之类|等等"  # 描述句而非词条
        r"|[A-Za-z]"                                    # 占位符 X/N 或英文
        r"|^\W*$")

    def _cheap_ok(self, w: str) -> bool:
        return bool(w) and 2 <= len(w) <= 12 and not self._CHEAP_REJECT.search(w)

    def gate_rules(self, cands: List[str]) -> Dict[str, List[str]]:
        """让模型判定每个候选词该硬禁、该限频、还是该丢弃。

        返回 {"hard": [...], "soft": [...], "drop": [{"w":…, "why":…}]}
          hard 穿帮词（真朝代名/现代词/真实历史人物）—— 出现即违规
          soft 被用滥的套话 —— 限频，超密度才扣分
          drop 角色称呼 / 剧情道具 / 正常描写 —— 禁掉会误伤，不进规则
        """
        cands = [w.strip() for w in cands if w and w.strip()]
        pre_drop = [{"w": w, "why": "格式不合（含条件说明/占位符/长度异常）"}
                    for w in cands if not self._cheap_ok(w)]
        rest = [w for w in cands if self._cheap_ok(w)]
        if not rest:
            return {"hard": [], "soft": [], "drop": pre_drop}

        roster = "、".join(c["name"] for c in self.roster()) or "（未知）"
        done = sorted(self.p.state.get("done", []))
        sample = "\n".join(self.p.chapter(n)[:700] for n in done[-2:])
        anchor = self.world_anchor()
        prompt = (
            f"你在给一套 AI 写作系统做「禁用词守门」。下面是候选词，"
            f"请判断每个词该怎么处理。\n\n"
            f"【本书】《{self.p.meta.get('title','')}》"
            f"{'（架空世界，国号「'+anchor['dynasty']+'」）' if anchor.get('dynasty') else ''}"
            # real 模式必须点明本书就发生在该朝代, 否则守门模型会把本书自己的年号
            # (洪武二十三年) 判成「真实朝代名 -> 穿帮词」, 全书 42 处合法用法被判违规
            + (f"（真实历史背景：{self.era_brief(120)}，"
               f"本书正是发生在这个朝代，本朝的年号、官职、地名、律法名"
               f"都是本书的正常用语，绝不是穿帮词）"
               if self.p.meta.get("history_mode") == "real" else "") + "\n"
            f"【出场角色】{roster}\n"
            f"【近两章正文节选】\n{sample[:2500]}\n\n"
            f"【候选词】{'、'.join(rest)}\n\n"
            f"分三类：\n"
            f"hard = 穿帮词，出现即错。判断必须以**本书的时代背景**为准：\n"
            f"  本书背景若是 2005 年，「流量/算法/带宽/服务器」当时互联网圈已在用，"
            f"不是穿帮；「大数据/用户画像/日活/扫码」才是。真实朝代名、真实历史人物、"
            f"晚于本书年代出现的词汇与概念，才判 hard。\n"
            f"soft = 被用滥的套话或固定搭配（如「脸色铁青」「目光如刀」），"
            f"偶尔用没问题、频繁用才是毛病，应当限频。\n"
            f"drop = **禁掉会误伤正常写作**的：角色的姓名/职务称呼/别号、"
            f"本书的剧情道具与专有名词、拟声词、必要的感官描写。\n\n"
            f"只输出 JSON（不要代码围栏）：\n"
            f'{{"hard":["…"],"soft":["…"],"drop":[{{"w":"…","why":"一句话理由"}}]}}\n'
            f"每个候选词必须且只能出现在一类里。拿不准就归 drop —— "
            f"误禁一个角色名的代价远大于漏禁一个套话。")
        try:
            r = call("judging", prompt, max_tokens=1200)
            m = re.search(r"\{.*\}", clean(r.text), re.S)
            d = json.loads(m.group(0)) if m else {}
        except Exception as e:
            print(f"[gate] 模型判定失败, 全部保守丢弃: {e}")
            return {"hard": [], "soft": [],
                    "drop": pre_drop + [{"w": w, "why": "守门模型不可用"} for w in rest]}

        hard = [w for w in (d.get("hard") or []) if w in rest]
        soft = [w for w in (d.get("soft") or []) if w in rest and w not in hard]
        judged = set(hard) | set(soft)
        drop = pre_drop + [x if isinstance(x, dict) else {"w": x, "why": ""}
                           for x in (d.get("drop") or [])]
        drop += [{"w": w, "why": "模型未归类，保守丢弃"}
                 for w in rest if w not in judged
                 and w not in {x.get("w") for x in drop if isinstance(x, dict)}]
        return {"hard": hard, "soft": soft, "drop": drop}

    def step_heal(self, on_delta=None) -> Dict[str, Any]:
        """自愈循环 —— 评审抓到的问题必须被消费, 不能只记录在案。

        实测四类问题评审都抓到了却没人处理, 最后靠人工修:
          * 角色档案与剧情演进脱节(赵无极档案还是初版打手, 正文已是集团副总)
          * era 卡缺维度(2005 无做空机制, 评审报了 4 次没人补卡)
          * 误禁词(「流量」2005 年互联网已有, 被当穿帮词禁掉, 全书扣 27 处)
          * 该定点返修的章节没进队列
        分诊交给模型, 执行由框架完成。每次自审后自动跑。
        """
        done = sorted(self.p.state.get("done", []))
        if len(done) < 5:
            return {"skipped": "章节太少"}
        # 收集近期评审的矛盾与高危问题
        findings = []
        for n in done[-10:]:
            c = self.p._load(f"audit/{n:03d}.critique.json", {})
            for x in (c.get("contradictions") or []):
                findings.append({"ch": n, "kind": "contradiction",
                                 "text": str(x.get("fact", ""))[:160]})
            for i in (c.get("issues") or []):
                if i.get("severity") == "high":
                    findings.append({"ch": n, "kind": i.get("dim", ""),
                                     "text": str(i.get("what", ""))[:160]})
        # 全书体检的禁用词命中(供误禁复核)
        names = [c["name"] for c in self.roster()]
        anchor = self.world_anchor()
        ba = book_audit({n: self.p.chapter(n) for n in done},
                        characters=names,
                        forbidden_terms=[w for w in (anchor.get("forbidden") or [])
                                         if w not in set(names)],
                        forbidden_people=anchor.get("forbidden_people"),
                        protagonist=names[0] if names else "")
        banned_hits = {}
        for i in ba.get("issues", []):
            if i["type"] == "禁用术语出现" and isinstance(i.get("detail"), dict):
                banned_hits = i["detail"]
        if not findings and not banned_hits:
            return {"clean": True}

        era_hint = self.era_brief(400)
        prompt = (
            f"你是这套 AI 写作系统的维护者。下面是最近评审抓到的问题与禁用词命中，"
            f"请分诊出系统该自动执行的修复动作。\n\n"
            f"【书的时代背景】{era_hint}\n"
            f"【近期评审发现】\n"
            + "\n".join(f"- 第{f['ch']}章 [{f['kind']}] {f['text']}" for f in findings[:20])
            + f"\n\n【禁用词命中次数】{json.dumps(banned_hits, ensure_ascii=False)}\n\n"
            f"【当前角色档案标题】{'、'.join(names[:20])}\n\n"
            f"输出 JSON（不要围栏），动作类型：\n"
            f'{{"actions":[\n'
            f'  {{"type":"update_profile","name":"角色名","reason":"档案与剧情脱节的具体点"}},\n'
            f'  {{"type":"extend_era","rule":"一条新的时代红线，带年份依据"}},\n'
            f'  {{"type":"unban","word":"被误禁的词","reason":"该词在本书年代真实存在"}},\n'
            f'  {{"type":"repair","ch":章号,"note":"定点返修指令，具体到改什么"}}\n'
            f"]}}\n"
            f"判据：反复出现的同一矛盾 -> update_profile 或 extend_era；"
            f"禁用词在本书年代真实存在且评审从未判它穿帮 -> unban；"
            f"单章硬伤 -> repair。最多 8 个动作，宁缺毋滥。")
        r = call("judging", prompt, on_delta, max_tokens=1400)
        m = re.search(r"\{.*\}", clean(r.text), re.S)
        if not m:
            return {"error": "分诊无输出"}
        try:
            actions = (json.loads(m.group(0)).get("actions") or [])[:8]
        except Exception:
            return {"error": "分诊解析失败"}

        report = []
        for act in actions:
            t = act.get("type")
            try:
                if t == "update_profile" and act.get("name"):
                    report.append(self._heal_profile(act["name"], act.get("reason", "")))
                elif t == "extend_era" and act.get("rule"):
                    card = self.p.read("era_card.md")
                    if act["rule"][:20] not in card:
                        self.p.write("era_card.md",
                                     card + "\n- " + act["rule"].strip())
                        self.p.mem.add("fact", f"era-{len(card)}", "时代红线补充",
                                       act["rule"])
                        report.append(f"era+『{act['rule'][:40]}』")
                elif t == "unban" and act.get("word"):
                    cur = self.p._load("rules.json", {})
                    wl = set(cur.get("whitelist", []))
                    wl.add(act["word"])
                    cur["whitelist"] = sorted(wl)
                    for k in ("forbidden_terms", "tics"):
                        cur[k] = [w for w in cur.get(k, []) if w != act["word"]]
                    self.p.write("rules.json", json.dumps(cur, ensure_ascii=False,
                                                          indent=2))
                    report.append(f"解禁『{act['word']}』")
                elif t == "repair" and act.get("ch"):
                    q = self.p._load("repair_queue.json", [])
                    if not any(x.get("ch") == act["ch"] for x in q):
                        q.append({"ch": int(act["ch"]), "note": act.get("note", "")[:300]})
                        self.p.write("repair_queue.json",
                                     json.dumps(q, ensure_ascii=False, indent=2))
                        report.append(f"返修队列+第{act['ch']}章")
            except Exception as e:
                report.append(f"{t}失败:{str(e)[:40]}")
        self._log("自愈: " + ("；".join(report) if report else "无动作"))
        return {"actions": report}

    def _heal_profile(self, name: str, reason: str) -> str:
        """按 canon 与近章重写某角色的档案段 —— 档案是活文档, 必须随剧情演进。"""
        ch = self.p.read("characters.md")
        m = re.search(rf"(###\s*\d+\.\s*姓名：{re.escape(name)}.*?)(?=###|\Z)",
                      ch, re.S)
        if not m:
            return f"档案无{name}"
        facts = [c for c in self.canon() if name in (c.get("subject") or "")]
        done = sorted(self.p.state.get("done", []))
        recent = "\n".join(self.p.chapter(n)[:600] for n in done[-3:]
                            if name in self.p.chapter(n))
        r = call("judging",
            f"角色档案与剧情已脱节（{reason}）。按既成事实重写该角色档案段，"
            f"格式与原档案完全一致（### N. 姓名：… 与 **字段**：…）。\n\n"
            f"【旧档案】\n{m.group(1)}\n\n"
            f"【已确立事实】\n" + "\n".join(f"- 第{f['chapter']}章 {f['fact']}"
                                            for f in facts[-8:])
            + f"\n\n【近期正文片段】\n{recent[:1500]}\n\n直接输出新档案段，无前言。",
            max_tokens=800)
        new = clean(r.text)
        if new.startswith("###") and len(new) > 80:
            self.save_doc("characters", ch.replace(m.group(1), new + "\n\n"))
            return f"档案更新『{name}』"
        return f"档案更新{name}失败"

    def _merge_rules(self, raw: str) -> Dict[str, Any]:
        """把自审抽出的结构化规则并进 rules.json —— 让模型的发现变成机器强制。

        这是闭环的关键: 自审如果只产出一段文字守则, 模型下次照样犯;
        只有把「严禁出现大汉一词」变成检测器里的 forbidden_terms,
        才会在生成后被真正拦下来。
        """
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            return {}
        try:
            new = json.loads(m.group(0))
        except Exception:
            return {}
        cur = self.p._load("rules.json", {})
        changed = {}
        # 禁用类候选统一交给模型守门, 分成硬禁与限频两档
        wl = set(cur.get("whitelist", []))
        cands = [str(x).strip() for k in ("forbidden_terms", "tics")
                 for x in (new.get(k) or [])
                 if str(x).strip() and str(x).strip() not in wl][:16]
        g = self.gate_rules(cands) if cands else {"hard": [], "soft": [], "drop": []}
        for k, add in (("forbidden_terms", g["hard"]), ("tics", g["soft"])):
            old = cur.get(k, [])
            merged = list(dict.fromkeys(old + add))[:40]
            if merged != old:
                changed[k] = [x for x in add if x not in old]
            cur[k] = merged
        for k in ("must_appear", "drop_roles"):
            add = [str(x).strip() for x in (new.get(k) or []) if str(x).strip()][:8]
            old = cur.get(k, [])
            merged = list(dict.fromkeys(old + add))[:40]
            if merged != old:
                changed[k] = [x for x in add if x not in old]
            cur[k] = merged
        if g["drop"]:
            self._log("守门丢弃: " + "；".join(
                f"{x.get('w')}（{x.get('why','')[:20]}）" for x in g["drop"][:5]))
        if changed:
            self.p.write("rules.json", json.dumps(cur, ensure_ascii=False, indent=2))
            self._log("自审新增机器规则: " + json.dumps(changed, ensure_ascii=False))
        return changed

    def learned_rules(self) -> Dict[str, Any]:
        """读取时二次校验 —— 只在写入时把关不够。

        实测: 加了守门器却没重启长跑, 旧进程照样把「蔡德茂」(出场 496 次的核心
        配角) 写进禁用词表, 于是提示词里赫然写着「禁用: 蔡德茂」。
        规则是外部数据, 每次使用前都要验, 不能信任落盘内容。
        """
        cur = self.p._load("rules.json", {})
        if not cur:
            return cur
        # 读取时只做零成本的格式预筛（模型守门在写入时已做过语义判断）,
        # 防止旧版本进程或手改文件塞进畸形条目
        names = {c["name"] for c in self.roster()}
        # 本书自己的时代锚点(年号/朝代/国号)绝不能进禁用表 —— 守门模型在 real 模式下
        # 会把本书年号误判成穿帮词, 一旦落盘, 全书每次提到自己的年代都算违规
        anchor_words = self.era_words()
        dirty = False
        for k in ("forbidden_terms", "tics"):
            ok = [w for w in cur.get(k, [])
                  if self._cheap_ok(w) and w not in names
                  and not any(aw and aw in w for aw in anchor_words)]
            if len(ok) != len(cur.get(k, [])):
                dirty = True
            cur[k] = ok
        if dirty:
            self.p.write("rules.json", json.dumps(cur, ensure_ascii=False, indent=2))
        return cur

    def _resolve_foreshadow(self, n: int, text: str) -> int:
        """让模型判断本章兑现了哪些伏笔 —— 靠文本匹配永远对不上。

        之前用 2-gram 重叠匹配, 模型报的措辞和埋设时的措辞几乎不重合,
        回收率卡在 4%（216 埋 9 收）。改成把候选清单编号给模型勾选。
        """
        pend = self.p.mem.pending_foreshadow()
        if not pend:
            return 0
        # 优先问最可能被兑现的: 埋得久的 + 最近的
        cands = list({f["id"]: f for f in
                      [x for x in pend if n - x["planted"] >= 20][:8]
                      + [x for x in pend if n - x["planted"] < 20][-10:]}.values())
        listing = "\n".join(f"{i+1}. （第{f['planted']}章）{f['text'][:50]}"
                             for i, f in enumerate(cands))
        prompt = (
            f"下面是一部小说里**尚未兑现的伏笔清单**，以及刚写完的第 {n} 章正文。\n"
            f"判断本章**明确兑现或解开**了哪几条（真相被揭示、承诺被履行、"
            f"疑点被解释、埋下的人或物起了作用）。\n"
            f"只是提到、只是继续铺垫、只是相关，都**不算**兑现。\n\n"
            f"【伏笔清单】\n{listing}\n\n【第{n}章正文】\n{self.condense(text, 9000)}\n\n"
            f"只输出被兑现的编号，逗号分隔，如：2,7。一条都没有就输出：无")
        try:
            out = clean(call("polishing", prompt, max_tokens=120).text)
        except Exception:
            return 0
        if "无" in out and not re.search(r"\d", out):
            return 0
        done_n = 0
        for idx in re.findall(r"\d+", out)[:6]:
            i = int(idx) - 1
            if 0 <= i < len(cands):
                self.p.mem.resolve_foreshadow(cands[i]["id"], n)
                done_n += 1
        return done_n

    def _l2(self, a: int, b: int):
        sm = self.p.state.get("summaries", {})
        body = "\n".join(f"第{i}章：{sm.get(str(i),'')}" for i in range(a, b + 1))
        r = call("polishing", f"把下面 {b-a+1} 章的剧情压缩成 500 字以内的连贯摘要，"
                              f"保留关键人物、转折、伏笔。直接输出：\n\n{body}", max_tokens=1000)
        self.p.write(f"l2_summary/{a:03d}-{b:03d}.md", clean(r.text))
        self._log(f"L2 摘要 {a}-{b} 完成")

    def _usage_snapshot(self) -> Dict[str, Any]:
        return {"calls": USAGE["calls"], "prompt": USAGE["prompt"],
                "completion": USAGE["completion"],
                "total": USAGE["prompt"] + USAGE["completion"],
                "elapsed_s": round(USAGE["elapsed"], 1),
                "by_profile": {k: dict(v) for k, v in USAGE["by_profile"].items()}}

    def _check_budget(self) -> None:
        """daily_call_budget 之前是空壳没人读。0 = 不限。"""
        lim = int(self.cfg["limits"].get("daily_call_budget") or 0)
        if lim and USAGE["calls"] >= lim:
            raise RuntimeError(f"已达调用预算上限 {lim} 次（config/settings.yaml → "
                               f"limits.daily_call_budget），本次停止")

    def save_doc(self, name: str, text: str) -> Dict[str, Any]:
        """前端编辑保存 世界观/角色/总纲/守则/时代卡, 并做联动更新。"""
        FILES = {"basis": "basis.md", "naming": "naming.md",
                 "world_bible": "world_bible.md", "characters": "characters.md",
                 "outline": "outline.md", "style_guide": "style_guide.md",
                 "era_card": "era_card.md"}
        if name not in FILES:
            raise ValueError(f"不可编辑的文档 {name}")
        self.p.write(FILES[name], text)
        out = {"ok": True, "name": name, "chars": len(text)}
        if name == "world_bible":
            out["indexed"] = self.p.mem.index_document("world", "world_bible", text)
            self.p.meta.pop("anchor", None); self.p.save()
        elif name == "characters":
            (self.p.dir / "roster.json").unlink(missing_ok=True)
            out["indexed"] = self.p.mem.index_document("role", "characters", text)
            out["roster"] = [c["name"] for c in self.roster()]
        elif name == "era_card":
            out["indexed"] = self.p.mem.index_document("fact", "era_card", text)
        return out

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.p.state.setdefault("log", []).append(f"[{ts}] {msg}")
        self.p.save()
        print(f"  [{ts}] {msg}", flush=True)


def create_project(title: str, type_id: str, genre_id: str, style_id: str,
                   target_chapters: int, target_words: int,
                   fields: Dict[str, Any], history_mode: str = "auto") -> Project:
    p = Project(slugify(title))
    _, kw = registry.resolve("drafting")
    p.meta = {"title": title, "type_id": type_id, "genre_id": genre_id, "style_id": style_id,
              "target_chapters": target_chapters, "target_words": target_words,
              "fields": fields, "model": kw["model"], "history_mode": history_mode,
              "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    p.save()
    return p
