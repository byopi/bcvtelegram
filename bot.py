import os
import requests
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from datetime import datetime

# Configuración inicial
# El token se lee de las variables de entorno de Render
TOKEN = os.getenv('TELEGRAM_TOKEN')
app = Flask(__name__)

# Función para obtener tasas desde la API
def obtener_tasas():
    try:
        # Consultamos Dolar (BCV)
        res_dolar = requests.get("https://pydolarve.org/api/v1/dollar?monitor=bcv").json()
        # Consultamos Euro (BCV)
        res_euro = requests.get("https://pydolarve.org/api/v1/euro?monitor=bcv").json()
        
        return {
            "dolar": res_dolar['monitors']['bcv']['price'],
            "euro": res_euro['monitors']['bcv']['price']
        }
    except Exception as e:
        print(f"Error API: {e}")
        return None

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Para utilizar este bot debes agregarme a un grupo como administrador.")

# Comando /bcv con tu formato personalizado
async def bcv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tasas = obtener_tasas()
    if not tasas:
        await update.message.reply_text("⚠️ Error al conectar con el BCV. Intente más tarde.")
        return

    fecha = datetime.now().strftime("%d/%m/%Y")
    
    # Formato solicitado: Equipo vs. Equipo / Canal / Plataforma
    mensaje = (
        f"TASAS EL DÍA {fecha}\n\n"
        f"Dólar vs. Bolívar\n"
        f"BCV / DolarAPI\n"
        f"🇺🇸 Dolar: {tasas['dolar']}\n\n"
        f"Euro vs. Bolívar\n"
        f"BCV / DolarAPI\n"
        f"🇪🇺 Euro: {tasas['euro']}"
    )
    await update.message.reply_text(mensaje)

# Comando /calcular
async def calcular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /calcular [monto]")
        return
    
    try:
        monto = float(context.args[0].replace(',', '.'))
        tasas = obtener_tasas()
        tasa_dolar = float(str(tasas['dolar']).replace(',', '.'))
        
        resultado = monto * tasa_dolar
        await update.message.reply_text(f"🧮 {monto} USD equivalen a {resultado:,.2f} Bs. (Tasa: {tasas['dolar']})")
    except:
        await update.message.reply_text("❌ Por favor, introduce un número válido. Ejemplo: /calcular 20")

# Ruta para que Render y Telegram se comuniquen (Webhook)
@app.route('/webhook', methods=['POST'])
async def webhook():
    # Esta parte procesa las actualizaciones de Telegram
    # Para simplificar el despliegue en Render con Flask, 
    # se recomienda usar polling si no tienes tráfico masivo.
    return "OK", 200

@app.route('/')
def index():
    return "Bot en línea", 200

if __name__ == '__main__':
    # Configuración de la aplicación de Telegram
    application = Application.builder().token(TOKEN).build()
    
    # Añadir manejadores de comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("bcv", bcv))
    application.add_handler(CommandHandler("calcular", calcular))
    
    # En Render usaremos Polling por simplicidad inicial, 
    # que funciona bien con UptimeRobot para no dormirse.
    print("Bot iniciado...")
    application.run_polling()
