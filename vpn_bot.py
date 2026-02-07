import telebot
from telebot import types
import requests
import time
import json
import os
from datetime import datetime, timedelta, timezone
from functools import partial 
import threading


BOT_TOKEN = os.getenv("BOT_TOKEN")
YOOKASSA_TOKEN = os.getenv("YOOKASSA_TOKEN")
MARZBAN_URL = os.getenv("MARZBAN_URL")

ADMIN_IDS = [1000649034, 1835304379]

DATA_FILE = "data/users_data.json"
PROMOCODES_FILE = "data/promocodes.json"

MARZBAN_ADMIN_USERNAME = "root"
MARZBAN_ADMIN_PASSWORD = "toor"



bot = telebot.TeleBot(BOT_TOKEN)

# Цены на тарифы (в рублях)
PRICES = {
    '1-month': {'price': 150, 'title': '1 месяц', 'description': 'VoidLink VPN на 1 месяц'},
    '2-months': {'price': 250, 'title': '2 месяца', 'description': 'VoidLink VPN на 2 месяца'},
    '4-months': {'price': 400, 'title': '4 месяца', 'description': 'VoidLink VPN на 4 месяца'},
    '6-months': {'price': 500, 'title': '6 месяцев', 'description': 'VoidLink VPN на 6 месяцев'},
}

pending_referrer_by_user = {}

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_users_data():
    return load_json(DATA_FILE, {})


def save_users_data(data):
    save_json(DATA_FILE, data)


def get_promocodes():
    # пример: {"MELL": {"days": 14, "active": True}}
    return load_json(PROMOCODES_FILE, {
        "MELL": {"days": 14, "active": True}
    })


def save_promocodes(data):
    save_json(PROMOCODES_FILE, data)

def get_or_create_user(user):
    users = get_users_data()
    uid = str(user.id)
    if uid not in users:
        users[uid] = {
            "telegram_username": user.username,
            "trial_used": False,
            "promo_used": [],
            "tariff_expire": "",           # новое поле
            "ref_free_keys": 0,
            "balance": 0.0,  
            "referred_by": None,
            "ref_bonus_paid": False,                      
        }
        save_users_data(users)
    else:
        changed = False
        if users[uid].get("telegram_username") != user.username:
            users[uid]["telegram_username"] = user.username
            changed = True
        if "tariff_expire" not in users[uid]:
            users[uid]["tariff_expire"] = ""
            changed = True
        if "ref_free_keys" not in users[uid]:
            users[uid]["ref_free_keys"] = 0
            changed = True
        if changed:
            save_users_data(users)
    return users[uid]


def process_promo_input(message):
    code = message.text.strip().upper()

    promocodes = get_promocodes()
    if code not in promocodes or not promocodes[code].get("active", True):
        bot.send_message(message.chat.id, "❌ Неверный или неактивный промокод")
        return

    user = message.from_user
    user_record = get_or_create_user(user)
    uid = str(user.id)

    used_codes = user_record.get("promo_used", [])

    if code in used_codes:
        bot.send_message(message.chat.id, "❌ Этот промокод уже был активирован на вашем аккаунте")
        return

    days = int(promocodes[code].get("days", 14))
    user_id = message.from_user.id
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📱 Выбрать устройство", callback_data=f"ask_device_{days}_promo"))

    bot.send_message(
        message.chat.id,
        f"✅ Промокод {code} принят! Для получения ключа выберите ваше устройство:",
        reply_markup=keyboard
)


def delayed_check_activity(user_id, username):
    """Проверка активности через 3 часа"""
    time.sleep(3 * 3600)  # Спим 3 часа
    
    try:
        token = get_marzban_token()
        url = f"{MARZBAN_URL}/api/user/{username}"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers, timeout=10, verify=False)
        
        if resp.status_code == 200:
            data = resp.json()
            # Если трафик 0, значит не подключился
            if data.get("used_traffic", 0) == 0:
                bot.send_message(
                    user_id, 
                    "👋 Вижу, вы до сих пор не начали использовать VPN. У вас возникли какие-то проблемы? Напишите в нашу поддержку: @suppVoidLink",
                    parse_mode="HTML"
                )
    except Exception as e:
        print(f"Ошибка в delayed_check: {e}")

