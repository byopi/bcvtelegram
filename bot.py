import os
import requests
import asyncio
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# Configuración inicial
TOKEN = os.getenv('TELEGRAM_TOKEN')
bot = Bot(token=TOKEN)
app = Flask(__name__)

# Configuración de la aplicación de Telegram
application = Application.builder().token(TOKEN).build()

# Función para obtener tasas (mantenemos tu lógica)
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

# --- COMANDOS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = ("¡Gracias por iniciarme!, puedes ver la tasa BCV del día de hoy a través de mis comando /bcv, "
               "y calcular cuanto es en bolívares cierta cantidad de dolares a través de /calcular")
    await update.message.reply_text(mensaje)

async def bcv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasas = obtener_tasas()
    if not tasas:
        await update.message.reply_text("Error al obtener las tasas.")
        return
    
    mensaje = (f"TASAS DEL DÍA\n\n"
               f"🇪🇺 Euro: {tasas['euro']} Bs.\n"
               f"🇺🇸 Dolar: {tasas['dolar']} Bs.")
    await update.message.reply_text(mensaje)

async def calcular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /calcular [cantidad]")
        return
    
    try:
        cantidad = float(context.args[0].replace(',', '.'))
        tasas = obtener_tasas()
        tasa_dolar = float(str(tasas['dolar']).replace(',', '.'))
        total = cantidad * tasa_dolar
        await update.message.reply_text(f"{cantidad}$ en bolívares serían: Bs. {total:,.2f}")
    except:
        await update.message.reply_text("Error: Asegúrate de ingresar un número válido.")

# Registrar comandos
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("bcv", bcv))
application.add_handler(CommandHandler("calcular", calcular))

# --- INICIALIZACIÓN Y WEBHOOK ---

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())

@app.route('/')
def index():
    return "Bot activo", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, bot)
    loop.run_until_complete(application.process_update(update))
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
