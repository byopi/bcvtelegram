import os
import logging
import requests
from datetime import datetime, timezone, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Zona horaria Venezuela UTC-4
VE_TZ = timezone(timedelta(hours=-4))

# Cache para las tasas
_cache = {
    "rates": None,
    "date": None  # fecha VE del día en que se cacheó
}


def get_ve_now():
    return datetime.now(VE_TZ)


def fetch_rates():
    """
    Obtiene las tasas BCV desde DolarApi.com.
    Mantiene la tasa cacheada hasta las 00:00 hora Venezuela del día siguiente.
    """
    now_ve = get_ve_now()
    today_ve = now_ve.date()

    # Si tenemos caché del mismo día, devolver sin actualizar
    if _cache["rates"] and _cache["date"] == today_ve:
        return _cache["rates"]

    try:
        r_usd = requests.get("https://ve.dolarapi.com/v1/dolares/oficial", timeout=10)
        r_eur = requests.get("https://ve.dolarapi.com/v1/euros/oficial", timeout=10)

        rates = {}

        if r_usd.status_code == 200:
            d = r_usd.json()
            promedio = d.get("promedio")
            if promedio:
                rates["USD"] = float(promedio)

        if r_eur.status_code == 200:
            d = r_eur.json()
            promedio = d.get("promedio")
            if promedio:
                rates["EUR"] = float(promedio)

        # Fallback al endpoint general
        if not rates:
            response = requests.get("https://ve.dolarapi.com/v1/dolares", timeout=10)
            response.raise_for_status()
            data = response.json()
            for item in data:
                fuente = item.get("fuente", "").lower()
                nombre = item.get("nombre", "").lower()
                promedio = item.get("promedio")
                if fuente in ("bcv", "oficial") and promedio:
                    if "euro" in nombre or "eur" in nombre:
                        rates["EUR"] = float(promedio)
                    else:
                        rates["USD"] = float(promedio)

        if rates:
            _cache["rates"] = rates
            _cache["date"] = today_ve
            logger.info(f"Tasas actualizadas para {today_ve}: {rates}")
            return rates
        else:
            return _cache["rates"]

    except Exception as e:
        logger.error(f"Error fetching rates: {e}")
        return _cache["rates"]


def format_number(value):
    """Formato venezolano: punto de miles, coma decimal"""
    formatted = f"{value:,.2f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def get_date_str():
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    now = get_ve_now()
    return f"{now.day} de {meses[now.month - 1]} de {now.year}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Gracias por iniciarme! Puedes ver la tasa BCV del día de hoy a través del comando /bcv, "
        "calcular cuánto es en bolívares cierta cantidad de dólares o euros con /calcular, "
        "y convertir bolívares a dólares o euros con /convertir"
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
        await update.message.reply_text(
            "Indica la cantidad y moneda.\n"
            "Ejemplos:\n"
            "/calcular 20 — dólares (por defecto)\n"
            "/calcular 20 eur — euros"
        )
        return

    try:
        cantidad = float(args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Valor no válido. Ejemplo: /calcular 20")
        return

    moneda = args[1].lower() if len(args) > 1 else "usd"

    if moneda in ("eur", "euro", "euros", "€"):
        clave = "EUR"
        simbolo = "€"
    else:
        clave = "USD"
        simbolo = "$"

    rates = fetch_rates()
    if not rates or clave not in rates:
        await update.message.reply_text("❌ No se pudo obtener la tasa del BCV en este momento. Intenta más tarde.")
        return

    resultado = cantidad * rates[clave]
    resultado_str = format_number(resultado)
    cant_str = str(int(cantidad)) if cantidad == int(cantidad) else str(cantidad)

    msg = f"{cant_str}{simbolo} en bolívares serían: Bs. {resultado_str}"
    await update.message.reply_text(msg)


async def convertir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Indica la cantidad en bolívares y la moneda destino.\n"
            "Ejemplos:\n"
            "/convertir 10000 — convierte Bs. a dólares (por defecto)\n"
            "/convertir 10000 eur — convierte Bs. a euros"
        )
        return

    try:
        cantidad = float(args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Valor no válido. Ejemplo: /convertir 10000")
        return

    moneda = args[1].lower() if len(args) > 1 else "usd"

    if moneda in ("eur", "euro", "euros", "€"):
        clave = "EUR"
        simbolo = "€"
        nombre = "euros"
    else:
        clave = "USD"
        simbolo = "$"
        nombre = "dólares"

    rates = fetch_rates()
    if not rates or clave not in rates:
        await update.message.reply_text("❌ No se pudo obtener la tasa del BCV en este momento. Intenta más tarde.")
        return

    resultado = cantidad / rates[clave]
    resultado_str = format_number(resultado)
    cant_str = format_number(cantidad)

    msg = f"Bs. {cant_str} en {nombre} serían: {simbolo}{resultado_str}"
    await update.message.reply_text(msg)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Falta la variable de entorno TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bcv", bcv))
    app.add_handler(CommandHandler("calcular", calcular))
    app.add_handler(CommandHandler("convertir", convertir))

    logger.info("Bot iniciado...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
