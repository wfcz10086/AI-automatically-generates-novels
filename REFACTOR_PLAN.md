# AI 小说创作助手 · 重构方案 v1

> 目标：从「写死三级流水线的单页 jQuery 应用」→ **可插拔的长文本创作引擎**
> 内容类型（小说 / 剧本 / 短剧 / 漫画脚本）、题材知识、模型供应商、导出格式 —— 全部插件化。
>
> 编写日期：2026-09-05 ｜ **状态：已全部执行完毕（P0–P5）**，本文档保留作设计记录。
> 实现与当前能力以 [`README.md`](README.md) 与 [`TODO.md`](TODO.md) 为准 ——
> 落地过程中有多处偏离本方案（如世界基底从三种扩到七种 + 模型判定的基底卡、
> 台账口径改为题材包声明），以代码为准。

---

## 0. 一句话结论

**代码里不该出现「大纲」「章节」「正文」这三个词，只该出现「层级」「节点」「模板」。**
「小说」和「剧本」都降级成一份 JSON 配置。题材知识从 3 行字符串升级为 `/opt/x10086` 里那 23 份专业 skill。

---

## 0.5 核心与卖点（重新定位）

### 核心不是「AI 写小说」

AI 写小说是**能力**，不是**产品**。任何人打开 ChatGPT 都能让它写一章。这件事没有壁垒。

本项目真正不可替代的核心是三样东西 —— **它们都是模型能力再强也不会自动获得的**：

| 核心 | 说明 | 为什么模型替代不了 |
|---|---|---|
| **① 领域约束库** | 把网文 know-how 编码成机器可执行的规则：修真的境界体系不许乱改、练气期该占 30-50 章、这 6 句是套话黑名单、女性角色不能只当奖励… | 模型知道"什么是修真"，但不知道"**这个工作室**要的修真长什么样"。约束是资产，不是提示词技巧 |
| **② 长程一致性引擎** | 100 万字里角色不崩、境界不乱、地名不飘、伏笔能回收 | LLM 没有记忆。80K 上下文装不下 100 万字。**必须靠外部档案 + 滚动摘要 + 交叉校验**，这是工程活不是模型活 |
| **③ 人在环的分层可控** | 大纲/细纲/正文逐层可编辑、选中右键局部改写、任意章回滚重写、状态永不丢 | ChatGPT 是"一次性生成，不满意重来"；作者要的是"**我的框架，AI 填充，我随时接管**" |

**一句话：模型是耗材，约束库 + 一致性引擎 + 人机协同工作流才是资产。**
这也正是为什么必须做模型抽象 —— 见 §0.5.3。

### 0.5.2 卖点重排序：从「小说生成器」到「叙事 IP 多态生产线」

**「支持剧本」的真正商业意义不是多一个类型，是打开 IP 复用。**

一部小说的世界观 / 角色档案 / 大纲，本来就是短剧、漫画、有声书的上游资产。现在工作室的做法是：小说写完，再找编剧从头改成剧本 —— 设定重录一遍，人设重写一遍。而在本架构下，`world_bible` + `characters.db` + `outline` 是**共享层**，换一个 ContentType 就换一种输出形态：

```
                        ┌──→ 小说正文      (txt / epub / docx)
  一次设定              ├──→ 影视剧本      (fountain / fdx)
  world_bible ─────────→├──→ 短剧分集台本  (含分镜 / srt 字幕)
  characters.db         ├──→ 漫画分镜脚本  (分格 + 画面描述)
  outline               └──→ 有声书稿      (角色标注 / 停顿标记)
```

按对工作室的价值排序：

| # | 卖点 | 强度 | 谁能抄 |
|---|---|---|---|
| 1 | **一稿多态**：一次设定 → 小说/剧本/短剧/漫画/有声书 | ★★★★★ | 需要同时具备 ①②③，抄不动 |
| 2 | **百万字不崩**：世界观/人设/伏笔一致性引擎 | ★★★★★ | 纯工程硬功夫 |
| 3 | **23 个题材专业包内置**：境界体系/节奏表/套话黑名单 | ★★★★☆ | 是积累，不是代码 |
| 4 | **全程可控可回滚**：分层编辑 + 局部重写 + 版本回滚 | ★★★★☆ | 交互设计门槛 |
| 5 | **产出质量可量化**：AI 味审查 + 一致性体检 + 裁判打分 | ★★★★☆ | 需要评估体系（§6） |
| 6 | 模型无关：115 个模型随意切换/混编 | ★★★☆☆ | **及格线，不是卖点** —— 但它是 1-5 的地基 |

> 短剧目前是内容行业最赚钱的形态。**能把小说一键转成短剧分集台本，比"能写小说"值钱得多。**

