# 第十二轮优化报告 · PHASE 2：CRM 客户 / 询盘详情工作区升级

- 日期：2026-09-07
- 状态：✅ 验收通过并停止（按要求不进入 Phase 3）
- 约束遵守：不修改 agent 引擎 / 数据模型语义；UI 文案与控件 key 全部保留（既有 AppTest 零回归）

---

## 0. 本轮结论（TL;DR）

1. 把「询盘分析详情」升级为 **Customer + Opportunity Workspace**，规格 7 节全部落地。
2. 数据层只做最小增量：`activity` 表新增 `result` 列（自愈迁移 R12）+ `update_draft()`，**没有**为 UI 伪造任何历史。
3. 隔离沙盒 `qa_acceptance/AI询盘agent_p2`（端口 8513）真实 Chrome E2E：**10 个场景步骤 / 24 项断言全绿**。
4. 既有回归 7 套全绿（workflow 65 / crm_r8 32 / scenarios_r11 34 / sidebar_r6 32 / smoke_r4 0 异常 / crm_r10 44 / scenarios_r10 37）→ **零回归**。
5. 验收后已关停沙盒服务器、释放端口、清理诊断脚本；真实库零污染。

---

## 1. 变更内容（对照规格七节）

### 一、顶部 Customer Header
- 公司大字标题（无归并客户时回退客户档案 Company）+ 品牌副行。
- 六格字段：AI 优先级 / Opportunity 商机 / 当前阶段 Current Stage / 最近联系 Last Contact / 下次跟进 Next Follow-up / 报价准备度。
- 右侧四按钮 `[✉️ 生成回复][📅 记录跟进][💰 创建报价][⋯ 更多]`：不可用时 **Disabled + 原因提示**（help 悬浮显示），如已成交/已丢单/暂缓/产品库无匹配/报价准备度不足等。
- Opportunity 显示商机阶段 emoji + 该客户名下询盘数 + 预估商机金额；商机由 crm.py 纯函数从真实询盘派生。

### 二、Pipeline（销售漏斗，真实状态驱动）
- 七段漏斗：新询盘→需求确认→产品匹配→待报价→已报价→谈判→成交，另支持 LOST（丢单可复活到谈判）/ 暂缓。
- 当前阶段高亮 = 真实 biz_status（读库，非 UI 静态）。
- 点击任一段 → 阶段知识卡：**进入条件 / 缺失信息（真实 blockers+跟进逾期）/ AI 建议（workflow next_action）/ 完成条件**。
- 推进按钮受 `workflow.can_transition` 约束；WON 走人工结单（checkbox + 二次确认），AI 不自动判成交。

### 三、AI Sales Assistant
- 卡片展示：**当前建议 / 为什么 / 报价准备度 / 风险 / 下一步**（全部来自真实询盘与 workflow 派生，不编造）。
- 附「✉️ 生成回复」快捷按钮，直接定位到草稿区。

### 四、Activity Timeline
- 时间倒序，每条含 **时间 / 类型 / 内容 / 执行人 / 结果**（activity 表新增 result 列承载「结果」）。
- 类型中文映射覆盖：询盘 / 邮件 / 回复 / 电话 / 会议 / 报价 / 跟进 / AI分析 / 阶段变化（PHONE_CALL / MEETING / EMAIL / QUOTE_CREATED / FOLLOW_UP_* / ANALYZED / REPLY_GENERATED / STATUS_CHANGE…）。
- 新建询盘只显示真实存在的两条 AI 记录（ANALYZED + REPLY_GENERATED），**不伪造历史**。

### 五、Related Records
- 四格：📬 相关询盘（同客户归并，真实）/ 📦 相关产品（AI 匹配结果，真实）/ 💰 相关报价（来自 Timeline 的 QUOTE_CREATED / QUOTE_SENT 真实记录）/ 👥 相关联系人（客户档案 contacts 去重）。

### 六、执行动作 → 全部产生 Activity（留痕闭环）
| 动作 | 产生的真实效果 |
|---|---|
| 生成回复 | 打开/生成草稿（已有 REPLY_GENERATED 记录） |
| 💾 保存修改 | `update_draft()` 写回 report_json.draft + Timeline「REPLY_EDITED / 销售修改回复」 |
| 📤 标记为已发送 | 业务状态 → REPLIED + last_replied_at + Timeline「REPLIED（人工确认）」+ 界面即时刷新 |
| 📅 记录跟进 + ⏰ 设置跟进 | follow_up_at 落库 + Timeline「FOLLOW_UP_CREATED / 计划跟进…」 |
| ➕ 记录活动（电话/会议/邮件） | Timeline「PHONE_CALL / MEETING / EMAIL」手动记录 |
| 推进 Pipeline | biz_status 落库 + Timeline「STATUS_CHANGE / 手动调整销售阶段…」 |
| 💰 创建报价 | QUOTE_CREATED 落库 + Related「相关报价（N）」+ READY_FOR_QUOTE 时自动推进到已报价 |
| 成交 / 丢单 | 人工确认后 set_deal 落库 + Timeline（AI 不自动判） |

所有状态写入 SQLite，**刷新/重开页面不丢失**。

### 七、测试（规格第七节逐项覆盖）
E2E 十步：① 打开客户 → ② 打开询盘（B #47）→ ③ 切换 Pipeline（真实推进到已报价 + 阶段变化事件）→ ④ 创建报价 → ⑤ 打开 A #46 生成回复+保存修改 → ⑥ 标记已回复 → ⑦ 记录跟进 → ⑧ 记录电话 Activity → ⑨ 刷新重开 A（状态不丢）→ ⑩ 刷新重开 B（Pipeline + 报价记录不丢）。

---

## 2. 代码变更

