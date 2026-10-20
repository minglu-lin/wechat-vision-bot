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
- 🔗 **Automatic Web Link Fetching & Text Parsing (Link Reader)**  
  Automatically extracts HTTP/HTTPS URLs from messages or quotes, strips HTML noise/scripts, and parses article titles and full text (up to 3,500 characters) directly into the LLM context for in-depth analysis and summarization.
- 📰 **WeChat Article Card Resolution (Full-Text Retrieval)**  
  Vision model detects WeChat rich card shares, article headlines, and news sources (e.g., Dahe Daily, The Paper, Zhihu). When asked to evaluate or summarize, the bot automatically searches and retrieves the complete news reporting and legal facts instead of speculating from a truncated title.
- ⏱️ **30-Second Follow-Up Window & Anti-Spam Gate**  
  Supports natural multi-turn conversations without repeatedly typing `@bot` (e.g. "look up X again", "why is that?"). Features strict anti-spam filtering: trivial reactions (👍, ?, "ok", "got it") and 3rd-person chatter ("no need to add bot") are silently memorized without spamming replies; batching guarantees at most one single reply per frame update.
- 🔌 **Modular API Library & Dual-Track Plugin Architecture**  
  Inspired by mature chatbot frameworks (NoneBot2, AstrBot, CoW), featuring a hybrid dual-track execution model:
  1. **Fast-Path Direct Execution (<100ms / 0 Token)**: Deterministic commands (e.g. `bot roll dice`, `bot lookup ip 8.8.8.8`, `bot 100 USD in CNY`, `bot calc 1024*768`, `bot choose between A and B`) match regex directly to invoke APIs in milliseconds with zero LLM overhead.
  2. **Autonomous LLM Function Calling**: For complex, conversational requests (e.g. "what's the 14-day weather in Beijing and is it good for hiking with seniors?"), automatically exposes `get_weather`, `get_exchange_rate`, `get_crypto_price`, `lookup_ip`, `calculate`, and `decision_and_roll` as OpenAI-compatible tools. The LLM extracts parameters, fetches real-time data, and synthesizes answers naturally.
  3. **Extensible**: Drop a single `.py` file into `bot/plugins/` to plug in any custom API.
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
