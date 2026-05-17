import time
import random
import re
import sqlite3
import os
import threading  # ДОБАВЛЕНО ДЛЯ RENDER
from flask import Flask  # ДОБАВЛЕНО ДЛЯ RENDER
from datetime import datetime, timedelta, timezone
from telebot import TeleBot, types

# --- НАСТРОЙКА ВЕБ-СЕРВЕРА ДЛЯ RENDER (ЖИВУЧЕСТЬ БОТА) ---
app = Flask('')

@app.route('/')
def home():
    return "Бот казино VIR успешно запущен и работает!"

def run():
    # Слушаем порт 8080, который требует Render
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

# Запускаем фоновый сервер-пустышку
keep_alive()
# --------------------------------------------------------

# --- Глобальные переменные (Изолированы по чатам) ---
is_spinning = {}        # chat_id -> флаг, крутится ли рулетка
round_start_time = {}   # chat_id -> время старта раунда
active_bets = {}        # chat_id -> {user_id -> ставки}
last_bets_db = {}       # chat_id -> {user_id -> предыдущие ставки}
ROUND_DURATION = 15     # длительность раунда в секундах

# Путь к базе данных (Изменено под Render: база создается прямо в корне)
DB_PATH = 'casino.db'

TOKEN = '8916428142:AAEzltLgSAZSuqLEVitNmbcOMqRhU8WKMfQ'
ADMIN_ID = 7463968638
CURRENCY = 'VIR'
GIF_URL = 'https://t.me/CHAT_VIRUSA/42830'

bot = TeleBot(TOKEN, parse_mode='HTML')

# --- Функции Базы Данных ---
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute('PRAGMA journal_mode=WAL;')  # Быстрый режим работы базы
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 3000,
            name TEXT,
            last_bonus_date TEXT DEFAULT ''
        )
    ''')
    
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'total_bets' not in columns:
        cursor.execute('ALTER TABLE users ADD COLUMN total_bets INTEGER DEFAULT 0')
    if 'total_losses' not in columns:
        cursor.execute('ALTER TABLE users ADD COLUMN total_losses INTEGER DEFAULT 0')
    if 'max_win' not in columns:
        cursor.execute('ALTER TABLE users ADD COLUMN max_win INTEGER DEFAULT 0')
        
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS game_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            win_number INTEGER
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_mines (
            user_id INTEGER PRIMARY KEY,
            chat_id INTEGER,
            bet INTEGER,
            mines TEXT,
            uncovered TEXT,
            message_id INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY,
            amount INTEGER,
            max_uses INTEGER,
            current_uses INTEGER DEFAULT 0,
            used_users TEXT DEFAULT '',
            source_chat_id INTEGER,
            source_chat_title TEXT,
            expires_at TEXT DEFAULT ''
        )
    ''')
    
    conn.commit()
    conn.close()

# --- Работа с Минами через БД ---
def db_get_mine_game(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id, bet, mines, uncovered, message_id FROM active_mines WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "chat_id": row[0], "bet": row[1],
            "mines": [int(x) for x in row[2].split(",") if x],
            "uncovered": [int(x) for x in row[3].split(",") if x],
            "message_id": row[4]
        }
    return None

