# 第十轮审查报告：从"AI 询盘分析工具"到"AI 外贸销售 CRM Workspace"

> 按规格书第一节要求：先审查、后确认、再动手。以下为 A~F 审查结论。
> 命名说明：上一轮已完成"第九轮（业务事实层）"，本轮指令按内容应属**第十轮**，避免混淆。

---

## A. 当前数据模型

| 实体 | 存储 | 关键字段 |
|---|---|---|
| **Inquiry** | `inquiries` 表（40 条） | id, created_at, source_text, report_json, grade, score, country, company, contact_name, customer_id, status（两态：待处理/已处理）, note + 第七轮 5 列：biz_status(12态), last_replied_at, follow_up_at, follow_up_done, deal_status |
| **Customer** | `customers` 表（10 条） | ckey（邮箱优先/公司名兜底去重键）, company, country, email, website, contact_name, inquiry_count, last_grade, last_score, first_seen, last_seen, grade(手动), note |
| **Contact** | **不存在独立层** | 联系人只是 customers / inquiries 上的一个字段。同一公司多个联系人会被归并成一个人 |
| **Opportunity** | **不存在独立层** | 商机 = 单条询盘的 biz_status 状态机（12 态）。同一客户 8 封询盘 = 8 个"商机"，没有客户级商机视图，无商机金额 |
| **Activity** | `activity` 表（仅 2 条） | inquiry_id, type, ts, description, actor。**只在 REPLIED / FOLLOW_UP_CREATED / FOLLOW_UP_COMPLETED / WON / LOST 五个动作点写事件** |
| **AI 分析** | report_json（JSON） | extracted（客户+需求信息）/ lead（评分）/ matches（产品匹配）/ insight（报价准备度+需求语义）/ draft（回复草稿）/ facts + reply_context（第九轮事实层） |
| **产品** | catalog.json | 产品库（名称/价格/交期/认证等） |

当前库：40 询盘、10 客户、2 条活动记录、biz_status 全部为 NULL（旧数据靠派生兜底）。

## B. 当前页面结构

5 个 Tab：`分析询盘 | 跟进台 | 客户档案 | 产品库 | 导出`
- 左侧栏：销售工作队列（搜索 + 6 个筛选 chip + 5 种排序 + **客户聚合组卡/子卡** + 独立滚动 + 空状态）
- 主工作区：4 个 KPI 卡（可点击联动筛选）→ AI 今日建议（4+1 行统计）→ 详情页 render_report（信息卡 / 事实层 / AI摘要 / 产品匹配 / 报价准备度 / 回复草稿 / 评分依据 / 活动时间线 / 历史询盘）
- Tab2 跟进台：优先级分排队 + 标记跟进/转回待回复
- Tab3 客户档案：等级筛选 + 档案卡（定级/备注/名下询盘列表）

## C. 当前业务流程（已打通的链路）

询盘录入 → 客户识别归并（email/公司键）→ AI 分析（extractor/matcher/lead_score/insight）→ 事实层三元组 → 产品匹配 → 报价准备度四态 → 回复生成（双重校验+防串数据）→ 标记发送（REPLIED）→ 设置跟进 → 到期提醒（今日待办）→ 报价（QUOTED）→ 谈判 → 成交/丢单（人工确认）。

## D. 已经具备 CRM 能力的部分（不要重做）

1. **客户聚合**：customers 表 + 左侧 customer_key 聚合组卡（customer_id > 公司名 > 联系人）
2. **12 态业务状态机** + 合法转换校验 + 旧数据派生兜底（workflow.py，236 行纯函数）
3. **Next Action 引擎**：阻塞项→Action 映射、P0-P2 优先级、卡片"下一步：xxx"、详情页 Primary CTA
4. **Activity 时间线**（表+详情页渲染）与**跟进闭环**（24h/48h/3天/7天/自定义 + 今日待办）
5. **AI 今日建议 + KPI 统计联动筛选**（同一口径，已防"顶部14点击只有10"）
6. **队列上一个/下一个导航**（沿筛选+排序）
7. **事实层防串数据**（第九轮：Source/Certainty 三元组 + allowed_facts + 提交校验）
8. **客户档案**（等级/备注/名下询盘）+ 导出 Excel

