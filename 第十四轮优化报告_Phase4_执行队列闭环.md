# 第十四轮优化报告 —— Phase 4：Sales Execution Queue（销售执行队列闭环）

> 日期：2026-09-07 ｜ 模块：workbench ｜ 性质：新功能（闭环）+ 数据一致性加固
> 硬约束延续：不修改 agent 引擎（extractor/insight/lead_score/replier）、不扩充 DB 业务语义（仅新增幂等记录函数）、UI 文案与既有控件 key 全部保留。

---

## 一、变更内容

### 1. 新增纯逻辑模块 `workbench/execution.py`（无 Streamlit 依赖，可单测）
- `build_exec_queue(items, flt, sort)`：**Current Queue = 当前 Filter + Sort + Status**（与侧边栏同一套筛选/排序规则，单一口径）；执行队列语义 = 待处理任务队列，两态「已处理」沉出。
- `route_of(action_type)`：NBA 八类 Action Type → 执行入口路由（reply / collect_info / create_quote / follow_up / schedule_follow_up / match_product / negotiate / mark_complete）。
- `completion_label(route)`：完成按钮文案（spec 五「已发送回复 · 下一条」）。
- `is_dup_activity(acts, type, minutes)`：Activity 去重判定。

### 2. `workbench/db.py`：`record_activity_once()`
幂等版 record_activity：同询盘 2 分钟内同类型事件视为重复点击，跳过写入（返回 False）。防重复 Activity / 重复状态更新。**零新列、零语义变更。**

### 3. `workbench/app.py`：执行队列升级为完整闭环
- **进入队列（spec 一）**：侧栏新增「▶ 开始处理」（按当前筛选+排序生成队列）；今日任务四卡「开始处理」保留原语义。
- **当前客户卡（spec 二）**：Customer/Contact/Inquiry #/Opportunity 商机阶段/AI Priority（0-100）/Stage/Next Action（含 type·priority）/AI Recommendation（NBA 理由 + AIP 前两条加减原因）。
- **执行路由（spec 三）**：按 Action Type 打开对应工作区——回复编写器（REPLY/COLLECT_INFO）、报价区（CREATE_QUOTE）、跟进区（FOLLOW_UP/SCHEDULE）、产品库引导（MATCH_PRODUCT）、内联谈判记录表单（NEGOTIATE，保存可推进 QUOTED→NEGOTIATING）、标记完成（MARK_COMPLETE）。
- **完成推进（spec 四/五）**：`_exec_advance` = 执行完成（保存 Activity、更新 Inquiry/Stage/Status/Last Contact，全部复用既有闭环函数）→ `load_queue()` 全量重算 AI Priority → 按进入时 Filter+Sort 重建队列 → 选下一条最高优先级 → 自动打开；无下一条 → 退出执行模式 +「🎉 处理完毕」。
- **异常（spec 六）**：任何一步失败 → 不跳下一条，错误卡「⚠️ 操作失败，当前记录未完成。」+ [🔄 重试]（重试重放完成+推进）；空队列 → 「当前筛选条件下没有待处理任务。」+ [返回工作台]。
- **数据一致性（spec 七）**：业务状态守卫（已 REPLIED/WON/LOST 不重复发送/更新）+ record_activity_once 去重 + exec_busy 禁用态 + 失败不推进（天然回滚语义：状态未变、队列不动）。

### 4. `workbench/priority3.py`：NBA 映射修正（Phase 4 路由依赖）
- `biz == READY_FOR_QUOTE` → NBA = CREATE_QUOTE（业务状态证据优先于泛型 type 映射；此前 `_ACT_TYPE_MAP` 预映射 REPLY 会挡住 `or` 链）。八类映射验证：READY_FOR_QUOTE→CREATE_QUOTE / READY_TO_REPLY→REPLY / QUOTED→FOLLOW_UP / NEGOTIATING→NEGOTIATE / FOLLOW_UP→FOLLOW_UP / NEEDS_INFO→COLLECT_INFO 全部正确。