def db_save_mine_game(user_id, game):
    conn = get_db_connection()
    cursor = conn.cursor()
    mines_str = ",".join(map(str, game["mines"]))
    uncovered_str = ",".join(map(str, game["uncovered"]))
    cursor.execute('''
        INSERT OR REPLACE INTO active_mines (user_id, chat_id, bet, mines, uncovered, message_id)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, game["chat_id"], game["bet"], mines_str, uncovered_str, game["message_id"]))
    conn.commit()
    conn.close()

def db_delete_mine_game(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM active_mines WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

MINES_MULTIPLIERS = [
    1.0, 1.28, 1.65, 2.15, 2.82, 3.73, 5.0, 6.78, 9.33, 13.06, 
    18.67, 27.22, 40.59, 61.98, 97.4, 157.79, 264.25, 458.04, 
    824.47, 1551.0, 3071.0, 6449.0, 14511.0, 36279.0, 108839.0
]

ROULETTE_MAP = {
    0: ('🟢', 'GREEN', 'none'),
    1: ('🔴', 'RED', 'odd'), 3: ('🔴', 'RED', 'odd'), 5: ('🔴', 'RED', 'odd'),
    7: ('🔴', 'RED', 'odd'), 9: ('🔴', 'RED', 'odd'), 12: ('🔴', 'RED', 'even'),
    14: ('🔴', 'RED', 'even'), 16: ('🔴', 'RED', 'even'), 18: ('🔴', 'RED', 'even'),
    19: ('🔴', 'RED', 'odd'), 21: ('🔴', 'RED', 'odd'), 23: ('🔴', 'RED', 'odd'),
    25: ('🔴', 'RED', 'odd'), 27: ('🔴', 'RED', 'odd'), 30: ('🔴', 'RED', 'even'),
    32: ('🔴', 'RED', 'even'), 34: ('🔴', 'RED', 'even'), 36: ('🔴', 'RED', 'even'),
    2: ('⚫', 'BLACK', 'even'), 4: ('⚫', 'BLACK', 'even'), 6: ('⚫', 'BLACK', 'even'),
    8: ('⚫', 'BLACK', 'even'), 10: ('⚫', 'BLACK', 'even'), 11: ('⚫', 'BLACK', 'odd'),
    13: ('⚫', 'BLACK', 'odd'), 15: ('⚫', 'BLACK', 'odd'), 17: ('⚫', 'BLACK', 'odd'),
    20: ('⚫', 'BLACK', 'even'), 22: ('⚫', 'BLACK', 'even'), 24: ('⚫', 'BLACK', 'even'),
    26: ('⚫', 'BLACK', 'even'), 28: ('⚫', 'BLACK', 'even'), 29: ('⚫', 'BLACK', 'odd'),
    31: ('⚫', 'BLACK', 'odd'), 33: ('⚫', 'BLACK', 'odd'), 35: ('⚫', 'BLACK', 'odd')
}

# --- Управление пользователями ---
def init_user(user: types.User):
    name_str = str(user.first_name) if user.first_name else "Игрок"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user.id,))
    row = cursor.fetchone()
    if not row:
        cursor.execute('INSERT INTO users (user_id, balance, name, total_bets, total_losses, max_win) VALUES (?, 3000, ?, 0, 0, 0)', (user.id, name_str))
    else:
        cursor.execute('UPDATE users SET name = ? WHERE user_id = ?', (name_str, user.id))
    conn.commit()
    conn.close()

def get_user_data(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT balance, name, last_bonus_date, total_bets, total_losses, max_win FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'balance': row[0], 'name': row[1], 'last_bonus_date': row[2],
            'total_bets': row[3], 'total_losses': row[4], 'max_win': row[5]
        }
    return {'balance': 3000, 'name': 'Игрок', 'last_bonus_date': '', 'total_bets': 0, 'total_losses': 0, 'max_win': 0}

def update_user_balance(user_id, new_balance):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET balance = ? WHERE user_id = ?', (new_balance, user_id))
    conn.commit()
    conn.close()

def update_user_stats(user_id, add_bets, add_losses, max_win_candidate):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users 
        SET total_bets = total_bets + ?, 
            total_losses = total_losses + ?,
            max_win = MAX(max_win, ?)
        WHERE user_id = ?
    ''', (add_bets, add_losses, max_win_candidate, user_id))
    conn.commit()
    conn.close()

def update_user_bonus(user_id, bonus_date):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET last_bonus_date = ? WHERE user_id = ?', (bonus_date, user_id))
    conn.commit()
    conn.close()

def save_win_number(num):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO game_history (win_number) VALUES (?)', (num,))
    conn.commit()
    conn.close()

def get_roulette_history():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT win_number FROM game_history ORDER BY id DESC LIMIT 12')
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in reversed(rows)]

def get_mention(user_id):
    user_data = get_user_data(user_id)
    name = str(user_data['name']).replace('<', '&lt;').replace('>', '&gt;')
    return f'<a href="tg://user?id={user_id}">{name}</a>'

def get_msk_now():
    return datetime.now(timezone(timedelta(hours=3)))

def get_msk_date():
    return get_msk_now().strftime("%Y-%m-%d")

def get_time_until_midnight():
    now = get_msk_now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    remaining = tomorrow - now
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}"

def check_bonus_available(user_id):
    user_data = get_user_data(user_id)
    return user_data['last_bonus_date'] != get_msk_date()

def fmt(num):
    return f"{num:,}".replace(",", " ")

def process_claim_bonus(user_id):
    user_data = get_user_data(user_id)
    if not check_bonus_available(user_id):
        time_left = get_time_until_midnight()
        if time_left.startswith("0") and time_left != ":": time_left = time_left[1:]
        return f"Осталось подождать {time_left} до следующего бонуса"
        
    if user_data['balance'] >= 70000:
        return f"❌ Бонус доступен только если ваш баланс меньше 70 000 {CURRENCY}!"
        
    new_balance = user_data['balance'] + 2500
    update_user_balance(user_id, new_balance)
    update_user_bonus(user_id, get_msk_date())
    
    time_left = get_time_until_midnight()
    if time_left.startswith("0") and time_left != ":": time_left = time_left[1:]
    
    return f"Вам начислено: 2500 {CURRENCY}\n\nСледующий бонус будет доступен через {time_left}"

