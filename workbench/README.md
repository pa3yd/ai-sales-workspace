# AI 外贸询盘分析 · 工作台（Workbench）

把原来的「命令行 Agent」升级成「网页工作台」：**粘贴询盘 → 自动分析 → 存入数据库 → 历史随时回看**。

> 分析大脑完全复用 `../main.py`，本目录只新增「界面 + 数据库」两层，不改动任何分析逻辑。

## 一、怎么用（两步）

1. 双击 `启动工作台.bat`
2. 浏览器自动打开 `http://localhost:8501`，开始粘贴询盘分析

关闭那个黑色窗口 = 停止工作台。

## 二、界面能做什么

| 区域 | 功能 |
|---|---|
| 顶部模式选择 | auto（有 Key 用 LLM，否则规则）/ llm（强制智能）/ rule（离线） |
| 主文本框 | 粘贴客户询盘原文，支持英/法/德/西任意语言 |
| 开始分析 | 调用引擎 → 自动存库 → 展示 ⓪状态与优先级 ①提取 ①·5缺失信息与追问 ②匹配 ③评分 ④回复草稿 |
| 左侧历史 | 每条带状态图标（🔵待处理/✅已处理）+ 优先级，可按状态筛选、按优先级排队 |
| 回复草稿 | 文本框可直接复制，也能一键下载 txt |
| 📌 跟进台 | **按客户等级自动排优先级**，一键标记 待处理/已处理，可折叠查看算法说明 |
| 👥 客户档案 | 自动按邮箱/公司归并去重；可按 **A/B/C/D 等级筛选与归档**，手动定级、加客户备注 |
| 📦 产品库 | 网页上直接新增/编辑/删除产品；含**库存管理**（当前库存/安全库存，低于安全线自动预警），改动后分析立即生效 |
| 📤 导出 | 一键导出：询盘记录（含产品名称/来源国/咨询数量/采购意向/备注）、客户档案（按等级归档）、产品库（含库存） |

## 三、文件说明

```
workbench/
├── app.py              界面（Streamlit），五个标签页：分析/跟进台/客户/产品/导出
├── db.py               数据库层（SQLite 标准库，零依赖），含客户归并
├── catalog.py          产品库读写（直接读写 ../data/products.json）
├── gapcheck.py         缺失信息检测 + 英文追问邮件生成（离线规则，可选 AI 润色）
├── workbench.db        自动生成的数据库文件（别手动删）
├── 启动工作台.bat      一键启动（已写死 Python 路径，双击即用）
└── README.md           本文件
```

## 四、从 Agent 到工作台的「一步步」实现（复盘）

**第 1 步 · 确认可复用的接口**
读 `main.py`，找到两个现成函数：`build_engine(mode)` 造引擎、`analyze(text, ex, mt, cl)` 出报告。结论：分析逻辑一行都不用重写。

**第 2 步 · 装界面框架**
在隔离 venv 装 Streamlit：`pip install streamlit -i https://pypi.tuna.tsinghua.edu.cn/simple`
（你之前装 uv 失败，但 pip 走清华镜像是通的。）

**第 3 步 · 写数据库层 `db.py`**
用 Python 内置 `sqlite3`，建 `inquiries` 表，提供 增/查/删 四个函数。这是「从脚本变系统」的分水岭——数据能存、能查、不丢。

**第 4 步 · 写界面 `app.py`**
`sys.path` 把父目录加进来 → `import main` → 按钮触发 `main.analyze()` → 结果存 `db` → `st.metric` / `st.text_area` 渲染四阶段报告 → 左侧 `st.sidebar` 列出历史。

**第 5 步 · 做启动器 `.bat`**
写死 WorkBuddy 内置 Python 绝对路径，`chcp 65001` 防中文乱码，`--server.headless` 不弹多余窗口。

**第 6 步 · 无头冒烟测试**
后台起服务 + curl 探活 + 查启动日志无 Traceback，确认可运行。

## 五、阶段二已完成（本目录新增能力）

