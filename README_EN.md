# WeChat Vision AI Bot

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![WeChat Linux 4.x](https://img.shields.io/badge/WeChat-Linux%204.x-green.svg)](https://linux.weixin.qq.com/)
[![Powered by Kimi](https://img.shields.io/badge/LLM-Moonshot%20Kimi%20Vision-purple.svg)](https://kimi.moonshot.cn/)

An autonomous, 24/7 intelligent WeChat group chat bot built on **official native Linux WeChat 4.x** and **multimodal vision AI (Moonshot Kimi Vision)**.

**Zero reverse-engineering, zero binary hooks, zero memory injection.** Runs inside a headless virtual display (`Xvfb`) on any Linux VPS (ARM64 or x86_64). Reads chat messages, images, and quotes with AI vision, and types replies using native OS-level keyboard/mouse input—behaving indistinguishably from a real human sitting in front of a computer screen.

---

## 🌟 Key Features

- 🛡️ **Zero Ban Risk (Zero-Hook Architecture)**  
  Traditional WeChat bots relying on iPad/Mac protocol reverse-engineering or DLL injection are heavily penalized by Tencent's anti-cheat systems. This project runs the **official Linux WeChat client** unmodified inside Xvfb.
- 👁️ **Multimodal Visual Screen Parsing**  
  Directly perceives the chat window using multimodal vision LLMs. Automatically parses sender nicknames, speech bubbles, incoming vs. outgoing messages, and `@` mentions.
- 📌 **WeChat Quote & Image OCR Recognition**  
  Understands native WeChat quotations (`「User: ...」`) and extracts referenced search results, code errors, charts, and photos using AI vision OCR.
- 🌐 **Real-time Web Search**  
  Integrated with Moonshot Kimi native `$web_search` engine to fetch live stock prices, news, facts, and public data on the fly.
- 🧹 **Plain-Text & LaTeX Sanitizer**  
  WeChat does not render Markdown or LaTeX math formulas. The built-in pipeline converts LaTeX formulas into clean, readable text (`\frac{a}{b} -> a/b`, `\sqrt{x} -> √(x)`, `\approx -> ≈`) and strips Markdown bold/header artifacts.
- 🧠 **Persistent SQLite Dialogue Memory**  
  Stores local conversation history in `chat_memory.db` to maintain multi-turn coherence and accurately resolve pronouns (e.g. "what does this mean?", "he just said").
- 🎛️ **Full Remote CLI Tool (`wechat_ctl.py`)**  
  Inspect bot status, fetch login QR codes, capture virtual screen, manually send messages, and dynamically update prompts and trigger keywords remotely.
- 🚀 **Systemd 24/7 Daemon**  
  Pre-configured with systemd service for auto-restart, auto-recovery, and headless display lifecycle management.

---

## 🚀 Quick Start

### 1. VPS Installation (Ubuntu / Debian / Oracle Linux / CentOS)

Clone the repository and run the automated installation script:

```bash
git clone https://github.com/minglu-lin/wechat-vision-bot.git
cd wechat-vision-bot
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

Install official Linux WeChat 4.x package:
- **Ubuntu/Debian**: `sudo dpkg -i wechat-linux-*.deb`
- **RHEL/CentOS/Oracle Linux**: `sudo rpm -ivh wechat-linux-*.rpm`

### 2. Configure API Key

Copy `config.example.json` to `~/wechat_bot/config.json`:
```bash
cp config.example.json ~/wechat_bot/config.json
nano ~/wechat_bot/config.json
```
Enter your `kimi_api_key` and customize `bot_names` and `system_prompt`.

### 3. Start Service & QR Code Login

Start the daemon:
```bash
sudo systemctl start wechat-bot.service
```

On your local computer, fetch the login QR code:
```bash
python ctl/wechat_ctl.py qr
```
Open `wechat_latest.png` and scan it with your WeChat mobile app to log in!

---

## 🛠️ CLI Remote Control (`wechat_ctl.py`)

```bash
python ctl/wechat_ctl.py status              # Check daemon status & logs
python ctl/wechat_ctl.py qr [path]           # Fetch latest login QR code
python ctl/wechat_ctl.py screen [path]       # Capture full 1280x800 desktop
python ctl/wechat_ctl.py read                # Vision read visible chat
python ctl/wechat_ctl.py send "Hello world"  # Type & send text
python ctl/wechat_ctl.py name "bot,assistant"# Update trigger names
python ctl/wechat_ctl.py prompt "New prompt" # Update System Prompt
python ctl/wechat_ctl.py history             # View SQLite chat memory
```

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
