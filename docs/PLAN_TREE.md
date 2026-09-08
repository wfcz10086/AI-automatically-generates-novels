# 细纲树（Plan Tree）设计

> 状态：**待确认**。Step 0 产物，确认后按 Step 1-5 实施。

## 一、为什么改

现在是「链式批次」：模型写 1-20 章，把这 20 章念给它听，它写 21-40 章；再念 40 章，
写 41-60……写到第 500 章，前面念不完，只能挑最近的念。

于是：
- 它记不住第 30 章埋了什么（`OUTLINE_INPUT_CHARS=40000`，排到第 500 章时第 1-150 章
  一个字都带不进去；输入随已排章数增长是**结构性**问题，调预算治不了）
- 它不知道第 800 章要收什么线（只知道过去，不知道未来）
- `step_volumes` 把所有卷挤进一次 4000 tok 调用，1000 章分 25 卷时每卷只剩一百来字
- 审阅在全部排完之后，发现结构问题时返工成本已是几百章

改成「树」：拆任何一块时，模型**只看它的爹和它的兄弟**。拆第 7 卷就看第 2 阶段是什么、
第 6 卷结束在哪、第 8 卷要从哪开始 —— 几千字，与全书 100 章还是 1 万章无关。
而且它知道「第 8 卷要从哪开始」，等于知道未来，伏笔自然往那边铺。

## 二、层级与扇出

| 层 | 含义 | 每父展开子数 | 单节点字数 | 1000 章时的节点数 |
|---|---|---|---|---|
| L0 | 总纲 | 1 | 2-3k | 1 |
| L1 | 部/阶段 | 5-8 | 1.5k | ~7 |
| L2 | 卷 | 3-6 | 1.5-2k | ~30 |
| L3 | 情节单元 | 4-6 | 800 | ~150 |
| L4 | 章 | 6-8 | 480 | ~1000 |

任何一次调用的输入 = 父节点全文 + 兄弟摘要 + 前后合同，**与全书长度无关**。

## 三、节点 schema（`plan.json`）

```json
{
  "id": "L2-07",
  "level": 2,
  "parent": "L1-02",
  "range": [141, 180],
  "title": "东平府囤粮",
  "summary": "一句话",
  "entry_state": {"主角身份": "…", "关键人物": {"武松": "股东·在阳谷"},
                  "钱粮与军资": "三千贯", "官职与兵权": "白身",
                  "open_threads": ["武大郎死因"]},
  "exit_state":  {"…同构…"},
  "threads_open": ["方腊密使"],
  "threads_close": ["武大郎死因"],
  "cast": ["林远", "武松"],
  "climax": "…",
  "hook": "…",
  "material": ["政和年间东平府常平仓弊案"],
  "review_notes": [],
  "needs_review": false,
  "body": "本节点的细纲正文（L4 层即现 chapter_outlines.json 里一章的内容）"
}
```

**状态字段不写死**：`entry_state`/`exit_state` 的资源类字段名取自
`Novelist.ledger_spec()`（历史题材 → 钱粮与军资 / 官职与兵权，修仙 → 灵石与资源 /
修为境界）。固定字段只有 `主角身份`、`关键人物`、`open_threads`。

## 四、文件布局

```
projects/<书名>/
  plan.json                 整棵树（唯一真相源）
  plan.partial.json         展开中的断点（每展开一个节点即写盘）
  outline.md                ← L0 节点的 body 导出（保留，只读方向不变）
  volumes.json              ← L2 节点导出（保留，供 volume_of/app.py 读）
  chapter_outlines.json     ← L4 叶子导出（保留，供 step_chapter 等 12 处读）
  outline_review.md         审阅报告（增加 level 维度）
```

导出是单向的：`plan.json` → 三个视图文件。**不再有人写这三个文件**（除导出器）。

## 五、展开循环（伪代码）

```python
def expand(node):                       # node = 父节点 P
    sibs = siblings_of(node)            # 同层兄弟
    ctx = {
        "parent_body":  node.body,                       # P 全文
        "grandparent":  parent_of(node).body,            # L1 展开时是总纲
        "entry":        prev_sibling(node).exit_state    # P 的进口（已知）
                        or node.entry_state,
        "exit":         next_sibling(node).entry_state   # P 的出口（已知）
                        or node.exit_state,
        "sibling_map":  [f"{s.id} {s.title} {s.summary}" for s in sibs],
        "open_threads": inherited_open_threads(node),    # 从祖先继承、尚未关闭
        "material":     ground("drive", context=node.body),   # 素材，不参与结构决定
        "ledger_keys":  ledger_spec(),                   # 状态字段名
    }
    for attempt in (1, 2, 3):
        children = parse_json(call(profile_for(node.level), render(tpl, ctx)))
        issues = validate_children(node, children)
        if not any(i.level == "error" for i in issues):
            break
        ctx["issues"] = issues                # 带着问题清单重试
    else:
        node.needs_review = True              # 不静默通过
    save(plan)                                # 每展开一个节点立刻写盘
    return children
```

**模型输出一律 JSON 数组**，不再用 `###fenge` / `[[CHn]]` / `—— 第N章 ——`
任何文本分隔符（已被证实会被模型当格式学走，污染过 15 章细纲）。
解析复用 `step_outline_review` 里的括号配平逻辑，抽成 `parse_json_block()` 公共函数。

