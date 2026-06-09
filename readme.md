# OSRS GE Quant: The Grand Exchange Bloomberg Terminal 🪙📈

A professional-grade quantitative trading, research, and sentinel intelligence suite for the **Old School RuneScape (OSRS)** Grand Exchange. 

Designed for high-frequency day-trading flip detection, automated skilling recipe profitability routing, and real-time social sentiment alert dispatching using advanced LLMs (Gemini/OpenAI).

---

## 🌟 Key Features

### 1. **OSRS Bloomberg-Style Terminal Dashboard**
* Built with a custom, sleek **charcoal and gold** aesthetic inspired by financial terminals.
* Single Page Application (SPA) dashboard containing:
  * **Overview**: Real-time metrics (unrealized P&L, holdings market value, active accounts) and daemon heartbeat sentinel logs.
  * **Day-Trading Flips**: Screener highlighting active flip targets based on RSI oversold indicators, Bollinger Band deviations, and volume surge signals.
  * **Portfolio & Positions**: Open holdings, average entry cost, and real-time mark-to-market valuations.
  * **Market Charts**: Autocomplete item lookup rendering historical timeseries charts and technical overlays.
  * **News & Sentiment**: Merged feed analyzing official game updates alongside Reddit discussions and YouTube influencer uploads.
  * **Settings & Blacklist**: Live UI forms to configure accounts, parameters, and street-trade filters dynamically.
  * **Backtest Simulator**: Historical simulation engine to model strategies.

### 2. **Multi-Account Hiscores Caching & Skill Check Verification**
* Automatically checks active account skill levels by querying Jagex Lite Hiscores (e.g. *Herblore, Crafting, Fletching, Smithing*).
* Caches player profiles locally (1-hour TTL) to prevent Jagex request rate-limits or IP blocks.
* Dynamically filters skilling-based processing suggestions to recommend only what active characters can perform, and appends eligibility lists to recommendations (e.g., `(Eligible: Pimpwurt, beast774)`).

### 3. **YouTube & Reddit Market Sentiment Tracking**
* Scrapes official game updates, high-activity threads on **r/2007scape**, and YouTube video uploads from major OSRS trading influencers (e.g., **FlippingOldSchool**).
* Uses Gemini model analysis to extract item keywords, project directions (up/down), confidence scores, and reasoning.
* Dispatches **Instant Discord webhook alerts** and **emails** for high-priority sentiment movements affecting your portfolio or high-value items (e.g. Twisted bow, Scythe).

### 4. **High-Resolution Timeseries Backfiller**
* Eliminates technical indicator "warm-up" delays.
* Backfills up to 365 historical price data points for the top $N$ liquid items across `5m`, `1h`, and `6h` timesteps via the official OSRS Wiki API.

### 5. **Anti-Spam Sentry Loop**
* Runs continuously in the background (as a thread inside the dashboard, or a dedicated CLI process).
* Monitors trade margins and volume continuously, sending alerts only on massive swings ($\ge$ 5M profit or $\ge$ 15% return) with sliding anti-spam database windowing.

---

## 🛠️ Installation & Setup

### Prerequisites
* Python 3.10+ (Anaconda environment recommended)
* `sqlite3`

### Setup Instructions
1. Clone this repository to your workspace.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up your `.env` file with your API keys and credentials:
   ```env
   # API Keys
   GEMINI_API_KEY="your-gemini-key"
   
   # Notifications (Optional)
   DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
   SMTP_USER="your-email-here"
   SMTP_PASSWORD="your-smtp-app-password"
   ```
4. Initialize the SQLite database and seed initial accounts:
   ```bash
   $env:PYTHONPATH="src"
   python -m osrs_ge_quant.cli init-db
   ```

---

## 🚀 Usage

### 🖥️ Launcher Options
* **Desktop Launcher:** Double-click `run_suite.bat` on your Desktop/repository root. It launches the dashboard and background daemon sentinel automatically, then pops open the dashboard in your default browser at `http://127.0.0.1:8050`.
* **CLI Manual Mode:**
  You can run commands using:
  ```powershell
  $env:PYTHONPATH="src"
  python -m osrs_ge_quant.cli <command> [options]
  ```