### 0.5.3 为什么"模型无关"是地基而不是卖点

看这个网关的模型列表就明白了：`qwen3.5-plus / qwen3.6-plus / qwen3.7-flash / qwen3.7-max / qwen3.8-flash / qwen3.8-max` **六代并存**，迭代周期以月计。

- 绑死一个模型 → 工具是模型的附庸，模型一换全部重调，资产归零
- 抽象掉模型 → 约束库、一致性引擎、评估基准**跨模型复利**，换模型只是换配置

所以模型抽象层不是"支持多家厂商"这种功能点，它是**让 ①②③ 三项资产不随模型贬值的保险**。


---

## 1. 现状盘点（已实测，非推测）

### 1.1 代码体量
| 位置 | 行数 | 问题 |
|---|---|---|
| `templates/index.html` | 1089 | HTML + 核心引擎 JS 混在一起（`:371-1076` 是 `<script>`） |
| `static/*.js` | 14043 | 无模块系统，全局函数，jQuery，`ai-update.js` 单文件 3042 行 |
| `app.py` | 85 | 硬编码 key/endpoint/模型 |
| `apps/` + `app各大模型/` | 1086×2 | **diff 验证：8 个文件字节级完全相同**，纯重复 |

### 1.2 五个结构性缺陷

1. **DOM 即数据库** — `saveState()`(`index.html:373`) 遍历 `.chapter-container` 读 `.val()` 拼 JSON 存 `localStorage['novelState']`(`:418`)。字段硬编码 `background/characters/relationships/plot/style/outline/chapters[]`。剧本要的 logline/幕/场次/对白，一个都对不上。
2. **三级流水线焊死** — `generateOutline()`(`:501`) → `generateChaptersFromOutline()`(`:524`) → `generateContent()`(`:605`)，函数名/DOM id/storage key/分隔符 `###fenge`(`:571`) 全绑死"大纲-章节-正文"。**issue #12 要插一层"小纲"就得改 6 处 + HTML。**
3. **提示词渲染有雷** — 链式 `.replace('${x}', v)`，JS 传字符串只替换**第一次出现**。当前模板恰好没有重复占位符（已扫描确认），所以还没炸；但用户自己写两次 `${background}` 第二个就静默失效。且注入内容无长度控制。
4. **`config.js` 组合爆炸** — 每题材 = 3 段提示词 + 10 条菜单硬拷贝。加"剧本"维度变 N×M。
5. **存储天花板** — localStorage 5MB。issue #12 要 100+ 章，必爆。

### 1.3 ⚠️ 实测：三个模型，三种 delta 字段命名 —— 现有 app.py 必然出空白

已实测三条通路（2026-09-05）：

| 通路 | 模型 | 思考字段名 | 正文字段 | 首字 | 备注 |
|---|---|---|---|---|---|
| **gether 网关**<br>`${NOVEL_GW3_URL}` | `qwen3.8-max` | `reasoning_content` | `content` | 3.1s | **115 个模型同一入口** |
| 同上 | `qwen3.8-flash` | `reasoning_content` | `content` | 2.7s | 便宜快 |
| 同上 | `qwen3.7-max` | `reasoning_content` | `content` | 15.7s | 思考极重 |
| **自建 vLLM**<br>`${NOVEL_GW2_URL}` | `Qwen3.6-35B` | **`reasoning`** ← 非标准 | `content` | **0.4s** | 私有/低延迟/免费 |

三种命名：`content` / `reasoning_content`（DeepSeek 系约定）/ `reasoning`（vLLM 自定义）。
更糟的是 Qwen3.6-35B 在某些 prompt 下**把答案整个放进 `reasoning` 并包在 `<answer>` 里，`content` 全空**（已复现）。

`app.py:44` 只读 `chunk.choices[0].delta.content` → 这类 chunk 直接丢弃 → **前端一片空白**，正是 issue #13 #14 那种"没反应"的体感。

**结论：Provider 响应适配器不是可选优化，是硬门槛。**

### 1.3.1 ⚠️ 实测：创作任务开思考 = 纯烧钱

同一 prompt（写 300 字修真正文）：

| 模型 | 思考 | 思考字数 | 正文字数 | **浪费率** | 首字 | 总耗时 |
|---|---|---|---|---|---|---|
| qwen3.8-max | 开 | 74 | 297 | 19.9% | 3.5s | 11.8s |
| qwen3.8-max | **关** | 0 | 317 | **0%** | 3.1s | **6.8s** |
| qwen3.8-flash | 开 | **2186** | 338 | **86.6%** | 17.9s | 19.7s |
| qwen3.8-flash | **关** | 0 | 322 | **0%** | **2.7s** | **8.8s** |
| Qwen3.6-35B(自建) | — | 0 | 405 | 0% | **0.4s** | **2.1s** |

