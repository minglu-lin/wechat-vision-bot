# WeChat Vision AI Bot (微信多模态视觉群聊机器人)

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![WeChat Linux 4.x](https://img.shields.io/badge/WeChat-Linux%204.x-green.svg)](https://linux.weixin.qq.com/)
[![Powered by Kimi](https://img.shields.io/badge/LLM-Moonshot%20Kimi%20Vision-purple.svg)](https://kimi.moonshot.cn/)

基于**官方原生 Linux 微信 4.x** 与**多模态大模型视觉认知**构建的 7x24 小时无人值守智能微信群聊机器人。

无需 Hook、无内存注入、无需逆向私有协议。在无头 Linux VPS（x86_64 或 ARM64）上通过 Xvfb 虚拟显示运行官方原生客户端，像真人一样“看”屏幕识别文字/图片/引用，并模拟真实键鼠交互，具备极高的账号安全性。

---

## 🌟 核心特性与创新亮点

- 🛡️ **极致安全（Zero-Hook / 零封号风险）**  
  市面常见的 Hook 注入或逆向 iPad/Mac 协议极易触发腾讯风控封号，而网页版接口早已封死。本项目运行**腾讯官方正版 Linux 客户端**，通过 X11 截图 + 模拟键鼠输入。在风控系统视角下，表现为一个真实用户在电脑前注视屏幕并键入文本。
- 👁️ **多模态视觉直接理解界面**  
  通过多模态视觉大模型（Kimi Vision）实时捕捉群聊消息流，自动区分左右气泡、发送者昵称，精准识别是否被 @ 呼叫。
- 📌 **微信原生“引用”与图文溯源**  
  支持识别微信的“引用”功能（`「某人: ...」`），准确抽取被引用的历史对话；遇到群友发送或引用的网页截图、搜索结果、代码报错等，自动进行 OCR 提取与画面解析，结合图文证据针对性解答。
- 🔗 **网页链接自动抓取与正文解析（Link Reader）**  
  自动捕获消息或引用中的 HTTP/HTTPS 链接，深度解析并自动剔除脚本和页面噪音，提取网页标题与完整纯文本正文（最多提取 3500 字），直接注入上下文供大模型深度总结、评价与答疑。
- 📰 **微信文章/新闻卡片深度研读（告别“光看标题脑补”）**  
  视觉多模态精准识别微信图文分享卡片、标题和媒体来源（如大河报、澎湃新闻、知乎等）。群友要求“评价一下/总结下”时，自动联动全网深度检索完整新闻报道全文与判决事实，彻底告别半截标题凭空脑补。
- ⏱️ **30 秒智能连续追问机制与严格防滥答引擎**  
  同一群友提问后开启 30 秒免 @ 追问窗口（如“那你再查一下”、“为什么呢”、“后来呢”）。内置严格防滥答过滤器：单纯表情（`👍`）、单纯标点（`?`）、敷衍确认（`好的`、`收到`）及第三人称讨论（“不用加bot”）自动静默入库绝不滥回；单次截帧合并去重，杜绝快刷并发连发两条回复。
- 🌐 **实时联网搜索（Live Web Search）**  
  接入 Moonshot Kimi 原生 `$web_search` 搜索引擎，当群友询问股市行情、最新新闻、实时资讯、人物资料时，自动触发多轮网络检索并汇总分析。
- 🧹 **微信纯文本与排版净化引擎**  
  微信移动端和桌面端均不支持 Markdown 和 LaTeX 渲染。内置两道清洗管道：指令级约束 + 正则转换管道，将 LaTeX 公式转化为自然字符（如 `\frac{a}{b} -> a/b`, `\sqrt{x} -> √(x)`, `\approx -> ≈`），去除星号粗体，呈现与真人微信打字无异的清爽纯文本。
- 🧠 **本地 SQLite 持久化多轮记忆**  
  自带轻量级本地数据库（`chat_memory.db`），自动记录群聊上下文，精准理解“这句话是什么意思”、“他刚刚说的”等复杂指代和上下文脉络。
- 🎛️ **远程运维 CLI 工具箱 (`wechat_ctl.py`)**  
  提供远程管理工具，支持一键拉取登录二维码、查看状态、截屏巡检、读取会话、手动发送、点击坐标、动态热重载人设和触发词。
- 🚀 **Systemd 7x24 小时守护进程**  
  支持开机自启、崩溃自动重启、自动拉起 Xvfb 虚拟桌面与微信客户端。

---

## 🏗️ 架构原理解析

```
+-----------------------------------------------------------------------------------+
| Linux VPS (Oracle Cloud / Ubuntu / Debian / CentOS / ARM64 or x86_64)            |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  | Xvfb (虚拟 X11 显示环境 :99)                                                 |  |
|  |   +-----------------------------------------------------------------------+ |  |
|  |   | 官方原生 Linux 微信 4.x (Tencent Official Client)                      | |  |
|  |   | [ MAGA (7) 群聊窗口 ]   [ 左侧会话红点 ]   [ 输入框 ]                 | |  |
|  |   +-----------------------------------------------------------------------+ |  |
|  +-----------------------------------------^-----------------------------------+  |
|                                            |                                      |
|                                     X11 截图 / 模拟键鼠                           |
|                                            |                                      |
|  +-----------------------------------------v-----------------------------------+  |
|  | bot_daemon.py (后台常驻守护核心)                                            |  |
|  |                                                                             |  |
|  |  [1. 像素差分检测] ----> [2. 截取聊天区域] ----> [3. 视觉模型多模态解析]    |  |
|  |                                                      |                      |  |
|  |  +--------------------------------------------------+                      |  |
|  |  | 识别要素: 发送者 / 消息正文 / 是否呼叫 / 微信引用 / 关联图片与OCR识别   |  |
|  |  +--------------------------------------------------+                      |  |
|  |                                                      |                      |  |
|  |  [4. 读取 SQLite 记忆] <--> [5. Kimi 对话与实时搜索] <------------------+  |  |
|  |                                                      |                  |  |
|  |  [6. 纯文本与 LaTeX 清洗] ---> [7. xclip 注入剪贴板] ---> [8. 发送回复] |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
                                         ^
                                         | SSH / HTTP 控制 API (:18890)
+----------------------------------------v------------------------------------------+
| 本地开发机 / 运维终端 (wechat_ctl.py)                                             |
|   - 扫码登录 (qr)          - 状态监控 (status)      - 调整人设 (prompt)           |
|   - 查看历史 (history)     - 视觉读取 (read)        - 模拟发信 (send)             |
+-----------------------------------------------------------------------------------+
```

---

## 📦 目录结构

```
wechat-vision-bot/
├── .gitignore               # 忽略密钥与本地数据库
├── LICENSE                  # MIT 开源协议
├── README.md                # 中文使用文档
├── README_EN.md             # 英文文档
├── requirements.txt         # Python 依赖
├── config.example.json      # 配置文件模板 (请勿提交真实密钥)
├── bot/
│   └── bot_daemon.py        # 视觉守护主进程 (X11 + Vision + Search + Memory)
├── ctl/
│   └── wechat_ctl.py        # 远程运维控制 CLI 工具
└── scripts/
    ├── deploy.sh            # Linux VPS 一键部署与环境安装脚本
    ├── wechat-bot.service   # Systemd 服务单元文件
    └── test_clean.py        # 纯文本清洗与格式转换验证脚本
```

---

## 🚀 快速上手部署指南

### 第一步：准备服务器与配置环境

推荐使用任何主流 Linux 服务器（例如 Oracle Cloud、阿里云、腾讯云、AWS 等），系统支持 **Ubuntu 22.04+ / Debian 11+ / Oracle Linux 8+ / CentOS 8+**，内存推荐 **2GB 及以上**。

1. **克隆本仓库到 VPS**：
   ```bash
   git clone https://github.com/minglu-lin/wechat-vision-bot.git
   cd wechat-vision-bot
   ```

2. **执行一键安装脚本**：
   ```bash
   chmod +x scripts/deploy.sh
   ./scripts/deploy.sh
   ```
   脚本将自动安装：
   - Xvfb 虚拟显示服务器与 X11 交互工具 (`xclip`, `xdotool`, `x11-utils`)
   - 中文字体包 (`fonts-wqy-zenhei`, `fonts-noto-cjk`，彻底杜绝微信中文字体方块乱码)
   - Python 3 运行环境及依赖库 (`Pillow`, `python-xlib`)

3. **下载安装官方原生 Linux 微信 4.x**：
   根据系统架构前往腾讯官网或下载安装包：
   - [微信官方 Linux 版下载地址](https://linux.weixin.qq.com/)
   - Ubuntu/Debian (`x86_64` 或 `arm64`):
     ```bash
     sudo dpkg -i wechat-linux-*.deb
     ```
   - RHEL / CentOS / Oracle Linux:
     ```bash
     sudo rpm -ivh wechat-linux-*.rpm
     ```

---

### 第二步：配置 API Key 与人设

复制配置文件模板：
```bash
cp config.example.json ~/wechat_bot/config.json
nano ~/wechat_bot/config.json
```

配置项说明：
```json
{
  "kimi_api_key": "sk-kimi-your-actual-api-key",
  "kimi_base_url": "https://api.kimi.com/coding/v1/chat/completions",
  "kimi_model": "kimi-for-coding-highspeed",
  "auto_reply": true,
  "trigger_mode": "mention_only",
  "bot_names": [
    "@",
    "bot",
    "Bot",
    "BOT",
    "AIbot",
    "aibot"
  ],
  "system_prompt": "你在这个群聊里的名字叫 bot。你是一个在群聊中提供帮助的朋友。说话风格自然、沉稳、客观直截了当，不用太俏皮或刻意抖机灵，严禁客服腔和AI味。回答篇幅根据问题需要自然展开。微信无法解析 Markdown 或 LaTeX，严禁使用 ** 粗体、* 斜体、反引号、LaTeX 公式（$）等符号，必须输出纯文本。"
}
```

---

### 第三步：启动服务与首次扫码登录

1. **启动微信守护服务**：
   ```bash
   sudo systemctl start wechat-bot.service
   sudo systemctl status wechat-bot.service
   ```

2. **在本地电脑拉取登录二维码**：
   本地电脑打开终端（设置 VPS 的 SSH 别名或 IP）：
   ```bash
   # Windows PowerShell 设置环境变量:
   $env:WECHAT_SSH_HOST="your-vps-ip"

   # 拉取微信二维码至本地:
   python ctl/wechat_ctl.py qr
   ```
   本地目录下将生成 `wechat_latest.png`，打开图片使用手机微信扫码，在手机上确认登录即可！

3. **进入群聊开始互动**：
   登录成功后，确保微信窗口保持在目标群聊界面中（如需切换群聊，可用 `python ctl/wechat_ctl.py click <x> <y>` 或在 VNC 中切换）。
   在群里发送：
   > `@bot 今天北京天气如何？`  
   > `bot 谷歌能搜到关于xxx的信息吗`  
   > （引用某条消息或图片）`bot 帮我看看这个报错怎么解决`

---

## 🛠️ 运维管理工具箱 (`wechat_ctl.py`)

在本地主机或运维脚本中，可直接使用 `ctl/wechat_ctl.py` 遥控远端机器人：

| 命令 | 说明 | 示例 |
|---|---|---|
| `status` | 查询当前微信登录状态、窗口尺寸与最新日志 | `python ctl/wechat_ctl.py status` |
| `qr` | 拉取远端微信窗口最新画面（用于扫码登录） | `python ctl/wechat_ctl.py qr login.png` |
| `screen` | 获取 Xvfb 完整桌面 1280x800 截图 | `python ctl/wechat_ctl.py screen screen.png` |
| `read` | 调用大模型直接审视并结构化提取群聊当前可见文本 | `python ctl/wechat_ctl.py read` |
| `send` | 手动在群内模拟键盘打字并发送消息 | `python ctl/wechat_ctl.py send "测试消息"` |
| `click` | 模拟鼠标左键点击指定坐标 `(x, y)` | `python ctl/wechat_ctl.py click 220 180` |
| `name` | 热更新群内唤醒机器人的关键词列表 | `python ctl/wechat_ctl.py name "bot,小助手,AI"` |
| `prompt` | 热更新 System Prompt（即刻生效，无需重启） | `python ctl/wechat_ctl.py prompt "你是专业的金融分析师"` |
| `history` | 查阅本地 SQLite 对话记忆库最近存储的群聊记录 | `python ctl/wechat_ctl.py history` |

---

## ❓ 常见问题排查 (FAQ)

### Q1: 二维码过期或没有弹出怎么办？
微信登录二维码每几分钟会刷新一次。如果二维码过期，执行：
```bash
python ctl/wechat_ctl.py click 640 400
python ctl/wechat_ctl.py qr
```
点击二维码中心刷新后重新拉取即可。

### Q2: 微信界面中文显示为方块豆腐块？
这通常是因为 Linux 服务器缺少中文字体支持。运行部署脚本 `scripts/deploy.sh` 会自动安装 `fonts-wqy-zenhei` 和 `fonts-noto-cjk`。安装后执行：
```bash
sudo fc-cache -fv
sudo systemctl restart wechat-bot.service
```

### Q3: 为什么微信里不能用 Markdown 和 LaTeX？
微信客户端不会将 `**加粗**` 渲染为粗体，而是原样显示丑陋的双星号；对于 LaTeX 公式（如 `$x^2$` 或 `\frac{a}{b}`）同样无法渲染。本项目在底层 `clean_bot_reply` 中实现了专业的纯文本清洗管道，不仅会自动剔除语法标记，还会将数学公式智能转写为符合普通人阅读习惯的纯文本（如 `a/b`, `√(x)`, `≈`）。

### Q4: 如何保证 7x24 小时稳定不掉线？
`wechat-bot.service` 提供了 `Restart=always` 守护机制；`bot_daemon.py` 会定期检查 Xvfb 虚拟显示与 WeChat 进程状态，如遇异常将自动重新拉起。同时微信原生 Linux 客户端具备长时间保持登录机制。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源。欢迎 Star、Fork 以及提交 Issue 与 Pull Request！
