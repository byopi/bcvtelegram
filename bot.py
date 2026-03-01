import os
import requests
import asyncio
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv('TELEGRAM_TOKEN')
bot = Bot(token=TOKEN)
app = Flask(__name__)

# Configuración de la aplicación
application = Application.builder().token(TOKEN).build()

# --- Funciones de comandos ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mensaje = ("¡Gracias por iniciarme!, puedes ver la tasa BCV del día de hoy a través de mis comando /bcv, "
               "y calcular cuanto es en bolívares cierta cantidad de dolares a través de /calcular")
    await update.message.reply_text(mensaje)

async def bcv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Aquí iría tu lógica de obtener_tasas que ya tienes
    await update.message.reply_text("TASAS DEL DÍA...")

# Registrar comandos
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("bcv", bcv))

# --- INICIALIZACIÓN CRÍTICA ---
# Esto debe ejecutarse una vez al iniciar la aplicación
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
loop.run_until_complete(application.initialize())

@app.route('/webhook', methods=['POST'])
def webhook():
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, bot)
    # Procesar la actualización usando el bucle de eventos ya inicializado
    loop.run_until_complete(application.process_update(update))
    return "OK", 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