**qwen3.8-flash 开思考：为 338 字正文烧掉 2186 字思考，首字慢 6.6 倍。**
批量写 300 章时这是数倍的成本和工期差。

**结论：创作类调用默认 `enable_thinking=false`；只在"大纲规划 / 一致性体检 / critic 打分"这类推理任务开思考。**
这条必须写进 Provider 配置的 per-task 策略，不能全局一刀切。

### 1.4 GitHub Issues：12 条全 Open

| 类 | Issue | 本方案对应阶段 |
|---|---|---|
| 部署阻塞 | #17 requirements 缺 openai、#16 `/` 404、#13 无法启动、#6 链接打不开 | **P0** |
| 真需求 | #12 大纲下加小纲→100+章 | **P2**（配置改一行） |
| 真需求 | #5 拆书结果无出口 | **P3** |
| Bug | #9 AI 助手最小化后展不开 | **P1** |
| 生态 | #15 接入 302.AI | **P1**（加一个 provider 文件） |
| 运营 | #14 #10 #8 #7 二维码/加群 | 非代码 |

---

## 2. 可复用资产盘点：`/opt/x10086` 的 skills

这是本次重构**最大的增值来源**。现在 `config.js` 里一个题材的提示词是 3 行字符串；skill 里是几千字的专业规则。

| Skill | 内容 | 怎么用 |
|---|---|---|
| **23 个 `novel-*` 题材包**<br>修真/玄幻/武侠/都市/科幻/言情/悬疑/宫斗/末世/重生/穿越/系统流/无限流/西幻/轻小说/电竞/军事/历史/历史架空/诡异/种田/… | 每份含：核心爽点、**标准境界/力量体系**、人物配置、金手指规则、**必守节奏（各阶段章节数）**、**套话黑名单** | → 题材插件 `packs/genre/*.json`，替换 `config.js` 的字符串提示词 |
| **`novel-common`** | 跨题材红线：9 类常见坑（开局杀手/主角崩/反派降智/女性角色/时间线崩/对话全台词/节奏崩/世界观漏洞/结尾雷） | → 全局约束，每次生成都注入 |
| **`long-novel`** | 10万~100万字工程架构：`world_bible.md` + `characters.db`(SQLite FTS5) + `state.json` + **每 10 章压 500 字的 L2 滚动摘要** + `PROJECT_BOARD.md` + `.ckpt/` 回滚 | → **服务端项目目录结构直接照抄**，这是"真正能自动写长篇"的答案 |
| **`anti-ai-tell-audit`** | 6 类 AI 味检测 + **现成 Python 审查脚本**：`【】`心理描写、套话黑名单（通用+小说各十几条）、句式重复、空洞形容词、结构问题（开头不抓人/结尾无钩子） | → **评估体系 L1 层直接落地**，且可做"不合格自动重写"闭环 |
| **`outline-generation`** | 大纲→并行逐章→合并，含结构化大纲协议 `===OUTLINE_START===` | → 编排层协议参考 |
| **`novel-writing`** | 章节三种重写模式：replace / polish / fork + 自动备份 | → 编辑器右键菜单语义化 |
| **`e2e-self-test`** | `run.py` + `lib/actor.py` + `TEST_PLAN.md` 的 E2E 骨架 | → Playwright 测试直接套这个模式 |
| **`browser-automation`** | Playwright 操作规范：定位→判断→操作→等 DOM→提取 | → 测试写法参考 |

环境已就绪：`playwright-python` 已装，`~/.cache/ms-playwright` 有 chromium-1234。

---

## 3. 目标架构：四个可插拔层

```
┌──────────────────────────────────────────────────────────────┐
│  UI 层  (renderer)                                            │
│  面板/层级/右键菜单 全部由 ContentType 配置驱动渲染，无硬编码    │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│  编排层 (orchestrator)  ← "真正自动写小说"在这里               │
│  world_bible → outline → 分章 → 逐章正文 → 自审 → 重写 → 导出  │
│  断点续写 / checkpoint / 滚动摘要控上下文                       │
└──┬────────────┬─────────────┬──────────────┬─────────────────┘
   │            │             │              │
┌──▼────┐  ┌────▼──────┐  ┌───▼───────┐  ┌───▼────────┐
│Provider│  │ContentType│  │ GenrePack │  │  Exporter  │
│  插件  │  │   插件    │  │   插件    │  │    插件    │
├────────┤  ├───────────┤  ├───────────┤  ├────────────┤
│qwen3.6 │  │ novel     │  │ 修真      │  │ txt / md   │
│deepseek│  │ screenplay│  │ 玄幻      │  │ docx       │
│claude  │  │ shortdrama│  │ 都市 …    │  │ fountain   │
│openai  │  │ comic     │  │ (23 个    │  │ fdx        │
│gemini  │  │ game      │  │  skill    │  │ srt        │
│ollama  │  │ …         │  │  转来)    │  │ epub       │
│302.AI …│  │           │  │           │  │            │
└────────┘  └───────────┘  └───────────┘  └────────────┘
   放一个文件即扩展，拔掉即卸载（借鉴 x10086 插件哲学）
```

