import os
import requests
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv('TELEGRAM_TOKEN')
app = Flask(__name__)

# 1. Creamos el loop global
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# 2. Inicializamos la aplicación de forma síncrona al arrancar el script
application = Application.builder().token(TOKEN).build()
loop.run_until_complete(application.initialize())

# 3. Definimos funciones
def obtener_tasas():
    try:
        res_dolar = requests.get("https://pydolarve.org/api/v1/dollar?monitor=bcv").json()
        res_euro = requests.get("https://pydolarve.org/api/v1/euro?monitor=bcv").json()
        return {"dolar": res_dolar['monitors']['bcv']['price'], "euro": res_euro['monitors']['bcv']['price']}
    except:
        return None

async def start(update, context):
    await update.message.reply_text("¡Gracias por iniciarme! Usa /bcv o /calcular.")

async def bcv(update, context):
    tasas = obtener_tasas()
    if tasas:
        await update.message.reply_text(f"TASAS DEL DÍA\n\n🇪🇺 Euro: {tasas['euro']} Bs.\n🇺🇸 Dolar: {tasas['dolar']} Bs.")

async def calcular(update, context):
    try:
        cant = float(context.args[0].replace(',', '.'))
        tasas = obtener_tasas()
        total = cant * float(str(tasas['dolar']).replace(',', '.'))
        await update.message.reply_text(f"{cant}$ en bolívares serían: Bs. {total:,.2f}")
    except:
        await update.message.reply_text("Usa /calcular [cantidad]")

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("bcv", bcv))
application.add_handler(CommandHandler("calcular", calcular))

# 4. Rutas Web
@app.route('/')
def index():
    return "Bot activo", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    # Procesar usando el mismo loop y la app ya inicializada
    update = Update.de_json(request.get_json(force=True), application.bot)
    loop.run_until_complete(application.process_update(update))
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
