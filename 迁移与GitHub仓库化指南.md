# AI 销售工作台 · 换电脑迁移 + GitHub 仓库化指南

> 面向零基础，全部命令可直接复制粘贴。已按 2026-09-08 产品代码（qa_acceptance/AI询盘agent，含 Phase A + V8.1 全部优化）勘察整理。
> 注意：桌面 `Desktop/AI询盘agent` 是旧版，**真正的最新代码在工作区** `WorkBuddy\2026-09-05-08-53-03\qa_acceptance\AI询盘agent`。迁移请以工作区这份为准。

---

## 0. 一句话结论

| 你的目的 | 推荐方案 | 预计耗时 |
|---|---|---|
| 另一台电脑继续用（两台都在手边） | **方案 A：U 盘整目录拷贝** | 10 分钟 |
| 版本管理 / 换电脑 / 以后对外分发 | **方案 B：GitHub 私有仓库（推荐）** | 30 分钟 |
| 公开分享 / 卖给客户 | 方案 B + **公开库**（已默认排除敏感文件） | 30 分钟 |

---

## 1. 项目文件体检表（勘察结论）

先看清每个文件该不该带走、能不能上传：

| 路径 | 作用 | 必须带？ | 能上传 GitHub？ |
|---|---|---|---|
| `workbench/`（app.py/db.py/priority3.py/execution.py 等） | 工作台核心 | ✅ 必须 | ✅ 可以 |
| `agent/` | AI 引擎（纯标准库） | ✅ 必须 | ✅ 可以 |
| `data/products.json` | 产品目录（运行必需） | ✅ 必须 | ✅ 可以 |
| `.streamlit/config.toml` | 蓝色主题 | ✅ 必须 | ✅ 可以 |
| `licensing/` | 卡密模块 | 按需 | ✅ 可以 |
| `test_*.py` | 回归测试 | 建议带 | ✅ 可以 |
| `config.json` | **DeepSeek Key + 你的公司信息** | ⚠️ 换电脑要重配 | ❌ **绝不能上传** |
| `workbench/workbench.db` | 业务数据（44 询盘/10 客户） | ⚠️ 看你要不要数据 | ⚠️ 含真实客户，公开库**绝不能**带 |
| `*.md` 优化报告 | 开发文档 | 可选 | ✅ 可选 |
| `*.bat` / `my_inquiry.txt` / `真实询盘_*.txt` | 本机脚本与个人样例 | 可选 | ⚠️ 先自查样例询盘是否含真实客户信息 |
| `*.log` / `__pycache__/` | 运行残留 | ❌ 不带 | ❌ 已排除 |

> **最重要的安全事实**：`config.json` 里现在有明文 DeepSeek key（sk-ace96…）。代码本身已支持环境变量 `DEEPSEEK_API_KEY`（优先级更高），所以公开仓库**零泄露风险**——clone 下来的人只需自己配 key。项目已生成 `.gitignore` 把它挡住。

---

## 2. 方案 A：U 盘整目录拷贝（最快，10 分钟）

适合"另一台电脑就在手边、直接搬家"。

**步骤：**

1. 关掉本机正在运行的工作台（浏览器页面关闭即可，如有黑窗口则 Ctrl+C）。
2. 把整个文件夹拷贝到 U 盘 / 移动硬盘：
   `WorkBuddy\2026-09-05-08-53-03\qa_acceptance\AI询盘agent`
   （整个文件夹复制，含 workbench.db —— 这样 44 条询盘数据一并带走）
3. 新电脑安装 Python：
   - 打开 https://www.python.org/downloads/ 下载 3.12 或 3.13
   - 安装时**务必勾选** `Add Python to PATH`
4. 打开命令行（开始菜单搜 `cmd`），粘贴：
   ```bash
   pip install streamlit
   ```
5. 把文件夹拷到新电脑（如 `D:\AI询盘agent`），进入目录：
   ```bash
   cd /d D:\AI询盘agent
   ```
6. 配 API Key（新电脑没有 config.json 就新建一个，内容见 README.md 模板；或设环境变量）：
   ```bash
   set DEEPSEEK_API_KEY=sk-你的key
   ```
7. 启动：
   ```bash
   .venv\\Scripts\\python.exe -m streamlit run workbench\\app.py --server.address 127.0.0.1 --server.port 8501
   ```
   浏览器打开 http://localhost:8501。

> 想保留当前 44 条询盘就保留 workbench.db；想清空重来就删掉 workbench.db（首次运行自动建空库）。

---

## 3. 方案 B：GitHub 仓库化（推荐，30 分钟）

### B0. 已替你生成的文件

| 文件 | 作用 |
|---|---|
| `.gitignore` | 已排除 config.json、*.db、日志、缓存（安全网） |
| `requirements.txt` | 依赖清单（仅 streamlit） |
| `README.md` | 项目说明 + 安装运行 + 测试命令（新电脑照它做） |

### B1. 决定两件事

**(1) 仓库公开还是私有？**
- 只想自己多台电脑用 / 版本管理 → **Private 私有**（免费，GitHub 私有仓库不收费）
- 想当作品集展示 / 对外分发 → **Public 公开**（.gitignore 已保证不含 key 和客户数据）