### 3.1 Provider 插件（模型接入）

一个文件一个供应商，注册即可用：

```python
# providers/qwen_vllm.py
class QwenVLLMProvider(BaseProvider):
    id, name = "qwen-vllm", "Qwen3.6 (vLLM 自建)"
    def stream(self, messages, **kw) -> Iterator[Delta]:
        ...
    def adapt(self, chunk) -> Delta:
        """★ 关键：处理非标准字段"""
        d = chunk["choices"][0]["delta"]
        # 兼容 reasoning / reasoning_content / thinking 三种命名
        think = d.get("reasoning") or d.get("reasoning_content") or d.get("thinking")
        text  = d.get("content")
        return Delta(text=text, reasoning=think)
```

- **前端分离渲染**：`reasoning` 折叠进"思考过程"面板，`content` 进正文框。用户看得见模型在想，也不会把思考污染到正文。
- **兜底策略**：整段流结束若 `content` 为空但 `reasoning` 非空 → 从 reasoning 里提 `<answer>…</answer>`，再兜底整体降级。**这条直接解决 1.3 的空白问题。**
- 配置化 `config/providers.yaml`，key 走 `.env`，**不再硬编码进代码**（现有 `app.py:9-10` 的 key 已进 git 历史，等于公开泄露，P0 必须吊销）。
- `apps/` 八个文件合并成八个 provider，删掉重复目录 `app各大模型/`。#15 的 302.AI = 新增一个文件。


#### 3.1.1 模型分层调度（成本核心）

不同环节该用不同档位的模型，这是长篇批量生产的成本关键：

| 环节 | 模型档位 | 思考 | 理由 |
|---|---|---|---|
| 世界观 / 总纲规划 | `qwen3.8-max` / `claude-opus-5` | **开** | 结构性推理，值得花钱，一次性 |
| 分章细纲 | `qwen3.8-max` | 开(低预算) | 承上启下，质量敏感 |
| **逐章正文（占 90% 调用量）** | **`Qwen3.6-35B` 自建 / `qwen3.8-flash`** | **关** | 见 §1.3.1，实测关思考不降质但快 2-6 倍 |
| AI 味自审 / 批量润色 | 自建 vLLM | 关 | 高频低价值，跑本地 |
| 一致性体检 / critic 打分 | 换一家（`claude-*` / `deepseek-*`） | **开** | 需推理，且**必须换厂避免自我偏好** |

配置形态（`config/providers.yaml`）：

```yaml
gateways:
  gether:                                    # 一个网关 = 115 个模型
    type: openai_compat
    base_url: ${NOVEL_GW3_URL}
    api_key: ${NOVEL_GW3_KEY}               # 走 .env，不进代码
    reasoning_field: reasoning_content
  local_vllm:
    type: openai_compat
    base_url: ${NOVEL_GW2_URL}
    api_key: ${NOVEL_GW2_KEY}
    reasoning_field: reasoning               # ★ 非标准命名在这里声明

profiles:                                    # 任务 → 模型 的映射，用户可改
  planning:   {gateway: gether,     model: qwen3.8-max,   thinking: true}
  drafting:   {gateway: local_vllm, model: Qwen3.6-35B,   thinking: false}
  polishing:  {gateway: gether,     model: qwen3.8-flash, thinking: false}
  judging:    {gateway: gether,     model: claude-sonnet-5, thinking: true}
```

**双通道策略**：gether 网关吃广度（115 模型，随时切最新代）+ 自建 vLLM 吃成本与隐私（0.4s 首字、免费、稿件不出内网 —— 工作室很在意这点）。任一通路挂了自动降级到另一条。

### 3.2 ContentType 插件（小说 / 剧本 / …）

