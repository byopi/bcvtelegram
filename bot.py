import os
import re
import asyncio
import logging
import threading
import requests
import urllib3
import time
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, ContextTypes,
                           CallbackQueryHandler, MessageHandler, filters,
                           ConversationHandler)

logging.basicConfig(level=logging.WARNING)
logging.getLogger("__main__").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

VE_TZ     = timezone(timedelta(hours=-4))
CANAL     = "@botsgfa"
CANAL_URL = "https://t.me/botsgfa"

# Configuración de Proxy (Añadir en las variables de entorno de Render)
PROXY_URL = os.environ.get("PROXY_URL")

# Estados ConversationHandler admin
(ADMIN_MENU, ESPERANDO_MSG_GLOBAL, ESPERANDO_USUARIO, ESPERANDO_MSG_USUARIO) = range(4)

# Cache en memoria + archivo para persistir entre reinicios
CACHE_FILE = "/tmp/rates_cache.json"

# "book": {"YYYY-MM-DD (fecha valor)": {"USD":.., "EUR":.., "manual": bool}}
_cache = {
    "bcv":     {"book": {}, "ts": 0.0, "last_fail": 0.0},
    "binance": {"rate":  None, "date": None},
}


# ── Antiflood ─────────────────────────────────────────────────────────────────
_flood_data: dict = defaultdict(lambda: {"timestamps": [], "muted_until": 0.0})

def check_flood(user_id: int) -> bool:
    now = time.monotonic()
    data = _flood_data[user_id]
    if now < data["muted_until"]:
        return True
    data["timestamps"] = [t for t in data["timestamps"] if now - t < 10]
    data["timestamps"].append(now)
    if len(data["timestamps"]) > 3:
        data["muted_until"] = now + 30
        data["timestamps"]  = []
        return True
    return False


# ── Sistema de Ban ────────────────────────────────────────────────────────────
def get_baneados(context: ContextTypes.DEFAULT_TYPE) -> set:
    if "baneados" not in context.bot_data:
        context.bot_data["baneados"] = set()
    return context.bot_data["baneados"]

def esta_baneado(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return user_id in get_baneados(context)

async def banear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    if not context.args: return
    objetivo = context.args[0].strip()
    baneados = get_baneados(context)
    ban_id = int(objetivo) if objetivo.lstrip("-").isdigit() else objetivo.lower()
    baneados.add(ban_id)
    await update.message.reply_text(f"🔨 Usuario `{objetivo}` baneado.")

async def desbanear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    if not context.args: return
    objetivo = context.args[0].strip()
    baneados = get_baneados(context)
    ban_id = int(objetivo) if objetivo.lstrip("-").isdigit() else objetivo.lower()
    baneados.discard(ban_id)
    await update.message.reply_text(f"✅ Usuario `{objetivo}` desbaneado.")

async def lista_baneados(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    baneados = get_baneados(context)
    if not baneados:
        await update.message.reply_text("✅ No hay baneados.")
        return
    await update.message.reply_text(f"Lista: {baneados}")

# ── Guard ──
async def _guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user: return False
    if esta_baneado(user.id, context): return True
    if user.username and ("@" + user.username.lower()) in get_baneados(context): return True
    if check_flood(user.id): return True
    return False


# ── Cache utils ──────────────────────────────────────────────────────────────
def save_cache():
    import json
    try:
        data = {
            "bcv_book": _cache["bcv"]["book"],
            "bcv_ts":   _cache["bcv"]["ts"],
            "bin_rate": _cache["binance"]["rate"],
            "bin_date": str(_cache["binance"]["date"]) if _cache["binance"]["date"] else None,
        }
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"No se pudo guardar cache: {e}")

def load_cache():
    import json
    from datetime import date
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data.get("bcv_book"), dict):
            _cache["bcv"]["book"] = data["bcv_book"]
            _cache["bcv"]["ts"]   = float(data.get("bcv_ts") or 0)
        if data.get("bin_rate") and data.get("bin_date"):
            _cache["binance"]["rate"] = data["bin_rate"]
            _cache["binance"]["date"] = date.fromisoformat(data["bin_date"])
    except Exception:
        logger.info("Iniciando sin cache previo.")