# --- Безопасный прием ставок ---
def apply_bets_safely(chat_id, user_id, collapsed_bets):
    mention = get_mention(user_id)
    user_data = get_user_data(user_id)
    current_balance = user_data['balance']
    
    if chat_id not in active_bets:
        active_bets[chat_id] = {}
    if user_id not in active_bets[chat_id]:
        active_bets[chat_id][user_id] = []
        
    if len(active_bets[chat_id][user_id]) >= 150:
        bot.send_message(chat_id, f"❌ Максимальное количество ставок: 150")
        return []

    responses = []
    total_cost = sum(bet['amount'] for bet in collapsed_bets)
    
    if current_balance < total_cost:
        bot.send_message(chat_id, f"❌ {mention}, недостаточно {CURRENCY} на балансе!")
        return []

    current_balance -= total_cost
    update_user_balance(user_id, current_balance)
    
    for bet in collapsed_bets:
        found = False
        for existing in active_bets[chat_id][user_id]:
            if existing['type'] == bet['type']:
                existing['amount'] += bet['amount']
                found = True
                break
        if not found:
            active_bets[chat_id][user_id].append(bet)
            
        responses.append(f"Ставка принята: {mention} <b>{fmt(bet['amount'])} {CURRENCY}</b> на <code>{bet['display']}</code>")
            
    return responses

def is_bet_message(message):
    if not message.text: return False
    tokens = message.text.split()
    if not tokens: return False
    return tokens[0].isdigit()

def get_private_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_profile = types.KeyboardButton("👤 Профиль")
    btn_bonus = types.KeyboardButton("🎁 Бонус")
    markup.row(btn_profile, btn_bonus)
    return markup


# --- СИСТЕМА ПРОМОКОДОВ ---
def parse_time_interval(param):
    match = re.match(r"^(\d+)([мчдг])$", param.lower())
    if not match: return None
    value = int(match.group(1))
    unit = match.group(2)
    
    now = get_msk_now()
    if unit == 'м': delta = timedelta(minutes=value)
    elif unit == 'ч': delta = timedelta(hours=value)
    elif unit == 'д': delta = timedelta(days=value)
    elif unit == 'г': delta = timedelta(days=value * 365)
    else: return None
    
    return (now + delta).strftime("%Y-%m-%d %H:%M:%S")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('+промо') and m.from_user.id == ADMIN_ID)