def auto_check_expiry():
    """Фоновая проверка срока годности подписки"""
    while True:
        users = get_users_data()
        now = datetime.now(timezone.utc)
        changed = False

        for uid, data in users.items():
            expire_str = data.get("tariff_expire")
            # Проверяем только если дата есть и уведомление еще не было отправлено
            if expire_str and not data.get("expiry_notified", False):
                expire_dt = datetime.fromisoformat(expire_str)
                
                if now > expire_dt:
                    try:
                        bot.send_message(
                            int(uid), 
                            "⚠️ Ваша подписка закончилась. Не хотите ли её продлить?",
                            reply_markup=types.InlineKeyboardMarkup().add(
                                types.InlineKeyboardButton("🔄 Продлить", callback_data="show_tariffs")
                            )
                        )
                        users[uid]["expiry_notified"] = True # Чтобы отправилось 1 раз
                        changed = True
                    except:
                        pass
            
            # Если купили новую подписку, сбрасываем флаг уведомления (в логику оплаты)
        
        if changed:
            save_users_data(users)
            
        time.sleep(3600) # Проверка раз в час

# Запуск потока проверки
threading.Thread(target=auto_check_expiry, daemon=True).start()


def give_vpn_access(user_id: int, days: int, reason: str):
    VLESS_TEMPLATE = (
    "vless://{uuid}@150.241.80.64:443"
    "?security=reality&type=tcp&headerType=&path=&host="
    "&sni=github.com&fp=chrome"
    "&pbk=x2J3YWBFpEnYr_EMxYXxvfVw57gsyjTEIkTBW8lcTQ8"
    "&sid=3ab57f27db18f735"
    "#🚀 VoidLink ({label}) [VLESS - tcp]"
)

    username = f"{user_id}_{int(time.time())}"

    # подбираем срок по тарифу
    user = create_marzban_user(username, days=days)
    
    uuid = user["proxies"]["vless"]["id"]
    try:
        vless_link = VLESS_TEMPLATE.format(
    uuid=uuid,
    label=username.split('_')[0]  # или что ты хочешь в названии
)
    except Exception as e:
        bot.send_message(
            user_id,
            "❌ Ошибка при выдаче ключа. Напишите в поддержку: @suppVoidLink",
        )
        print("MARZBAN ERROR:", e)
        return

    success_text = f"""
✅ Доступ к VPN выдан ({reason})
Срок: {days} дней

Ваш ключ:

<code>{vless_link}</code>

💬 Возникли вопросы? Пишите в поддержку
"""

    keyboard = types.InlineKeyboardMarkup()

    btn_support = types.InlineKeyboardButton(
        text="💬 Поддержка",
        url="https://t.me/suppVoidLink"
    )

    btn_guide = types.InlineKeyboardButton(
        text="📱 Инструкция по подключению",
        url="https://telegra.ph/Gajd-na-podklyuchenie-k-VoidLink-02-01"
    )

    keyboard.add(btn_support)
    keyboard.add(btn_guide)

    bot.send_message(
        user_id,
        success_text,
        parse_mode='HTML',
        reply_markup=keyboard
    )

    users = get_users_data()
    uid = str(user_id)
    expire_dt = datetime.now(timezone.utc) + timedelta(days=days)
    users[uid]["tariff_expire"] = expire_dt.isoformat()
    save_users_data(users)
    threading.Thread(target=delayed_check_activity, args=(user_id, username)).start()

    for admin_id in ADMIN_IDS:
        bot.send_message(
            admin_id,
            f"✅ Новая выдача доступа: User {user_id}, причина: {reason}, срок {days} дней",
            parse_mode='HTML',
        )

    print(f"✅ Новая выдача: User {user_id}, причина {reason}, срок {days} дней")
    # --------- КОНЕЦ: твой существующий код ---------