## 二、原因
Phase 3 产出了 AI Priority 与 Next Best Action，但从「推荐」到「执行完成再推荐下一条」缺少系统化闭环：销售需要手动回列表、手动选下一条、无失败恢复、无重复操作防护。Phase 4 把推荐引擎接上执行引擎，形成 AI Priority → Next Best Action → Execute → Activity → Update State → Recalculate → Next Customer 的完整循环。

## 三、测试结果

### Phase 4 验收 `test_execution_r14.py`：46/46 全绿（临时库隔离，真实库零污染）
| 场景 | 结果 |
|---|---|
| 1 完成回复（7 项：视图/按钮/biz/LastContact/Status/Activity×1/下一条） | ✅ |
| 2 完成跟进（路由按钮/follow_up_done/FOLLOW_UP_COMPLETED/推进） | ✅ |
| 3 创建报价路由（打开报价区/完成按钮/不伪造 QUOTE_CREATED） | ✅ |
| 4 修改 Stage（工作区 Pipeline QUOTED→NEGOTIATING + STATUS_CHANGE×1） | ✅ |
| 5 Queue 重新排序（重建/完成记录沉出/重派生） | ✅ |
| 6 下一条（selected_id = 重建后最高优先级） | ✅ |
| 7 最后一条（退出执行模式 + 处理完毕提示） | ✅ |
| 8 空队列（提示文案 + 返回工作台） | ✅ |
| 9 API Error（不跳下一条/错误卡/[重试]/恢复后成功/错误消失） | ✅ |
| 10 Refresh（队列与指针保持） | ✅ |
| 11 Browser Back（退出回工作区/新会话正常） | ✅ |
| 12 重复点击（幂等记录/重复发送守卫/路由降级/状态不重复更新） | ✅ |

### 回归 8 套零失败
priority_r13 50 ｜ workflow_r7 65 ｜ crm_r8 32 ｜ scenarios_r11 34 ｜ sidebar_r6 32 ｜ smoke_r4 页面 0 异常 ｜ crm_r10 44 ｜ scenarios_r10 37

### 真实 Chrome 视觉验收（shots_p4）
- `p4_exec_queue.png`：Sales Execution Queue 1/44 卡（ABC Trading #14 · AI Priority 61 · 新商机 · 待回复 · Next Action · AI Recommendation · 五操作按钮）
- `p4_route_reply.png`：路由打开详情工作区（Header 六格 + Pipeline 知识卡联动）

## 四、异常情况与说明
1. **执行队列口径收紧**：两态「已处理」的记录不进入执行队列（无待执行动作；查看走侧边栏）。筛选「已回复」在执行模式下队列为空 → 正确提示"没有待处理任务"。
2. **已回复记录重复进入队列**：路由自动降级为「标记完成」（不再提供发送入口），二次防护由状态守卫承担——属预期设计。
3. **验收期间的测试修正**：场景 2.4 首版断言误写"下一条=进入队列的第二条"，实际规格是"重建队列后的最高优先级"；场景 12 首版误将系统正确行为（已完成记录路由降级写 NOTE）判为失败。均已按规格修正测试（新规范覆盖旧断言），产品代码无需回退。
4. **真实库零污染**：全部变更测试在临时库（tempfile 复制）上进行；视觉验收服务器（8515）已关停。

## 五、对 Agent 核心的架构影响
- **agent 引擎零改动**：评分/匹配/Prompt 一行未动；NBA 仍是对 workflow.next_action 的包装（仅修正 READY_FOR_QUOTE 的映射优先级，Phase 3 语义内的 bug fix）。
- **DB 零新列**：仅新增幂等写入函数 `record_activity_once`，自愈迁移版本号未变。
- **分层保持**：execution.py 纯逻辑可单测；app.py 只做渲染与回调装配；闭环更新全部复用第七轮既有闭环函数（mark_replied/complete_follow_up/update_inquiry_status），没有平行实现。
- **可分发就绪度**：执行队列 + 失败恢复 + 防重复，使产品从「AI 分析工具」升级为「可托付的日常销售操作系统」，支撑卡密分发后的核心使用场景。