def get_effective_date():
    now = get_ve_now()
    weekday = now.weekday()
    if weekday == 5: return (now - timedelta(days=1)).date()
    elif weekday == 6: return (now - timedelta(days=2)).date()
    return now.date()

# ── HTTP server ──
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): pass

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), PingHandler).serve_forever()


# ── Utilidades ──
def get_ve_now(): return datetime.now(VE_TZ)
def get_admin_id():
    val = os.environ.get("ADMIN_ID")
    return int(val) if val else None
def es_admin(user_id): return user_id == get_admin_id()
def es_privado(update): return update.message.chat.type == "private"
def format_number(value): return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
def get_date_str():
    meses = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    now = get_ve_now()
    return f"{now.day} de {meses[now.month - 1]} de {now.year}"


# ── Tasas ──

HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
}
RATES_TTL = 15 * 60        # no reconsultar si la última consulta exitosa es más reciente que esto
RETRY_TTL = 2 * 60         # si todo falló, esperar esto antes de reintentar
SCHED_EVERY = 10 * 60      # el planificador revisa las fuentes cada 10 min (captura la publicación de la tarde)
STALE_AFTER = 3 * 3600     # avisar si no se pudo verificar la tasa en este tiempo
BOOK_MAX = 20
_fetch_lock = threading.Lock()
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
         "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def _proxies():
    return {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None


def _iso_or_none(txt):
    txt = str(txt or "")[:10]
    return txt if ISO_RE.match(txt) else None


# ── Fuentes. Cada una devuelve (rates, fecha_valor_ISO | None) ──
def _parse_bcvtoday(j):
    rates = {}
    for cur in ("USD", "EUR"):
        if j.get(cur):
            rates[cur] = float(j[cur])
    if not rates.get("USD"):
        raise RuntimeError("bcv.today sin USD")
    return rates, _iso_or_none(j.get("effective_date"))


def _fetch_bcvtoday():
    """Fuente principal: bcv.today (publica la Fecha Valor oficial del BCV)."""
    h = dict(HTTP_HEADERS, **{"Cache-Control": "no-cache"})
    r = requests.get("https://bcv.today/api/v1/rate.json", headers=h, proxies=_proxies(), timeout=15)
    r.raise_for_status()
    return _parse_bcvtoday(r.json())


def _fetch_bcvtoday_history(iso_date: str):
    r = requests.get(f"https://bcv.today/api/v1/history/{iso_date}.json",
                     headers=HTTP_HEADERS, proxies=_proxies(), timeout=15)
    r.raise_for_status()
    return _parse_bcvtoday(r.json())


def _parse_bcv_number(txt: str) -> float:
    txt = txt.strip().replace(" ", "")
    if "," in txt:   # BCV usa coma decimal: "481,12340000" (también soporta "1.234,56")
        txt = txt.replace(".", "").replace(",", ".")
    return float(txt)


def _parse_valor_bcv(soup):
    """Fecha Valor que muestra la web del BCV. None si no se pudo leer."""
    for span in soup.find_all("span", class_="date-display-single"):
        iso = _iso_or_none(span.get("content"))
        if iso:
            return iso
        m = re.search(r"(\d{1,2})\s+([A-Za-záéíóúÁÉÍÓÚ]+)\s+(\d{4})", span.get_text(" "))
        if m and m.group(2).lower() in MESES:
            return f"{int(m.group(3)):04d}-{MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


def _fetch_bcv_direct():
    """Respaldo: scraping directo de bcv.org.ve (lento y a veces bloquea IPs extranjeras)."""
    r = requests.get("https://www.bcv.org.ve/", headers=HTTP_HEADERS, proxies=_proxies(),
                     timeout=25, verify=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rates = {"USD": _parse_bcv_number(soup.find("div", id="dolar").find("strong").text)}
    try:
        rates["EUR"] = _parse_bcv_number(soup.find("div", id="euro").find("strong").text)
    except Exception:
        pass
    return rates, _parse_valor_bcv(soup)


def _fetch_dolarapi():
    """Último recurso: DolarApi. No informa la fecha valor, así que la tasa se asume del día efectivo."""
    urls = {"USD": "https://ve.dolarapi.com/v1/dolares/oficial",
            "EUR": "https://ve.dolarapi.com/v1/euros/oficial"}
    rates = {}
    for cur, url in urls.items():
        try:
            r = requests.get(url, headers=HTTP_HEADERS, proxies=_proxies(), timeout=15)
            r.raise_for_status()
            j = r.json()
            val = float(j.get("promedio") or j.get("venta") or 0)
            if val > 0:
                rates[cur] = val
        except Exception as e:
            logger.error(f"DolarApi {cur}: {e}")
    if not rates.get("USD"):
        raise RuntimeError("DolarApi no devolvió USD")
    return rates, None


# ── Libro de tasas por fecha valor ──
def _store_rates(rates: dict, valor):
    book = _cache["bcv"]["book"]
    key = valor or get_effective_date().isoformat()
    existing = book.get(key)
    if existing and existing.get("manual"):
        return                      # la tasa manual del admin no se pisa
    if valor is None and existing:
        return                      # una fuente sin fecha nunca pisa una tasa con fecha
    entry = dict(existing or {})
    entry.update(rates)
    entry["manual"] = False
    book[key] = entry
    for k in sorted(book)[:-BOOK_MAX]:
        book.pop(k, None)


def _select(today_iso: str):
    """Tasa vigente: la de mayor fecha valor que sea <= hoy (hora Venezuela).
    Sábado/domingo/feriados caen solos en la última tasa anterior; la del lunes (publicada el
    viernes) queda guardada pero solo se activa a las 00:00 del lunes."""
    book = _cache["bcv"]["book"]
    keys = sorted(k for k in book if k <= today_iso)
    if not keys:
        return None, None
    return book[keys[-1]], keys[-1]


def _refresh_sources(today_iso: str) -> bool:
    ok = False
    for nombre, fn in (("bcv.today", _fetch_bcvtoday), ("BCV directo", _fetch_bcv_direct),
                       ("DolarApi", _fetch_dolarapi)):
        try:
            rates, valor = fn()
            if rates.get("USD", 0) > 0:
                _store_rates(rates, valor)
                logger.info(f"[{nombre}] tasas={rates} fecha_valor={valor}")
                ok = True
                break
        except Exception as e:
            logger.error(f"Fuente {nombre} falló: {e}")

    # Si la fuente ya mostraba la tasa del próximo día hábil (p. ej. deploy nuevo un viernes por
    # la noche) y no hay ninguna tasa vigente para hoy, se pide la de hoy al histórico.
    if ok and _select(today_iso)[0] is None:
        d = get_ve_now().date()
        for back in range(0, 6):
            iso = (d - timedelta(days=back)).isoformat()
            try:
                rates, valor = _fetch_bcvtoday_history(iso)
                _store_rates(rates, valor or iso)
                if _select(today_iso)[0]:
                    break
            except Exception as e:
                logger.error(f"Histórico {iso}: {e}")
    save_cache()
    return ok


def fetch_bcv_rates(force: bool = False):
    """Devuelve {'USD':..,'EUR':..} vigentes HOY (o None si nunca se obtuvo ninguna)."""
    c = _cache["bcv"]
    with _fetch_lock:
        now = time.time()
        today = get_ve_now().date().isoformat()
        current = _select(today)[0]
        fresh   = now - c["ts"] < RATES_TTL
        cooling = now - c["last_fail"] < RETRY_TTL
        if not force and current and (fresh or cooling):
            return current
        if not force and not current and cooling:
            return None
        if _refresh_sources(today):
            c["ts"], c["last_fail"] = now, 0.0
        else:
            c["last_fail"] = now
        return _select(today)[0]


def current_valor_date():
    """Fecha valor (ISO) de la tasa que se está mostrando."""
    return _select(get_ve_now().date().isoformat())[1]


def rates_are_stale() -> bool:
    return (time.time() - _cache["bcv"]["ts"]) > STALE_AFTER


async def rates_scheduler():
    """Revisa las fuentes en segundo plano: al arrancar, cada 10 min y justo a las 00:00 (VE)."""
    while True:
        try:
            await asyncio.to_thread(fetch_bcv_rates, True)
        except Exception as e:
            logger.error(f"Planificador: {e}")
        now = get_ve_now()
        medianoche = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        await asyncio.sleep(max(1.0, min((medianoche - now).total_seconds(), SCHED_EVERY)))


async def _post_init(app):
    app.bot_data["rates_task"] = asyncio.create_task(rates_scheduler())


def fetch_binance_rate():
    c = _cache["binance"]
    now_ve = get_ve_now()
    if c["rate"] and c["date"] == now_ve.date():
        cached_ts = c.get("ts")
        if cached_ts and (now_ve.timestamp() - cached_ts) < 300:
            return c["rate"]
    try:
        url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        payload = {"asset": "USDT", "fiat": "VES", "merchantCheck": False, "page": 1, "publishType": "BUY", "rows": 5, "tradeType": "BUY"}
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        r = requests.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0"}, proxies=proxies, timeout=10)
        data = r.json()
        prices = [float(ad["adv"]["price"]) for ad in data.get("data", []) if ad.get("adv", {}).get("price")]
        if prices:
            price = sum(prices) / len(prices)
            c.update({"rate": price, "date": now_ve.date(), "ts": now_ve.timestamp()})
            return price
        return c["rate"]
    except Exception as e:
        logger.error(f"Error Binance: {e}")
        return c["rate"]


# ── Suscripción ──
async def check_suscripcion(user_id, context):
    try:
        member = await context.bot.get_chat_member(chat_id=CANAL, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except: return False

async def pedir_suscripcion(update):
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Suscribirme", url=CANAL_URL)], [InlineKeyboardButton("✅ Ya me suscribí", callback_data="check_sub")]])
    await update.message.reply_text("⚠️ Suscríbete al canal para usar el bot.", reply_markup=keyboard)


# ── Comandos Públicos ──
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _guard(update, context): return
    if es_privado(update) and not await check_suscripcion(update.effective_user.id, context):
        await pedir_suscripcion(update); return
    await update.message.reply_text("Bot iniciado. Usa /bcv, /calcular o /convertir.")

async def bcv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _guard(update, context): return
    if es_privado(update) and not await check_suscripcion(update.effective_user.id, context):
        await pedir_suscripcion(update); return
    rates, binance = await asyncio.gather(
        asyncio.to_thread(fetch_bcv_rates),
        asyncio.to_thread(fetch_binance_rate),
    )
    if not rates:
        await update.message.reply_text("❌ Error al obtener tasas."); return
    eur = f"Bs. {format_number(rates['EUR'])}" if rates.get("EUR") else "No disponible"
    bin_txt = f"Bs. {format_number(binance)}" if binance else "No disponible"
    msg = f"*TASAS DEL DÍA*\n📅 {get_date_str()}\n\n🇪🇺 Euro: {eur}\n🇺🇸 Dólar: Bs. {format_number(rates.get('USD', 0))}\n💲 Binance: {bin_txt}"
    valor = current_valor_date()
    if valor:
        y, m, d = valor.split("-")
        msg += f"\n\n🏦 Tasa BCV con fecha valor: {d}/{m}/{y}"
    if rates_are_stale():
        msg += "\n⚠️ No pude verificar si hay una tasa más reciente; esta es la última guardada."
    await update.message.reply_text(msg, parse_mode="Markdown")

async def calcular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _guard(update, context): return
    if es_privado(update) and not await check_suscripcion(update.effective_user.id, context):
        await pedir_suscripcion(update); return
    args = context.args
    if not args:
        await update.message.reply_text("Ejemplo: /calcular 20"); return
    try:
        cantidad = float(args[0].replace(",", "."))
        moneda = args[1].lower() if len(args) > 1 else "usd"
        if moneda in ("eur", "euro", "€"): tasa = ((await asyncio.to_thread(fetch_bcv_rates)) or {}).get("EUR")
        elif moneda in ("usdt", "binance"): tasa = await asyncio.to_thread(fetch_binance_rate)
        else: tasa = ((await asyncio.to_thread(fetch_bcv_rates)) or {}).get("USD")
        if not tasa:
            await update.message.reply_text("❌ No pude obtener la tasa ahora, intenta en un minuto."); return
        await update.message.reply_text(f"Resultado: Bs. {format_number(cantidad * tasa)}")
    except: await update.message.reply_text("❌ Error en cálculo.")

async def convertir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await _guard(update, context): return
    if es_privado(update) and not await check_suscripcion(update.effective_user.id, context):
        await pedir_suscripcion(update); return
    args = context.args
    if not args:
        await update.message.reply_text("Ejemplo: /convertir 1000"); return
    try:
        cantidad = float(args[0].replace(",", "."))
        moneda = args[1].lower() if len(args) > 1 else "usd"
        if moneda in ("eur", "euro", "€"): tasa = ((await asyncio.to_thread(fetch_bcv_rates)) or {}).get("EUR")
        elif moneda in ("usdt", "binance"): tasa = await asyncio.to_thread(fetch_binance_rate)
        else: tasa = ((await asyncio.to_thread(fetch_bcv_rates)) or {}).get("USD")
        if not tasa:
            await update.message.reply_text("❌ No pude obtener la tasa ahora, intenta en un minuto."); return
        await update.message.reply_text(f"Resultado: {format_number(cantidad / tasa)}")
    except: await update.message.reply_text("❌ Error en conversión.")

async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await check_suscripcion(query.from_user.id, context):
        await query.edit_message_text("✅ Verificado. Ya puedes usar los comandos.")
    else: await query.answer("❌ Aún no estás suscrito.", show_alert=True)


# ── Panel Admin ──
async def gfa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    await update.message.reply_text("🔐 Panel Admin", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📣 Global", callback_data="admin_global"), InlineKeyboardButton("✉️ Usuario", callback_data="admin_usuario")]]))
    return ADMIN_MENU

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "admin_global": return ESPERANDO_MSG_GLOBAL
    if query.data == "admin_usuario": return ESPERANDO_USUARIO
    return ConversationHandler.END

async def cancelar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END

async def recibir_msg_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for uid in context.bot_data.get("usuarios", []):
        try: await context.bot.send_message(chat_id=uid, text=update.message.text)
        except: pass
    await update.message.reply_text("✅ Enviado.")
    return ConversationHandler.END

async def recibir_usuario_destino(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["admin_destino"] = update.message.text.strip()
    await update.message.reply_text("Escribe el mensaje:")
    return ESPERANDO_MSG_USUARIO

async def recibir_msg_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    destino = context.user_data.get("admin_destino")
    try:
        chat_id = int(destino) if destino.lstrip("-").isdigit() else destino
        await context.bot.send_message(chat_id=chat_id, text=update.message.text)
        await update.message.reply_text("✅ Enviado.")
    except: await update.message.reply_text("❌ Error.")
    return ConversationHandler.END

async def registrar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        if "usuarios" not in context.bot_data: context.bot_data["usuarios"] = set()
        context.bot_data["usuarios"].add(update.effective_user.id)

async def settasa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    try:
        usd, eur = float(context.args[0]), float(context.args[1])
        _cache["bcv"]["book"][get_effective_date().isoformat()] = {"USD": usd, "EUR": eur, "manual": True}
        save_cache()
        await update.message.reply_text("✅ Tasas manuales fijadas.")
    except: await update.message.reply_text("Uso: /settasa USD EUR")


# ── Main ──
if __name__ == "__main__":
    load_cache()
    threading.Thread(target=run_http_server, daemon=True).start()
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token: exit(1)

    app = Application.builder().token(token).post_init(_post_init).build()

    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("settasa",   settasa))
    app.add_handler(CommandHandler("bcv",       bcv))
    app.add_handler(CommandHandler("calcular",  calcular))
    app.add_handler(CommandHandler("convertir", convertir))
    app.add_handler(CommandHandler("banear",    banear))
    app.add_handler(CommandHandler("desbanear", desbanear))
    app.add_handler(CommandHandler("baneados",  lista_baneados))
    app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("gfa", gfa)],
        states={
            ADMIN_MENU: [CallbackQueryHandler(admin_callback, pattern="^admin_")],
            ESPERANDO_MSG_GLOBAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_msg_global),
                CommandHandler("cancelar", cancelar_admin),
            ],
            ESPERANDO_USUARIO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_usuario_destino),
                CommandHandler("cancelar", cancelar_admin),
            ],
            ESPERANDO_MSG_USUARIO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_msg_usuario),
                CommandHandler("cancelar", cancelar_admin),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cancelar_admin)],
    )
    app.add_handler(admin_conv)
    app.add_handler(MessageHandler(filters.ALL, registrar_usuario), group=1)

    logger.info("Bot en marcha...")
    app.run_polling(drop_pending_updates=True)