| 能力 | 实现位置 | 说明 |
|---|---|---|
| 客户档案自动归并 | `db.py` 的 `customers` 表 + `upsert_customer` | 按邮箱（优先）或公司名去重，同一客户的多次询盘合并成一条档案并计数 |
| 客户 A/B/C/D 等级分类归档 | `db.py` 的 `customers.grade` + `app.py` 客户档案标签 | 顶部可按等级筛选，每个客户可手动定级 A/B/C/D 并保存，顶部展示各级客户数 |
| 产品库存管理 | `catalog.py` + `app.py` 产品库标签 | 每个产品含 `stock`/`safety_stock`；低于安全库存时红色预警；可直接在表单里改 |
| Excel 导出（按需求定制列） | `app.py` 导出标签 + `db.dump_inquiries` | 询盘记录导出含：产品名称 / 来源国家 / 咨询数量 / 采购意向 / 紧急度 / 备注 / 客户等级；客户档案按等级归档导出；产品库含库存导出 |
| 询盘/客户备注 | `db.py` 的 `note` 列 | 分析页与每位客户均可加备注，导出 Excel 会带上 |
| 界面交互打磨 | `app.py` | 顶部显示卖方公司名与优势；分析后可「清空输入」「复制草稿」；等级用颜色标识 |

## 五之二、询盘状态标记 + 优先级排队（本次新增）

**状态**：每条询盘有 `待处理` / `已处理` 两态，`db.py` 的 `inquiries.status` 列（旧库自动 ALTER 补列，默认「待处理」）。

| 位置 | 能做什么 |
|---|---|
| 📥 分析询盘 | 结果顶部显示「处理状态 + 处理优先级」，两个按钮一键切换 待处理 / 已处理 |
| 📌 跟进台 | 集中排队：顶部 4 个计数（待处理 / 🔴优先 / 🟠正常 / 🟢延后），可切换「待处理 / 已处理 / 全部」，每行一键改状态 |
| 🔍 左侧历史 | 带 🔵/✅ 状态图标，可按状态筛选，默认按优先级排队（A 级客户在最前） |
| 📤 导出 | Excel 新增「处理状态」列 |

**优先级算法**（`db.calc_priority`）：

```
分值 = 客户等级分(A50/B35/C20/D5)
     + 线索等级分 × 0.6
     + 评分 × 0.1
     + 紧急度加分（高 +15 / 中 +7）
```

| 档位 | 分值 | 大致对应 |
|---|---|---|
| 🔴 优先处理 | ≥ 65 | A 级客户、B 级高分客户 |
| 🟠 正常处理 | 35 ~ 64 | B 级低分、C 级客户 |
| 🟢 可延后 | < 35 | D 级客户 |
| ⚪ 已完成 | — | 已标为「已处理」，不再排队 |

客户等级取**手动归档的等级**（客户档案里定的 A/B/C/D），没手动定级时退回线索等级。

## 五之二·加、AI 询盘评分（四维模型，`agent/lead_score.py` v2）

**解决的问题**：旧模型只给一个总分（如 45 分=B），一条"3000pcs + looking for supplier +
European market + customized logo + premium packaging + certificates + fast delivery"
的明确商业询盘，和空泛询盘混在同一档，业务员看不出该优先处理谁。

**新模型**：四个维度分别打 0~100 分，再加权合成综合分：

```
综合评分 = 采购意向 × 0.3 + 订单价值 × 0.3 + 需求明确度 × 0.2 + 成交可能性 × 0.2
```

| 维度 | 权重 | 看什么 |
|---|---|---|
| 采购意向 | 30% | 有无采购数量、looking for supplier / 要报价 / 要样品 / 复购信号 |
| 订单价值 | 30% | 数量档位（≥5000 大单 45 分 / ≥1000 中大 35 分 / ≥300 中等 25 分）、目标价、年度量、欧美主流市场 |
| 需求明确度 | 20% | 产品匹配、型号规格、定制（logo/颜色）、包装、认证、目的地、交期 |
| 成交可能性 | 20% | 公司 / 官网 / 邮箱 / 联系人、与自有产品匹配、替换现有供应商机会、紧急度 |

**等级与建议回复时限**（80 / 60 / 40 分档）：

| 综合分 | 等级 | 建议 |
|---|---|---|
| ≥80 | 🔥 A - High Priority | **4 小时内回复** |
| 60~79 | 🟠 B - Medium-High Priority | 24 小时内回复 |
| 40~59 | 🟢 C - Normal Priority | 48 小时内回复 |
| <40 | ⚪ D - Low Priority | 可先归档，定期发开发信培养 |

