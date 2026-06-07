# 🚀 OpportunityBot: Multi-Modal AI Fellowship & Ingestion Pipeline

An advanced, production-grade, asynchronous WhatsApp automation bot that transforms raw social media feeds, screenshots, and text blogs into structured tracking data.

Forward an Instagram Reel, a YouTube Short, a LinkedIn blog post link, or a direct image screenshot of an internship/scholarship flyer to the bot via WhatsApp. The pipeline seamlessly ingests the asset, runs automated media authentication or session cookies, deploys a multi-modal Gemini AI agent for deep extraction, runs real-time live Google Search web grounding to locate official application forms and deadlines, and maps the structured data row-by-row into Google Sheets.

---

## 🏗️ System Architecture & Workflow

The architecture is explicitly decoupled to handle variable media ingestion types, network bottlenecks, and rate-limiting constraints without drop-out failures:

1. **Ingestion & Instant Webhook Acknowledgment:** The Flask server captures incoming Twilio POST webhooks. It instantly spins up a decoupled background processing thread via Python's `threading` library and flashes an `HTTP 200 OK` back to Twilio, cleanly bypassing Twilio's strict 15-second gateway timeout constraint.

2. **Multi-Modal Content Ingestion & Routing:**
   - **Direct Image/Screenshot Attachments:** Fetches raw image binaries directly from Twilio's protected asset infrastructure using authenticated headers (`auth=(TWILIO_SID, TWILIO_AUTH)`).
   - **Video Tracks/Reels:** Orchestrates `yt-dlp` using external browser Netscape `cookies.txt` sessions to scale past aggressive Instagram login walls.
   - **LinkedIn & Static Blog Links:** Gracefully catches download restrictions through fallback exceptions, preserving textual URLs for direct search engine grounding.

3. **Clustered Processing & Polling:** Media tracks are streamed via the `google-genai` File API. The system executes a strict type-safe state evaluation check loop to hold operations until the asset moves from `PROCESSING` to a verified `ACTIVE` cluster state.

4. **Agentic Extraction & Live Web Grounding:** Deploys `gemini-2.5-flash` embedded with a rigid programmatic schema and the live `Google Search` tool. The agent identifies the opportunity, searches Google for the official portal webpage, handles missing details, and outputs structured JSON data.

5. **Anti-Throttling Sheet Storage:** Parses the JSON schema and streams data row-by-row into Google Workspace Spreadsheet layers via `gspread`, incorporating micro-sleep sequences to safely respect Google Sheets API write quotas.

---

## ✨ Features

- **Multi-Modal Native Processing:** Evaluates `.mp4`, `.mov`, `.jpg`, `.jpeg`, and `.png` formats seamlessly.
- **Resilient Error-Correction & Auto-Retry Loop:** Outfitted with an exponential backoff delay retry loop catching `503 UNAVAILABLE` cluster demands, allowing automated recovery when batch forwarding multiple screenshots concurrently.
- **Type-Agnostic File Handlers:** Formatted with deep type-safety filters that interpret SDK fluctuations between string primitive IDs and nested response maps transparently.
- **Anti-Rate Limiting Throttling:** Governs pipeline executions and sheet interactions safely within standard vendor API rate boundaries.

---

## 🛠️ Technology Stack

| Layer | Technology |
| :--- | :--- |
| Core Backend Framework | Python 3.11+, Flask (WSGI) |
| AI / Extraction | `google-genai` (v0.3.0+), `gemini-2.5-flash` |
| Media Ingestion | `yt-dlp`, Twilio Media API |
| Storage | Google Sheets API, `gspread` (v6.1.0) |
| Auth | `oauth2client`, Google Cloud IAM Service Accounts |
| Messaging | `twilio` (v9.0.2+) |
| Concurrency | Python native `threading` |

---

## ⚙️ Environment Variables

Configure these keys in your cloud hosting dashboard (e.g., Render, AWS EC2, or Heroku):

| Variable | Purpose |
| :--- | :--- |
| `GEMINI_API_KEY` | Auth token for the Google AI Studio SDK suite |
| `GOOGLE_CREDENTIALS_JSON` | Service account JSON credential block for Sheets read/write |
| `IG_COOKIES` | Netscape-formatted cookies for Instagram session authentication |
| `TWILIO_ACCOUNT_SID` | Core routing ID for the Twilio developer console workspace |
| `TWILIO_AUTH_TOKEN` | Secure token for push status updates and Twilio Media API auth |

---

## 💻 Installation & Local Setup

**1. Clone the repository:**
```bash
git clone https://github.com/codeshrek/opportunity-bot.git
cd opportunity-bot
```

**2. Create and activate a virtual environment:**
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

**3. Install dependencies:**
```bash
pip install -r requirements.txt
```

**4. Set environment variables** (create a `.env` file or export directly):
```bash
export GEMINI_API_KEY=your_key_here
export TWILIO_ACCOUNT_SID=your_sid_here
export TWILIO_AUTH_TOKEN=your_token_here
# ... (see Environment Variables section above)
```

**5. Run the development server:**
```bash
python app.py
```

> **Sandbox Testing:** Route your local port (default: `5000`) through [ngrok](https://ngrok.com/) to expose a public HTTPS URL for Twilio webhook configuration:
> ```bash
> ngrok http 5000
> ```
> Paste the generated URL into your Twilio WhatsApp sandbox webhook settings.

---

## 📋 Google Cloud IAM Setup

To enable Google Sheets sync:

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project and enable the **Google Drive API** and **Google Sheets API**.
3. Navigate to **IAM & Admin → Service Accounts** and create a new service account.
4. Generate a key in **JSON format** and download it.
5. Copy the full JSON contents into your `GOOGLE_CREDENTIALS_JSON` environment variable.
6. Open your target Google Sheet, click **Share**, and grant **Editor** access to the service account email (found in the JSON under `client_email`).

---

## 🛡️ Operational Limits

| Constraint | Behavior |
| :--- | :--- |
| Twilio Sandbox (free tier) | 50 outbound messages per 24-hour cycle. Beyond this, completions are logged locally; Sheet integrations continue uninterrupted. |
| Gemini Free Tier bursts | Concurrent multi-modal requests trigger adaptive 5–15 second pause-and-retry sequences to respect traffic quotas. |

---

## 📁 Project Structure

```
opportunity-bot/
├── app.py                  # Flask server & Twilio webhook handler
├── ingestion.py            # Media routing & yt-dlp orchestration
├── extractor.py            # Gemini AI agent & web grounding logic
├── sheets.py               # gspread sheet write pipeline
├── requirements.txt
├── cookies.txt             # Netscape cookie file (gitignored)
└── .env.example
```

---

## 🤝 Contributing

Pull requests are welcome. For significant changes, open an issue first to discuss the proposed modification.

---

*Developed to systematically automate information retrieval loops for worldwide academic programs, research placements, and engineering fellowships.*