def get_marzban_token():
    url = f"{MARZBAN_URL}/api/admin/token"
    
    # ВАЖНО: form-data (x-www-form-urlencoded), grant_type=password
    data = {
        "grant_type": "password",
        "username": MARZBAN_ADMIN_USERNAME,
        "password": MARZBAN_ADMIN_PASSWORD,
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }

    resp = requests.post(url, data=data, headers=headers, timeout=10, verify=False)
    print("TOKEN STATUS:", resp.status_code, resp.text)  # для отладки
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_marzban_user(username: str, days: int = 30,) -> str:
    token = get_marzban_token()
    url = f"{MARZBAN_URL}/api/user"

    expire = None
    if days > 0:
        # Marzban ждёт expire как UTC timestamp (секунды) [web:107]
        import time, datetime
        expire_dt = datetime.datetime.utcnow() + datetime.timedelta(days=days)
        expire = int(expire_dt.timestamp())

    body = {
        "username": username,
        "proxies": {
            "vless": {}  # пустой объект → Marzban сам сгенерит uuid/пароль [web:108]
        },
        "expire": expire,
        "status": "active",
        "inbounds": {
        "vless": ["VLESS TCP REALITY"]  # ← ID или имя твоего инбаунда
    },
    }

    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(url, json=body, headers=headers, timeout=10)
    resp.raise_for_status()


    url = f"{MARZBAN_URL}/api/user/{username}"
    resp = requests.get(url, headers=headers, timeout=10, verify=False)
    resp.raise_for_status()
    data = resp.json()


    # В ответе обычно есть links и/или subscription_url [web:107]
    return data




@bot.message_handler(commands=['start'])
def start(message):
    """Обработчик команды /start"""
    user_id = str(message.from_user.id)
    user_record = get_or_create_user(message.from_user)
    
    # Проверяем, есть ли в команде /start ID пригласившего
    args = message.text.split()
    if len(args) > 1:
        referrer_id = args[1]
        users = get_users_data()
        
        # Если пользователь новый и не сам себя пригласил
        if users[user_id].get("referred_by") is None and referrer_id != user_id:
            if referrer_id in users:
                users[user_id]["referred_by"] = referrer_id
                save_users_data(users)
                bot.send_message(user_id, "🎁 Вы перешли по реферальной ссылке!")
    

    welcome_text = """
👋 Добро пожаловать в <b>VoidLink</b>, пользователь!

💨 Высокая скорость
👾 Доступ ко всем сайтам
🗓️ Три дня бесплатно!

👫 Пригласите друзей в наш сервис, за это вы получаете 60% от их покупок!

📌 <b>Обязательно (!!)</b>
<a href='https://t.me/voidlinkvpn'>Подпишитесь на наш канал</a>

⚡️ Подключение мгновенное после оплаты!
<b>Поддержка:</b> <a href='https://t.me/suppVoidLink'>VoidLink Support</a>
    """

    # Создание клавиатуры с кнопками
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    btn_buy = types.InlineKeyboardButton(text="💳 Купить VPN", callback_data="show_tariffs")
    btn_trial = types.InlineKeyboardButton(text="🎁 Пробный период", callback_data="promo_free_trial")
    btn_promo = types.InlineKeyboardButton(text="🏷 Ввести промокод", callback_data="promo_enter")
    
    # Кнопки рефералки и юр. инфо
    btn_ref = types.InlineKeyboardButton(text="👥 Реферальная система", callback_data="ref_system")
    btn_legal = types.InlineKeyboardButton(text="📄 Юр. информация", callback_data="legal_info")
    keyboard.add(btn_buy)
    keyboard.add(btn_trial, btn_promo)
    keyboard.add(btn_ref)
    keyboard.add(btn_legal)
 

    with open('banner.jpg', 'rb') as photo:
        bot.send_photo(message.chat.id, photo, caption=welcome_text, parse_mode='HTML', reply_markup=keyboard)