```jsonc
// packs/type/screenplay.json
{
  "id": "screenplay", "name": "影视剧本",
  "fields": [                        // 左侧面板由此渲染，不再写死 5 个 textarea
    {"id":"logline","label":"一句话故事","type":"text"},
    {"id":"roles","label":"角色表","type":"table",
     "columns":["角色","年龄","身份","人物小传"]},
    {"id":"scenes","label":"主要场景","type":"list"},
    {"id":"style","label":"影像风格","type":"text"}
  ],
  "levels": [                        // 任意深度，不再写死三级
    {"id":"story","name":"故事大纲","prompt":"@tpl/screenplay/story"},
    {"id":"act","name":"分幕","prompt":"@tpl/screenplay/act","splitter":"###fenge"},
    {"id":"scene","name":"分场","prompt":"@tpl/screenplay/scene",
     "splitter":"^\\s*\\d+[、.]?\\s*(内景|外景|INT|EXT)",
     "context":["parent","prevSiblings:2","kb:auto","worldbible"]},
    {"id":"script","name":"剧本页","prompt":"@tpl/screenplay/page","editor":"fountain"}
  ],
  "menus": {"scene":[{"name":"加冲突","prompt":"…"}]},
  "exporters": ["txt","fountain","fdx","docx"]
}
```

小说是同一 schema 的另一实例：`levels = [大纲, 章节细纲, 正文]`。
**issue #12 的"小纲" = 往 novel 的 `levels` 里插一个元素，零代码改动。**

首批交付 5 个类型：`novel` / `screenplay`(影视剧本) / `shortdrama`(短剧) / `comic`(漫画分镜) / `report`(通用长文，白送)。

### 3.3 GenrePack 插件（题材知识，来自 x10086 skills）

把 `novel-xiuzhen/SKILL.md` 这类 Markdown 转成结构化数据：

```jsonc
// packs/genre/xiuzhen.json  ← 由 novel-xiuzhen/SKILL.md 转换
{
  "id":"xiuzhen","name":"修真仙侠",
  "corePleasure":["长生与大道","师门传承","历劫飞升"],
  "powerSystem":{"name":"境界体系","locked":true,
    "levels":["练气(1-9层)","筑基","金丹","元婴","化神","炼虚","合体","大乘","渡劫","飞升"],
    "rules":["练气 9 层内无天劫","筑基必有筑基雷劫","金丹分九品莲花，品阶决定上限","元婴可出窍分神"]},
  "pacing":[{"stage":"练气","chapters":"30-50"},{"stage":"筑基","chapters":"40-60"},
            {"stage":"金丹","chapters":"60-80"}],
  "castTemplate":{"protagonist":"散修/世家/外门弟子，资质差但有奇遇",
                  "mentor":"1 位真传师父（中期去世推高潮）","companion":"道侣 1-2（不后宫）"},
  "goldenFinger":{"allow":["系统/戒指老爷爷（给功法不给境界）","先天灵根/天生道体"],
                  "deny":["加速修炼过于离谱"]},
  "clicheBlacklist":["大道三千，殊途同归","天地不仁，以万物为刍狗","此乃吾辈修仙之不二法门",
                     "师祖留下一线生机","你等凡夫俗子","我辈修仙为求长生"]
}
```

组装策略：**`novel-common`（全局红线）+ `genre`（题材规则）+ `type`（结构）+ 用户设定 → 提示词**。
三者正交，不做笛卡尔积复制。`clicheBlacklist` 双路使用：① 生成时作为 negative constraint 注入；② 生成后交给审查脚本判分。

> 现有 `config.js`/`mode-shortcut.js` 里那几百条修真词条不丢，作为 `genre` pack 的 `shortcuts` 段并入。

### 3.4 Exporter 插件

`txt` / `md` / `docx`（小说）· `fountain` / `fdx`（剧本，工作室必需）· `srt`（短剧字幕）· `epub`。
统一接口 `export(project, nodes) -> bytes`。**"支持导出剧本/小说/大纲"在这一层落地**，可选导出层级（只导大纲 / 只导正文 / 全导）。

---

## 4. 数据模型

```ts
Project {
  id, name, typeId, genreId, providerProfile,
  fields: { [fieldId]: any },        // 由 ContentType.fields 决定
  root: Node,                        // 一棵树，深度由 ContentType.levels 决定
  worldBible: string,                // 世界观圣经（压缩版 ~2000 字）
  characters: Character[],           // 角色档案，做一致性校验
  summaries: { [range]: string },    // L2 滚动摘要，每 10 章 500 字
  foreshadowing: Foreshadow[],       // 伏笔清单：埋于第N章 / 是否回收
}
Node { id, level, index, title, body, meta, status, children[] }
```