## E. 仍然是"AI Demo"式结构的部分（本轮要补的差距）

| # | 差距 | 现状 |
|---|---|---|
| E1 | **Contact 无独立层** | 多联系人公司被归并成一人，无法区分采购经理/老板 |
| E2 | **Opportunity 非实体** | 商机=单条询盘状态；同客户多询盘没有"一个商机"视图，无商机金额（Opportunity Value） |
| E3 | **Activity 覆盖不全** | 只记 5 类事件；缺 ANALYZED / REPLY_GENERATED / STATUS_CHANGE / NOTE；实际库里仅 2 条 |
| E4 | **无客户视角统一页** | 点击客户仍是打开单条询盘；缺"客户概览+当前商机+历史记录+AI画像"一屏视图 |
| E5 | **无商机状态条** | 详情页看不出"这个客户走到哪一步"的 7 段流程可视化 |
| E6 | **今日建议≠行动队列** | 点击只切筛选，无"1/5 连续处理 + 标记完成/稍后处理"模式 |
| E7 | **搜索/筛选维度不足** | 搜索不含联系人/产品；筛选缺商机阶段/报价准备度/国家；排序缺最久未跟进/报价准备度 |
| E8 | **主工作区层级失衡** | AI 分析文字仍是视觉主体；评分依据默认展开（spec 十一/十二要求 Business Result > Next Action > 商机状态 > AI 判断） |

## F. 本轮准备修改的地方（8 个 Phase，全部增量）

| Phase | 内容 | 改动方式 |
|---|---|---|
| 1 | 左侧队列 CRM 化：组卡升级为公司视角（询盘数·待回复数/最新需求/下一步/国家·时间·AI级），子卡弱化 | 仅改 app.py 卡片 HTML + queue_ui.py 纯函数 |
| 2 | Contact 层：customers 加 `contacts` JSON 列（轻量多联系人），识别到新联系人时追加；客户视图展示 | db.py 补列 + 归并逻辑追加，不改现有字段 |
| 3 | Opportunity 层（轻量）：**不建新表**——客户级商机 = 从该客户全部询盘的 biz_status/QR 派生"当前阶段 + 商机金额（Σ 数量×目标价，无数据不显示）" | queue_ui.py / 新纯函数 + 展示 |
| 4 | Next Action 五要素：详情页行动卡（action/priority/due_date/owner/status），客户级 Next Action 取最高优先级询盘 | 复用 workflow.next_action，仅展示层 |
| 5 | AI Action Queue：今日建议点击 → 进入连续处理模式（1/N + 生成回复/标记完成/稍后处理/查看客户 + 上一个/下一个） | app.py 会话状态 + 复用队列导航 |
| 6 | Activity 全覆盖：ANALYZED / REPLY_GENERATED / STATUS_CHANGE 写入点 + 时间线分"已发生/未来跟进"两区 | db 调用点追加（只加不删） |
| 7 | 主工作区层级：详情页顶部 7 段商机状态条（3 秒看懂阶段）+ 评分依据默认折叠 + AI 分析文字降级为折叠层 | app.py render_report 内部重排 |
| 8 | 搜索/筛选/排序升级：搜索加联系人/产品；筛选加商机阶段/报价准备度；排序加最久未跟进/报价准备度；收进"高级筛选" | app.py + 纯函数 |

**明确不改**：Agent 四模块核心逻辑、所有 Prompt、防串数据机制、状态机转换规则、现有两态+12态字段、产品库、导出、现有筛选默认行为。数据结构只有**追加**（contacts 列 + activity 类型），无破坏性变更。

**风险点提前声明**：Phase 7 要重排详情页信息层级，是本轮唯一触碰大块 UI 的地方，会放在最后并全程跑回归（r6 32 / r7 65 / r8 32 / sidebar_ui 24 / r9 41 等全套）。
