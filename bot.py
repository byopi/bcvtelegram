import os
import requests
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv('TELEGRAM_TOKEN')
bot = Bot(token=TOKEN)
app = Flask(__name__)

# Configuración básica
application = Application.builder().token(TOKEN).build()

# --- COMANDOS (Sin cambios) ---
async def start(update, context):
    await update.message.reply_text("¡Gracias por iniciarme! Usa /bcv o /calcular.")

async def bcv(update, context):
    # Simulación rápida para probar que el comando responde
    await update.message.reply_text("TASAS DEL DÍA\n\n🇪🇺 Euro: 40.0 Bs.\n🇺🇸 Dolar: 38.0 Bs.")

async def calcular(update, context):
    await update.message.reply_text("Cálculo recibido.")

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("bcv", bcv))
application.add_handler(CommandHandler("calcular", calcular))

# --- RUTA WEBHOOK CORREGIDA ---
@app.route('/webhook', methods=['POST'])
def webhook():
    # Usamos request directamente para obtener los datos
    update = Update.de_json(request.get_json(force=True), bot)
    # Ejecutamos el procesador de forma que no bloquee el hilo de Flask
    application.update_queue.put(update)
    return "OK", 200

@app.route('/')
def index():
    return "Bot activo", 200

if __name__ == '__main__':
    # Esto es solo para pruebas locales; en Render usa gunicorn
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