### 🎛️ CLI Command Guide

| Command | Description | Options |
| :--- | :--- | :--- |
| `init-db` | Initializes the SQLite database schema and seeds initial accounts. | None |
| `refresh-universe` | Refreshes the active GE items mapping and fetches the latest 24h price snapshots. | None |
| `update-timeseries` | Fetches and stores timeseries prices for real-time analysis. | `--timestep <5m\|1h\|6h>` |
| `backfill-timeseries` | Backfills high-res historical prices for the top liquid items to pre-warm indicators. | `--timestep <5m\|1h\|6h>`, `--top-n <int>` |
| `analyze` | Runs a single full analysis cycle, updates recommendations, and sends email digests. | None |
| `daemon` | Launches the continuous Day-Trading Daemon sentinel loop. | None |
| `dashboard` | Starts the Flask Web Dashboard and launches the background daemon thread. | None |
| `backtest` | Simulates a mean-reversion flip strategy over historical daily prices. | `--years <int>`, `--top-n <int>`, `--k-std <float>` |
| `portfolio` | Summarizes open positions, mark-to-market value, and unrealized returns. | None |

#### Backfilling High-Resolution Prices Example:
```powershell
$env:PYTHONPATH="src"; python -m osrs_ge_quant.cli backfill-timeseries --timestep 5m --top-n 300
```

---

## 🔌 RuneLite Partner Plugin

We have packaged a custom Java partner plugin in [runelite-plugin/](file:///c:/Users/londo/OneDrive/Desktop/osrs-ge-quant/runelite-plugin) that integrates the game client directly with this terminal server:

### Features
1. **Real-time Stats Sync**: Syncs player skill levels automatically to configure skilling recipe validations instantly.
2. **Asset Tracker**: Sums coins and active bank inventories, displaying them directly inside the plugin sidebar and updating your terminal portfolio.
3. **Auto-Trade Logging**: Tracks active slots in the Grand Exchange and inserts trades into the database instantly upon buy/sell completions.
4. **Sidebar UI Panels**: View targeted day-trading flips and skilling recipes side-by-side inside RuneLite, styled with professional gold/charcoal accents. Double-click any item name to copy it to the clipboard.

### Build and Load Instructions
1. Open your favorite Java IDE (IntelliJ IDEA recommended) and import the [runelite-plugin](file:///c:/Users/londo/OneDrive/Desktop/osrs-ge-quant/runelite-plugin) directory as a Maven project.
2. Run a clean build to package the JAR:
   ```bash
   mvn clean package
   ```
3. Load the resulting `.jar` file from `/target` into your custom local RuneLite client bootstrap or external loader directory (or run it via RuneLite's sandbox client).
4. Configure your backend server settings in the RuneLite plugin panel (defaults to `http://127.0.0.1:8050`).

---

## 🎨 UI Showcase

* **Bloomberg-Style Dark Dashboard:** Deep charcoal and gold card layouts with clear gridlines and high-contrast tables.
* **Community & YouTube Sentiment:** Combined feeds for Jagex Updates, Reddit posts, and YouTube uploads. Displays platform badges (`REDDIT` in orange, `YOUTUBE` in red).
* **Interactive Configuration Manager:** Dynamically update thresholds, modify active multi-accounts, or blacklist street-traded items directly from the Settings tab without rebooting.

---

## 🔒 Street-Trade Filter Blacklist
High-value items that trade above the 2.14B GE max cash limit (e.g. *3rd Age Longsword*, *Twisted Bow*) or are subject to extreme merchanting ring manipulation are completely filtered from screens to prevent invalid indicator alerts. The blacklist can be edited directly inside the **Settings** manager in the web UI.

---

## 📝 License
Proprietary tool built for quantitative research on the Old School RuneScape Grand Exchange. Not affiliated with Jagex Ltd.
