import os
import datetime
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build

from linebot.v3.messaging import (
    MessagingApi, Configuration, ApiClient,
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError

# ====== Render対応: サービスアカウントJSONを環境変数から生成 ======
CREDENTIALS_JSON = os.environ.get("CREDENTIALS_JSON", "").strip()
if CREDENTIALS_JSON:
    with open("credentials.json", "w", encoding="utf-8") as f:
        f.write(CREDENTIALS_JSON)

# ====== 環境変数からLINEの情報を取得 ======
CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"].strip()
CHANNEL_TOKEN  = os.environ["LINE_CHANNEL_ACCESS_TOKEN"].strip()

print("SECRET_LEN:", len(CHANNEL_SECRET))
print("TOKEN_LEN :", len(CHANNEL_TOKEN))

# ====== LINE v3 クライアント ======
config = Configuration(access_token=CHANNEL_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ====== Google Calendar 設定 ======
SERVICE_ACCOUNT_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = "cyclonepop0001@gmail.com"

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
calendar_service = build("calendar", "v3", credentials=creds)

def list_today_events():
    tz = datetime.timezone(datetime.timedelta(hours=9))  # JST
    today = datetime.datetime.now(tz).date()
    start = datetime.datetime.combine(today, datetime.time(0,0,0,tzinfo=tz)).astimezone(datetime.timezone.utc).isoformat()
    end   = datetime.datetime.combine(today, datetime.time(23,59,59,tzinfo=tz)).astimezone(datetime.timezone.utc).isoformat()

    res = calendar_service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=start, timeMax=end,
        singleEvents=True, orderBy="startTime",
        timeZone="Asia/Tokyo"
    ).execute()
    items = res.get("items", [])
    if not items:
        return "今日は予定なし。"
    lines = []
    for e in items:
        s = e["start"].get("dateTime") or (e["start"].get("date") + " 00:00")
        if "T" in s:
            dt = datetime.datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(tz)
            hm = dt.strftime("%H:%M")
        else:
            hm = "終日"
        title = e.get("summary","（無題）")
        lines.append(f"- {hm} {title}")
    return "今日の予定：\n" + "\n".join(lines)

def add_simple_event_jp(text):
    import re
    tz = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(tz)

    # -------------------------
    # 終日予定
    # 例: 今日 終日 出張
    # -------------------------
    m = re.match(r"(今日|明日)\s*終日\s*(.+)", text)
    if m:
        day_word, title = m.groups()
        date = now.date() if day_word == "今日" else (now.date() + datetime.timedelta(days=1))
        body = {
            "summary": title,
            "start": {"date": date.isoformat()},
            "end": {"date": (date + datetime.timedelta(days=1)).isoformat()},
        }
        calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        return f"{day_word} 終日『{title}』を登録しました。"

    # -------------------------
    # 時間範囲
    # 例: 今日10:30-11:45 打合せ
    # -------------------------
    m = re.match(r"(今日|明日)\s*(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})\s*(.+)", text)
    if m:
        day_word, sh, sm, eh, em, title = m.groups()
        date = now.date() if day_word == "今日" else (now.date() + datetime.timedelta(days=1))
        start = datetime.datetime.combine(date, datetime.time(int(sh), int(sm), tzinfo=tz))
        end = datetime.datetime.combine(date, datetime.time(int(eh), int(em), tzinfo=tz))
        body = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
        }
        calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        return f"{day_word}{sh}:{sm}〜{eh}:{em}『{title}』を登録しました。"

    # -------------------------
    # 分指定（＋オプションで長さ指定）
    # 例: 明日10:15 90分 面談
    # -------------------------
    m = re.match(r"(今日|明日)\s*(\d{1,2})(?::(\d{2}))?時?\s*(\d+)?分?\s*(.+)", text)
    if m:
        day_word, hour, minute, dur, title = m.groups()
        minute = int(minute) if minute else 0
        dur = int(dur) if dur else 60  # デフォルト1時間
        date = now.date() if day_word == "今日" else (now.date() + datetime.timedelta(days=1))
        start = datetime.datetime.combine(date, datetime.time(int(hour), minute, tzinfo=tz))
        end = start + datetime.timedelta(minutes=dur)
        body = {
            "summary": title,
            "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Tokyo"},
            "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Tokyo"},
        }
        calendar_service.events().insert(calendarId=CALENDAR_ID, body=body).execute()
        return f"{day_word}{hour}:{minute:02d}〜{dur}分『{title}』を登録しました。"

    return None



# ====== Flask ======
app = Flask(__name__)

@app.route("/callback", methods=["GET", "POST"])
def callback():
    if request.method == "GET":
        return "OK", 200

    body = request.get_data(as_text=True)
    signature = request.headers.get("X-Line-Signature", "")
    print("HIT /callback", signature, body[:200])

    if '"events":[]' in body:
        print("Empty events (verify). return 200")
        return "OK", 200

    try:
        handler.handle(body, signature)
    except InvalidSignatureError as e:
        print("InvalidSignature:", e)
        return "Bad signature", 400
    except Exception as e:
        print("Other Exception:", repr(e))
        return "OK", 200

    return "OK", 200

@handler.add(MessageEvent, message=TextMessageContent)
def on_message(event):
    text = event.message.text.strip()

    if ("今日" in text) and ("予定" in text):
        reply_text = list_today_events()
    else:
        created = add_simple_event_jp(text)
        reply_text = created if created else f"受け取りました: {text}\n例）明日10時 会議 / 今日15時 打合せ / 今日の予定"

    with ApiClient(config) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                replyToken=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)



