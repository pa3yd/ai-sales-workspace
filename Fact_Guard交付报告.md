# Fact Guard 交付报告：Customer-facing Email Fact Guard

## 一、四源分类（FACT SOURCE）

每个商业事实必须属于以下四源之一，已在分析结果「📋 业务事实层」中逐行标注：

| 来源 | 含义 | 对应既有标注 |
|---|---|---|
| CUSTOMER_FACT | 客户原文明确提供 | Customer Fact |
| COMPANY_FACT | 公司知识库/产品库/价格库/政策库明确存在 | Company Data / Product Data |
| SYSTEM_RULE | 系统预定义业务规则 | 系统判定 |
| UNKNOWN | 无法确认 | Unknown / AI Inference |

UNKNOWN 与 AI_INFERENCE 一律不得写成 COMPANY_FACT——由下述守卫强制执行。

## 二、邮件守卫（generate → validate → 拦截）

**1. Prompt 侧（生成前约束）**：`agent/replier.py` SYSTEM_PROMPT 新增
EMAIL FACT GUARD (ABSOLUTE) 规则：列出全部禁承诺项；明确规格书例句——
客户说 "We need samples." → 只能写 "We can arrange samples for evaluation
and will confirm the sample cost and courier arrangement."，禁止
"free samples"；无法验证时写确认句 "Let me confirm ... and get back to you."

**2. 校验侧（生成后拦截）**：`agent/facts.py::validate_email_fact_guard`
新增 13 类禁止自动承诺检测（公司知识库无依据即拦截）：
免费样品/运费/模具/设计/认证 · 固定价格 · MOQ 数值 · 固定交期（沿用第九轮）·
库存 · 产能 · 认证已具备 · 付款条件（含 T/T）· 保修 · 运输时效 · 担保句式。
有产品库依据时放行：MOQ 数字与 `product.moq` 一致、认证在 `product.certification`
清单内。已接入 `_validate_all` 四路校验链（事实一致性 + 追问策略 + 商业承诺 + Fact Guard）。

**3. UI 侧（人工复核闸口）**：
- 草稿存在任何未验证承诺 → Email 预览折叠区**外**显示红色横幅
  **⚠️ HUMAN REVIEW REQUIRED**（逐条列出违规项），折叠区标题同步加标记；
- 「📋 业务事实层」标题改为 FACT SOURCE 四源图例，每行来源下叠加四源归一标注。

## 三、修改文件

| 文件 | 改动 |
|---|---|
| `agent/facts.py` | +13 类守卫正则 + `validate_email_fact_guard()` |
| `agent/replier.py` | SYSTEM_PROMPT 守卫规则；`_validate_all` 接入守卫；**修复存量合规漏洞**（见下） |
| `main.py` | report 新增 `human_review_required` 字段 |
| `workbench/app.py` | HUMAN REVIEW 横幅 + expander 标记 + FACT SOURCE 四源标注 |
| `test_fact_guard.py` | 新增测试 31 项 |

## 四、顺手修复的存量合规漏洞

回归测试暴露：规则模式模板原本会**自己生成违禁承诺**——
① 样品请求提示词含 "mention free samples are available (buyer pays courier only)"；
② 认证请求在无任何数据时会写 "we are happy to share our available certificates"。
均已改为确认式/数据驱动表述（样品 → arrange + confirm cost；认证 → 有配置列
"Our certifications include X"，无配置 → "we will confirm which certifications apply"）。

## 五、测试结果

- 新增 `test_fact_guard.py`：**31/31 通过**（15 类违禁句全拦截、5 类确认式全放行、
  MOQ/认证有据放行·无据拦截、规格书 "We need samples." 端到端不含 free sample、
  FACT SOURCE 四源归一、第九轮原三类承诺校验不受影响）。
- 全套回归 **13 套件全绿**：r5 109/109（修复后）、r9 41、r7 65、r10 44、r11 34、
  r6 32、r8 32、patch03 32、数量语义 47、一致性 11、意图 39。
- UI 无头验证：违规草稿 → 横幅+标记出现；合规草稿 → 无横幅；图例正常。

## 六、边界说明

- 本系统当前无自动发送通道（仅生成草稿+复制/下载），"阻止自动发送"落实为：
  违规草稿被显式标记 HUMAN REVIEW REQUIRED 且不可绕过提示直接使用。
- LLM 模式下守卫同样生效（校验链对 LLM 草稿一并执行），Prompt 与校验双保险。

## 七、下一步建议

1. 把 `config.json` 的 SELLER 字段补全（certifications / payment_terms / warranty /
   lead_time 等），让更多承诺从 UNKNOWN 升级为 COMPANY_FACT；
2. 事实守卫规则可在界面上做成可配置开关（按公司政策微调严格度）；
3. 若接入邮件发送 API，发送按钮必须校验 `human_review_required == False`。
