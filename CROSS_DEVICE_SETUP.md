# 跨设备开发配置

本文用于在 Windows 和 Mac 之间同步项目，并让 Codex 在每台电脑上能够修改代码、
使用 Git 和上传 GitHub。文档只记录配置方法，不包含任何真实 Token。

## 权限结构

本项目使用两套相互独立的权限：

- 本地 Codex 权限：控制 Codex 能否修改项目文件和项目内的 `.git`。
- GitHub Token 权限：控制 Codex 能否读取、提交或创建 GitHub 仓库。

`.git` 是每个本地仓库的隐藏目录，保存提交、分支、暂存区和远端状态。允许写入
`.git` 不等于开放整台电脑，也不自动授予 GitHub 账号权限。

## 首次获取项目

### Windows

```powershell
git clone https://github.com/OverHabit/szy.git
cd szy
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

### Mac

先安装 Git 和 Python。可使用 Homebrew：

```bash
brew install git python
```

然后克隆并启动项目：

```bash
git clone https://github.com/OverHabit/szy.git
cd szy
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

浏览器访问 `http://localhost:8501`。

## Codex 全局 Git 权限

每台电脑都需要单独配置。打开用户目录中的 Codex 配置：

- Windows：`C:\Users\<用户名>\.codex\config.toml`
- Mac：`~/.codex/config.toml`

在配置顶部设置默认权限，并加入以下权限表：

```toml
default_permissions = "all-projects-git"

[permissions.all-projects-git]
description = "Allow Git operations in every opened project"

[permissions.all-projects-git.filesystem]
":minimal" = "read"

[permissions.all-projects-git.filesystem.":workspace_roots"]
"." = "write"
".git" = "write"
```

这项设置的目标是：

- 允许修改每个已作为 Codex 工作区打开的项目。
- 允许更新这些项目内部的 `.git`。
- 不向项目目录之外授予普遍写权限。

保存后完全退出 Codex，再重新打开并新建任务。使用 `/status` 检查当前权限，然后
运行 `git status` 和 `git fetch` 验证。若电脑由公司统一管理，管理员策略可能覆盖
个人配置；此时 Git 操作仍可能要求单独批准。

`.codex` 和 `.agents` 默认保持只读，用于防止智能体自行修改权限、技能或协作规则。
确实需要修改时应单独授权。

## GitHub Token

当前采用 Fine-grained personal access token。为了覆盖当前和未来的个人仓库并允许
创建仓库，配置为：

- Resource owner：`OverHabit`
- Expiration：`No expiration`
- Repository access：`All repositories`
- Contents：`Read and write`
- Administration：`Read and write`
- Metadata：`Read-only`

`Administration: Read and write` 同时包含创建、重命名、改变可见性和删除仓库等管理
能力。创建仓库以外的高风险操作必须由用户明确确认。

Token 不得写入本文、聊天截图或 Git 提交。GitHub 只在生成时完整显示 Token 一次。
如果 Token 泄露，应立即在 GitHub 设置中撤销并重新生成。

### 本地保存

Windows 当前使用项目根目录下的 `.github_token`，该文件已被 `.gitignore` 排除。
Mac 可以采用相同方式：

```bash
cd szy
touch .github_token
chmod 600 .github_token
```

然后只将完整 Token 写入 `.github_token`，不要添加引号或说明文字。确认忽略状态：

```bash
git check-ignore -v .github_token
```

更稳妥的长期方案是使用 GitHub CLI 和系统钥匙串：

```bash
brew install gh
gh auth login
gh auth status
```

如果 Codex 所在沙箱无法读取系统钥匙串，再使用已被忽略并限制文件权限的
`.github_token`。

## 日常同步

开始工作前：

```bash
git pull --ff-only origin master
```

完成修改后：

```bash
git status
python -m pytest -q
git add <本次修改的文件>
git commit -m "描述本次变化"
git push origin master
```

同一时间尽量只在一台电脑修改同一文件。发现远端有新提交或发生冲突时，先检查差异，
不要用强制推送覆盖另一台电脑的工作。

## Codex 操作边界

Codex可以在用户要求后创建仓库、提交和推送。以下操作必须再次明确确认：

- 删除仓库。
- 强制推送或重写公开历史。
- 改变仓库公开或私有状态。
- 批量删除分支、标签或项目文件。
- 扩大 Token、网络或本地文件权限。

