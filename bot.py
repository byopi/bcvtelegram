import os
import requests
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler

TOKEN = os.getenv('TELEGRAM_TOKEN')
app = Flask(__name__)

# --- INICIALIZACIÓN ---
# Creamos el loop global para mantener el contexto
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Inicializamos la aplicación
application = Application.builder().token(TOKEN).build()

# Función con timeout para no bloquear el bot
def obtener_tasas():
    try:
        # Timeout de 3 segundos para evitar bloqueos
        res_dolar = requests.get("https://pydolarve.org/api/v1/dollar?monitor=bcv", timeout=3).json()
        res_euro = requests.get("https://pydolarve.org/api/v1/euro?monitor=bcv", timeout=3).json()
        return {
            "dolar": res_dolar['monitors']['bcv']['price'], 
            "euro": res_euro['monitors']['bcv']['price']
        }
    except:
        return None

# --- COMANDOS ---
async def start(update, context):
    await update.message.reply_text("¡Bot activo! Usa /bcv o /calcular [cantidad].")

async def bcv(update, context):
    tasas = obtener_tasas()
    if tasas:
        await update.message.reply_text(f"TASAS DEL DÍA\n\n🇪🇺 Euro: {tasas['euro']} Bs.\n🇺🇸 Dolar: {tasas['dolar']} Bs.\n")
    else:
        await update.message.reply_text("Error al obtener las tasas, intenta de nuevo.")

async def calcular(update, context):
    if not context.args:
        await update.message.reply_text("Uso: /calcular [cantidad]")
        return
    try:
        cant = float(context.args[0].replace(',', '.'))
        tasas = obtener_tasas()
        if tasas:
            tasa = float(str(tasas['dolar']).replace(',', '.'))
            total = cant * tasa
            await update.message.reply_text(f"{cant}$ son {total:,.2f} Bs.")
        else:
            await update.message.reply_text("No pude obtener la tasa para calcular.")
    except:
        await update.message.reply_text("Error: Asegúrate de ingresar un número.")

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("bcv", bcv))
application.add_handler(CommandHandler("calcular", calcular))

# --- INICIALIZACIÓN DE LA APP ---
loop.run_until_complete(application.initialize())

# --- RUTAS WEB ---
@app.route('/')
def index():
    return "Bot activo", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    # Recibimos el update de Telegram
    data = request.get_json(force=True)
    update = Update.de_json(data, application.bot)
    
    # Procesamos en segundo plano para responder "OK" a Telegram inmediatamente
    # y evitar el error de TimedOut
    asyncio.run_coroutine_threadsafe(application.process_update(update), loop)
    
    return "OK", 200

if __name__ == '__main__':
    # Usar el puerto 10000 o el que asigne Render
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
