import os
import time
import json
import glob
import requests
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

# Secure cookie file creation
if IG_COOKIES:
    with open("cookies.txt", "w") as f:
        f.write(IG_COOKIES)

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
# 3. CORE PROCESSING LOGIC
# ==========================================
def download_media_from_url(url, output_filename="temp_media"):
    """Download media or extract metadata without crashing on photos/carousels."""
    ydl_opts = {
        'outtmpl': f'{output_filename}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'skip_download': False 
    }
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
        files = glob.glob(f"{output_filename}.*")
        return files[0] if files else None
    except Exception as e:
        print(f"Media download failed, falling back to text analysis: {e}")
        return None

def analyze_with_gemini(file_path, incoming_text):
    contents = []
    uploaded_file = None
    
    # 1. Upload and wait for ACTIVE state
    if file_path and os.path.exists(file_path):
        uploaded_file = client.files.upload(path=file_path)
        
        # Robust Wait: Check if file is ACTIVE
        print(f"File uploaded: {uploaded_file.name}. Waiting for processing...")
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
        
        if uploaded_file.state.name == "FAILED":
            raise ValueError("Gemini file processing failed.")

        ext = file_path.split('.')[-1].lower()
        mime_type = "video/mp4" if ext in ['mp4', 'webm', 'mov'] else f"image/{ext if ext != 'jpg' else 'jpeg'}"
        media_part = types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=mime_type)
        contents.append(media_part)

    # 2. Structured Prompt
    prompt = f"""
    You are an expert academic advisor. 
    Incoming message/link context: "{incoming_text}"
    
    1. Extract every program, fellowship, or scholarship mentioned.
    2. Google Search the program name to find the official application portal and the latest deadline.
    3. Output strictly as a raw JSON list of objects:
    [
      {{"name": "Program", "deadline": "Date", "link": "URL", "qualifications": "Details", "source_link": "{incoming_text}"}}
    ]
    """
    contents.append(prompt)
    
    config = {"tools": [{"google_search": {}}], "temperature": 0.2}
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents,
            config=config
        )
        return response.text
    finally:
        if uploaded_file:
            try: client.files.delete(name=uploaded_file.name)
            except: pass

def append_to_sheet(json_text):
    clean = json_text.strip().lstrip("```json").rstrip("```").strip()
    opps = json.loads(clean)
    if isinstance(opps, dict): opps = [opps]
    for opp in opps:
        sheet.append_row([opp.get("name", "N/A"), opp.get("deadline", "N/A"), opp.get("link", "N/A"), opp.get("qualifications", "N/A"), opp.get("source_link", "N/A")])
        time.sleep(0.5)
    return len(opps)

# ==========================================
# 4. BACKGROUND WORKER & WEBHOOK
# ==========================================
def process_background(incoming_text, num_media, media_url, sender_id):
    twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body="📥 Opportunity received! Processing...", to=sender_id)
    
    media_path = None
    try:
        # WhatsApp Image Logic
        if num_media > 0 and media_url:
            media_path = "temp_whatsapp_img.jpg"
            resp = requests.get(media_url, auth=(TWILIO_SID, TWILIO_AUTH))
            with open(media_path, "wb") as f: f.write(resp.content)
        # Instagram/Link Logic
        elif "http" in incoming_text:
            media_path = download_media_from_url(incoming_text.split('$')[0].strip())
        
        raw_json = analyze_with_gemini(media_path, incoming_text)
        count = append_to_sheet(raw_json)
        
        twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body=f"🎉 Success! Logged {count} opportunities.", to=sender_id)
    except Exception as e:
        twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body=f"❌ Error: {str(e)}", to=sender_id)
    finally:
        for f in glob.glob("temp_*"):
            try: os.remove(f)
            except: pass

@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    num_media = int(request.values.get('NumMedia', 0))
    media_url = request.values.get('MediaUrl0') if num_media > 0 else None
    sender_id = request.values.get('From') 
    
    if num_media > 0 or "http" in incoming_msg:
        Thread(target=process_background, args=(incoming_msg, num_media, media_url, sender_id)).start()
        return "OK", 200
    return "OK", 200

@app.route("/", methods=['GET'])
def health_check():
    return "Opportunity Bot is awake and running!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
