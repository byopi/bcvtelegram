import os
import re
import logging
import requests
from bs4 import BeautifulSoup
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


def fetch_bcv_rates():
    """Obtiene las tasas del BCV scrapeando su web oficial."""
    now = datetime.now()
    if _cache["rates"] and _cache["timestamp"]:
        diff = (now - _cache["timestamp"]).total_seconds()
        if diff < CACHE_SECONDS:
            return _cache["rates"]

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get("https://www.bcv.org.ve/", headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        rates = {}

        # Dolar
        dolar_div = soup.find("div", {"id": "dolar"})
        if dolar_div:
            strong = dolar_div.find("strong")
            if strong:
                rates["USD"] = float(strong.text.strip().replace(",", "."))

        # Euro
        euro_div = soup.find("div", {"id": "euro"})
        if euro_div:
            strong = euro_div.find("strong")
            if strong:
                rates["EUR"] = float(strong.text.strip().replace(",", "."))

        if rates:
            _cache["rates"] = rates
            _cache["timestamp"] = now
            return rates
        else:
            return None

    except Exception as e:
        logger.error(f"Error fetching BCV rates: {e}")
        return _cache["rates"]  # devolver caché viejo si hay error


def get_date_str():
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    now = datetime.now()
    return f"{now.day} de {meses[now.month - 1]} de {now.year}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Gracias por iniciarme! Puedes ver la tasa BCV del día de hoy a través de mis comandos /bcv, "
        "y calcular cuánto es en bolívares cierta cantidad de dólares a través de /calcular"
    )


async def bcv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rates = fetch_bcv_rates()
    if not rates:
        await update.message.reply_text("❌ No se pudo obtener la tasa del BCV en este momento. Intenta más tarde.")
        return

    usd = rates.get("USD", "N/A")
    eur = rates.get("EUR", "N/A")

    usd_str = f"{usd:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if isinstance(usd, float) else usd
    eur_str = f"{eur:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if isinstance(eur, float) else eur

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

    rates = fetch_bcv_rates()
    if not rates or "USD" not in rates:
        await update.message.reply_text("❌ No se pudo obtener la tasa del BCV en este momento. Intenta más tarde.")
        return

    resultado = cantidad * rates["USD"]

    resultado_str = f"{resultado:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    # Formatear cantidad: si es entero mostrar sin decimales
    if cantidad == int(cantidad):
        cant_str = str(int(cantidad))
    else:
        cant_str = str(cantidad)

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
