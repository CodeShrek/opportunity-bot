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
# 3. MULTIMODAL EXTRACTION PIPELINE
# ==========================================
def download_media_from_url(url, output_filename="temp_media"):
    # Updated to grab whatever format exists (mp4, jpg, webp)
    ydl_opts = {
        'outtmpl': f'{output_filename}.%(ext)s',
        'quiet': True,
        'no_warnings': True
    }
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = 'cookies.txt'

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)
        
    # Find whatever file was actually downloaded
    files = glob.glob(f"{output_filename}.*")
    if files:
        return files[0]
    return None

def analyze_with_gemini(file_path, incoming_text):
    contents = []
    uploaded_file = None
    
    # 1. If we successfully grabbed a Photo or Video, format it for Gemini
    if file_path and os.path.exists(file_path):
        uploaded_file = client.files.upload(path=file_path)
        time.sleep(3)
        
        ext = file_path.split('.')[-1].lower()
        if ext in ['mp4', 'webm', 'mov']:
            mime_type = "video/mp4"
        elif ext in ['jpg', 'jpeg', 'png', 'webp']:
            mime_type = f"image/{ext if ext != 'jpg' else 'jpeg'}"
        else:
            mime_type = "application/octet-stream"
            
        media_part = types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=mime_type)
        contents.append(media_part)

    # 2. The Smart Prompt (Works with media, or just raw links)
    prompt = f"""
    You are an expert academic advisor. 
    I am giving you an incoming message/link: "{incoming_text}"
    And potentially an attached image or video.
    
    1. Extract EVERY single program, fellowship, or scholarship mentioned in the media OR the text link.
    2. If no media is attached, use your Google Search tool to browse the text link (e.g. LinkedIn) to find the context.
    3. Use Google Search to find the official application portal link and latest application deadline.
    
    Output response STRICTLY as a valid JSON list of objects.
    [
      {{
        "name": "Program Name",
        "deadline": "Deadline found via Google Search",
        "link": "Official link found via Google Search",
        "qualifications": "Eligibility",
        "source_link": "{incoming_text}"
      }}
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
            try:
                client.files.delete(name=uploaded_file.name)
            except:
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
def process_background(incoming_text, num_media, media_url, sender_id):
    twilio_client.messages.create(
        from_=TWILIO_PHONE_NUMBER,
        body="📥 Opportunity received! Deploying AI to analyze media/links...",
        to=sender_id
    )
    
    media_path = None
    try:
        # Scenario A: User forwarded a direct Photo/Screenshot on WhatsApp
        if num_media > 0 and media_url:
            media_path = "temp_whatsapp_img.jpg"
            img_data = requests.get(media_url).content
            with open(media_path, "wb") as f:
                f.write(img_data)
                
        # Scenario B: User sent a link (Instagram, YouTube, LinkedIn)
        elif "http" in incoming_text:
            clean_url = incoming_text.split('$')[0].split('%')[0].strip()
            try:
                media_path = download_media_from_url(clean_url)
            except Exception as e:
                # If it's a LinkedIn link that blocks downloads, we ignore media 
                # and let Gemini just Google Search the text URL directly!
                print(f"Media extraction skipped, falling back to text search: {e}")
                media_path = None
        
        raw_json = analyze_with_gemini(media_path, incoming_text)
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
        # Cleanup all temp files dynamically
        for f in glob.glob("temp_*"):
            try:
                os.remove(f)
            except:
                pass

@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    num_media = int(request.values.get('NumMedia', 0))
    media_url = request.values.get('MediaUrl0') if num_media > 0 else None
    sender_id = request.values.get('From') 
    
    # We now trigger if there is ANY link OR any attached photo
    if num_media > 0 or "http" in incoming_msg:
        thread = Thread(target=process_background, args=(incoming_msg, num_media, media_url, sender_id))
        thread.start()
        return "OK", 200
    else:
        twilio_client.messages.create(
            from_=TWILIO_PHONE_NUMBER,
            body="👋 Forward an Instagram/LinkedIn link OR a Screenshot, and I will parse it into your tracker.",
            to=sender_id
        )
        return "OK", 200

@app.route("/", methods=['GET'])
def health_check():
    return "Opportunity Bot is awake and running!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
