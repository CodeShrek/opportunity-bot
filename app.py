import os
import time
import json
import gspread
from flask import Flask, request
from threading import Thread
from twilio.rest import Client
from oauth2client.service_account import ServiceAccountCredentials
from google import genai
from google.genai import types
import yt_dlp

# ==========================================
# 1. CLOUD ENVIRONMENT VARIABLES
# ==========================================
API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
IG_COOKIES = os.environ.get("IG_COOKIES")
TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH = os.environ.get("TWILIO_AUTH_TOKEN")

if not all([API_KEY, GOOGLE_CREDS_JSON, TWILIO_SID, TWILIO_AUTH]):
    raise ValueError("Missing critical Environment Variables. Check Render Dashboard.")

if IG_COOKIES:
    with open("cookies.txt", "w") as f:
        f.write(IG_COOKIES)

# Twilio Client Initialization for pushing messages
twilio_client = Client(TWILIO_SID, TWILIO_AUTH)
TWILIO_PHONE_NUMBER = 'whatsapp:+14155238886' 

# ==========================================
# 2. GOOGLE SHEETS & GEMINI INITIALIZATION
# ==========================================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
sheet_client = gspread.authorize(creds)
sheet = sheet_client.open("Fellowships").sheet1 

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
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)
        return f"{output_filename}.mp4"

def analyze_video_with_gemini(video_path, original_url):
    video_file = client.files.upload(path=video_path)
    time.sleep(3)
    
    video_part = types.Part.from_uri(file_uri=video_file.uri, mime_type="video/mp4")
    
    prompt = f"""
    You are an expert academic advisor. 
    1. Extract EVERY single program, fellowship, or scholarship mentioned.
    2. Google Search the official program webpage.
    3. Find the official application portal link and latest application deadline.
    
    Output response STRICTLY as a valid JSON list of objects.
    [
      {{
        "name": "Program Name",
        "deadline": "Deadline found via Google Search",
        "link": "Official link found via Google Search",
        "qualifications": "Eligibility",
        "source_link": "{original_url}"
      }}
    ]
    """
    
    config = {"tools": [{"google_search": {}}], "temperature": 0.2}
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[video_part, prompt],
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
    if isinstance(opportunities, dict): opportunities = [opportunities]
        
    rows_added = 0
    for opp in opportunities:
        row_data = [opp.get("name", "N/A"), opp.get("deadline", "N/A"), opp.get("link", "N/A"), opp.get("qualifications", "N/A"), opp.get("source_link", "N/A")]
        sheet.append_row(row_data)
        rows_added += 1
        time.sleep(0.5) 
    return rows_added

# ==========================================
# 4. BACKGROUND WORKER & WEBHOOK
# ==========================================
def process_video_background(url, sender_id):
    """Runs in the background so Twilio doesn't timeout."""
    twilio_client.messages.create(
        from_=TWILIO_PHONE_NUMBER,
        body="📥 Link received! Waking up the server and deploying Gemini...",
        to=sender_id
    )
    
    video_path = None
    try:
        video_path = download_complete_reel(url)
        raw_json = analyze_video_with_gemini(video_path, url)
        count = append_multiple_to_sheet(raw_json)
        
        twilio_client.messages.create(
            from_=TWILIO_PHONE_NUMBER,
            body=f"🎉 Success! Extracted and logged {count} verified opportunities directly into your 'Fellowships' sheet.",
            to=sender_id
        )
    except Exception as e:
        twilio_client.messages.create(
            from_=TWILIO_PHONE_NUMBER,
            body=f"❌ Pipeline encountered an error: {str(e)}",
            to=sender_id
        )
    finally:
        if video_path and os.path.exists(video_path):
            os.remove(video_path)

@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    sender_id = request.values.get('From') 
    
    if "instagram.com" in incoming_msg or "youtube.com" in incoming_msg or "youtu.be" in incoming_msg:
        clean_url = incoming_msg.split('$')[0].split('%')[0].strip()
        
        thread = Thread(target=process_video_background, args=(clean_url, sender_id))
        thread.start()
        
        return "OK", 200
    else:
        twilio_client.messages.create(
            from_=TWILIO_PHONE_NUMBER,
            body="👋 Send an Instagram/YouTube Reel link, and I will instantly parse it into your Google Sheet tracker.",
            to=sender_id
        )
        return "OK", 200

@app.route("/", methods=['GET'])
def health_check():
    return "Opportunity Bot is awake and running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
