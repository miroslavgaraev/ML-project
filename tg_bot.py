# -*- coding: utf-8 -*-
import telebot
from telebot import types
from keras.models import load_model
from keras.optimizers import Adam
from PIL import Image
import numpy as np
from io import BytesIO
import os

TOKEN = "8373525982:AAEIzhLgrTDFDci6o_qpCLioM5JmDWIdaT0"
MODEL_PATH = "trained_model.h5"
RETRAINING_DIR = "retraining_data"

bot = telebot.TeleBot(TOKEN)
user_states = {}

os.makedirs(RETRAINING_DIR, exist_ok=True)
os.makedirs(f"{RETRAINING_DIR}/fake", exist_ok=True)
os.makedirs(f"{RETRAINING_DIR}/real", exist_ok=True)

model = load_model(MODEL_PATH)
model.compile(optimizer="adam", loss="categorical_crossentropy", metrics=["accuracy"])

def prepare_image(image_bytes, target_size=(128, 128)):
    img = Image.open(BytesIO(image_bytes)).convert('RGB')
    img = img.resize(target_size)
    img_array = np.array(img) / 255.0
    img_array = np.expand_dims(img_array, axis=0)
    return img_array

def predict_deepfake(image_bytes):
    prepared_img = prepare_image(image_bytes)
    predictions = model.predict(prepared_img, verbose=0)
    pred_fake = predictions[0][0]
    pred_real = predictions[0][1]
    predicted_class_idx = np.argmax(predictions, axis=-1)[0]
    class_labels = ("fake", "real")
    predicted_class = class_labels[predicted_class_idx]
    confidence = max(pred_fake, pred_real) * 100
    actual_class = "fake" if predicted_class_idx == 0 else "real"
    return predicted_class, confidence, actual_class, pred_fake, pred_real

def retrain_model_with_feedback(image_bytes, actual_class, user_id):
    prepared_img = prepare_image(image_bytes)
    label = np.array([[1.0, 0.0]]) if actual_class == "fake" else np.array([[0.0, 1.0]])
    
    model.compile(optimizer="adam", loss="categorical_crossentropy", metrics=["accuracy"])
    model.fit(prepared_img, label, epochs=3, batch_size=1, verbose=0)
    
    timestamp = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{RETRAINING_DIR}/{actual_class}/{user_id}_{timestamp}.jpg"
    img = Image.open(BytesIO(image_bytes)).convert('RGB')
    img.resize((128, 128)).save(filename)
    
    model.save(MODEL_PATH)
    return True



@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    user_states[user_id] = {"mode": None}
    response = "🤖 Выбери режим работы:"
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📸 Проверить фото", "🎓 Дообучение")
    bot.send_message(message.chat.id, response, reply_markup=markup)


@bot.message_handler(commands=['help'])
def send_help(message):
    response = """
📸 Проверить фото - определит дипфейк или нет
🎓 Дообучение - модель будет учиться на твоих ответах

Отправь фото для анализа (JPG, PNG)
/start - вернуться в меню
"""
    bot.reply_to(message, response)

@bot.message_handler(func=lambda m: m.text == "📸 Проверить фото")
def select_simple_mode(message):
    user_id = message.from_user.id
    user_states[user_id] = {"mode": "simple"}
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📸 Проверить фото", "🎓 Дообучение")
    bot.send_message(message.chat.id, "📸 Режим проверки активен", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "🎓 Дообучение")
def select_retrain_mode(message):
    user_id = message.from_user.id
    user_states[user_id] = {"mode": "retrain"}
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📸 Проверить фото", "🎓 Дообучение")
    bot.send_message(message.chat.id, "🎓 Режим дообучения активен", reply_markup=markup)



@bot.message_handler(content_types=['photo'])
def handle_image(message):
    user_id = message.from_user.id
    if user_id not in user_states or user_states[user_id].get("mode") is None:
        bot.reply_to(message, "Сначала выбери режим работы! /start")
        return
    
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    msg = bot.reply_to(message, "⏳ Анализирую...")
    
    predicted_class, confidence, actual_class, pred_fake, pred_real = predict_deepfake(downloaded_file)
    
    response_text = f"{'🚨 ДИПФЕЙК' if predicted_class == 'fake' else '✅ РЕАЛЬНОЕ'}\n\n📊 Уверенность: {confidence:.1f}%"
    
    mode = user_states[user_id].get("mode")
    
    if mode == "simple":
        bot.edit_message_text(response_text, chat_id=message.chat.id, message_id=msg.message_id)
    elif mode == "retrain":
        user_states[user_id]["image_bytes"] = downloaded_file
        user_states[user_id]["predicted_class"] = predicted_class
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ Правильно", callback_data=f"correct_{user_id}"))
        markup.add(types.InlineKeyboardButton("❌ Неправильно", callback_data=f"wrong_{user_id}"))
        
        confirmation_text = response_text + "\n\n❓ Я правильно определил?"
        bot.edit_message_text(confirmation_text, chat_id=message.chat.id, 
                             message_id=msg.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("correct_") or call.data.startswith("wrong_"))
def process_feedback(call):
    user_id = call.from_user.id
    
    if user_id not in user_states or "image_bytes" not in user_states[user_id]:
        bot.edit_message_text("❌ Ошибка", chat_id=call.message.chat.id, message_id=call.message.message_id)
        return
    
    image_bytes = user_states[user_id]["image_bytes"]
    predicted_class = user_states[user_id]["predicted_class"]
    
    if call.data == f"correct_{user_id}":
        retrain_model_with_feedback(image_bytes, predicted_class, user_id)
        final_text = "✅ Спасибо! Модель дообучена!"
    else:
        actual_class = "real" if predicted_class == "fake" else "fake"
        retrain_model_with_feedback(image_bytes, actual_class, user_id)
        final_text = f"✅ Спасибо! Модель переучена на класс: {actual_class.upper()}"
    
    bot.edit_message_text(final_text + "\n\n📸 Отправь еще фото или выбери режим", 
                         chat_id=call.message.chat.id, message_id=call.message.message_id,
                         )
    
    user_states[user_id].pop("image_bytes", None)
    user_states[user_id].pop("predicted_class", None)

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    bot.reply_to(message, "📸 Отправь фото для анализа или /start для меню")

if __name__ == '__main__':
    print("🤖 Бот запущен...")
    bot.infinity_polling()
