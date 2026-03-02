import os
import logging
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache para las tasas
_cache = {
    "rates": None,
    "timestamp": None
}

CACHE_SECONDS = 300  # 5 minutos


def fetch_rates():
    """Obtiene las tasas BCV desde DolarApi.com"""
    now = datetime.now()
    if _cache["rates"] and _cache["timestamp"]:
        diff = (now - _cache["timestamp"]).total_seconds()
        if diff < CACHE_SECONDS:
            return _cache["rates"]

    try:
        response = requests.get("https://ve.dolarapi.com/v1/dolares", timeout=10)
        response.raise_for_status()
        data = response.json()

        rates = {}
        for item in data:
            fuente = item.get("fuente", "").lower()
            nombre = item.get("nombre", "").lower()
            promedio = item.get("promedio")

            if fuente == "bcv" and promedio:
                if "dólar" in nombre or "dollar" in nombre or "usd" in nombre:
                    rates["USD"] = float(promedio)
                elif "euro" in nombre or "eur" in nombre:
                    rates["EUR"] = float(promedio)

        if rates:
            _cache["rates"] = rates
            _cache["timestamp"] = now
            logger.info(f"Tasas actualizadas: {rates}")
            return rates
        else:
            logger.warning(f"Respuesta de la API: {data}")
            return _cache["rates"]

    except Exception as e:
        logger.error(f"Error fetching rates: {e}")
        return _cache["rates"]


def format_number(value):
    """Formatea número con punto de miles y coma decimal (formato venezolano)"""
    formatted = f"{value:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def get_date_str():
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    now = datetime.now()
    return f"{now.day} de {meses[now.month - 1]} de {now.year}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Gracias por iniciarme! Puedes ver la tasa BCV del día de hoy a través del comando /bcv, "
        "y calcular cuánto es en bolívares cierta cantidad de dólares a través de /calcular"
    )


async def bcv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rates = fetch_rates()
    if not rates:
        await update.message.reply_text("❌ No se pudo obtener la tasa del BCV en este momento. Intenta más tarde.")
        return

    usd = rates.get("USD")
    eur = rates.get("EUR")

    usd_str = format_number(usd) if usd else "No disponible"
    eur_str = format_number(eur) if eur else "No disponible"

    msg = (
        f"*TASAS DEL DÍA*\n"
        f"📅 {get_date_str()}\n\n"
        f"🇪🇺 Euro: Bs. {eur_str}\n"
        f"🇺🇸 Dólar: Bs. {usd_str}"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def calcular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Por favor indica la cantidad. Ejemplo: /calcular 20")
        return

    try:
        cantidad = float(args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ El valor ingresado no es válido. Ejemplo: /calcular 20")
        return

    rates = fetch_rates()
    if not rates or "USD" not in rates:
        await update.message.reply_text("❌ No se pudo obtener la tasa del BCV en este momento. Intenta más tarde.")
        return

    resultado = cantidad * rates["USD"]
    resultado_str = format_number(resultado)
    cant_str = str(int(cantidad)) if cantidad == int(cantidad) else str(cantidad)

    msg = f"{cant_str}$ en bolívares serían: Bs. {resultado_str}"
    await update.message.reply_text(msg)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta la variable de entorno TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bcv", bcv))
    app.add_handler(CommandHandler("calcular", calcular))

    logger.info("Bot iniciado...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
