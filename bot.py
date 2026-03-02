import os
import requests
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv('TELEGRAM_TOKEN')
app = Flask(__name__)

# --- INICIALIZACIÓN ---
# Construimos la app y usamos el método build para obtener el objeto Bot correctamente
application = Application.builder().token(TOKEN).build()

# Definimos funciones después de tener la aplicación
def obtener_tasas():
    try:
        res_dolar = requests.get("https://pydolarve.org/api/v1/dollar?monitor=bcv").json()
        res_euro = requests.get("https://pydolarve.org/api/v1/euro?monitor=bcv").json()
        return {
            "dolar": res_dolar['monitors']['bcv']['price'],
            "euro": res_euro['monitors']['bcv']['price']
        }
    except:
        return None

async def start(update, context):
    await update.message.reply_text("¡Gracias por iniciarme! Usa /bcv o /calcular.")

async def bcv(update, context):
    tasas = obtener_tasas()
    if tasas:
        await update.message.reply_text(f"TASAS DEL DÍA\n\n🇪🇺 Euro: {tasas['euro']} Bs.\n🇺🇸 Dolar: {tasas['dolar']} Bs.\n")

async def calcular(update, context):
    try:
        cantidad = float(context.args[0].replace(',', '.'))
        tasas = obtener_tasas()
        total = cantidad * float(str(tasas['dolar']).replace(',', '.'))
        await update.message.reply_text(f"{cantidad}$ en bolívares serían: Bs. {total:,.2f}")
    except:
        await update.message.reply_text("Usa /calcular [cantidad]")

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("bcv", bcv))
application.add_handler(CommandHandler("calcular", calcular))

# --- INICIALIZACIÓN ASÍNCRONA ---
# Esto es necesario para que el bot conozca su propio nombre de usuario
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())

@app.route('/')
def index():
    return "Bot activo", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    # Convertimos el JSON recibido en un objeto Update
    # Usamos application.bot directamente
    update = Update.de_json(request.get_json(force=True), application.bot)
    # Procesamos la actualización usando el loop global
    loop.run_until_complete(application.process_update(update))
    return "OK", 200