**业务规则**：不能因为一个信息缺失就直接降低客户意向——采购意向（purchase_intent）
和紧急度（urgency）是两个**独立字段**分开输出；客户明确"3000 pcs + OEM + 定制 +
欧洲市场 + 3 周交期"即使容量未定，也是较高价值询盘。

**界面表现**（③ AI 询盘评分）：综合评分 + 等级 + 回复建议一目了然，
下面四条进度条（采购意向 / 订单价值 / 需求明确度 / 成交可能性），
展开「查看评分依据」可看到每一分的来由。

**兼容性**：历史记录存的是旧模型单一总分 → 打开时自动用新模型重算展示，无需迁移；
线索等级现在存纯字母 A/B/C/D，优先级排队（五之二）从此能正确吃到线索等级分。
命令行版（分析询盘.bat）输出同步升级为四维条形图。

## 五之三、缺失信息检测 + 英文追问（`gapcheck.py`）

**解决的问题**：客户只丢一句 "We are interested in your products. Please send price." 就完了，
业务员不知道该问什么。现在工作台会**先告诉你缺什么，再帮你写好英文追问信**。

**检测的 17 个关键字段**（`gapcheck.FIELD_DEFS`，可自行增删），按外贸实际报价流程分三级：

> 核心原则：**Target Price（目标价格）是可选问题（C 类）**——客户有目标价可以帮销售
> 制定价格策略，但没有它照样报价。A 类才是"缺了真报不了价"的。

⚠️ **A 类 · 报价前需要确认**（缺这些没法报价，第一封信必须问）

| 字段 | 中文名 | 英文追问句 |
|---|---|---|
| product_spec | 产品型号 / 规格 | Which model or specification are you interested in? |
| quantity | 采购数量 | What quantity do you require? |
| customization | 定制要求（Logo / 颜色 / 尺寸） | Do you need any customization, such as logo printing, specific color or size? |
| destination | 目的地（收货国家 / 港口） | Which country or port should we deliver to? |
| certification | 关键认证要求 | Do you need any specific certification (e.g. CE, EN71, REACH, FDA)? |
| company | 公司信息 | Could you share your company name and website? |
| email | 联系邮箱 | Could you please provide your email address so we can send the quotation? |

📌 **B 类 · 建议确定**（影响价格方案与跟进节奏，能问到最好）

| 字段 | 中文名 | 英文追问句 |
|---|---|---|
| delivery | 目标交期 | What is your target delivery date? |
| packaging | 包装要求 | Any specific packaging requirements (e.g. polybag, printed box, barcode)? |
| payment | 付款方式 | What payment terms do you usually work with (e.g. T/T, L/C at sight)? |
| incoterm | 贸易术语 | Which trade term do you prefer (e.g. FOB, CIF, EXW, DDP)? |

💡 **C 类 · 可选问题**（参考信息，建议第二封信再问，一次问太多会把客户问跑）

| 字段 | 中文名 | 英文追问句 |
|---|---|---|
| target_price | 目标价格 | If you have a target price in mind, please let us know… |
| company_scale | 公司规模 | Could you tell us a bit about your company scale? |
| annual_volume | 年度采购量 | What is your estimated annual purchase volume for this item? |
| sales_channel | 销售渠道 | Which sales channel do you sell through (retail, wholesale, Amazon…)? |
| competitor | 现有供应商 / 竞争对手 | Are you currently sourcing this item from another supplier? |
| purchase_cycle | 采购周期 | How often do you place orders (monthly, quarterly, or per season)? |

**判定方式**（纯离线正则 + 引擎提取结果，不花钱）：
- 产品型号：产品匹配分 ≥ 0.3 算"客户说清楚了"（阈值别定太高，"5000pcs swim caps" 实际只有 0.4 分）
- 数量 / 目的地 / 目标价 / 公司：看 `extracted` 里有没有值
- **邮箱（特殊逻辑）**：三个来源任一命中就不再追问——① 本次提取结果；② **客户档案里归并过的邮箱**（询盘通常来自 Email / Alibaba / WhatsApp / LinkedIn / 展会 / CRM 等渠道，历史询盘早存过联系方式）；③ 询盘原文 / 邮件签名里直接出现（正则自动找）。只有网页表单等真拿不到邮箱的情况，才会追问 "Could you please provide your email address…"。邮箱已知时名片卡也会标注「来自客户档案」
- 定制 / 认证 / 包装 / 付款 / 贸易术语 / 交期 / C 类各字段：正则扫原文关键词（logo / OEM / CE / T/T / FOB / deliver / Amazon…）