**存储双写：**
- 浏览器 `IndexedDB`（解 5MB 上限）+ 版本化迁移（老 `novelState` 自动升级，不丢老用户数据）
- 服务端项目目录（**照抄 long-novel 结构**，让 CLI/agent 也能接管）：
  ```
  projects/<name>/
    PROJECT_BOARD.md  world_bible.md  outline.json  state.json
    style_guide.md    characters.db    timeline.md   edit_log.md
    chapters/001.md … + .ckpt/        l2_summary/001-010.md …
  ```

---

## 5. 「真正能自动写小说」= 编排层

这是核心诉求。**80K 上下文写不了 100 万字，靠的是外部档案 + 滚动摘要，不是靠长上下文。**

### 5.1 自动流水线

```
① 立项      用户给：题材 + 一句话设定 + 目标字数/章数
② 世界观    生成 world_bible.md（注入 genre.powerSystem，境界体系不许乱改）
③ 角色      生成 characters.db（按 genre.castTemplate）
④ 总纲      生成 outline.json（按 genre.pacing 分配各阶段章节数）
⑤ 分章细纲  逐段生成，每段带前后文
⑥ 逐章正文  ← 循环体
     context = world_bible + style_guide + 最近 3 章摘要
             + 对应 L2 摘要 + 本章细纲 + 角色卡(相关) + genre 红线
⑦ 自审      跑 anti-ai-tell 脚本 → 不合格自动 polish 重写（最多 N 轮）
⑧ 状态更新  写 state.json / timeline.md / 摘要 / 伏笔清单 / PROJECT_BOARD.md
⑨ 每 10 章  压 L2 摘要；每 30 章 critic 做一致性 + 伏笔回收体检
⑩ 导出
```

### 5.2 三个必须做对的工程点

1. **上下文预算器** — 每次组装按 token 预算裁剪，优先级：本章细纲 > 世界观 > 最近摘要 > 角色卡 > 远期摘要。qwen3.6 上限 80000，留足输出空间。
2. **断点续写** — 每章 checkpoint。关掉浏览器、重启服务、换模型都能从 `state.json` 接着写。这是长篇的生死线。
3. **失败重试与降级** — 流中断/空输出（见 1.3）自动重试；连续失败标红写进 `PROJECT_BOARD.md` 已知问题，不静默吞掉。

### 5.3 交互模式

- **手动档**：现有体验完全保留（逐级点按钮），老用户零迁移成本
- **半自动**：选中若干章 → "批量生成正文"
- **全自动**：设定 → 开跑 → 进度条 + 实时流 + 随时暂停/干预/回滚

---

## 6. 评估方案（三层 + 回归基准）

> 没有评估就没有优化。目标：**改一次提示词，能量化知道变好还是变坏。**

### L1 机器指标（自动，秒级，来自 `anti-ai-tell-audit` 现成脚本）

| 指标 | 判定 | 通过线 |
|---|---|---|
| `【】`心理描写 | 正则计数 | ≤ 2 次/章 |
| 散文里的 `**粗体**` / `---` / markdown 表格 | 正则 | 表格分隔符 = 0 |
| prompt 残留（`[THINK]`/`【需求分析】`） | 正则 | = 0 |
| 通用套话（综上所述/毫无疑问/众所周知…） | 计数 | ≤ 1 次/千字 |
| 小说套话（瞳孔一缩/冷笑一声/缓缓+动词…） | 计数 | ≤ 2 次/千字 |
| **题材专属黑名单**（genre pack 提供） | 计数 | = 0 |
| 空洞形容词（非常/十分/极其…） | 密度 | ≤ 3‰ |
| 句式重复（连续 3 段同开头/连续 5 句纯"A说"） | 模式匹配 | = 0 |
| 字数达标率 | 实际/目标 | 90%–120% |

### L2 结构指标（自动，规则+轻量 LLM）

| 指标 | 说明 | 通过线 |
|---|---|---|
| 章节完成率 | 有正文/总章数 | 100% |
| **设定一致性** | 角色名/境界/地名/年龄 跨章交叉校验（characters.db 比对） | 冲突 = 0 |
| **伏笔回收率** | 埋点数 vs 回收数 | 结尾前 ≥ 85% |
| 对话占比 | 引号内字数/总字数 | 30%–50% |
| 每章推进点 | LLM 抽取"本章发生了什么" | ≥ 1 |
| 爽点间隔 | 大事件章号间距 | ≤ 5 章 |
| 段落长度方差 | 防"全是 50 字段落" | 方差 > 阈值 |

### L3 LLM-as-Judge（critic agent 打分，1–5 分）

维度：**开头钩子 / 人设立体度 / 节奏张弛 / 文风统一 / 爽感 / 对话自然度 / 世界观自洽**。
- 用**不同于生成模型**的模型当裁判（避免自我偏好）
- 每次评估随机抽 5 章 × 3 次取均值，降方差
- 输出扣分理由，直接喂回 polish 环节

