# 第十三轮优化报告 · Phase 3：AI Priority + Next Best Action

> 目标：建立**真实、可解释、稳定**的 AI 销售优先级系统。
> 硬约束延续前轮：不动 agent 引擎分析逻辑、不改数据模型语义、UI 文案与控件 key 保留。
> 本轮全部为「只读派生 + 纯逻辑模块 + 展示增量」，真实库零污染。

---

## 一、评分公式（全部可复算，无任何随机源）

### 1. AI Priority（0-100）

```
AI Priority = round( Σ 维度分 × 权重 )
```

| # | 维度 | 权重 | 数据来源 |
|---|------|------|----------|
| 1 | 采购意向 Purchase Intent | 0.20 | `report.lead.purchase_intent_score`（LeadScorer 关键词证据链） |
| 2 | 需求明确度 Product Clarity | 0.10 | `report.lead.requirement_clarity_score` |
| 3 | 产品匹配 Product Match | 0.15 | `report.matches` 匹配度 / db 只读派生 `match_top` / 品类 |
| 4 | 报价准备度 Quote Readiness | 0.10 | `insight.quotation_readiness` 统一四态枚举映射 |
| 5 | 客户价值 Customer Value | 0.15 | (订单规模分 + 客户信息完整度分) ÷ 2 |
| 6 | 紧迫度 Urgency | 0.15 | `lead.urgency_score`（urgent/ASAP/交期信号） |
| 7 | 跟进风险 Follow-up Risk | 0.15 | 沉默时长 ÷ 72h × 100（48h≈67、72h+=100） |
| — | **合计** | **1.00** | |

**跟进风险锚点规则**（真实时间字段，不伪造）：
- 待我方先动作（新询盘/待补信息/待报价）→ 从 `created_at` 起算（客户在等我们）；
- 已回复/已报价/谈判 → 从 `last_replied_at` 起算（我们在等客户，回复会重置沉默）；
- 成交/丢单/暂缓 → 风险 = 0（终态无跟进风险）；
- 无时间数据 → 显示「暂无数据」，按 0 计入（不猜）。

**报价准备度枚举 → 分数映射**（固定写死，可复算）：
`NOT_READY=15 · PARTIALLY_READY=55 · READY_FOR_QUOTE=85 · QUOTED=92`

### 2. Queue Score（队列默认排序，不只按 Priority）

```
Queue Score = round( AI Priority×0.50 + 跟进风险×0.15 + 紧迫度×0.15 + 阶段紧急度×0.20 )
```

阶段紧急度（表达「该行现在多需要被处理」）：
到期/逾期跟进 95 ＞ 待回复/待补信息 85 ＞ 可正式报价 82 ＞ 已报价 60 ＞
谈判 58 ＞ 待安排跟进 55 ＞ 在等客户 45 ＞ 本轮跟进已完成 20 ＞
历史已处理(无待办) 10 ＞ 终态/暂缓 0。

### 3. Next Best Action（NBA）

每个询盘输出六要素：`Current Stage / Current Status / Next Action / Reason / Action Type / Priority`。
- Next Action 沿用既有 `workflow.next_action`（12 态状态机，**不重写**）；
- Action Type 八类映射（Phase 3 规范）：

| Action Type | 触发（workflow 动作 / 状态） |
|---|---|
| REPLY | REVIEW_REPLY / SEND_REPLY / ANALYZE_REPLY |
| COLLECT_INFO | CONFIRM_*（产品/数量/规格/目的地/条款/联系方式）、认证、样品、目录 |
| MATCH_PRODUCT | 产品库未命中需人工选品（COLLECT_INFO 语义内，reason 指明） |
| CREATE_QUOTE | 报价条件满足 |
| FOLLOW_UP | 到期跟进 / 报价后跟进 |
| NEGOTIATE | NEGOTIATING（客户还价中） |
| SCHEDULE_FOLLOW_UP | 已回复未设跟进 |
| MARK_COMPLETE | WON / LOST / ON_HOLD（终态归档） |

- Reason 基于真实状态生成（阻塞字段中文 / 报价条件 / 逾期提醒 / 草稿就绪），不新造判断。

---

## 二、缺失数据语义（不猜数）

| 情形 | 处理 |
|---|---|
| 客户没说清要什么产品、库无匹配痕迹 | Product Match：**暂无数据**，按 0 计入总分 |
| 客户说了产品但产品库未命中 | 显示低分（5-20）+「需人工选品/确认供应」，绝不给 83 这类无依据高分 |
| 缺关键信息 | Quote Readiness 显示枚举 **NOT_READY**（不虚报成分数） |
| 旧记录无 lead 评分数据 | 对应维**暂无数据**按 0 计入（AIP 自然走低，不虚高） |
| 时间字段缺失/损坏 | 跟进风险：暂无数据，按 0 计入 |
| readiness 未知枚举值 | 暂无数据（不猜） |

铁律：**缺失 = 0 分 + 明确标注**，永远不给无依据的分数；同一输入永远同一输出（无随机源）。

---

## 三、UI 交付

1. **Customer Header「AI Priority」格升级**：大数字 `58 / 100` + 线索等级 + 优先档提示 + Queue Score 小字。
2. **AI 销售助手新增「AI Priority 评分解释卡」**（spec 三样式）：
   - 顶部大数字 + 优先档；
   - 逐维原因行：`+50 高采购意向（采购意向 100/100 × 权重0.20）`、`-5 需求方向清楚但细节不全`、`◻ 暂无数据（不虚估）`；
   - 跟进风险独立警示行（⚠️ 红，不与加分项混淆）；
   - 底部公式行：`AI Priority = Σ(维度分×权重) = 0.15×100 + … = 58（可复算）`。