**智能追问（引用客户自己的话，不泛泛地问）**：

客户明明写了 "500ml or 750ml, not sure yet."，机械模板还问 "Which model or specification are you interested in?" 就太外行了。工作台会识别四类"客户已给线索但没说完"的情况，把追问句换成引用客户原话的定向问题：

| 客户原话 | ❌ 机械版 | ✅ 智能版 |
|---|---|---|
| 500ml or 750ml, not sure yet | Which model or specification…? | You mentioned "500ml or 750ml" - which capacity would you prefer? |
| blue or black, not decided | Which color…? | You mentioned "blue or black" - which color would you prefer? |
| We sell in European market | Which country or port…? | Which country in Europe should we deliver to? |
| certificates needed | Do you need any specific certification? | Which certifications do you require - CE, EN71, REACH, or others? |
| about 3000pcs | What quantity do you require? | Could you confirm the quantity? I saw a rough number in your message - is that per order? |
| delivery within 3 weeks | What is your target delivery date? | Could you please confirm whether the 3 weeks delivery requirement includes shipping time? |

产品已匹配但客户仍有未定选项时，会单独追加一条「客户未定的选项」A 类追问放在最前面。
「✨ AI 润色」的系统提示词同样要求 AI 引用客户原话、禁止在客户已给提示时问泛泛问题。

**自动排序**（业务员看一眼就知道先回复什么）：
- 组间：⚠️ 报价前需确认 → 📌 建议确定 → 💡 可选问题，编号跨组连续
- 组内：**🎯 智能追问**（引用客户原话的定向问题）排在模板句前面——先解决客户话里没说死的，再补客户没提的
- 每个问题带**中文业务说明**：为什么问这个（如"客户在「500ml or 750ml」之间还没确定，报价前必须先锁定这个规格"），新人也能直接照着跟进

**界面表现**：分析完在 ① 和 ② 之间多一块「⚠️ 缺失信息（按外贸报价流程分 A / B / C 三级）」
- 顶部 4 个指标卡：**报价就绪度**（A 类已有数 / A 类总数，A 类齐了就能报价）+ A / B / C 各缺几项
- 横幅提示：A 类齐 → 绿色"可直接报价"；A 类还差 ≤2 项 → 黄色"先追问再报价"；差得多 → 红色"信息严重不足，别急着报价"
- 三个折叠区按 ⚠️A → 📌B → 💡C 列出：编号 + 字段名（🎯 标记智能追问）+ 中文业务说明 + 英文追问句（A 类默认展开）
- **交期特殊逻辑**：客户说了具体交期（"within 3 weeks" / "4 weeks"）也算半答案，仍会追问"是否含运输时间"——这是业务上最容易踩坑的点
- **勾选要追问的等级**（A / B 默认勾上，C 默认不勾），按勾选组合实时生成英文追问邮件（可编辑、可下载 txt）
- 有 API Key 时可点「✨ AI 润色」，让 AI 结合原文重写一封更自然的（失败自动保留规则版）

信息齐全时不显示这块，改为绿色提示「✅ 关键信息齐全，可以直接报价」。

## 五之四、报价准备度 + 下一步动作（`agent/insight.py`，业务洞察层）

分析流水线在 缺失信息 之后新增一层业务判断，直接回答业务员最关心的问题：
**「现在能不能报价？卡在哪？我下一步该干什么？」**

**报价准备度（quotation_readiness）**：
- `quotation_readiness_score`（0-100）：不是"已填字段/总字段"，而是按
  产品方向 30 + 数量 25 + 规格 15 + 定制 10 + 目的地 10 + 交期 10 + 包装 5 + 认证 5
  加权，再按阻塞项扣分（high -15 / medium -8），代表"距离可以合理报价有多近"
- `quotation_readiness_status` 四种状态：
  `ready_for_quotation` 可直接正式报价 / `preliminary_quote_ready` 可推进初步报价 /
  `needs_confirmation` 需先确认关键信息 / `cannot_quote` 暂无法报价
  ——**不能因为缺一个普通字段就判 cannot_quote**
