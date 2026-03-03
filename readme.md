# 🤖 BCV Telegram Bot

Bot de Telegram que consulta las tasas oficiales del BCV (Banco Central de Venezuela) en tiempo real y permite calcular conversiones entre bolívares, dólares y euros.

---

## ✨ Funciones

- 📊 Consulta la tasa oficial del BCV del día (USD y EUR)
- 💱 Convierte dólares o euros a bolívares
- 🔄 Convierte bolívares a dólares o euros
- 🔒 Requiere suscripción al canal para uso en privado
- ♾️ Corre 24/7 gracias a UptimeRobot + Render

---

## 📋 Comandos

| Comando | Descripción | Ejemplo |
|---|---|---|
| `/start` | Inicia el bot | `/start` |
| `/bcv` | Muestra la tasa oficial del día | `/bcv` |
| `/calcular` | Convierte USD o EUR a bolívares | `/calcular 20` · `/calcular 20 eur` |
| `/convertir` | Convierte bolívares a USD o EUR | `/convertir 10000` · `/convertir 10000 eur` |

---

## 🚀 Despliegue en Render

### Requisitos
- Cuenta en [Render](https://render.com) (gratis)
- Token de bot obtenido desde [@BotFather](https://t.me/BotFather)
- Cuenta en [UptimeRobot](https://uptimerobot.com) (gratis)

### Pasos

1. Clona o sube este repositorio a GitHub
2. En Render, crea un nuevo **Web Service** conectado a tu repo
3. Configura el servicio:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
4. En **Environment**, agrega la variable:
   - `TELEGRAM_BOT_TOKEN` = tu token de BotFather
5. Despliega y espera que el build termine

### Mantenerlo activo con UptimeRobot

1. Crea una cuenta en [uptimerobot.com](https://uptimerobot.com)
2. Agrega un nuevo monitor:
   - **Tipo:** HTTP(s)
   - **URL:** `https://bcvtelegram.onrender.com`
   - **Intervalo:** 5 minutos
3. Guarda — el bot nunca se apagará por inactividad

---

## ⚙️ Variables de entorno

| Variable | Descripción |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token del bot obtenido desde @BotFather |
| `PORT` | Puerto HTTP para el servidor de ping (por defecto: 8080) |

---

## 📦 Dependencias

```
python-telegram-bot==20.7
requests==2.31.0
```

---

## 🔒 Sistema de suscripción

En chats privados, el bot verifica que el usuario esté suscrito al canal [@botsgfa](https://t.me/botsgfa) antes de responder. En grupos funciona libremente sin restricciones.

---

## 📡 Fuente de datos

Las tasas son obtenidas desde [DolarApi.com](https://dolarapi.com) — API pública que refleja la tasa oficial publicada por el BCV. La tasa se mantiene fija durante el día (en hora Venezuela, UTC-4) y se actualiza automáticamente al inicio de cada nuevo día.