3. **Next Best Action 徽标**：`🎯 Next Best Action：查看并发送回复 · REPLY · 回复客户 · P1` + 一句 Reason + 阶段流向。
4. **队列默认排序改为「AI 综合排序（Queue Score）」**（spec 六），原排序选项全部保留；
5. **侧栏队列卡片新增徽标**：`AI 61 · 队列 76`（紫=AIP，青=QS）。

## 四、数据层增量（最小、只读）

- `workbench/priority3.py`（**新增**，约 400 行纯逻辑，无 Streamlit 依赖）：
  `analyze() / ai_priority() / seven_dims() / next_best_action() / queue_score() / stage_emergency()`；
- `workbench/db.py`：版本标记 `R13_LEAD_SUMMARY`；`_derive_need` 新增**只读**派生键
  `product_cat`（客户已明确品类）、`match_top`（首匹配名+匹配度）、`lead_summary`（LeadScorer 八维摘要，兼容新旧 lead 结构）；
- `workbench/app.py`：load_queue 逐条计算 aip3/qs3；排序默认项与排序键；卡片徽标；
  Header AI 格；评分解释卡 + NBA 徽标；约 +150 行展示代码。
- 不新增任何数据库列/表，不写回任何业务数据。

---

## 五、测试结果

### Phase 3 验收 `test_priority_r13.py`：**50/50 全绿**

| Case | 断言 | 结果 |
|---|---|---|
| Case 1 高采购意向+产品明确 | AIP≥75；产品匹配=matched；Next Action=CREATE_QUOTE | ✅ |
| Case 2 一句简单询价 | AIP≤40 不虚高；缺数据维显示「暂无数据」 | ✅ |
| Case 3 缺产品信息 | Quote Readiness=NOT_READY 枚举；匹配≤20；NBA=COLLECT_INFO 且 reason 引用缺失字段 | ✅ |
| Case 4 超 48h 未跟进 | 60h 风险 ≥60 且显著高于 2h；理由点明超 48 小时；QS 上升 | ✅ |
| Case 5 数据缺失 | 同输入两次结果完全一致（无随机）；nodata 维 0 分且显示「暂无数据」 | ✅ |
| Case 6 报价完成 | 报价前 CREATE_QUOTE → 报价后 FOLLOW_UP（Next Action 改变）；准备度=QUOTED | ✅ |
| 公式可复算 | 权重和=1.00；6 组样本 AIP == Σ(维分×权重)；可复现 | ✅ |
| Action Type 八类 | REPLY/COLLECT_INFO/CREATE_QUOTE/FOLLOW_UP/SCHEDULE_FOLLOW_UP/MARK_COMPLETE/NEGOTIATE 映射全对 | ✅ |
| Queue Score | 高意向新单最高；WON 沉底；stage 综合项生效 | ✅ |
| 异常情况 | 空输入/坏时间/need=None/readiness 未知值 全部不抛异常、不猜数 | ✅ |

### 回归 7 套（零失败）

| 套件 | 结果 |
|---|---|
| test_workflow_r7 | 65/65 ✅ |
| test_crm_r8 | 32/32 ✅ |
| test_scenarios_r11 | 34/34 ✅ |
| test_sidebar_r6 | 32/32 ✅（Test F/Test A 断言更新为 Phase 3 新规范，见下） |
| test_smoke_r4 | 0 异常 ✅ |
| test_crm_r10 | 44/44 ✅ |
| test_scenarios_r10 | 37/37 ✅ |

**新规范覆盖旧断言（2 处，均为默认排序口径变化）**：
1. `test_sidebar_r6` Test F：「优先分最高者置顶」→「Queue Score 最高者置顶」（默认排序从旧优先分改为 AI 综合排序，spec 六）；
2. 同文件 Test A：组展开断言改为遍历点击命中目标组（组按钮先后随排序变化，不再依赖顺序）。

### 真实浏览器视觉验收（Chrome headless，8514）

- 队列页：默认排序显示「AI 综合排序（Queue Score）」，侧栏卡片出现 `AI 61 · 队列 76` 徽标 ✅
- 工作区：AI 销售助手完整呈现评分解释卡（AI Priority 58/100 + 七维 ± 原因 + 警示行 + 可复算公式）与 NBA 徽标 ✅
- 截图：`qa_acceptance/shots_p3/p3_queue.png`、`p3_workspace.png`

---

## 六、异常情况与已知边界

1. **旧种子数据 AIP 偏低是正确行为**：demo 库早期询盘（#1-6 等）入库时无 LeadScorer 结果 → 五维「暂无数据」按 0 计入，AIP=17。这不是 bug——不虚估原则下，没有评分依据的询盘不应获得高优先级；新分析的询盘七维齐全。
2. **队列与详情口径一致性**：详情页用完整 report（matches/lead）计算，队列行用 db 只读派生摘要（`match_top`/`lead_summary`）保证同一输入同一结果；两者已对齐。
3. **历史遗留**：高级筛选「报价准备度」选项与 need.readiness 原始值（insight 四级）不匹配的问题在第十轮前已存在，本轮未扩大处理范围（不在 Phase 3 规格）。
4. **验收后停止**：按用户指令，Phase 3 完成后停止，不进入下一阶段。

## 七、对 Agent 核心的架构影响

- **零侵入**：agent/*（insight/lead_score/matcher/replier 等）一行未改；状态机 workflow.py 未改；未新增 DB 列/表。
- priority3.py 是 LeadScorer（分析时的八维可解释评分）之上的**派生视图层**：LeadScorer 回答「这个客户价值如何」，AI Priority 回答「这条询盘现在多值得投入 + 下一步做什么」——两层语义清晰分离。
- 后续如需调权重，只需改 `priority3.AIP_DEF` 一处（权重和恒等校验已在测试里）。
