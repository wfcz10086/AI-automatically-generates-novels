---
name: novel
description: 长篇小说生产（可跑到百万字）。种子发散 → 卷纲 → 章细纲 → 逐章正文 → 更新台账 → 再读再写。用 projects/<书名>/ 下的台账文件当记忆，每章只读需要的那几层，上下文永远有界。适用于新开一本书、续写已有的书、或定点重排某几章。
---

# 长篇小说生产

## 为什么这套流程成立

一百万字 ≈ 346 章 × 2900 字。**不可能靠上下文记住全书**——写到第 300 章时，第 104 章还剩几发子弹早就滑出窗口了。

解法不是更大的上下文，是**把记忆落到文件**：

```
写一章 → 更新台账 → 下一章只读需要的那几层 → 再写
```

每一章的上下文都是有界的（约 3-6 万 token），所以**能一直跑下去**。台账文件才是这本书的大脑，我只是当下这一章的手。

---

## 五条铁律（违反了后面全白做）

### 1. 真读产出，别信统计

**丢弃 0、空洞 0、章数在涨，什么都证明不了。** 实测踩过三次：

- 第 114-117 章被**整段复制**到 122-125，所有字段齐全、守护日志写「收 5 章」，只有用眼睛看才发现
- 子弹账从第 104 章的 105 发涨回第 145 章的 119 发，**烂了 200 章没人吭声**
- 「承诺挨饿」连报三批，查下去 8 条全是误报

每写完一批，**必须真读**，至少读一句话 + 爽点 + 章末钩子。

### 2. 台账每章必更，当场更

写完正文不更新台账，等于没写。下一章读到的还是旧状态，错会一直滚下去。用 `scripts/after_chapter.py`（见下），别手改 JSON。

### 3. 铁律要进每一层提示词

`hard_rules` 不是摆设。实测它只进了章细纲，没进 world_bible / characters / 总纲，于是：

- world_bible 写出「他的阶层天花板是『商』字…的结构性屈辱」
- characters 写出「隐忍 / 待宰肥羊」
- 总纲写出「恐惧→算计→侥幸」

**三处全跟铁律相反，因为那三个提示词里根本没有铁律。** 每次生成任何资产，先把 `hard_rules` 原文贴进去。

### 4. 不许要求返工已经落盘的章

纠偏只能说「**下一批**怎么写」。实测在纠偏里点名「第29章写错了」，模型真的跑去重写第 29 章，连着两批各多吐十几章，全被越界丢弃，白烧 token。

要改旧章，走 `scripts/replan.py`（定点重排，前后不动）。

### 5. 噪声检测器比没有更糟

名额是有限的（纠偏单只并前几条）。**一条假警报挤掉一条真警报。**

今晚亲手造过两个噪声指标：钩子重合度 0.15 报了 166 章脱节（真读全是好的）、死亡正则报了 7 个角色复活（全是「查武大郎的**死**」这种误伤）。**报之前先自己抽查几条。**

---

## 台账文件（projects/<书名>/）

| 文件 | 是什么 | 谁改 |
|---|---|---|
| `project.json` | premise / hard_rules / dials / 目标字数章数 | 人 |
| `world_bible.md` | 世界观常驻层（L1） | 阶段0 |
| `characters.md` `roster.json` | 角色档案 / 出场登记 | 阶段0，新人物随时补 |
| `outline.md` | 总纲 | 阶段0 |
| `volumes.json` `stages.json` | 分卷 / 阶段功能位 | 阶段0 |
| `threads.json` | 支线：`name/kind/span/cadence/last_touched/owner` | **每章更** |
| `state.json` | `promises`（承诺，含 `last_advanced`）、`summaries`、`done`、`outline_guide`（纠偏）、`swept_at`/`selfchecked_at` | **每章更** |
| `ladders.json` | 力量/爽点/人设三条阶梯，每级带「验证」条款 | 阶段0 |
| `chapter_outlines.json` | 章细纲，字段：一句话/承接/出场角色/剧情N/重场/爽点/章末钩子 | 阶段1 |
| `chapters/` | 正文 | 阶段2 |
| `l2_summary/` | 中期摘要（L3） | 每 25 章 |
| `facts.json` | 考据卡（跨书共享库在 `.cache/facts.json`） | 检索时 |

---

## 流程

### 阶段 0 · 种子发散（新书才跑）

从 `project.json` 的 premise + hard_rules + dials 出发，**按顺序**产出，每一步都把铁律原文带进提示：

```
world_bible.md → characters.md → outline.md → volumes.json
              → stages.json → threads.json → state.promises → ladders.json
```

要点：
- **hard_rules 点名的东西强制进 promises**，别指望从总纲里抽。实测 premise 写明「一把手枪一百二十发」，总纲没当承诺，于是 346 章一发没开
- 每条 promise 要有 `done_when`（兑现判据），否则「到底发生过没有」查不了
- ladders 每一级要写「**验证**：第X章应该看到什么」，否则能力线会虚写

也可以直接跑 `python3 run_novel.py init --title <书名>` 让流水线做，我再逐份真读修。

### 阶段 1 · 章细纲（每 5 章一批）

**读**：`outline.md` + `stages.json` 当前阶段 + 上一章的章末钩子 + `state.outline_guide`（上一批的纠偏）+ `threads.json` 里该露面的支线

**写**：`chapter_outlines.json` 的 5 章，字段必须齐：

```
第N章 <章名>
一句话：
承接：      ← 必须接住上一章的章末钩子
出场角色：
剧情1-6：
重场：剧情N
爽点：      ← 当场兑现的小胜，不是「他感到很爽」
章末钩子：
```