- `blocking_reasons`：每条 `{field, severity, impact, reason}`，说明哪个字段阻塞、
  为什么、影响什么（如"客户在 500ml/750ml 之间尚未确定，不同容量对应不同 SKU 和价格"）

**partially_confirmed**（候选未定 ≠ 缺失）：客户说 "Maybe 500ml or 750ml, not sure yet"
识别为 `{"field":"capacity","status":"partially_confirmed","values":["500ml","750ml"]}`，
不算 missing，追问时直接引用原话让客户二选一。

**认证兴趣 vs 认证要求**（`certification_interest` / `certification_requirement`）：
"What certificates do you have?" 是兴趣（不阻塞报价）；"We require CE certification."
才是要求。只有明确要求且影响产品/成本时才进 A 类。

**客户信息补全**：新增 job_title / phone / whatsapp / city / customer_type /
business_type 字段，识别不到一律 `null`，绝不猜测；联系方式（邮箱/公司）缺失
**不阻塞报价**（从 A 类降为 B 类，见五之三字段表）。

**风险（risks）**：只输出有事实依据的风险——交期短于产品库常规交期、规格未定、
认证只问不要求、目标价低于产品库参考价下限、数量低于 MOQ。没有依据不制造风险。

**下一步动作（next_actions）**：最多 5 条按优先级排序，动作枚举：
ask_for_information / prepare_preliminary_quote / prepare_formal_quote /
recommend_products / send_reply / follow_up / manual_review。

**产品匹配升级**：只返回 Top 3，每个候选带 `matched_attributes`（命中了什么）、
`uncertainty`（如数量低于 MOQ）、`recommendation`（推荐语 + 还需确认什么）；
产品库真没有相关产品时如实提示「当前产品库未找到匹配产品」，**禁止编造产品/价格/
认证/MOQ/库存**。未匹配到产品时也会生成回复草稿（请客户提供产品名/图片/链接）。

> 阶段三（多用户 / 上云 / 批量分析 / 看板）尚未做，按需再加。

## 五之五、第三轮优化：四级报价状态 / A·B·C 确认项 / 八维评分 / 一致性检查

### 1. 报价准备度 v3（`agent/insight.py`：`STATUS_*` / `SCORE_BANDS` / `build_readiness`）
- **四级报价状态**（评分与状态硬一致，绝不打架）：
  `insufficient_info` 信息不足，无法有效报价（≤55 分）/
  `preliminary_quote_ready` 可初步报价，可给参考价/区间（56~88 分）/
  `ready_for_quotation` 可正式报价，产品/规格/数量及关键商务条件已确认（≥89 分）/
  `quoted` 已报价（人工标记，系统不自动判定）。
  分数先按 10 项因素加权算出来，再按状态区间**收口**；历史库里的旧状态
  `needs_confirmation` / `cannot_quote` 由 `normalize_status()` 归一显示为信息不足。
- **10 项因素**：产品(25) 数量(20) 规格(12) 认证(8) 目的地(8) 交期(10)
  定制(4) 贸易术语(3) 付款(3) 联系方式(2)，每项输出
  「输入事实 → 判断 → 分数」的 `reason` 与 `readiness_basis`。
- **确认项分级**：A=必须确认（产品/规格/数量，未确认绝不能显示"可正式报价"）；
  B=建议确认（认证/目的地/交期/定制，未确认可先初步报价）；C=可后续确认
  （贸易术语/付款/联系方式，不阻塞任何报价）。结果存 `confirmation_items{A,B,C}`。

### 2. 部分确认升级（`detect_partially_confirmed`）
partially_confirmed ≠ missing。每个 item 新增：
`customer_options` 客户当前意向（如 500ml/750ml）、`status_text` 尚未最终确认、
`sales_advice` 给销售的方案式建议（"可同时提供两个规格的价格、MOQ 和基本参数，
帮客户做决定"）、`question` 升级为**方案式追问**（"We can offer both 500ml and
750ml options. Would you like us to quote both for comparison..."），不再干巴巴问
"Which one do you prefer?"。