### 回归基准集（关键）

固定 **5 个题材 × 3 个固定种子设定 = 15 个基准项目**，每个跑前 10 章。
每次改提示词/换模型/改上下文策略 → 全量跑一遍 → 出对比报告：

```
基准跑分 2026-09-XX  vs  baseline
题材        L1均分   L2一致性   L3裁判分   Δ
修真        92 (+4)  0 冲突     3.8 (+0.3) ↑
玄幻        88 (-1)  2 冲突     3.5 (0.0)  →
悬疑        95 (+7)  0 冲突     4.1 (+0.5) ↑↑
```

**验收线（v1）**：L1 均分 ≥ 85，L2 一致性冲突 = 0，L3 均分 ≥ 3.5，且连续 10 章无人工干预跑通。

---

## 7. Playwright 测试方案

这是**纯前端应用，业务逻辑全在浏览器里**，只有 E2E 能真正验证。分三层：

| 层 | 工具 | 对象 | 跑多久 |
|---|---|---|---|
| 单测 | vitest | 纯函数：模板渲染、分割器、上下文预算器、类型包校验、审查脚本 | < 5s |
| 集成 | vitest + mock SSE | Provider 适配器（**含 1.3 的 reasoning 分支**）、编排状态机 | < 30s |
| **E2E** | **Playwright + 真实 Qwen3.6** | 全链路 | 3–10 min |

### E2E 用例清单

| # | 用例 | 断言 |
|---|---|---|
| 1 | 冒烟：`/` 和 `/legacy` 都能开 | 200 + 关键 DOM 存在（防 #16 复现） |
| 2 | Provider 切换 | 选 qwen-vllm → 面板显示模型名 + 健康检查通过 |
| 3 | **流式输出非空** | 点"生成大纲"，30s 内正文框字数 > 200（**回归 1.3 的空白 bug**） |
| 4 | 思考/正文分离 | reasoning 进折叠面板，正文框内无 `<answer>` 残留 |
| 5 | 小说全链路 | 设定→大纲→分章→点第 1 章生成正文→字数 > 1500 |
| 6 | **剧本全链路** | 切 screenplay → 面板变成 logline/角色表 → 生成到"分场"→ 含"内景/外景"场头 |
| 7 | **层级可配**（#12） | novel 加"小纲"层 → UI 出现四级，无需改代码 |
| 8 | 右键菜单 | 选中文本→右键→"扩写"→ 选区被替换且变长 |
| 9 | 导出 | 导出 fountain 下载成功，文件含 `INT.`/`EXT.` |
| 10 | 持久化 | 写 120 章→刷新→数据完整（**IndexedDB 迁移回归**） |
| 11 | **#9 回归** | AI 助手最小化→再点→能展开 |
| 12 | 断点续写 | 自动跑到第 3 章→刷新→点继续→从第 4 章接上 |
| 13 | 主题/肤色 | 切换后关键元素对比度达标 + 截图对比 |
| 14 | 拆书（#5） | 上传文本→拆书→结果可一键写入知识库 |

### 工程配置
- 复用 `x10086/litecodeext/skills/e2e-self-test` 的 `run.py` + `lib/actor.py` 模式
- 每步 `page.screenshot()` 存 `reports/e2e/<date>/`，失败自动存 `trace.zip`
- 长流式用 `expect.poll` 轮询字数，**不用固定 sleep**
- 一条命令：`python3 tests/e2e/run.py --provider qwen-vllm --case all`

---

## 8. 重构后目录结构

```
AI-automatically-generates-novels/
├── server/
│   ├── app.py                 # Flask 入口，/ 和 /legacy 都注册
│   ├── providers/             # ★ 插件：一文件一供应商
│   │   ├── base.py            #   BaseProvider + Delta + 适配器契约
│   │   ├── qwen_vllm.py       #   ★ 含 reasoning 适配
│   │   ├── openai_compat.py deepseek.py claude.py gemini.py
│   │   ├── doubao.py qwen_dashscope.py wenxin.py ollama.py ai302.py
│   ├── orchestrator/          # 自动写作流水线 + 上下文预算 + checkpoint
│   ├── evaluator/             # ★ 评估：ai_tell_audit.py / consistency.py / judge.py
│   ├── exporters/             # txt md docx fountain fdx srt epub
│   └── projects/              # 项目目录（long-novel 结构）
├── packs/
│   ├── type/     novel.json screenplay.json shortdrama.json comic.json report.json
│   ├── genre/    xiuzhen.json xuanhuan.json … （23 个，由 x10086 skills 转换）
│   └── common/   novel-common.json（全局红线）
├── web/
│   ├── core/     store.js  prompt-engine.js  pipeline.js  provider-client.js
│   ├── ui/       panel.js  tree.js  editor.js  context-menu.js  theme.js
│   ├── features/ knowledge-base.js book-splitter.js mind.js shortcuts.js
│   └── index.html            # 只剩结构，无业务 JS
├── tests/
│   ├── unit/  integration/
│   └── e2e/    run.py  lib/actor.py  cases.py
├── config/   providers.yaml   .env.example
├── requirements.txt           # 补 openai / playwright / python-docx …
└── README.md                  # P5 重写
```

