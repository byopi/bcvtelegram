import os
import requests
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv('TELEGRAM_TOKEN')
app = Flask(__name__)

# Creamos la aplicación y el bot
application = Application.builder().token(TOKEN).build()

# --- Funciones de respuesta ---
def get_tasas():
    try:
        r_dol = requests.get("https://pydolarve.org/api/v1/dollar?monitor=bcv").json()
        r_eur = requests.get("https://pydolarve.org/api/v1/euro?monitor=bcv").json()
        return f"🇪🇺 Euro: {r_eur['monitors']['bcv']['price']} Bs.\n🇺🇸 Dolar: {r_dol['monitors']['bcv']['price']} Bs.\n"
    except:
        return "Error al consultar tasas."

async def start(update, context):
    await update.message.reply_text("¡Bot activo! Usa /bcv o /calcular.")

async def bcv(update, context):
    await update.message.reply_text("TASAS DEL DÍA\n\n" + get_tasas())

async def calcular(update, context):
    try:
        cant = float(context.args[0].replace(',', '.'))
        # Simplificamos el cálculo
        await update.message.reply_text(f"{cant}$ en bolívares serían: Bs. {cant * 40.0:,.2f}")
    except:
        await update.message.reply_text("Uso: /calcular [cantidad]")

# Registramos comandos
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("bcv", bcv))
application.add_handler(CommandHandler("calcular", calcular))

# --- WEBHOOK ---
@app.route('/webhook', methods=['POST'])
def webhook():
    # Esta es la única forma que no lanza RuntimeError en Render
    update = Update.de_json(request.get_json(force=True), application.bot)
    # Ejecutamos de forma síncrona para Flask
    import asyncio
    asyncio.run(application.process_update(update))
    return "OK", 200

@app.route('/')
def index():
    return "Bot funcionando", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
