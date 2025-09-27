import os
import re
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
from googleapiclient.errors import HttpError

# ====== Render対応: サービスアカウントJSONを環境変数から生成 ======
CREDENTIALS_JSON = os.environ.get("CREDENTIALS_JSON", "").strip()
if CREDENTIALS_JSON:
    with open("credentials.json", "w", encoding="utf-8") as f:
        f.write(CREDENTIALS_JSON)

# ====== 環境変数からLINEの情報を取得 ======
CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"].strip()
CHANNEL_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"].strip()

print("SECRET_LEN:", len(CHANNEL_SECRET))
print("TOKEN_LEN :", len(CHANNEL_TOKEN))

# ====== LINE v3 クライアント ======
config = Configuration(access_token=CHANNEL_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ====== Google Calendar 設定 ======
SERVICE_ACCOUNT_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CALENDAR_ID = os.getenv("CALENDAR_ID", "primary")

creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
calendar_service = build("calendar", "v3", credentials=creds)

# -------------------------
# 予定登録（分・範囲・終日対応）
# -------------------------
def add_simple_event_jp(text):
    tz = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(tz)

    # 終日予定
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

    # 時間範囲
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

    # 分指定（＋長さ指定）
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

# -------------------------
# 今日・週・月の予定取得
# -------------------------
def list_events(period="today"):
    tz = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(tz)

    if period == "today":
        start = datetime.datetime.combine(now.date(), datetime.time(0,0,0,tzinfo=tz))
        end = datetime.datetime.combine(now.date(), datetime.time(23,59,59,tzinfo=tz))
        label = "今日の予定"
    elif period == "week":
        start = now - datetime.timedelta(days=now.weekday())
        end = start + datetime.timedelta(days=7)
        label = "今週の予定"
    elif period == "nextweek":
        start = now - datetime.timedelta(days=now.weekday()) + datetime.timedelta(days=7)
        end = start + datetime.timedelta(days=7)
        label = "来週の予定"
    elif period == "month":
        start = now.replace(day=1)
        if start.month == 12:
            end = start.replace(year=start.year+1, month=1, day=1)
        else:
            end = start.replace(month=start.month+1, day=1)
        label = "今月の予定"
    else:
        return "不明な期間です。"

    res = calendar_service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        timeZone="Asia/Tokyo"
    ).execute()
    items = res.get("items", [])
    if not items:
        return f"{label}はありません。"

    lines = []
    for e in items:
        s = e["start"].get("dateTime") or (e["start"].get("date") + " 00:00")
        if "T" in s:
            dt = datetime.datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(tz)
            dstr = dt.strftime("%m/%d %H:%M")
        else:
            dstr = s  # 終日
        title = e.get("summary","（無題）")
        lines.append(f"- {dstr} {title}")

    return f"{label}：\n" + "\n".join(lines)

# -------------------------
# 予定削除
# -------------------------
def delete_event(text):
    m = re.match(r"削除\s*(今日|明日)\s*(\d{1,2})時\s*(.+)", text)
    if not m:
        return None
    day_word, hour, title = m.groups()
    hour = int(hour)

    tz = datetime.timezone(datetime.timedelta(hours=9))
    base = datetime.datetime.now(tz).date()
    date = base if day_word == "今日" else (base + datetime.timedelta(days=1))
    start = datetime.datetime.combine(date, datetime.time(hour,0,tzinfo=tz))
    end = start + datetime.timedelta(hours=1)

    res = calendar_service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        timeZone="Asia/Tokyo"
    ).execute()
    items = res.get("items", [])

    for e in items:
        if title in e.get("summary",""):
            calendar_service.events().delete(calendarId=CALENDAR_ID, eventId=e["id"]).execute()
            return f"{day_word}{hour}時『{title}』を削除しました。"

    return "該当する予定が見つかりませんでした。"

# -------------------------
# Flask
# -------------------------
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

    try:
        if text == "今日の予定":
            reply_text = list_events("today")
        elif text == "今週の予定":
            reply_text = list_events("week")
        elif text == "来週の予定":
            reply_text = list_events("nextweek")
        elif text == "今月の予定":
            reply_text = list_events("month")
        elif text.startswith("削除"):
            deleted = delete_event(text)
            reply_text = deleted if deleted else "削除できませんでした。"
        else:
            created = add_simple_event_jp(text)
            reply_text = created if created else (
                "受け取りました: " + text +
                "\n例）明日10時 会議 / 今日15:30 打合せ / 今日 終日 出張 / 今週の予定 / 削除 今日15時 打合せ"
            )

    except HttpError as e:
        reply_text = f"Google APIエラー: {e}"
    except Exception as e:
        reply_text = f"予期せぬエラー: {e}"

    with ApiClient(config) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                replyToken=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