**(2) 数据库带不带？**
- 私有自用 + 想同步当前 44 条询盘 → 打开 `.gitignore`，把这三行的 `#` 删掉让 db 入库：
  ```
  # workbench/workbench.db
  # workbench/*.db
  # workbench/*.db.*
  ```
- 公开库 / 不要数据 → **保持默认排除**，别人 clone 后首次运行自动建空库。

### B2. 在 GitHub 建仓库

网页操作：
1. 登录 https://github.com （没有账号先注册）
2. 右上角 **+** → **New repository**
3. Repository name 填 `ai-sales-workspace`
4. 选 **Private** 或 **Public**
5. **不要**勾选 "Add a README"（本地已有）
6. 点 **Create repository**，停在出现的命令页（下一步要用）

### B3. 本机初始化并推送（在项目根目录执行）

打开命令行，逐条粘贴（每粘贴一条按回车等它跑完）：

```bash
cd /d "C:\Users\Administrator\WorkBuddy\2026-09-05-08-53-03\qa_acceptance\AI询盘agent"
```

```bash
git init
git add .
git status
```

最后一条 `git status` 先自己核对：
- 列表里**必须没有** `config.json`
- 列表里**必须没有** `workbench/workbench.db`（除非你在 B1 决定带库）
- 有的话说明 .gitignore 没生效，**先别继续**，找我检查

确认干净后，提交并关联远程仓库（把下面 `<你的用户名>` 换成你的，网页上那两行直接复制也行）：

```bash
git commit -m "AI Sales Workspace 初始版本"
```

```bash
git branch -M main
```

```bash
git remote add origin https://github.com/<你的用户名>/ai-sales-workspace.git
```

```bash
git push -u origin main
```

提示输入 GitHub 用户名和密码时：密码不是登录密码，是 **Personal Access Token**（见 B3.1）。推送成功后在 GitHub 网页刷新即可看到代码。

#### B3.1 生成 Personal Access Token（仅首次推送需要）
1. GitHub 右上角头像 → **Settings** → 最底部 **Developer settings**
2. **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**
3. 勾选 `repo` 权限 → 有效期选 90 天 → Generate
4. 复制生成的 `ghp_...` 字符串（只显示一次），推送时当密码粘贴

> 若命令行装了 GitHub CLI（`gh`），可简化：`gh auth login` 登录后，建仓库用 `gh repo create ai-sales-workspace --private --source . --push` 一步完成。

### B4. 新电脑从 GitHub 部署（以后任何电脑都这么做）

```bash
pip install streamlit
```

```bash
git clone https://github.com/<你的用户名>/ai-sales-workspace.git
cd ai-sales-workspace
```

```bash
set DEEPSEEK_API_KEY=sk-你的key
.venv\\Scripts\\python.exe -m streamlit run workbench\\app.py --server.address 127.0.0.1 --server.port 8501
```

浏览器打开 http://localhost:8501 即完成。以后代码有更新：

```bash
git pull
```

### B5. 日常更新工作流（改完代码存回 GitHub）

```bash
cd /d "你的项目目录"
git add .
git commit -m "本次改了什么，一句话说明"
git push
```

---

## 4. 数据迁移对照表

| 场景 | 操作 |
|---|---|
| 新电脑想继续用现有 44 条询盘 | 方案 A 整拷；或方案 B 私有库 + 取消 .gitignore 的 db 排除 |
| 新电脑空库开始 | 不带 .db，首次 `streamlit run` 自动建表 |
| 把本机数据手动搬到已 clone 的库 | 拷贝 `workbench/workbench.db` 覆盖到新电脑同名位置 |
| 想备份数据 | 复制 `workbench/workbench.db` 一份存 U 盘即可（SQLite 单文件） |

## 5. 常见问题

- **Q：启动报 API 错误 / Key 无效？** A：确认环境变量或 config.json 里 key 是 `sk-` 开头完整串；环境变量优先级更高，若设过错的先删掉。
- **Q：端口被占用？** A：`.venv\\Scripts\\python.exe -m streamlit run workbench\\app.py --server.address 127.0.0.1 --server.port 8502` 换端口。
- **Q：数据库在哪个目录？** A：代码锚定 `workbench/workbench.db`，无论从哪启动都读写这个文件，不会漂移。
- **Q：忘了 key 在哪台电脑配过？** A：config.json 只在本地、已被 git 排除，不会随仓库传播——每台电脑都要单独配。
- **Q：桌面 `Desktop/AI询盘agent` 是旧的？** A：对。最新代码在工作区 `WorkBuddy\...\qa_acceptance\AI询盘agent`。若坚持用桌面版，请先把工作区新版覆盖过去再打包。

## 6. 公开分发前的自查清单（可选进阶）

- [ ] `.gitignore` 生效，仓库无 config.json / *.db
- [ ] `SELLER` 信息若是你的真实公司名，公开库建议在 config.json 模板里留空（README 已给模板）
- [ ] `真实询盘_*.txt` / `my_inquiry.txt` 若含真实客户名 → 移出仓库或脱敏
- [ ] README 首页截图若含真实客户数据 → 打码