@bot.callback_query_handler(func=lambda call: call.data == "legal_info")
def legal_info(call):
    text = (
        "Продолжая пользоваться нашим ботом, вы соглашаетесь с нашей "
        "политикой конфиденциальности и договором оферты."
    )

    keyboard = types.InlineKeyboardMarkup()
    btn_policy = types.InlineKeyboardButton(
        text="🔐 Политика конфиденциальности",
        url="https://telegra.ph/Politika-konfidencialnosti-01-09-56"  # поставь реальные ссылки
    )
    btn_offer = types.InlineKeyboardButton(
        text="📃 Договор оферты",
        url="https://telegra.ph/Dogovor-oferty-01-09-4"
    )
    btn_back = types.InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data="back_to_start"
    )

    keyboard.add(btn_policy)
    keyboard.add(btn_offer)
    keyboard.add(btn_back)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        print(f"Ошибка удаления: {e}")
    bot.send_message(
        call.message.chat.id,
        text,
        reply_markup=keyboard
    )


@bot.callback_query_handler(func=lambda call: call.data == "promo_free_trial")
def handle_free_trial(call):
    user = call.from_user
    user_record = get_or_create_user(user)

    if user_record.get("trial_used"):
        bot.answer_callback_query(call.id, "❌ Пробный период уже использован")
        bot.send_message(
            call.message.chat.id,
            "❌ Вы уже активировали бесплатный период. Выберите платный тариф."
        )
        return

    users = get_users_data()
    uid = str(user.id)
    users[uid]["trial_used"] = True
    save_users_data(users)
    
    call.data = "ask_device_3_trial"
    ask_device(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith("ask_device_"))
def ask_device(call):
    # Извлекаем количество дней и причину из callback_data (например, ask_device_3_trial)
    parts = call.data.split("_")
    days = parts[2]
    reason_key = parts[3] # 'trial' или 'promo'

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    # Передаем данные дальше в кнопки
    keyboard.add(
        types.InlineKeyboardButton("🍏 iOS", callback_data=f"guide_ios_{days}_{reason_key}"),
        types.InlineKeyboardButton("🤖 Android", callback_data=f"guide_and_{days}_{reason_key}"),
        types.InlineKeyboardButton("💻 PC", callback_data=f"guide_pc_{days}_{reason_key}")
    )

    bot.edit_message_text(
        "📱 <b>Выберите ваше устройство:</b>\nЧтобы мы подобрали правильную инструкцию.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=keyboard
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("guide_"))
def show_guide(call):
    # Формат: guide_ios_3_trial
    parts = call.data.split("_")
    device = parts[1]
    days = parts[2]
    reason_key = parts[3]

    # Ссылки на ваши гайды
    guides = {
        "ios": "https://apps.apple.com/us/app/v2raytun/id6476628951", 
        "and": "https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru",
        "pc": "http://v2raytun.com/"
    }

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ Я установил(а)!", callback_data=f"final_give_{days}_{reason_key}"))

    bot.edit_message_text(
        f"📖 <b>Скачать приложение можно по этой ссылке:</b>\n{guides.get(device, '')}\n\n"
        "Установите его, а затем нажмите на кнопку ниже, чтобы получить ваш ключ.",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="HTML",
        reply_markup=keyboard
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("final_give_"))
def final_give(call):
    parts = call.data.split("_")
    days = int(parts[2])
    reason_key = parts[3]
    
    reason = "бесплатный период" if reason_key == "trial" else "промокод"
    
    # Удаляем сообщение с инструкцией, чтобы выдать ключ
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass

    # ВЫЗОВ ВАШЕЙ ФУНКЦИИ ИЗ ФАЙЛА
    give_vpn_access(call.from_user.id, days, reason)


@bot.callback_query_handler(func=lambda call: call.data == "promo_enter")
def promo_enter(call):
    msg = bot.send_message(
        call.message.chat.id,
        "Введите промокод:"
    )
    bot.register_next_step_handler(msg, process_promo_input)