---

## 9. 分阶段路线（每阶段可独立发布，不停机）

| 阶段 | 内容 | 验收 | 工期 |
|---|---|---|---|
| **P0 止血** | requirements 补 `openai`；`/` 与 `/legacy` 双注册；key 移 `.env` + **吊销已泄露的旧 key**；删 `app各大模型/` 与 `nohup.out`；补 `.gitignore` | E2E #1 通过；关掉 #17 #16 #13 #6 | 1 天 |
| **P1 Provider 插件化** | `BaseProvider` + 适配器；8 个 app 合并；接 **qwen3.8-max/flash + 自建 Qwen3.6，修复三种 reasoning 字段命名**；分层调度 profiles；加 302.AI(#15)；顺手修 #9 | E2E #1-4 通过；qwen3.6 稳定出字 | 3 天 |
| **P2 状态收口 + 引擎化** | `store.js` 单一状态源（节点树），DOM 降为渲染层；`prompt-engine`（全局替换+预算）；`pipeline` 按 `levels` 驱动，取代三个写死函数 | 行为与老版一致（回归全绿）；**#12 加"小纲"仅改配置** | 5 天 |
| **P3 类型包 + 题材包** | ContentType schema；`novel` 固化；新增 **screenplay/shortdrama/comic**；23 个 skill → genre pack；面板 fields 驱动；拆书结果落知识库(#5) | E2E #5-8、#14 通过；**剧本上线** | 7 天 |
| **P4 自动写作 + 评估** | 编排层（世界观→大纲→逐章→自审→重写）；IndexedDB + 迁移；long-novel 项目目录；评估三层 + 15 项基准集 | 无人干预连写 10 章达标；基准报告可出 | 7 天 |
| **P5 导出 + 测试 + README** | 7 种 exporter；Playwright 14 用例全绿；README 重写（架构图/插件开发指南/部署/评估/测试） | 全绿 + 文档交付 | 3 天 |

**合计约 26 人日。** P0-P1 是止血，可先行合并；P2 是地基，**必须在 P3 之前**——否则直接加剧本 UI 只会得到第二份 1089 行的 index.html。

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| 老用户 localStorage 数据丢失 | P2 写版本化迁移 + 自动备份导出，E2E #10 守住 |
| 23 个 skill 转 JSON 有信息损耗 | 保留 `raw` 字段存原文，结构化字段只提关键约束；人工抽查 5 个题材 |
| qwen3.6 单点故障（自建 vLLM） | Provider 多路 + 健康检查 + 自动降级到备用 profile |
| 80K 上下文写长篇溢出 | 上下文预算器硬裁 + L2 滚动摘要；P4 用 100 章压测 |
| 大重构期间主干不可用 | 每阶段独立分支 + E2E 全绿才合并；老入口 `/legacy` 全程保留 |
| API key 已在 git 历史泄露 | P0 立刻吊销 `app.py:10` 那把 key（改代码不够，历史里还在） |

---

## 11. README 更新计划（P5）

新增章节：架构图与四层插件说明 · **如何加一个模型供应商（30 行示例）** · **如何加一种内容类型（剧本示例）** · 如何加一个题材包 · 自动写作模式使用指南 · 评估体系与基准跑分 · 测试怎么跑 · 部署（含 `.env` 配置，不再有明文 key）。
保留：现有功能亮点、在线体验、效果示例。

---

## 12. 待你拍板

1. **前端要不要上构建工具**？建议 Vite + 原生 ES Module（不引框架，保留 jQuery 兼容层）— 不引 Vue/React，避免老用户和二次开发者门槛陡增。
2. **默认模型**？建议 `planning=qwen3.8-max` + `drafting=自建 Qwen3.6-35B` 的双通道分层（见 §3.1.1），去掉代码里的公网 key。
3. **首批内容类型 5 个够不够**？（novel / 影视剧本 / 短剧 / 漫画分镜 / 通用长文）
4. **P0-P1 先合主干止血**，还是整体做完再发？建议前者。