**检**（零成本，每批都跑）：

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from server.orchestrator import Project, Novelist
n=Novelist(Project('书名'))
for x in n.outline_finite_check(): print('台账:',x[:200])
for j in n.outline_repairs(): print(j['kind'], j['chapters'], j['demand'][:80])
"
```

把结论**原文并进下一批的 `state.outline_guide`**，别经模型改写（会被吃掉）。

### 阶段 2 · 正文（逐章）

**读这五层，别多读**：

| 层 | 内容 | 来源 |
|---|---|---|
| L1 常驻 | 世界观 + 本章出场角色卡 + 考据硬事实 | `world_bible.md` + `cast_for()` + `facts.json` |
| L2 近期 | 最近 **2 章原文** + 最近 8 章一句话摘要 | `chapters/` + `state.summaries` |
| L3 中期 | `l2_summary/*.md` | 每 25 章压一次 |
| L4 本章 | 本章细纲全文 | `chapter_outlines.json` |
| L5 约束 | **hard_rules 原文** + 本批纠偏 | `project.json` + `state.outline_guide` |

**写**：`chapters/<n>.md`，目标字数见 `project.json.target_words / target_chapters`。

**写完立刻**：

```bash
python3 scripts/after_chapter.py --title <书名> --chapter <n>
```

它会更新 summaries / done / threads.last_touched / promises.last_advanced，并把台账矛盾打出来。

### 阶段 3 · 巡检与自审

- **每 10 章**：跑 `outline_repairs()`，把「支线断线 / 有戏无位 / 张力静默消解 / 台账对不上」并进纠偏
- **每 25 章**：压一份 `l2_summary/`，跑一次自审（钩子类型、章名字数、爽点密度）
- **每 50 章**：查高潮密度——**少于 2 个高燃点就是死区**

高潮密度速查：

```bash
python3 - <<'PY'
import json,re,collections
d=json.load(open('projects/书名/chapter_outlines.json'))
DEATH=re.compile(r'击杀|斩杀|处决|毙|战死|牺牲|灭火|斩首|爆头')
GUN=re.compile(r'开枪|扣动扳机|一枪|枪响')
PUB=re.compile(r'当众|当场|众目|公开宣|阶下')
REV=re.compile(r'反杀|反将|反咬|翻盘|将计就计|识破|拆穿|逼退|逼降')
POW=re.compile(r'接管|夺回|正式确立|登基|坐上|收服|归附|取代')
pk=[n for n in sorted(map(int,d)) if
    (2 if DEATH.search(d[str(n)]) else 0)+(2 if GUN.search(d[str(n)]) else 0)
   +(1 if PUB.search(d[str(n)]) else 0)+(1 if REV.search(d[str(n)]) else 0)
   +(1 if POW.search(d[str(n)]) else 0) >= 5]
print('高潮章:',pk)
g=[pk[i+1]-pk[i] for i in range(len(pk)-1)]
print('最大断档 %d 章'%max(g) if g else '')
PY
```

**断档超过 20 章就要塞高潮。** 实测本书 101-150、201-250 各有 50 章零高潮，最长断档 75 章——番茄读者的留存窗口只有 3 章。

死区成因通常是**题材没给暴力出口**：权谋段全是账目文书，打不起来。要么给这一段安排能见血的冲突，要么把这一段压短。

---

## 收工前自检

```bash
# 1 连续性：有没有空洞
python3 -c "
import json;d=json.load(open('projects/书名/chapter_outlines.json'))
N=max(map(int,d));print('空洞:',[n for n in range(1,N+1) if str(n) not in d] or '无')"

# 2 查重：新写的章跟前文是不是撞了
python3 -c "
import json,difflib
d=json.load(open('projects/书名/chapter_outlines.json'))
ks=sorted(map(int,d))
for i in ks[-10:]:
  for j in ks:
    if j>=i: break
    if difflib.SequenceMatcher(None,d[str(i)][:400],d[str(j)][:400]).ratio()>0.75:
      print('!! 重复',i,j)"

# 3 台账：有限资源有没有涨回去
# （见阶段1的 outline_finite_check）

# 4 死人有没有复活：拿确切死亡章号核，别用正则猜
```

**跑测试再提交**（踩过两次先 push 后跑测试）：

```bash
python3 -m pytest tests/unit -q
```

---

## 改代码时

- **替换要唯一**：`str.replace` 前先 `assert s.count(old)==1`
- **别拆散装饰器**：往类里插方法，先看清楚上一个 `@property` 在哪
- **改完就提交**，作者 `wfcz10086 <ssss@ssss>`，不带任何 AI 署名
- 守护在跑时改代码会**热轮转**（`cmd_outline` 退出码 3），当前批次跑完才生效——所以纠偏文案的改动，要隔一批才看得到效果

---

## 常见静默故障（都真实发生过）

| 现象 | 真因 |
|---|---|
| 检测器从不报警 | 它返回空。如 `_finite_units()` 要求「数量」和「只减不增」在同一条铁律里，而它们分在两条 |
| 报警了但没人改 | 结论没有消费者。检测器只报不修 = 有生产者没消费者 |
| 报警被挤掉 | 名额被常驻约束占满（铁律不该参与「挨饿」判定） |
| 报警太晚 | 硬矛盾住在每 25 章一次的通道里，该走每批都跑的那条 |
| 说了但改不对 | 门槛和判据不是同一个数，等于给了个够不着的靶子 |
| 改了旧章反而更糟 | 纠偏里点名旧章，模型跑去返工，全被越界丢弃 |