@bot.callback_query_handler(func=lambda call: call.data == "ref_system")
def ref_system(call):
    user = call.from_user
    user_record = get_or_create_user(user)
    uid = str(user.id)
    users = get_users_data()
    balance = users[uid].get("balance", 0)
    bot_username = bot.get_me().username
    ref_link = f"https://t.me/{bot_username}?start={uid}"

    text = (
        f"👥 <b>Реферальная система</b>\n\n"
        f"Ваш баланс: <b>{balance} руб.</b>\n"
        f"Для вывода средств напишите нашему саппорту, либо обменяйте 100р на месяц нашего VPN\n"
        f"Ваша доля: <b>60%</b> от каждой покупки друга\n\n"
        f"Ваша ссылка для приглашения:\n<code>{ref_link}</code>"
    )

    

    keyboard = types.InlineKeyboardMarkup()
    btn_support = types.InlineKeyboardButton(
        text="💸 Вывести деньги",
        url="https://t.me/suppVoidLink"
    )
    btn_get_key = types.InlineKeyboardButton(
        text="🗝 Обменять 100р на подписку",
        callback_data="ref_get_key"
    )
    btn_back = types.InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start")
    keyboard.add(btn_get_key)
    keyboard.add(btn_support)
    keyboard.add(btn_back)

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="HTML",
        reply_markup=keyboard
    )
@bot.callback_query_handler(func=lambda call: call.data == "ref_get_key")
def ref_get_key(call):
    user = call.from_user
    uid = str(user.id)
    users = get_users_data()
    user_record = get_or_create_user(user)
    balance = users[uid].get("balance", 0.0)
    exchange_cost = 100.0  # Цена обмена

    if balance < exchange_cost:
        bot.answer_callback_query(call.id, f"❌ Недостаточно средств (нужно {exchange_cost} руб.)", show_alert=True)
        return
    users[uid]["balance"] = round(balance - exchange_cost, 2)
    save_users_data(users)
    # Выдаем 30 дней (месяц)
    give_vpn_access(call.from_user.id, 30, "обмен баланса на 1 месяц")

    bot.answer_callback_query(call.id, "✅ Успешно! Вам начислен 1 месяц VPN.", show_alert=True)
    
    # Сразу обновляем текст меню рефералки, чтобы увидеть новый баланс
    ref_system(call)