def create_smart_promocode(message: types.Message):
    tokens = message.text.split()
    if len(tokens) < 4:
        bot.reply_to(message, "❌ Формат: <code>+промо [код] [сумма] [лимит/время]</code>")
        return
    
    code = tokens[1].upper().replace('/', '')
    try: amount = int(tokens[2])
    except ValueError:
        bot.reply_to(message, "❌ Сумма должна быть числом!")
        return

    limit_or_time = tokens[3]
    max_uses = 999999999
    expires_at = ""
    is_time_promo = False
    
    time_parsed = parse_time_interval(limit_or_time)
    if time_parsed:
        expires_at = time_parsed
        is_time_promo = True
    else:
        try: max_uses = int(limit_or_time)
        except ValueError:
            bot.reply_to(message, "❌ Неверно указан лимит активаций или время (пример: 5м, 2ч, 3д, 1г)!")
            return

    chat_title = message.chat.title if message.chat.title else "источник"
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO promocodes (code, amount, max_uses, current_uses, used_users, source_chat_id, source_chat_title, expires_at)
            VALUES (?, ?, ?, 0, '', ?, ?, ?)
        ''', (code, amount, max_uses, message.chat.id, chat_title, expires_at))
        conn.commit()
        
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass

        if is_time_promo:
            dt = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            formatted_date = dt.strftime("%d.%m.%Y %H:%M")
            post_text = (
                f"🎁 <b>Новый промокод!</b>\n\n"
                f"🔥 Промокод: <code>/{code}</code>\n"
                f"💰 Награда: <b>{fmt(amount)} {CURRENCY}</b>\n"
                f"⏳ Действует до: <b>{formatted_date} МСК</b>"
            )
        else:
            post_text = (
                f"🎁 <b>Новый промокод!</b>\n\n"
                f"🔥 Промокод: <code>/{code}</code>\n"
                f"💰 Награда: <b>{fmt(amount)} {CURRENCY}</b>\n"
                f"👥 Лимит: <b>{max_uses} активаций</b>"
            )
            
        bot.send_message(message.chat.id, post_text)
        
    except sqlite3.IntegrityError:
        bot.send_message(message.chat.id, "❌ Такой промокод уже существует!")
    finally:
        conn.close()

@bot.message_handler(func=lambda m: m.text and (m.text.startswith('/') or len(m.text.strip().split()) == 1))
def check_and_activate_promo(message: types.Message):
    text_clean = message.text.replace('/', '').strip().upper()
    tokens = text_clean.split()
    if not tokens: return
    
    code_candidate = tokens[0]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT amount, max_uses, current_uses, used_users, source_chat_id, source_chat_title, expires_at FROM promocodes WHERE code = ?', (code_candidate,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return
        
    init_user(message.from_user)
    user_id = message.from_user.id
    mention = get_mention(user_id)
    
    amount, max_uses, current_uses, used_users_str, source_chat_id, source_chat_title, expires_at = row
    used_users_list = used_users_str.split(",") if used_users_str else []
    
    if str(user_id) in used_users_list:
        bot.reply_to(message, f"❌ {mention}, вы уже активировали этот промокод!")
        conn.close()
        return
        
    try:
        member = bot.get_chat_member(source_chat_id, user_id)
        if member.status not in ['creator', 'administrator', 'member']:
            bot.reply_to(message, f"❌ {mention}, чтобы активировать промокод, нужно подписаться на <b>{source_chat_title}</b>!")
            conn.close()
            return
    except:
        bot.reply_to(message, f"❌ {mention}, не удалось проверить подписку на ресурс активации!")
        conn.close()
        return

    if expires_at:
        now_str = get_msk_now().strftime("%Y-%m-%d %H:%M:%S")
        if now_str > expires_at:
            bot.reply_to(message, f"🏁 Этот промокод уже испорчен и его нельзя активировать!")
            conn.close()
            return

    if current_uses >= max_uses:
        bot.reply_to(message, f"🏁 Этот промокод уже активировало максимальное количество пользователей")
        conn.close()
        return
        
    used_users_list.append(str(user_id))
    new_used_users_str = ",".join(used_users_list)
    new_uses = current_uses + 1
    
    cursor.execute('UPDATE promocodes SET current_uses = ?, used_users = ? WHERE code = ?', (new_uses, new_used_users_str, code_candidate))
    
    user_data = get_user_data(user_id)
    update_user_balance(user_id, user_data['balance'] + amount)
    conn.commit()
    conn.close()
    
    bot.reply_to(message, f"🎉 {mention}, вы успешно активировали промокод <code>#{code_candidate}</code>\n🏆 Награда: <b>{fmt(amount)} {CURRENCY}</b>")


# --- Команда СТАВКИ ---
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['ставки', '/ставки'])
def show_active_bets(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    init_user(message.from_user)
    mention = get_mention(user_id)
    
    if chat_id not in active_bets or user_id not in active_bets[chat_id] or not active_bets[chat_id][user_id]:
        bot.send_message(chat_id, f"Не найдено ни одной активной ставки {mention}")
        return
        
    lines = [f"🎰 <b>Активные ставки игрока {mention}:</b>\n"]
    for bet in active_bets[chat_id][user_id]:
        lines.append(f"Ставка: <b>{fmt(bet['amount'])} {CURRENCY}</b> на <code>{bet['display']}</code>")
            
    bot.send_message(chat_id, "\n".join(lines))

# --- Прием текстовых ставок ---
@bot.message_handler(func=is_bet_message)
def place_bet(message: types.Message):
    chat_id = message.chat.id
    global is_spinning, round_start_time
    if chat_id in is_spinning and is_spinning[chat_id]: return  

    init_user(message.from_user)
    user_id = message.from_user.id
    tokens = message.text.lower().split()
    
    try: bet_amount = int(tokens[0])
    except: return
    if bet_amount <= 0: return

    bet_targets = tokens[1:]
    if not bet_targets: return

    parsed_targets = []
    for target in bet_targets:
        grid_type = None
        if target in ['к', 'красный', 'red']:
            grid_type, display_name, multiplier = 'color_red', 'RED', 2
        elif target in ['ч', 'черный', 'черный', 'black']:
            grid_type, display_name, multiplier = 'color_black', 'BLACK', 2
        elif target in ['одд', 'odd', 'нечетное', 'нечётное', 'одщ']:
            grid_type, display_name, multiplier = 'size_odd', 'ODD', 2
        elif target in ['евен', 'even', 'четное', 'чётное']:
            grid_type, display_name, multiplier = 'size_even', 'EVEN', 2
        elif '-' in target:
            try:
                start, end = map(int, target.split('-'))
                if 0 <= start <= 36 and 0 <= end <= 36 and start < end:
                    grid_type = f"range_{start}_{end}"
                    display_name = f"{start}-{end}"
                    multiplier = round(36 / ((end - start) + 1), 2)
            except: pass
        elif target.isdigit():
            num = int(target)
            if 0 <= num <= 36:
                grid_type, display_name, multiplier = f"num_{num}", f"{num}", 36

        if grid_type:
            parsed_targets.append({'type': grid_type, 'display': display_name, 'mult': multiplier, 'amount': bet_amount})

    if not parsed_targets: return

    combined_dict = {}
    for b in parsed_targets:
        key = b['type']
        if key not in combined_dict:
            combined_dict[key] = b.copy()
        else:
            combined_dict[key]['amount'] += bet_amount

    responses = apply_bets_safely(chat_id, user_id, combined_dict.values())
    
    if responses:
        if chat_id not in round_start_time or round_start_time[chat_id] == 0:
            round_start_time[chat_id] = time.time()
        bot.send_message(chat_id, "\n".join(responses))

# --- Команда ГО ---
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['го', 'гоу', 'go'])
def spin_roulette(message: types.Message):
    chat_id = message.chat.id
    global is_spinning, round_start_time
    if chat_id in is_spinning and is_spinning[chat_id]: return  

    init_user(message.from_user)
    mention = get_mention(message.from_user.id)

    if chat_id not in active_bets or not any(active_bets[chat_id].values()):
        bot.send_message(chat_id, f"Не найдено ни одной активной ставки {mention}")
        return

    current_time = time.time()
    elapsed_time = current_time - round_start_time.get(chat_id, 0)
    if elapsed_time < ROUND_DURATION:
        time_left = int(ROUND_DURATION - elapsed_time)
        bot.send_message(chat_id, f"Ошибка. Закончить раунд можно через {time_left} секунд")
        return

    is_spinning[chat_id] = True
    try: gif_msg = bot.send_animation(chat_id, GIF_URL)
    except: gif_msg = bot.send_message(chat_id, "🎰 Рулетка крутится...")

    time.sleep(5)
    try: bot.delete_message(chat_id, gif_msg.message_id)
    except: pass

    winning_number = random.randint(0, 36)
    save_win_number(winning_number) 
    
    emoji, color_text, parity = ROULETTE_MAP[winning_number]
    result_headline = f"<b>Рулетка: {winning_number}{emoji}</b>"

    lines_all_bets = []
    lines_winners = []

    if chat_id not in last_bets_db: last_bets_db[chat_id] = {}

    for uid, bets in active_bets[chat_id].items():
        if bets: last_bets_db[chat_id][uid] = bets

    for user_id, bets in list(active_bets[chat_id].items()):
        player_mention = get_mention(user_id)
        player_data = get_user_data(user_id)
        current_balance = player_data['balance']
        
        round_bets_sum, round_losses_sum, round_max_win = 0, 0, 0
        
        for bet in bets:
            lines_all_bets.append(f"{player_mention} {fmt(bet['amount'])} {CURRENCY} на {bet['display']}")
            round_bets_sum += bet['amount']
            is_win = False
            b_type = bet['type']
            
            if b_type == 'color_red' and color_text == 'RED': is_win = True
            elif b_type == 'color_black' and color_text == 'BLACK': is_win = True
            elif b_type == 'size_odd' and parity == 'odd': is_win = True
            elif b_type == 'size_even' and parity == 'even': is_win = True
            elif b_type.startswith('num_'):
                if winning_number == int(b_type.replace('num_', '')): is_win = True
            elif b_type.startswith('range_'):
                _, start, end = b_type.split('_')
                if int(start) <= winning_number <= int(end): is_win = True

            if is_win:
                win_sum = int(bet['amount'] * bet['mult'])
                current_balance += win_sum
                if win_sum > round_max_win: round_max_win = win_sum
                lines_winners.append(f"{player_mention} ставка {fmt(bet['amount'])} {CURRENCY} выиграл {fmt(win_sum)} на {bet['display']}")
            else:
                round_losses_sum += bet['amount']
                
        update_user_balance(user_id, current_balance)
        update_user_stats(user_id, round_bets_sum, round_losses_sum, round_max_win)

    active_bets[chat_id].clear()
    round_start_time[chat_id] = 0

    final_response = [result_headline]
    if lines_all_bets: final_response.extend(lines_all_bets)
    if lines_winners:
        final_response.append("")
        final_response.extend(lines_winners)

    markup = types.InlineKeyboardMarkup()
    btn_repeat = types.InlineKeyboardButton("🔁 Повторить", callback_data="btn_repeat", style="primary")
    btn_double = types.InlineKeyboardButton("✖2 Удвоить", callback_data="btn_double", style="primary")
    markup.row(btn_repeat, btn_double)

    bot.send_message(chat_id, "\n".join(final_response), reply_markup=markup)
    is_spinning[chat_id] = False

@bot.callback_query_handler(func=lambda call: call.data in ["btn_repeat", "btn_double"])
def handle_rebet_buttons(call: types.CallbackQuery):
    chat_id = call.message.chat.id
    global is_spinning, round_start_time
    if chat_id in is_spinning and is_spinning[chat_id]:
        bot.answer_callback_query(call.id, "❌ Дождитесь завершения текущего раунда!")
        return
    user_id = call.from_user.id
    if chat_id not in last_bets_db or user_id not in last_bets_db[chat_id] or not last_bets_db[chat_id][user_id]:
        bot.answer_callback_query(call.id, "❌ У вас нет истории ставок для повтора!", show_alert=True)
        return
    factor = 1 if call.data == "btn_repeat" else 2
    
    adjusted_bets = []
    for bet in last_bets_db[chat_id][user_id]:
        new_bet = bet.copy()
        new_bet['amount'] *= factor
        adjusted_bets.append(new_bet)

    responses = apply_bets_safely(chat_id, user_id, adjusted_bets)
    if responses:
        if chat_id not in round_start_time or round_start_time[chat_id] == 0:
            round_start_time[chat_id] = time.time()
        bot.send_message(chat_id, "\n".join(responses))
        
    bot.answer_callback_query(call.id)

# --- Остальные команды ---
@bot.message_handler(commands=['start'])
def cmd_start(message: types.Message):
    init_user(message.from_user)
    if message.chat.type == 'private':
        markup = get_private_keyboard()
        tokens = message.text.split()
        if len(tokens) > 1 and tokens[1] == 'bonus':
            result_text = process_claim_bonus(message.from_user.id)
            bot.send_message(message.chat.id, result_text, reply_markup=markup)
        else:
            bot.send_message(message.chat.id, "Привет! Здесь ты можешь забрать свой ежедневный бонус.", reply_markup=markup)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text == "🎁 Бонус")
def private_bonus(message: types.Message):
    init_user(message.from_user)
    result_text = process_claim_bonus(message.from_user.id)
    bot.send_message(message.chat.id, result_text, reply_markup=get_private_keyboard())

@bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text in ["👤 Профиль", "профиль", "/профиль"])
def private_profile(message: types.Message):
    init_user(message.from_user)
    data = get_user_data(message.from_user.id)
    response = (
        f"👤 Ник: {data['name']}\n🆔 ID: {message.from_user.id}\n💰 Баланс: {fmt(data['balance'])} {CURRENCY}\n"
        f"🎰 Все ставки: {fmt(data['total_bets'])} {CURRENCY}\n🏆 Самый большой выигрыш: {fmt(data['max_win'])} {CURRENCY}"
    )
    bot.send_message(message.chat.id, response, reply_markup=get_private_keyboard())

# --- Команда Б (Баланс) с Умной Кнопкой Бонуса ---
@bot.message_handler(func=lambda m: m.text and m.text.lower() == 'б')
def check_balance(message: types.Message):
    init_user(message.from_user)
    user_id = message.from_user.id
    user_data = get_user_data(user_id)
    mention = get_mention(user_id)
    
    bot_info = bot.get_me()
    bonus_url = f"t.me/{bot_info.username}?start=bonus"
    
    markup = None
    # НОВОЕ ПРАВИЛО: Кнопка бонуса горит зеленым ТОЛЬКО до 69 999 VIR и если бонус доступен
    if user_data['balance'] <= 69999 and check_bonus_available(user_id):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🎁 Бонус", url=bonus_url, style="success"))
        
    bot.send_message(message.chat.id, f"{mention}\n💰 Баланс: {fmt(user_data['balance'])} {CURRENCY}", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text and m.text.lower() == 'отмена')
def cancel_bets(message: types.Message):
    chat_id = message.chat.id
    global is_spinning, round_start_time
    if chat_id in is_spinning and is_spinning[chat_id]: return  
    init_user(message.from_user)
    user_id = message.from_user.id
    mention = get_mention(user_id)
    if chat_id not in active_bets or user_id not in active_bets[chat_id] or not active_bets[chat_id][user_id]:
        bot.send_message(chat_id, f"Не найдено ни одной активной ставки {mention}")
        return
    returned_amount = sum(bet['amount'] for bet in active_bets[chat_id][user_id])
    user_data = get_user_data(user_id)
    update_user_balance(user_id, user_data['balance'] + returned_amount)
    active_bets[chat_id][user_id] = []
    if not any(active_bets[chat_id].values()): round_start_time[chat_id] = 0
    bot.send_message(chat_id, f"Ставки отменены {mention}")

@bot.message_handler(func=lambda m: m.text and m.text.lower() == 'лог')
def show_log(message: types.Message):
    history = get_roulette_history()
    if not history:
        bot.send_message(message.chat.id, "📋 История рулетки пока пуста.")
        return
    history_lines = [f"{num}{ROULETTE_MAP[num][0]}" for num in history]
    bot.send_message(message.chat.id, "\n".join(history_lines))

@bot.message_handler(func=lambda m: m.chat.type != 'private' and m.text and m.text.lower() in ['топ', '/топ', 'top', '/top'])
def show_chat_top(message: types.Message):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, balance, name FROM users ORDER BY balance DESC')
    all_wealthy = cursor.fetchall()
    conn.close()
    top_lines = []
    rank = 1
    for row in all_wealthy:
        uid, bal, name = row
        try:
            member = bot.get_chat_member(message.chat.id, uid)
            if member.status in ['creator', 'administrator', 'member']:
                top_lines.append(f"{rank}. {name}  {fmt(bal)} {CURRENCY}")
                rank += 1
            if rank > 16: break
        except: continue
    bot.send_message(message.chat.id, "\n".join(top_lines) if top_lines else "Топ чата пуст.")

# --- Переводы валюты ---
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('п '))
def transfer_currency(message: types.Message):
    init_user(message.from_user)
    sender_id = message.from_user.id
    tokens = message.text.split()
    target_id, amount, comment = None, 0, ""
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        try:
            amount = int(tokens[1])
            if len(tokens) > 2: comment = " ".join(tokens[2:])
        except: return
    else:
        if len(tokens) < 3: return
        try:
            target_id = int(tokens[1])
            amount = int(tokens[2])
            if len(tokens) > 3: comment = " ".join(tokens[3:])
        except: return
    if sender_id == target_id or amount <= 0: return
    sender_data = get_user_data(sender_id)
    
    if sender_data['balance'] < amount:
        bot.send_message(message.chat.id, f"❌ Недостаточно {CURRENCY} для перевода!")
        return
        
    target_data = get_user_data(target_id)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (target_id,))
    if not cursor.fetchone():
        bot.send_message(message.chat.id, "❌ Этот пользователь еще не зарегистрирован!")
        conn.close()
        return
    conn.close()
    update_user_balance(sender_id, sender_data['balance'] - amount)
    update_user_balance(target_id, target_data['balance'] + amount)
    if comment: comment = re.sub(r'\bgram\b', CURRENCY, comment, flags=re.IGNORECASE)
    response = f"{get_mention(sender_id)} перевел {fmt(amount)} {CURRENCY} для {get_mention(target_id)}"
    if comment: response += f"\n💬 {comment}"
    bot.send_message(message.chat.id, response)

@bot.message_handler(func=lambda m: m.reply_to_message and m.from_user.id == ADMIN_ID and m.text.startswith('+'))
def add_balance_admin(message: types.Message):
    try:
        amount = int(message.text.replace('+', '').strip())
        target_user = message.reply_to_message.from_user
        init_user(target_user)
        update_user_balance(target_user.id, get_user_data(target_user.id)['balance'] + amount)
        bot.send_message(message.chat.id, f"✅ Администратор начислил {fmt(amount)} {CURRENCY} пользователю {get_mention(target_user.id)}!")
    except: pass


# --- ОБНОВЛЕННАЯ ИГРА МИНЫ ---

def make_mines_keyboard(user_id, game, state="play"):
    markup = types.InlineKeyboardMarkup()
    for i in range(5):
        row_btns = []
        for j in range(5):
            idx = i * 5 + j
            callback_data = f"mine_click_{user_id}_{idx}"
            
            if state == "play":
                if idx in game["uncovered"]:
                    # Нажатые безопасные ячейки подсвечиваются СИНИМ и БЕЗ СМАЙЛИКОВ внутри
                    row_btns.append(types.InlineKeyboardButton(" ", callback_data=callback_data, style="primary"))
                else: 
                    row_btns.append(types.InlineKeyboardButton("❓", callback_data=callback_data))
            elif state == "lose":
                if idx in game["mines"]: 
                    # Показываем только мины
                    row_btns.append(types.InlineKeyboardButton("💣", callback_data="mine_disabled"))
                else: 
                    # Безопасные остаются полностью пустыми и без расцветки
                    row_btns.append(types.InlineKeyboardButton(" ", callback_data="mine_disabled"))
            else: # win
                if idx in game["uncovered"]:
                    row_btns.append(types.InlineKeyboardButton("✔️", callback_data="mine_disabled", style="success"))
                else:
                    row_btns.append(types.InlineKeyboardButton(" ", callback_data="mine_disabled"))
                    
        markup.row(*row_btns)
        
    if state == "play":
        count = len(game["uncovered"])
        if count == 0: 
            # НОВОЕ ПРАВИЛО: Если 0 открытых ячеек, горит КРАСНАЯ кнопка отмены
            markup.row(types.InlineKeyboardButton("❌ Отмена", callback_data=f"mine_claim_{user_id}", style="danger"))
        else: 
            # Если открыл хотя бы одну, она превращается в ЗЕЛЕНУЮ кнопку забрать выигрыш
            markup.row(types.InlineKeyboardButton("💸 Забрать выигрыш", callback_data=f"mine_claim_{user_id}", style="success"))
    return markup

@bot.message_handler(func=lambda m: m.chat.type != 'private' and m.text and m.text.lower().startswith('мины '))
def start_mines_game(message: types.Message):
    init_user(message.from_user)
    user_id = message.from_user.id
    tokens = message.text.split()
    if db_get_mine_game(user_id) is not None:
        bot.reply_to(message, "❌ У вас уже запущена игра! Закончите её.")
        return
    try: bet_amount = int(tokens[1])
    except: return
    if bet_amount <= 0: return
    user_data = get_user_data(user_id)
    if user_data['balance'] < bet_amount:
        bot.reply_to(message, f"❌ Недостаточно баланса! Ваш баланс: {fmt(user_data['balance'])} {CURRENCY}")
        return
    update_user_balance(user_id, user_data['balance'] - bet_amount)
    game_data = {"chat_id": message.chat.id, "bet": bet_amount, "mines": random.sample(range(25), 6), "uncovered": [], "message_id": 0}
    sent_msg = bot.send_message(message.chat.id, f"{get_mention(user_id)}, вы начали игру минное поле!\n💰Ставка: {fmt(bet_amount)} {CURRENCY}", reply_markup=make_mines_keyboard(user_id, game_data, "play"))
    game_data["message_id"] = sent_msg.message_id
    db_save_mine_game(user_id, game_data)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mine_"))
def handle_mines_callbacks(call: types.CallbackQuery):
    user_id = call.from_user.id
    data_parts = call.data.split("_")
    action = data_parts[1]
    if action == "disabled": return
    target_user_id = int(data_parts[2])
    if user_id != target_user_id:
        bot.answer_callback_query(call.id, "❌ Это не ваша игра!", show_alert=True)
        return
    game = db_get_mine_game(user_id)
    if game is None: return
    mention = get_mention(user_id)

    if action == "click":
        idx = int(data_parts[3])
        if idx in game["uncovered"]: return
        if idx in game["mines"]:
            update_user_stats(user_id, game["bet"], game["bet"], 0)
            try: bot.edit_message_text(f"{mention}, вы начали игру минное поле!\n💰Ставка: {fmt(game['bet'])} {CURRENCY}\n💥 Вы подорвались на мине!", game["chat_id"], game["message_id"], reply_markup=make_mines_keyboard(user_id, game, "lose"))
            except: pass
            db_delete_mine_game(user_id) 
            return
        game["uncovered"].append(idx)
        mult = MINES_MULTIPLIERS[len(game["uncovered"])]
        try: bot.edit_message_text(f"{mention}, вы начали игру минное поле!\n💰Ставка: {fmt(game['bet'])} {CURRENCY}\n💵Выигрыш: x{mult} | {fmt(int(game['bet']*mult))} {CURRENCY}", game["chat_id"], game["message_id"], reply_markup=make_mines_keyboard(user_id, game, "play"))
        except: pass
        db_save_mine_game(user_id, game)
    elif action == "claim":
        count = len(game["uncovered"])
        if count == 0: 
            # НОВОЕ ПРАВИЛО: Моментальный возврат ставки, если ничего не открыл
            update_user_balance(user_id, get_user_data(user_id)['balance'] + game["bet"])
            try: bot.edit_message_text(f"{mention}, игра отменена, ставка возвращена на баланс.", game["chat_id"], game["message_id"])
            except: pass
            db_delete_mine_game(user_id)
            return
        win_cash = int(game["bet"] * MINES_MULTIPLIERS[count])
        update_user_balance(user_id, get_user_data(user_id)['balance'] + win_cash)
        update_user_stats(user_id, game["bet"], 0, win_cash)
        try: bot.edit_message_text(f"{mention}, вы забрали выигрыш!\n💰Сумма: {fmt(win_cash)} {CURRENCY}", game["chat_id"], game["message_id"], reply_markup=make_mines_keyboard(user_id, game, "win"))
        except: pass
        db_delete_mine_game(user_id)

if __name__ == '__main__':
    init_db() 
    bot.infinity_polling(skip_pending=True)
