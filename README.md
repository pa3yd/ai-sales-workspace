# AI 外贸销售工作台（AI Sales Workspace）

AI 驱动的外贸销售执行工作台：把海外询盘 → 客户画像 → AI 优先级 → 下一步动作 →
执行跟进 → 活动留痕 → 阶段推进，收敛成一条「每天只处理最值得处理的客户」的销售闭环。

## 功能一览

- 询盘队列（搜索 / 筛选 / AI 综合排序 Queue Score）
- AI Priority 0-100（七维可解释评分 + 原因展开）
- Next Best Action（8 类动态主 CTA）
- Customer + Opportunity 工作区（Header / Pipeline / 阶段知识）
- AI Sales Assistant（该做什么、为什么、风险）
- Activity Timeline（跟进 / 回复 / 报价留痕）
- Sales Execution Queue（任务模式：完成一条自动进下一条）
- 回复草稿生成（Fact Guard 防幻觉：不编造价格 / MOQ / 交期 / 认证）

## 目录结构

```
agent/          AI 引擎：解析、评分、匹配、回复、事实层（纯标准库，无第三方依赖）
workbench/      工作台：app.py（入口）、db.py、priority3.py、execution.py 等
licensing/      卡密模块（gen_key.py 生成、licensing.py 校验）
data/           产品目录 data/products.json
main.py         CLI 版询盘分析入口
```

## 安装与运行（新电脑）

1. 安装 Python 3.12 / 3.13（勾选 Add Python to PATH）
2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 配置 DeepSeek API Key（二选一，推荐环境变量）：

```bash
# 方式一：环境变量（config.json 不存在时的唯一方式）
set DEEPSEEK_API_KEY=sk-你的key        # Windows CMD
$env:DEEPSEEK_API_KEY="sk-你的key"     # Windows PowerShell

# 方式二：在项目根目录新建 config.json（参考下方模板）
```

config.json 模板（**注意：该文件已被 .gitignore 排除，不会上传**）：

```json
{
  "DEEPSEEK_API_KEY": "sk-你的key",
  "MODEL": "deepseek-chat",
  "SELLER": {
    "company": "你的公司名",
    "city": "Your City, China",
    "sales_name": "Sales Team",
    "company_type": "direct manufacturer",
    "certifications": [],
    "strength": "一句话公司优势"
  }
}
```

> 环境变量优先于 config.json。`SELLER` 是回复草稿的事实来源：草稿只会声称这里明确写的身份与资质，留空则不会自称 manufacturer、不会点名认证。

4. 启动工作台（首次运行自动创建空数据库）：

```bash
cd workbench
streamlit run app.py
```

浏览器打开 http://localhost:8501 即进入工作台。

## 跑测试（回归验证）

```bash
python test_priority_r13.py    # AI Priority / NBA 逻辑
python test_execution_r14.py   # 执行队列闭环
python test_reply_strategy_r5.py  # 回复策略（109 项）
python test_sidebar_r6.py      # 侧栏 UI 行为
python test_smoke_r4.py        # 冒烟
...
```

## 数据说明

- 业务数据存于 `workbench/workbench.db`（SQLite），**已从版本库排除**。
  首次运行自动建表；想保留数据请整库拷贝该文件。
- `data/products.json` 为产品目录（运行必需，随仓库分发）。

## 设计规范

- UI 遵循 Design Token（`:root` CSS 变量）+ `.streamlit/config.toml` 主题（品牌蓝 #2563EB）。
- 硬约束：AI 评分 / 匹配 / Prompt 层只读数据来源，UI 只做展示与执行，不做业务状态猜测。