### 3. 八维可解释评分 v4（`agent/lead_score.py`）
综合分 = 采购意向×.20 + 订单价值×.20 + 需求明确度×.12 + 客户信息完整度×.12
+ 紧迫度×.08 + 产品匹配度×.10 + 商务成熟度×.08 + 成交可能性×.10（权重和=1.00，
随结果输出 `weights`，任何人均可复算）。每维输出：分数 + 原因(`detail`) +
**证据链 `evidence`（输入事实→判断→分数）**。低分必须说得清为什么
（如成交可能性因"规格未定 -15 / 无邮箱 -10 / 预算未知 -5 / 认证未定 -5"）。
订单价值不虚构金额，保留 `order_value_confidence` / `order_value_basis`。

### 4. 产品匹配八要素（`agent/matcher.py`）
每个候选输出：匹配产品 / 匹配度 / 匹配依据(`match_basis`) / 已满足条件
(`matched_conditions`) / 未满足条件(`unmatched_conditions`，产品库无法证实的
属性如"Logo印刷：产品库未提供该属性，需人工确认") / 缺失信息(`missing_info`) /
推荐理由(`recommendation`)。完全没有证据时明确提示
「当前产品库没有足够证据找到匹配产品」，禁止虚构。

### 5. 邮件追问规则（`agent/replier.py`）
LLM 与模板双模式统一：第一轮最多 2~3 问、按 P0（产品/规格/数量/认证/目的地/
交期）> P1（付款/贸易术语/目标价）> P2 动态选择；认证询问改为"主动提供"；
候选规格用方案式问句；称呼回退链 联系人 > 公司。

### 6. Anti-Hallucination + 数据来源 + 一致性检查
- 10 类禁编造清单 `ANTI_HALLUCINATION_RULES`；`tag_data_sources()` 把报告数据分为
  客户信息 / 公司资料 / 产品数据 / AI 推理 四类，AI 推理不得覆盖前三类真实数据。
- `check_consistency()`：每次分析完成自动跑 6 项（准备度分数↔状态 / 追问↔已知禁止项 /
  候选规格↔邮件 / 无匹配↔邮件不报价 / 评分↔依据复算 / 称谓↔客户信息），
  冲突进 `report["consistency"]`，界面以 ❌ 高亮并要求优先人工核对。

## 五之六、第四轮优化一：回复时限 / 跟进优先级对齐客户价值（2026-09-04）

### 解决的问题
产品库没有匹配时（如客户问的是库外品类），`product_match` / `conversion`
两个维度会把综合分拉低，进而把「3000pcs + 明确交期 + ASAP」的真实询盘判成
C 级 48 小时回复，跟客户的明确时间要求、跟进台排序互相打架。

### 改动（`agent/lead_score.py` / `workbench/db.py` / `workbench/app.py`）
- **等级 / 回复时限只看「客户质量分」**：取意向 / 数量 / 明确度 / 信息完整 /
  紧迫度 / 成熟度 六维按权重归一（不含产品库匹配与成交概率），新增输出
  `customer_quality_total`；8 维综合分 `overall_score` 保留仅作参考展示。
- **明确时间要求兜底 24h**：客户质量分 40~60 之间（原判 C-48h）的线索，若出现
  ASAP/urgent 或明确交期（within N weeks / by March 等），自动收紧为 B 级、
  24 小时内回复——避免"客户在催却排 48h 档"。
- **跟进台高紧迫保底**：`calc_priority` 对 `urgency=high` 的待处理询盘最低给 40 分
  （🟠 正常处理），不允许掉进 🟢 可延后；升级后的等级同时提高队列排序分。
- **界面口径说明**：③ 评分区在"综合评分 ≠ 客户质量分"时自动加一行小字，
  解释等级为何由客户质量分判定（综合分含我方接单能力，仅供参考）。

### 效果（David / ABC Trading UK 实测）
改动前 C 级·48h·评分 58；改动后 **B 级·24 小时内回复·客户质量分 72**（8 维
综合仍 58，因库无瓶类匹配）→ 跟进台 🔴 优先处理(76 分)；报价准备度 78
（可初步报价）；6 项一致性检查全通过。空泛询盘仍 D 级，泳帽完整询盘仍 B 级，
无回归。

## 六、常见问题
- **Q：点「开始分析」报错说没 Key？** 选 `rule` 模式可离线跑；要用 LLM 就在 `../config.json` 填 `DEEPSEEK_API_KEY`。
- **Q：历史记录在哪？** 全在 `workbench.db`，删库即清空所有历史。
- **Q：换电脑能用吗？** 能，只要把整个 `AI询盘agent` 文件夹拷走，重新 `pip install streamlit` 即可。
