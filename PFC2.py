import os
import json
import re
import sqlite3
from datetime import date
from functools import wraps

from flask import Flask, request, abort
import requests

from linebot import LineBotApi, WebhookHandler


from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, PostbackEvent,
    TemplateSendMessage, ButtonsTemplate, PostbackAction, MessageAction
)
LINE_CHANNEL_ACCESS_TOKEN = "a/F0EfPdHmmZ+h9zMYAhHBPwpZdCOoMpvNGjBZWNk1eLVI0hzJucJY3nNG9u2m7KK4UEliZu8v058haxlt6e8C6VtfkH+klHuzlDwQ7fp1wF0xkQcm7WQNRCtmX3NtSwlxd5QUcZdR9AiTWK5vv7uQdB04t89/1O/w1cDnyilFU="
LINE_CHANNEL_SECRET = "f4bfab1694cd322b732ff20a3cc6e507"
USDA_API_KEY = "H26HaFS0WU4qxM4FJ3NWD2VZA1GIfZjcIO8LimPW"
# https://fdc.nal.usda.gov/api-key-signup.html

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    print("Warning: LINE credentials not set. Set LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET env vars.")
if not USDA_API_KEY:
    print("Warning: USDA_API_KEY not set. Food search will fail without it.")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)
DB_PATH = "pfc_bot.db"