| 文件 | 变更 |
|---|---|
| `workbench/workspace.py`（新增） | 纯逻辑模块（无 Streamlit 依赖）：FUNNEL_CN / STAGE_TARGET_BIZ / STAGE_INFO 七段知识卡 / ACT_META / ACT_CATEGORY / TIMELINE_FILTERS / build_timeline() / stage_knowledge_html() / money_text() |
| `workbench/db.py` | `R12_ACT_RESULT=True` 版本标记；init_db 幂等 `ALTER TABLE activity ADD COLUMN result TEXT`；`record_activity(..., result="")` 写入 result 列；`list_activity` 返回 5 元组；新增 `update_draft(inquiry_id, draft_text)` |
| `workbench/app.py` | 导入并重载探测 workspace/update_draft；PAGE_CSS 新增 .ws-hd/.ws-cells/.pipe-line/.pchip/.pipe-know/.asst/.tl-item/.rel-grid 等；`FIELD_CN`+`_field_cn`；`_ws_save_draft` 回调；`_render_customer_workspace()`（Header/Pipeline/Assistant/Timeline/Related/创建报价）；render_report 接入 workspace；跟进区收进 expander 等 |

关键自愈迁移逻辑保留：`init_db` 幂等 ALTER + 版本标记，老库自动补列、不影响已有数据。

---

## 3. 验收过程与结果

- 环境：隔离沙盒 `qa_acceptance/AI询盘agent_p2`（复制真实询盘 Decathlon 泳帽询盘 id 46/47，客户 11 Decathlon France / Marie Dubois），8513 端口，真实 Chrome headless（playwright-core）。
- 脚本：`qa_acceptance/qa_p2_ws.js`（保留为验收资产）。
- E2E 稳定性处理：Streamlit 会渲染「隐形孪生按钮」，脚本改为**只点首个可见按钮 + 无效果自动重试**；断言一律用**落库后的持久文案**（Timeline/DB 驱动），避免 toast/session 瞬态误判。

| 步骤 | 场景 | 断言数 | 结果 |
|---|---|---|---|
| ①② | 打开客户 + 打开询盘（B #47） | 4 | ✅ |
| ③ | 切换 Pipeline 待报价→已报价 + 阶段知识卡 + Timeline 事件 | 3 | ✅ |
| ④ | 创建报价（Header 按钮可用 / 草稿 / 提交 / Related(1)/ Timeline） | 4 | ✅ |
| ⑤ | A #46 生成回复草稿 + 保存修改（REPLY_EDITED） | 1 | ✅ |
| ⑥ | 标记已回复（状态→已回复 + Timeline 人工确认） | 2 | ✅ |
| ⑦ | 记录跟进（下一步跟进时间落库） | 1 | ✅ |
| ⑧ | 记录电话 Activity（内容进 Timeline） | 1 | ✅ |
| ⑨ | 刷新重开 A：阶段 / 电话活动 / 计划跟进 / 上次回复 全持久 | 4 | ✅ |
| ⑩ | 刷新重开 B：已报价 / 相关报价(1) / Header 商机 全持久 | 3 | ✅ |
| — | 页面无 JS 异常 | 1 | ✅ |
| **合计** | | **24** | **24 通过 / 0 失败** |

---

## 4. 回归（零回归验证）

| 套件 | 覆盖 | 结果 |
|---|---|---|
| `test_workflow_r7.py` | 12 态状态机 / 回复 / 跟进 / 结单闭环 | 65 通过 / 0 失败 |
| `test_crm_r8.py` | CRM 商机队列（客户聚合 / 商机阶段 / 身份键） | 32 通过 / 0 失败 |
| `test_scenarios_r11.py` | 十项业务检查 + 状态机防跳变 + UI | 34 通过 / 0 失败 |
| `test_sidebar_r6.py` | 销售队列 UI（筛选 / 排序 / 导航 / KPI 联动） | 32 通过 / 0 失败 |
| `test_smoke_r4.py` | AppTest 全页 + render_report 全分支 | 0 异常 |
| `test_crm_r10.py` | CRM 纯函数 / 金额 / 空输入兜底 | 44 通过 / 0 失败 |
| `test_scenarios_r10.py` | R10 场景 + AI 能力不受影响 | 37 通过 / 0 失败 |

> `list_activity` 5 元组改动、删除旧「询盘进度 Timeline」、render_report 接入 workspace 等 Phase 2 变更，均未破坏既有断言（文案与 mark_replied/stage_pick 等控件 key 保留）。

---

## 5. 对 Agent 核心的架构影响

- **引擎零改动**：`agent/`（评分 / 匹配 / 回复策略 / facts 层）与 `workflow.py` 12 态状态机语义未动；数据模型语义未扩展。
- 数据层增量仅两处、向后兼容：
  1. `activity.result` 展示列（R12 自愈迁移，老库自动补列）；
  2. `update_draft()` 把草稿修改写回 `report_json.draft`（此前草稿只存在会话里）。
- **Timeline 是真实活动日志，不是 UI 编年史**：任何进入 Timeline 的事件都已在 activity 表留痕（含执行人、结果），刷新重开由 DB 还原。
- 商机/Opportunity 仍由 `crm.py` 纯函数从真实询盘派生（funnel 七段映射 biz_status），Pipeline 高亮与 Current Stage 同一数据源，UI 与业务一致。

---

## 6. 停止说明

- Phase 2 验收达成，按用户指令**停止，不进入 Phase 3**。
- 沙盒：`qa_acceptance/AI询盘agent_p2`（保留作为验收证据与后续回归资产）；8513 服务器已关停、端口已释放；临时探针脚本已清理。
- 真实工作库零污染（验收全程在隔离沙盒完成）。
