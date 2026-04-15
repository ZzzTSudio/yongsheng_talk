# Cyber Colleague

致谢 https://github.com/titanwings/colleague-skill  提供的skill蒸馏方案，本软件是一个独立、可视化、可自行配置api、无需额外设置更身临其境与skill数字人对话的程序。

<img width="2569" height="1389" alt="image" src="https://github.com/user-attachments/assets/8c30ddab-e547-43d5-90c9-75b69daff8a1" />


基于 PySide6 的桌面 LLM 对话客户端：将 Cursor 风格的 **Skill** 目录（`SKILL.md`、`persona_skill.md`、`work_skill.md`、`meta.json` 等）拼入 **system** 消息，通过 OpenAI 兼容 API **流式**输出回复。

## 环境要求

- Python 3.10+
- 图形界面（运行 GUI）

## 安装与运行

在项目根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app.main
```

首次使用请点击左下角 **设置**，填写 API 地址、密钥、模型名称；可选填写 **Skill 存放路径**（用于「+ 新建同事」时复制导入）。

### 环境变量（可选，优先级高于配置文件）

| 变量 | 含义 |
|------|------|
| `CYBER_COLLEAGUE_API_KEY` | API 密钥 |
| `CYBER_COLLEAGUE_API_BASE` | API 根地址（如 `https://api.siliconflow.cn/v1`） |
| `CYBER_COLLEAGUE_MODEL` | 模型名 |

配置文件路径：

- Windows：`%APPDATA%\CyberColleague\settings.json`
- Linux/macOS：`~/.config/CyberColleague/settings.json`

**请勿**将密钥提交到版本库。

## Windows 打包为 exe

在 **Windows** 上使用 **干净虚拟环境**（仅安装 `requirements.txt`）可避免 PyInstaller 误打包本机其他大型库：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pyinstaller --noconfirm cyber_colleague.spec
```

生成物通常在 `dist\CyberColleague.exe`（单文件、`console=False` 无控制台窗口）。`cyber_colleague.spec` 已将仓库内 `skill_lib/zhang_jing/` 打入包内（解压为 `zhang_jing/`），供内置同事使用。

在 Linux/macOS 上也可执行同一 spec，得到对应平台可执行文件；发布 Windows exe 建议在 Windows 上构建并实测。

## 项目结构

- `app/main.py` — 入口
- `app/settings.py` — 配置持久化
- `app/skill_loader.py` — Skill 解析与同事列表
- `app/llm_client.py` — 流式 Chat Completions
- `app/ui/` — 主窗口、设置、流式线程
- `skill_lib/zhang_jing/` — 默认 Skill 资源（内置张静）