def with_db(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            res = func(conn, *args, **kwargs)
            conn.commit()
            return res
        finally:
            conn.close()
    return wrapper

@with_db
def init_db(conn):
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        state TEXT,        -- registration flow state e.g. "await_age", "done"
        gender TEXT,
        activity TEXT,
        age INTEGER,
        weight REAL,
        height REAL,
        goals TEXT         -- json string: {"cal":..., "protein":..., "fat":..., "carb":...}
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS intake (
        user_id TEXT,
        date TEXT,
        protein REAL,
        fat REAL,
        carb REAL,
        PRIMARY KEY (user_id, date)
    )""")
    conn.commit()

init_db()

def calc_goal(age, sex, weight, height, activity):
    """
    Mifflin-St Jeor BMR -> TDEE -> P/F/C
    sex: "male" or "female"
    activity: "low", "mid", "high"
    """
    if sex == "male":
        bmr = 10*weight + 6.25*height - 5*age + 5
    else:
        bmr = 10*weight + 6.25*height - 5*age - 161

    activity_factors = {"low": 1.2, "mid": 1.55, "high": 1.725}
    tdee = bmr * activity_factors.get(activity, 1.55)

    protein_g = weight * 1.8  # g, 中間値
    fat_kcal = tdee * 0.25
    fat_g = fat_kcal / 9
    remaining_kcal = tdee - (protein_g * 4 + fat_g * 9)
    carb_g = remaining_kcal / 4
    return {
        "cal": int(round(tdee)),
        "protein": int(round(protein_g)),
        "fat": int(round(fat_g)),
        "carb": int(round(carb_g))
    }

@with_db
def get_user(conn, user_id):
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    if not row:
        return None
    data = dict(row)
    if data.get("goals"):
        data["goals"] = json.loads(data["goals"])
    return data

@with_db
def upsert_user(conn, user_id, **fields):
    c = conn.cursor()
    # fetch existing
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if c.fetchone():
        # build SET
        sets = ", ".join([f"{k}=?" for k in fields.keys()])
        vals = list(fields.values()) + [user_id]
        c.execute(f"UPDATE users SET {sets} WHERE user_id=?", vals)
    else:
        # insert with provided fields (others NULL)
        cols = ",".join(["user_id"] + list(fields.keys()))
        placeholders = ",".join(["?"] * (1 + len(fields)))
        vals = [user_id] + list(fields.values())
        c.execute(f"INSERT INTO users ({cols}) VALUES ({placeholders})", vals)

@with_db
def add_meal_record(conn, user_id, protein, fat, carb):
    today = str(date.today())
    c = conn.cursor()
    c.execute("SELECT protein,fat,carb FROM intake WHERE user_id=? AND date=?", (user_id, today))
    row = c.fetchone()
    if row:
        new_p = row["protein"] + protein
        new_f = row["fat"] + fat
        new_c = row["carb"] + carb
        c.execute("UPDATE intake SET protein=?, fat=?, carb=? WHERE user_id=? AND date=?", (new_p,new_f,new_c,user_id,today))
    else:
        c.execute("INSERT INTO intake (user_id,date,protein,fat,carb) VALUES (?,?,?,?,?)", (user_id,today,protein,fat,carb))

@with_db
def get_today_totals(conn, user_id):
    today = str(date.today())
    c = conn.cursor()
    c.execute("SELECT protein,fat,carb FROM intake WHERE user_id=? AND date=?", (user_id, today))
    row = c.fetchone()
    if row:
        return (row["protein"], row["fat"], row["carb"])
    return (0.0,0.0,0.0)

def send_text(reply_token, text):
    line_bot_api.reply_message(reply_token, TextSendMessage(text=text))

def push_text(user_id, text):
    line_bot_api.push_message(user_id, TextSendMessage(text=text))

def ask_gender(reply_token):
    message = TemplateSendMessage(
        alt_text='性別を選んでください',
        template=ButtonsTemplate(
            title='性別を選んでください',
            text='どちらですか？',
            actions=[
                PostbackAction(label='男性', data='action=select_gender&gender=male', display_text='男性'),
                PostbackAction(label='女性', data='action=select_gender&gender=female', display_text='女性')
            ]
        )
    )
    line_bot_api.reply_message(reply_token, message)

def ask_activity(reply_token):
    message = TemplateSendMessage(
        alt_text='活動レベルを選んでください',
        template=ButtonsTemplate(
            title='活動レベルを選んでください',
            text='日常の活動レベルを選んでください',
            actions=[
                PostbackAction(label='低い（ほぼ運動なし）', data='action=select_activity&activity=low', display_text='活動:低い'),
                PostbackAction(label='普通（週1〜3回）', data='action=select_activity&activity=mid', display_text='活動:普通'),
                PostbackAction(label='高い（ほぼ毎日運動）', data='action=select_activity&activity=high', display_text='活動:高い')
            ]
        )
    )
    line_bot_api.reply_message(reply_token, message)

def show_goals_and_help(reply_token, user):
    goals = user.get("goals") or {}
    text = ("登録完了しました！\n\n"
            f"1日の目標値:\nカロリー: {goals.get('cal','-')} kcal\n"
            f"P: {goals.get('protein','-')} g\n"
            f"F: {goals.get('fat','-')} g\n"
            f"C: {goals.get('carb','-')} g\n\n"
            "食事を記録するには、例: 「鶏むね肉 150g」 と送ってください。\n"
            "今日の合計と残りを返します。")
    send_text(reply_token, text)

def usda_search_nutrients(query, grams=100):
    """
    Search USDA FoodData Central and return (protein_g, fat_g, carb_g) for specified grams.
    This implementation takes first search result and scales per 100g -> grams.
    Note: robust production code would better match food items and handle unit conversions.
    """
    if not USDA_API_KEY:
        return None

    url = "https://api.nal.usda.gov/fdc/v1/foods/search"
    params = {"api_key": USDA_API_KEY, "query": query, "pageSize": 2}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("USDA request error:", e)
        return None

    if "foods" not in data or len(data["foods"]) == 0:
        return None

    food = data["foods"][0]

    nutrient_map = {}
    for n in food.get("foodNutrients", []):
        name = n.get("nutrientName", "").lower()
        value = n.get("value", 0)
        if "protein" in name:
            nutrient_map["protein"] = value
        elif "total lipid" in name or "fat" in name:
            nutrient_map["fat"] = value
        elif "carbohydrate" in name and "by difference" in name:
            nutrient_map["carb"] = value

    p = nutrient_map.get("protein", 0.0)
    f = nutrient_map.get("fat", 0.0)
    c = nutrient_map.get("carb", 0.0)

    scale = grams / 100.0
    return (round(p * scale, 2), round(f * scale, 2), round(c * scale, 2))

def parse_food_input(text):
    """
    Expect format: "<food name> <number>g" or "<food name> <number> g"
    Returns (food_name, grams) or (None, None)
    """
    text = text.strip()
    m = re.search(r'(.+?)\s+([0-9]+(?:\.[0-9]+)?)\s*g$', text, flags=re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        grams = float(m.group(2))
        return (name, grams)
    return (text, 100.0)

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()

    user = get_user(user_id)

    if not user or (user.get("state") and user.get("state") != "done"):
        if not user:
            upsert_user(user_id, state="await_gender")
            ask_gender(event.reply_token)
            return

        state = user.get("state")
        if state == "await_age":
            if text.isdigit():
                age = int(text)
                upsert_user(user_id, age=age, state="await_weight")
                send_text(event.reply_token, "年齢を受け取りました。次に体重(kg)を数字で入力してください（例: 60）")
            else:
                send_text(event.reply_token, "年齢は数字で入力してください（例: 25）")
            return

        if state == "await_weight":
            try:
                weight = float(text)
                upsert_user(user_id, weight=weight, state="await_height")
                send_text(event.reply_token, "体重を受け取りました。次に身長(cm)を数字で入力してください（例: 170）")
            except:
                send_text(event.reply_token, "体重は数字で入力してください（例: 60）")
            return
        if state == "await_height":
            try:
                height = float(text)
                upsert_user(user_id, height=height)
                u = get_user(user_id)
                if not u:
                    send_text(event.reply_token, "エラー: ユーザー情報が見つかりません。再登録をお願いします。")
                    upsert_user(user_id, state="await_gender")
                    ask_gender(event.reply_token)
                    return
                gender = u.get("gender")
                activity = u.get("activity")
                age = u.get("age")
                weight = u.get("weight") if u.get("weight") else None

                if not all([gender, activity, age, weight, height]):
                    missing = []
                    if not gender: missing.append("性別")
                    if not activity: missing.append("活動レベル")
                    if not age: missing.append("年齢")
                    if not weight: missing.append("体重")
                    if not height: missing.append("身長")
                    send_text(event.reply_token, "まだ設定が足りません: " + ",".join(missing))
                    return

                goals = calc_goal(age=int(age), sex=gender, weight=float(weight), height=float(height), activity=activity)
                upsert_user(user_id, goals=json.dumps(goals), state="done")
                user = get_user(user_id)
                show_goals_and_help(event.reply_token, user)
            except Exception as e:
                print("height parse err:", e)
                send_text(event.reply_token, "身長は数字で入力してください（例: 170）")
            return
        send_text(event.reply_token, "登録が完了していません。まず性別を選んでください。")
        ask_gender(event.reply_token)
        return
    lower = text.lower()
    if lower in ["今日の合計", "今日の合計を教えて", "合計"]:
        p,f,c = get_today_totals(user_id)
        goals = user.get("goals") or {}
        remain_p = max(0, goals.get("protein",0) - p)
        remain_f = max(0, goals.get("fat",0) - f)
        remain_c = max(0, goals.get("carb",0) - c)
        reply = (f"今日の合計:\nP: {p} g\nF: {f} g\nC: {c} g\n\n"
                 f"目標まで:\nP: あと {remain_p} g\nF: あと {remain_f} g\nC: あと {remain_c} g")
        send_text(event.reply_token, reply)
        return
    if lower in ["目標", "目標値", "mygoal"]:
        goals = user.get("goals") or {}
        reply = (f"あなたの1日の目標値:\nカロリー: {goals.get('cal','-')} kcal\n"
                 f"P: {goals.get('protein','-')} g\nF: {goals.get('fat','-')} g\nC: {goals.get('carb','-')} g")
        send_text(event.reply_token, reply)
        return
    if lower in ["登録情報", "プロフィール"]:
        reply = (f"性別: {user.get('gender')}\n活動: {user.get('activity')}\n年齢: {user.get('age')}\n"
                 f"体重: {user.get('weight')}\n身長: {user.get('height')}")
        send_text(event.reply_token, reply)
        return
    if lower in ["リセット", "初期化"]:
        upsert_user(user_id, state="await_gender", gender=None, activity=None, age=None, weight=None, height=None, goals=None)
        send_text(event.reply_token, "登録をリセットしました。性別の選択から始めます。")
        ask_gender(event.reply_token)
        return
    food_name, grams = parse_food_input(text)
    nutrients = usda_search_nutrients(food_name, grams=grams)
    if nutrients:
        p,f,c = nutrients
        add_meal_record(user_id, p, f, c)
        tp, tf, tc = get_today_totals(user_id)
        goals = user.get("goals") or {}
        remain_p = max(0, goals.get("protein",0) - tp)
        remain_f = max(0, goals.get("fat",0) - tf)
        remain_c = max(0, goals.get("carb",0) - tc)
        reply = (f"{food_name} {grams}g を記録しました。\n摂取: P={p}g, F={f}g, C={c}g\n\n"
                 f"今日の合計: P={tp}g, F={tf}g, C={tc}g\n"
                 f"目標まで: P=あと{remain_p}g, F=あと{remain_f}g, C=あと{remain_c}g")
        send_text(event.reply_token, reply)
        return
    else:
        send_text(event.reply_token, "食品データが見つからないかUSDA APIエラーです。食品名とgを「鶏むね肉 150g」のように送ってください。")
        return

@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    data = event.postback.data  # e.g. 'action=select_gender&gender=male'
    params = {}
    for part in data.split("&"):
        if "=" in part:
            k,v = part.split("=",1)
            params[k]=v

    action = params.get("action")
    if action == "select_gender":
        gender = params.get("gender")
        upsert_user(user_id, gender=gender, state="await_activity")
        ask_activity(event.reply_token)
        return
    if action == "select_activity":
        activity = params.get("activity")
        upsert_user(user_id, activity=activity, state="await_age")
        send_text(event.reply_token, "活動レベルを受け取りました。次に年齢を数字で入力してください（例: 25）")
        return

    send_text(event.reply_token, "受け付けました。")

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        print("Exception in handler:", e)
    return "OK"

@app.route("/", methods=["GET"])
def index():
    return "PFC LINE Bot is running."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
