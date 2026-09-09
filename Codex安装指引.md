# Codex 安装指引(换电脑 / 新环境快速上手)

> 面向想用 **OpenAI Codex(AI 编程助手)** 快速在本项目上「下载 → 安装 → 运行」,
> 以及之后让 Codex 帮你改代码的人。零基础可照做,每条命令都能直接复制。

---

## 0. 这份文档解决什么问题

- **场景一:换新电脑**。不想手动敲一堆命令,想让 Codex 自己把工作台装好跑起来。
- **场景二:日常维护**。装好之后,用自然语言让 Codex 帮你修 bug、加功能、跑测试。

> 不会用 Codex / 没有 OpenAI 付费账号?不影响使用本项目——
> 见文末「第 6 节:不用 Codex 的手动版」,README 里也有完整手动步骤。

---

## 1. Codex 是什么?和 GitHub 仓库有什么关系?

- Codex 是 OpenAI 的**本地编程代理**:装在你自己电脑的终端里,
  听得懂自然语言,能自己读文件、改文件、执行命令。
- 它和你的 GitHub 仓库**没有绑定关系**,GitHub 上**不需要**任何授权或连接设置。
  你只要把仓库网址告诉它,它就会用 `git clone` 把它拉下来。
- 本项目仓库是**公开的**,clone 时不需要 GitHub 账号或 Token。

---

## 2. 前提清单(缺一个都走不通)

| 需要准备 | 说明 | 没有怎么办 |
|---|---|---|
| Node.js ≥ 22 | 只在「安装 Codex CLI」时需要 | 去 nodejs.org 下载安装包,一路下一步 |
| ChatGPT 付费订阅 或 OpenAI API Key | Codex 登录用 | 没有 → 直接用第 6 节手动版 |
| DeepSeek API Key | 本项目调 AI 分析询盘用(自己的 `sk-...`) | 去 DeepSeek 开放平台申请 |
| 网络能访问 github.com | 拉取仓库用 | 企业网络下被拦截 → 见第 7 节 FAQ |

> 说明:仓库里**故意没有** `config.json`(已 .gitignore 排除,防 Key 泄露)。
> clone 下来后需自己配置 DeepSeek Key,第 4 步由 Codex 帮你完成。

---

## 3. 安装 Codex(每台电脑只需一次)

**第 1 步**:先确认已装 Node.js(终端执行,应显示 v22 或更高):

```bash
node -v
```

**第 2 步**:全局安装 Codex CLI:

```bash
npm install -g @openai/codex
```

> 国内网络较慢时可先切换镜像源再装:
> `npm config set registry https://registry.npmmirror.com`

**第 3 步**:启动并登录:

```bash
codex
```

首次运行选 **Sign in with ChatGPT**,浏览器会弹出授权页,登录后凭据存本机,以后不用重复登。
(也可选 API Key 方式,需自行配置 `OPENAI_API_KEY`。)

---

## 4. 让 Codex 自动安装并启动本项目(核心步骤)

在你想运行工作台的目录打开终端,执行 `codex`,然后把下面这段**整段复制**进对话:

```
帮我把这个项目安装并跑起来:
1. 克隆公开仓库 https://github.com/pa3yd/ai-sales-workspace 到当前目录
2. 进入项目目录,先读 README.md 了解结构,再读 requirements.txt
3. 创建 Python 虚拟环境并安装依赖(pip install -r requirements.txt)
4. 提示我提供 DeepSeek API Key,在项目根目录创建 config.json 并填入
   (模板按 README 的格式,SELLER 部分先留空)
5. 进入 workbench 目录,执行 streamlit run app.py
6. 启动成功后告诉我浏览器访问地址
```

Codex 会一步步执行,中途**问你 DeepSeek Key** 时,把 `sk-...` 那串贴给它即可。
它建好 `config.json` 后工作台就能正常调 AI。

**完成后**:浏览器打开 `http://localhost:8501` 即进入工作台。
首次运行会自动创建空的 `workbench/workbench.db`,**无需手动建数据库**。

> 如果中途报错(如网络卡住),直接把报错复制给 Codex,它会自己排查重试。

---

## 5. 之后怎么让 Codex 帮你维护(可选,进阶)

以后想改功能,先 `cd` 到项目目录再运行 `codex`,然后说人话,例如:

- 「帮我在队列卡片上加一个 Risk 徽标,只改 UI/CSS,不要动评分逻辑。」
- 「跑一遍 workbench 目录下的测试,把失败的用例修好。」
- 「我想把新建询盘加一个『来源』字段,需要动哪些文件?先给我方案。」

Codex 会直接改代码并告诉你改了什么。
> 注意:本项目有硬约束——AI 评分 / 匹配 / Prompt 属于核心逻辑,改 UI 时不要顺手改它们;
> 想让 Codex 尊重这一点,可以把它写进口令里(如上面第一条)。

---

## 6. 不用 Codex 的手动版(兜底)

没有 ChatGPT 订阅也能跑,核心就 3 句:

```bash
git clone https://github.com/pa3yd/ai-sales-workspace.git
cd ai-sales-workspace && pip install -r requirements.txt
cd workbench && streamlit run app.py
```

然后在项目根目录手动新建 `config.json`(README「安装与运行」一节有完整模板),填入:

```json
{
  "DEEPSEEK_API_KEY": "sk-你的key",
  "MODEL": "deepseek-chat",
  "SELLER": { "company": "你的公司名", "city": "Your City, China" }
}
```

保存后重启 `streamlit` 即可。更完整的说明见仓库根目录 **README.md**。

---

## 7. 常见问题 FAQ

| 问题 | 解决办法 |
|---|---|
| 启动后 AI 功能报 Key 错误 | 检查 `config.json` 是否在**项目根目录**且 Key 正确;若之前设过 `DEEPSEEK_API_KEY` 环境变量,它优先于 config.json,需重开终端或 `unset` 后再试 |
| 改了 config.json 不生效 | 重启 streamlit(终端按 `Ctrl+C` 后重新 `streamlit run app.py`) |
| `git clone` 卡住 / Empty reply | 企业网络常见。先浏览器访问 github.com 确认能通;不行则走代理,或按 hosts 修复教程固定 github.com IP |
| 8501 端口被占用 | 换端口启动:`streamlit run app.py --server.port 8502`,访问对应地址 |
| 想带旧数据过去 | 把旧电脑 `workbench/workbench.db` 整个文件拷到新电脑同名位置(该文件不入库,直接覆盖即可) |
| Key 会不会泄露到 GitHub | 不会。`config.json` 已被 .gitignore 排除,git 不会提交它;也别把 Key 写进任何代码或文档 |

---

*本文件随仓库分发,更新于 2026-09-09。*