## 六、校验器（纯程序，不调模型）

`validate_children(parent, children) -> List[Issue]`

| # | 规则 | 级别 |
|---|---|---|
| 1 | 子节点 `range` 严格拼满父 `range`，不重叠不留空 | error |
| 2 | `child[0].entry_state == parent.entry_state` | error |
| 3 | `child[-1].exit_state == parent.exit_state` | error |
| 4 | `child[i].exit_state == child[i+1].entry_state` | error |
| 5 | 父 `threads_close` 每条都分配到某个子的 `threads_close` | error |
| 6 | 子 `threads_close` 每条在树中更早节点的 `threads_open` 出现过 | error |
| 7 | `cast` ⊆ `roster()` | error |
| 8 | exit_state 标记死亡/退场的人物不得出现在后续节点 `cast` | error |
| 9 | 子节点数在该层扇出区间内 | warn |
| 10 | `body` 长度在该层字数区间内 | warn |

error → 重试该次展开（最多 2 次，带问题清单）；仍失败则落盘并标 `needs_review`。

## 七、审阅前移

| 层 | 审阅方式 |
|---|---|
| L2 展开完 | 全量六项审阅（复用现有 CHECKS 与分段逻辑，输入换成 L2 节点），问题回写各节点 `review_notes`，**人工确认后再展开 L3** |
| L3 / L4 | 只做相邻窗口审阅（前后各一个兄弟） |

每层展开完**先跑校验器，再跑模型审阅**。

## 八、模型策略

| 层 | profile | 思考 |
|---|---|---|
| L0-L2 展开、审阅 | planning / judging | 开 |
| L3-L4 展开 | planning | 关 |

沿用 `config/settings.yaml` 的 per-task 配置，不全局改。

## 九、函数去留

| 函数 | 处置 | 影响 |
|---|---|---|
| `step_outline` | **保留**，产物成为 L0 节点 body | 无 |
| `step_volumes` | **改写**：从 L1 展开 L2 并导出 volumes.json；签名不变 | run_novel.py:80、orchestrator:1828/1831 |
| `volume_of` | **保留**（读 volumes.json 视图） | orchestrator:1828/1831 |
| `step_chapter_outlines(start,count)` | **改写**：定位 start 所在 L3 单元并展开 L4，导出 chapter_outlines.json；签名不变 | run_novel.py:97/147 |
| `step_outline_review` | **改写**：增加 `level` 参数 | run_novel.py cmd_review |
| `outline_digest` | **删除**（树结构下不需要 digest） | 仅 step_chapter_outlines 内部用 |
| `OUTLINE_INPUT_CHARS` | **删除** | 同上 |
| `outline_batch` | **删除** | run_novel.py:85/146 需改 |
| `clean_outline` | **保留**（导出时兜底） | — |
| `compile_outline_prompt` | **改写**为树展开提示词编译器 | 仅 step_chapter_outlines 用 |
| 新增 `step_plan_level(level)` | 供 run_novel.py 与 web 端按层触发 | — |

## 十、调用点影响分析

**读 `chapter_outlines.json` 共 12 处**，全部只读，导出后行为不变：
`orchestrator` 1121/1609/1669/1854/1893/1903/2109/2205/2432/2517、
`exporters.py` 16/47、`scripts/build_release.py` 18、`run_novel.py` 86/144。

**读 `volumes.json` 共 5 处**：`run_novel.py` 80、`app.py` 260、
`orchestrator` 1541/1576/1673。导出后行为不变。

**读 `outline.md` 共 9 处**：全部只读，L0 body 导出后不变。

**需要改的调用方**：
- `run_novel.py` 的 `cmd_outline`（改为按层展开）、`cmd_run` 里的 `batch` 计算
- `packs/type/novel.json` 的 `levels` 增加 L1/L3 两层的模板

## 十一、验收

- 单测全绿、e2e 全绿
- 旧流程生成前 60 章细纲存快照 → 新流程生成同范围 → `scripts/compare.py` 对比，
  第 21-40 章并排放进 `docs/PLAN_TREE_DIFF.md`
- **任意一次展开调用的提示词 < 15k 字符**，日志打印每次调用的输入字符数

## 十二、待确认的设计分歧

1. **状态字段怎么比对**（规则 2/3/4）。字段值是自然语言（「三千贯」vs「约三千贯」、
   「股东·在阳谷」vs「在阳谷，已入股」）。三种做法：
   - a) 严格字符串相等 —— 模型几乎不可能写出完全一致的两份，会疯狂重试
   - b) normalize 后比较（去空格标点、数字归一、同义词表）—— 能治大部分，但同义词表要维护
   - c) 交给模型判「这两个状态是不是同一个」—— 准，但每次展开多一次调用
   **倾向 b + c 兜底**：normalize 后不等时再问一次模型，模型说等就放行。请确认。

2. **L1 的「部/阶段」与现有七级台阶的关系**。现在 background 里写死了七级台阶
   （阳谷起家→州府东京→平方腊→联金灭辽→靖康→灭金→篡宋登基），正好 7 个，
   是否直接作为 L1 的 7 个节点、不再让模型自己划分？

3. **已排的 160 章细纲**。树结构下这些是 L4 叶子但没有 L1-L3 父节点。
   是丢弃重排，还是反向构建父节点（从叶子倒推卷与单元）？倒推的质量存疑。
