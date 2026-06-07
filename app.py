import os
import time
import json
import gspread
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from oauth2client.service_account import ServiceAccountCredentials
from google import genai
import yt_dlp

# ==========================================
# 1. CLOUD ENVIRONMENT VARIABLES
# ==========================================
API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
IG_COOKIES = os.environ.get("IG_COOKIES")

if not API_KEY or not GOOGLE_CREDS_JSON:
    raise ValueError("Missing Environment Variables. Please set them in the Render dashboard.")

# SECURE COOKIE HANDLING: Create a temporary file safely on the server
if IG_COOKIES:
    with open("cookies.txt", "w") as f:
        f.write(IG_COOKIES)

# ==========================================
# 2. GOOGLE SHEETS & GEMINI INITIALIZATION
# ==========================================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
sheet_client = gspread.authorize(creds)

SHEET_NAME = "Fellowships"
sheet = sheet_client.open(SHEET_NAME).sheet1 

client = genai.Client(api_key=API_KEY)

app = Flask(__name__)

# ==========================================
# 3. AUDIO-VISUAL EXTRACTION PIPELINE
# ==========================================
def download_complete_reel(url, output_filename="temp_video"):
    ydl_opts = {
        'format': 'best[ext=mp4]/best',
        'outtmpl': f'{output_filename}.mp4',
        'quiet': True,
        'no_warnings': True
    }
    
    # Only use the cookie file if we successfully generated it securely
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)
        return f"{output_filename}.mp4"

def analyze_video_with_gemini(video_path, original_url):
    video_file = client.files.upload(path=video_path)
    time.sleep(3)
    
    prompt = f"""
    You are an expert academic and professional career advisor. 
    1. Analyze this video completely to extract EVERY single program, fellowship, or scholarship mentioned.
    2. For each opportunity you find, use your Google Search tool to look up the official program webpage.
    3. Find the official application portal link and the latest application deadline.
    
    Output your response STRICTLY as a valid JSON list of objects, with no markdown code blocks, no ```json formatting, and no extra text.

    Follow this structure exactly:
    [
      {{
        "name": "Exact Name of the Program",
        "deadline": "The deadline found via Google Search (or 'Not specified' if unknown)",
        "link": "The official web page or application link found via Google Search",
        "qualifications": "Eligibility, requirements, or descriptions shown in the video",
        "source_link": "{original_url}"
      }}
    ]
    """
    
    config = {
        "tools": [{"google_search": {}}],
        "temperature": 0.2
    }
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[video_file, prompt],
            config=config
        )
        return response.text
    finally:
        try:
            client.files.delete(name=video_file.name)
        except Exception:
            pass

def append_multiple_to_sheet(json_response_text):
    clean_text = json_response_text.strip().lstrip("```json").rstrip("```").strip()
    opportunities = json.loads(clean_text)
    
    if isinstance(opportunities, dict):
        opportunities = [opportunities]
        
    rows_added = 0
    for opp in opportunities:
        row_data = [
            opp.get("name", "N/A"),
            opp.get("deadline", "N/A"),
            opp.get("link", "N/A"),
            opp.get("qualifications", "N/A"),
            opp.get("source_link", "N/A")
        ]
        sheet.append_row(row_data)
        rows_added += 1
        time.sleep(0.5) 
    return rows_added

# ==========================================
# 4. LIVE TWILIO WEBHOOK
# ==========================================
@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    resp = MessagingResponse()
    msg = resp.message()
    
    if "instagram.com" in incoming_msg or "youtube.com" in incoming_msg or "youtu.be" in incoming_msg:
        msg.body("📥 Link received! Waking up the server and deploying Gemini to parse opportunities...")
        
        clean_url = incoming_msg.split('$')[0].split('%')[0].strip()
        video_path = None
        try:
            video_path = download_complete_reel(clean_url)
            raw_json = analyze_video_with_gemini(video_path, clean_url)
            count = append_multiple_to_sheet(raw_json)
            
            msg.body(f"🎉 Success! Extracted and logged {count} verified opportunities directly into your 'Fellowships' sheet.")
        except Exception as e:
            msg.body(f"❌ Pipeline processing encountered an error: {str(e)}")
        finally:
            if video_path and os.path.exists(video_path):
                os.remove(video_path)
    else:
        msg.body("👋 Welcome! Send or forward an Instagram Reel link to this chat, and I will instantly parse it directly into your Google Sheet tracker.")
        
    return str(resp)

@app.route("/", methods=['GET'])
def health_check():
    return "Opportunity Bot is awake and running!"

# ==========================================
# 5. SERVER ACTIVATION
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