@bot.callback_query_handler(func=lambda call: call.data == 'show_tariffs')
def show_tariffs(call):
    """Показать тарифы для выбора"""


    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for key, value in PRICES.items():
        btn = types.InlineKeyboardButton(
            text=f"💎 {value['title']} - {value['price']}₽",
            callback_data=f"buy_{key}"
        )
        keyboard.add(btn)

    btn_back = types.InlineKeyboardButton(
        text="◀️ Назад",
        callback_data="back_to_start"
    )
    keyboard.add(btn_back)

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    bot.send_message(
        call.message.chat.id,
        "Выберите подходящий тариф:",
        parse_mode='HTML',
        reply_markup=keyboard
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def process_payment(call):
    tariff = call.data.replace('buy_', '')

    if tariff not in PRICES:
        bot.answer_callback_query(call.id, "❌ Неизвестный тариф")
        return

    price_info = PRICES[tariff]
    user_id = call.from_user.id

    prices = [types.LabeledPrice(
        label=price_info['title'],
        amount=price_info['price'] * 100  # Копейки
    )]

    # Удаляем старое меню с тарифами
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass

    bot.send_invoice(
        chat_id=call.message.chat.id,
        title=f"VoidLink - {price_info['title']}",
        description=price_info['description'],
        invoice_payload=f"{tariff}_{user_id}",
        provider_token=YOOKASSA_TOKEN,
        currency='RUB',
        prices=prices,
        start_parameter='servers-payment',
    )
    bot.send_message(call.message.chat.id, "✅ Счёт создан! Оплатите его через встроенную форму.")


@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(pre_checkout_query):
    """Обработка перед оплатой"""

    bot.answer_pre_checkout_query(
        pre_checkout_query.id,
        ok=True
    )

@bot.message_handler(content_types=['successful_payment'])
def process_successful_payment(message):
    payment_info = message.successful_payment
    tariff_key = payment_info.invoice_payload.split('_')[0]
    total_amount = payment_info.total_amount / 100
    days_map = {
        "1-month": 30,
        "2-months": 60,
        "4-months": 120,
        "6-months": 180,
    }
    days = days_map.get(tariff_key)

    # получаем/создаём запись пользователя
    user_record = get_or_create_user(message.from_user)
    uid_int = message.from_user.id  # int
    uid = str(uid_int)
    users = get_users_data()

    if days:
        give_vpn_access(uid_int, days, f'подписка {days} дней')
        users[uid]["expiry_notified"] = False

        # реферальный бонус
        referrer_id = users[uid].get("referred_by")
        if referrer_id and not users[uid].get("ref_bonus_paid", False):
            ref_uid = str(referrer_id)
            if ref_uid in users:
                reward = round(total_amount * 0.6, 2)  # Считаем 60%
                users[str(referrer_id)]["balance"] = users[str(referrer_id)].get("balance", 0.0) + reward
                users[uid]["ref_bonus_paid"] = True
                bot.send_message(
                    referrer_id,
                    f"💰 Вам начислено <b>{reward} руб.</b> за покупку вашего реферала!\n"
                    f"Проверить баланс можно в разделе «Бонусы».",
                    parse_mode="HTML"
            )

        save_users_data(users)


@bot.callback_query_handler(func=lambda call: call.data == 'back_to_start')
def back_to_start(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except:
        pass

    # Вызываем функцию start, которую мы изменили в предыдущем шаге
    start(call.message)


@bot.message_handler(commands=['notify_expiry'])
def notify_expiry(message):
    # Проверка админа
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Доступ запрещён")
        return
    
    try:
        # /notify_expiry user_id тариф
        parts = message.text.split()
        user_id = int(parts[1])
        tariff = "подписка" if len(parts) < 3 else " ".join(parts[2:])

        text = f"""
❌ <b> Ваша подписка на {tariff} закончилась!</b>

🔄 Вы можете продлить доступ, выбрав тариф ниже.
        """

        keyboard = types.InlineKeyboardMarkup(row_width=1)
        btn_renew = types.InlineKeyboardButton(
            text="🔄 Продлить подписку",
            callback_data="show_tariffs"  # та же коллбэк-данные, что и в твоей кнопке "Купить VPN"
        )
        btn_support = types.InlineKeyboardButton(
            text="💬 Поддержка",
            url="https://t.me/suppVoidLink"
        )
        keyboard.add(btn_renew)
        keyboard.add(btn_support)

        bot.send_message(user_id, text, parse_mode='HTML', reply_markup=keyboard)
        bot.reply_to(message, f"✅ Уведомление отправлено user {user_id}")

    except Exception as e:
        bot.reply_to(message, "❌ Используй: /notify_expiry 123456789 1 месяц")


@bot.message_handler(commands=['add_promocode'])
def add_promocode(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        # Формат: /add_promocode НАЗВАНИЕ ДНИ
        parts = message.text.split()
        if len(parts) < 3:
            bot.reply_to(message, "❌ Ошибка. Используйте: <code>/add_promocode NAME DAYS</code>", parse_mode="HTML")
            return

        name = parts[1].upper() # Сохраняем всегда в верхнем регистре
        days = int(parts[2])

        with open(PROMOCODES_FILE, 'r', encoding='utf-8') as f:
            promos = json.load(f)

        promos[name] = {
            "days": days,
            "active": True
        }

        with open(PROMOCODES_FILE, 'w', encoding='utf-8') as f:
            json.dump(promos, f, indent=4, ensure_ascii=False)

        bot.reply_to(message, f"✅ Промокод <b>{name}</b> на {days} дн. успешно добавлен!", parse_mode="HTML")

    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=['delete_promocode'])
def delete_promocode(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        # Формат: /delete_promocode NAME
        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "❌ Ошибка. Используйте: <code>/delete_promocode NAME</code>", parse_mode="HTML")
            return

        name = parts[1].upper()

        with open(PROMOCODES_FILE, 'r', encoding='utf-8') as f:
            promos = json.load(f)

        if name in promos:
            del promos[name]
            with open(PROMOCODES_FILE, 'w', encoding='utf-8') as f:
                json.dump(promos, f, indent=4, ensure_ascii=False)
            bot.reply_to(message, f"🗑 Промокод <b>{name}</b> удален.", parse_mode="HTML")
        else:
            bot.reply_to(message, "❓ Такого промокода не существует.")

    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")


# Запуск бота
threading.Thread(target=auto_check_expiry, daemon=True).start()
if __name__ == '__main__':
    print("🚀 Бот запущен и работает!")
    print("💳 Платежи через ЮKassa подключены!")
    bot.infinity_polling()
