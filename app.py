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

# Secure cookie file creation for yt-dlp authentication
if IG_COOKIES:
    with open("cookies.txt", "w") as f:
        f.write(IG_COOKIES)

# Initialize Twilio Client for background push messaging
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
# 3. CORE MULTIMODAL PROCESSING PIPELINE
# ==========================================

def download_media_from_url(url, output_filename="temp_media"):
    """Securely downloads videos, reels, or photo metadata using cookies."""
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
        print(f"Media download skipped or failed: {e}")
        return None

def analyze_with_gemini(file_path, incoming_text):
    """Uploads file safely, isolates IDs dynamically, and features adaptive 503 retry loops."""
    contents = []
    file_name = None
    
    if file_path and os.path.exists(file_path):
        # CORRECTED: Changed 'path=' keyword argument to official 'file=' parameter
        uploaded_file = client.files.upload(file=file_path)
        
        # Type-Safety Check: Isolate File ID structural maps regardless of SDK primitive variants
        if isinstance(uploaded_file, str):
            file_name = uploaded_file
        elif hasattr(uploaded_file, 'name'):
            file_name = uploaded_file.name
        elif isinstance(uploaded_file, dict) and 'name' in uploaded_file:
            file_name = uploaded_file['name']
        else:
            file_name = str(uploaded_file)
        
        # Defensive Polling Loop for ACTIVE processing states
        while True:
            file_meta = client.files.get(name=file_name)
            
            if hasattr(file_meta, 'state'):
                state_val = file_meta.state
                state_name = state_val.name if hasattr(state_val, 'name') else str(state_val)
            elif isinstance(file_meta, dict) and 'state' in file_meta:
                state_val = file_meta['state']
                state_name = state_val.get('name') if isinstance(state_val, dict) else str(state_val)
            else:
                state_name = "ACTIVE"
            
            state_name = state_name.upper()
            if "ACTIVE" in state_name:
                break
            elif "FAILED" in state_name:
                raise ValueError("Gemini file processing optimization failed.")
            time.sleep(2)
        
        file_uri = file_meta.uri if hasattr(file_meta, 'uri') else (file_meta.get('uri') if isinstance(file_meta, dict) else None)
        if not file_uri:
            clean_id = file_name.split('/')[-1]
            file_uri = f"https://generativelanguage.googleapis.com/v1beta/files/{clean_id}"

        ext = file_path.split('.')[-1].lower()
        mime_type = "video/mp4" if ext in ['mp4', 'webm', 'mov'] else f"image/{ext if ext != 'jpg' else 'jpeg'}"
        
        contents.append(types.Part.from_uri(file_uri=file_uri, mime_type=mime_type))

    prompt = f"""
    You are an expert academic advisor and career placement officer. 
    Analyze the incoming message string or link: "{incoming_text}"
    Cross-reference any attached image visual information or video tracks.
    
    1. Extract every individual program, fellowship, or scholarship mentioned.
    2. Execute a Google Search for the program name to locate the official portal webpage and deadline.
    3. Output strictly as a raw JSON list of objects containing these keys:
    [
      {{"name": "Program", "deadline": "Date", "link": "URL", "qualifications": "Details", "source_link": "{incoming_text}"}}
    ]
    """
    contents.append(prompt)
    
    # CORRECTED: Upgraded dictionary configuration to explicit strict SDK types for validation pass
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.2
    )
    
    # ADVANCED RETRY PATTERN FOR HANDLING 503 SPIKES DURING CONCURRENT BATCH SENDS
    max_retries = 4
    try:
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=contents,
                    config=config
                )
                return response.text
            except Exception as e:
                err_str = str(e).upper()
                if "503" in err_str or "UNAVAILABLE" in err_str or "DEMAND" in err_str:
                    if attempt < max_retries - 1:
                        wait_time = 5 + (attempt * 5)
                        print(f"Server load spike hit. Retry {attempt + 1}/{max_retries}. Sleeping {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                raise e
    finally:
        if file_name:
            try: client.files.delete(name=file_name)
            except: pass

def append_to_sheet(json_text):
    """Cleans JSON wrap formatting and streams row-by-row onto Google Sheets."""
    clean = json_text.strip().lstrip("```json").rstrip("```").strip()
    opps = json.loads(clean)
    if isinstance(opps, dict): 
        opps = [opps]
        
    for opp in opps:
        sheet.append_row([
            opp.get("name", "N/A"), 
            opp.get("deadline", "N/A"), 
            opp.get("link", "N/A"), 
            opp.get("qualifications", "N/A"), 
            opp.get("source_link", "N/A")
        ])
        time.sleep(0.5) # Anti-rate limit throttling
    return len(opps)

# ==========================================
# 4. ASYNCHRONOUS WORKER & TWILIO WEBHOOK
# ==========================================

def process_background(incoming_text, num_media, media_url, sender_id):
    """Processes pipeline asynchronously to guarantee execution past Twilio timeouts."""
    twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body="📥 Opportunity received! Processing...", to=sender_id)
    
    media_path = None
    try:
        # Condition A: Native image asset forwarded via WhatsApp message
        if num_media > 0 and media_url:
            media_path = "temp_whatsapp_img.jpg"
            resp = requests.get(media_url, auth=(TWILIO_SID, TWILIO_AUTH))
            with open(media_path, "wb") as f: 
                f.write(resp.content)
                
        # Condition B: Raw web link extracted from plain text string
        elif "http" in incoming_text:
            url_to_fetch = incoming_text.split('$')[0].split('%')[0].strip()
            media_path = download_media_from_url(url_to_fetch)
        
        # Process data payloads through Gemini and append out directly
        raw_json = analyze_with_gemini(media_path, incoming_text)
        count = append_to_sheet(raw_json)
        
        twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body=f"🎉 Success! Logged {count} opportunities.", to=sender_id)
    
    except Exception as e:
        twilio_client.messages.create(from_=TWILIO_PHONE_NUMBER, body=f"❌ Error during backend parsing: {str(e)}", to=sender_id)
    
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
