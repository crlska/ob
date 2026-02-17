import json
import os
import logging
import urllib.request
import urllib.parse
from datetime import datetime, time
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from google import genai
from supabase import create_client, Client

# --- Config ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "7"))
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", "-6"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("outfit-bot")

ALL_CATEGORIES = [
    "underwear", "socks", "calzado", "pantalones", "tops", "capas",
    "gorras", "smartwatch_bands", "relojes", "anillos", "cadenas",
    "pulseras", "plugs", "lentes", "extras"
]

# --- Supabase DB ---
db: Client = None

def init_db():
    global db
    db = create_client(SUPABASE_URL, SUPABASE_KEY)

def db_get_profile():
    result = db.table("profile").select("*").eq("id", 1).execute()
    if result.data:
        return result.data[0]
    default = {
        "id": 1,
        "city": "Saltillo, Coahuila",
        "age": 36, "height_cm": 162, "weight_kg": 75,
        "target_weight_kg": 60,
        "skin_tone": "moreno claro / light medium",
        "undertone": "cálido-neutral, más dorado que rosado",
        "hair": "al hombro",
        "identity": "Mujer queer, prefiere vestir masculino/andrógino",
        "style_notes": "Casual urbano, edgy pero simple. Colores oscuros y neutros.",
        "daily_enabled": False
    }
    db.table("profile").insert(default).execute()
    return default

def db_update_profile(**kwargs):
    db.table("profile").update(kwargs).eq("id", 1).execute()

def db_add_item(category, name, details=None, location=None):
    item = {
        "category": category,
        "name": name,
        "status": "clean",
        "details": details or {},
        "location": location,
        "times_worn": 0,
        "last_worn": None,
    }
    result = db.table("items").insert(item).execute()
    return result.data[0] if result.data else None

def db_get_items(status=None, category=None):
    query = db.table("items").select("*")
    if status:
        query = query.eq("status", status)
    if category:
        query = query.eq("category", category)
    return query.order("category").execute().data or []

def db_update_item(item_id, **kwargs):
    db.table("items").update(kwargs).eq("id", item_id).execute()

def db_find_item(search):
    """Find item by partial ID or name match"""
    # Try by ID first
    try:
        item_id = int(search)
        result = db.table("items").select("*").eq("id", item_id).execute()
        if result.data:
            return result.data[0]
    except ValueError:
        pass
    # Search by name
    result = db.table("items").select("*").ilike("name", f"%{search}%").execute()
    if result.data:
        return result.data[0]
    return None

def db_get_history(limit=7):
    result = db.table("outfit_history").select("*").order("created_at", desc=True).limit(limit).execute()
    return result.data or []

def db_add_history(outfit_text, occasion):
    db.table("outfit_history").insert({
        "outfit_text": outfit_text,
        "occasion": occasion,
    }).execute()

def db_add_feedback(text):
    db.table("feedback").insert({"text": text}).execute()

def db_get_feedback(limit=10):
    result = db.table("feedback").select("*").order("created_at", desc=True).limit(limit).execute()
    return result.data or []

# --- Packing Lists ---
def db_get_lists():
    result = db.table("packing_lists").select("*").order("name").execute()
    return result.data or []

def db_get_list(name):
    result = db.table("packing_lists").select("*").eq("name", name.lower()).execute()
    return result.data[0] if result.data else None

def db_create_list(name, description=""):
    existing = db_get_list(name)
    if existing:
        return None
    result = db.table("packing_lists").insert({
        "name": name.lower(), "description": description, "items": []
    }).execute()
    return result.data[0] if result.data else None

def db_update_list_items(name, items):
    db.table("packing_lists").update({"items": items}).eq("name", name.lower()).execute()

def db_delete_list(name):
    result = db.table("packing_lists").delete().eq("name", name.lower()).execute()
    return bool(result.data)


# --- Weather ---
def get_weather(city: str) -> str:
    try:
        encoded = urllib.parse.quote(city)
        url = f"https://wttr.in/{encoded}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        current = data["current_condition"][0]
        temp = current["temp_C"]
        feels = current["FeelsLikeC"]
        desc_list = current.get("lang_es", current.get("weatherDesc", [{}]))
        desc = desc_list[0].get("value", "") if desc_list else ""
        humidity = current["humidity"]
        forecast = data["weather"][0]
        max_t = forecast["maxtempC"]
        min_t = forecast["mintempC"]
        hourly = forecast.get("hourly", [])
        rain = hourly[4].get("chanceofrain", "0") if len(hourly) > 4 else "0"
        return (
            f"Clima en {city}: {desc}, {temp}°C (sensación {feels}°C), "
            f"min {min_t}°C / max {max_t}°C, humedad {humidity}%, lluvia {rain}%"
        )
    except Exception as e:
        logger.warning(f"Weather error for {city}: {e}")
        return f"(clima no disponible para {city})"


# --- AI Context Builder ---
def build_ai_context():
    profile = db_get_profile()
    available = db_get_items(status="clean")
    dirty = db_get_items(status="dirty")
    history = db_get_history(7)
    feedback = db_get_feedback(10)

    context = {
        "profile": {
            "city": profile.get("city"),
            "age": profile.get("age"),
            "height_cm": profile.get("height_cm"),
            "weight_kg": profile.get("weight_kg"),
            "target_weight_kg": profile.get("target_weight_kg"),
            "skin_tone": profile.get("skin_tone"),
            "undertone": profile.get("undertone"),
            "hair": profile.get("hair"),
            "identity": profile.get("identity"),
            "style_notes": profile.get("style_notes"),
        },
        "available_items": [
            {
                "id": i["id"], "name": i["name"], "category": i["category"],
                "details": i.get("details", {}),
                "location": i.get("location"),
            }
            for i in available
        ],
        "dirty_items": [f"{i['name']} ({i['category']})" for i in dirty],
        "recent_outfits": [{"occasion": h.get("occasion"), "outfit": h.get("outfit_text"), "date": h.get("created_at")} for h in history],
        "feedback": [f.get("text") for f in feedback],
    }
    return json.dumps(context, ensure_ascii=False, indent=2)


# --- AI Outfit Engine ---
SYSTEM_PROMPT = """Eres un stylist personal de Los Angeles. Tu clienta es una mujer queer de 36 años que prefiere vestir masculino/andrógino. Tu vibe es edgy pero accesible — piensa East LA meets Silverlake, no West Hollywood.

SOBRE ELLA:
- Trabaja en tech/data/automation, día a día casual y funcional
- No quiere verse flashy, influencer, ni overdressed
- Prefiere upgrades sutiles, no cambios drásticos
- Colores oscuros y neutros, evita lo formal
- Valora comodidad pero quiere verse más intencional y atractiva
- Su meta: verse más confident, clean y put together sin cambiar quién es

REGLAS:
1. SOLO sugiere prendas DISPONIBLES (status: clean) en su guardarropa — referencia por nombre exacto
2. Incluye: underwear, calcetines, pantalón, top, calzado. Capa solo si el clima lo requiere
3. Sugiere reloj O smartwatch+banda según el outfit (tiene ambos). Un reloj análogo puede elevar más el look
4. Sugiere plugs/expansores que combinen con el outfit (los usa siempre, tiene varios colores/estilos)
5. Si sugiere gorra, menciona modelo y forma específica
6. Si algo importante está sucio, dile que lo lave con humor
7. Considera el CLIMA (se te dará info) y la ocasión
8. Considera últimos outfits para no repetir
9. Sé directo, breve, con personalidad. Stylist amigo edgy de LA
10. Responde en español casual mexicano (con anglicismos naturales de moda)
11. Toma en cuenta tipo de cuerpo, tono de piel y undertone del perfil
12. Sugiere prendas que favorezcan su figura actual sin hacerla sentir mal
13. El fit importa: sugiere cómo debería quedar cada prenda
14. Usa marca, modelo y color cuando estén disponibles
15. Joyería: no mezclar metales, max 2-3 anillos, sugiere mano/dedo. Complementar sin saturar
16. Para VIAJES: minimiza items, maximiza combinaciones. Repetir calzado está bien
17. Para viajes de varios días, sugiere outfits que compartan piezas

FORMATO para outfit de un día:
🔥 [Nombre creativo del outfit]

🩲 Underwear: [prenda]
🧦 Calcetines: [prenda]
👖 Pantalón: [prenda]
👕 Top: [prenda]
👟 Calzado: [prenda]
🧥 Capa (si aplica): [prenda]
🧢 Gorra (si aplica): [modelo]
⌚ Reloj/Smartwatch: [cuál y por qué]
👂 Plugs/Expansores: [color/estilo que combine]
💍 Joyería: [anillos, cadenas, pulseras]
🎒 Extras: [otros]

💡 [Por qué funciona - 1-2 líneas]
⚠️ [Alertas si hay]

FORMATO para viaje de varios días:
🧳 PACKING LIST — [destino] ([días] días)

📦 LO QUE LLEVAS:
[lista de prendas únicas a empacar]

📅 DÍA X — [ocasión]
[outfit del día]

💡 NOTAS DE VIAJE:
[tips de combinación, qué se repite]
"""

async def get_ai_suggestion(user_message: str, city_override: str = None) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)
    wardrobe_context = build_ai_context()
    profile = db_get_profile()
    city = city_override or profile.get("city", "Saltillo, Coahuila")
    weather = get_weather(city)
    today = datetime.now()
    day_info = f"Hoy es {today.strftime('%A %d de %B %Y')}, hora: {today.strftime('%H:%M')}"

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"""CONTEXTO DEL GUARDARROPA:
{wardrobe_context}

CLIMA ACTUAL:
{weather}

FECHA: {day_info}
CIUDAD: {city}

SOLICITUD: {user_message}""",
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=2000,
        ),
    )
    return response.text


# --- Telegram Handlers ---
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = db_get_profile()
    city = profile.get("city", "Saltillo, Coahuila")
    daily = "ON" if profile.get("daily_enabled") else "OFF"
    await update.message.reply_text(
        "👔 Outfit Bot — tu stylist personal\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💬 PEDIR OUTFIT:\n"
        "Escríbeme directo:\n"
        "• 'voy a un bar con amigos'\n"
        "• 'me voy a CDMX 3 días, concierto de rock'\n"
        "• 'outfit para hoy'\n"
        "O usa /outfit [ocasión]\n\n"
        "👕 GUARDARROPA:\n"
        "/add [cat] [nombre] — Agregar prenda\n"
        "/addpro — Agregar con detalles\n"
        "/bulk — Agregar muchas de golpe\n"
        "/closet — Ver todo\n"
        "/available — Solo lo limpio\n\n"
        "🧺 STATUS:\n"
        "/dirty [id] [razón] — Marcar sucia\n"
        "/clean [id] — Marcar limpia\n"
        "/lost [id] [dónde] — Marcar perdida\n"
        "/where [id] [ubicación] — Guardar dónde está\n\n"
        "👤 PERFIL:\n"
        "/profile — Ver perfil\n"
        "/profile peso 70 — Actualizar\n"
        "/city — Ver ciudad + clima\n"
        "/city CDMX — Cambiar ciudad\n\n"
        "📋 LISTAS:\n"
        "/lists — Ver todas\n"
        "/list basicos — Ver una\n"
        "/listadd basicos Kindle — Agregar\n"
        "/listdel basicos 3 — Quitar #3\n"
        "/listnew nombre desc — Crear\n"
        "/listremove nombre — Eliminar\n\n"
        "⚙️ CONFIG:\n"
        "/daily on/off — Outfit diario\n"
        "/feedback [texto] — Dar feedback\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 {city} | ⏰ Daily: {daily}\n"
        f"Categorías: {', '.join(ALL_CATEGORIES)}"
    )

async def cmd_outfit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    occasion = " ".join(context.args) if context.args else "día normal, ir al trabajo"
    await update.message.reply_text("🤔 Checando clóset y clima...")
    try:
        suggestion = await get_ai_suggestion(occasion)
        db_add_history(suggestion, occasion)
        await update.message.reply_text(suggestion)
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("❌ Error. Intenta de nuevo.")

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text(
            f"Uso: /add [categoría] [nombre]\n\n"
            f"Categorías:\n{', '.join(ALL_CATEGORIES)}\n\n"
            f"Ej: /add calzado Dr Martens 1460 negras\n"
            f"Detallado: /addpro"
        )
        return
    category = context.args[0].lower()
    name = " ".join(context.args[1:])
    if category not in ALL_CATEGORIES:
        await update.message.reply_text(f"❌ '{category}' no existe.\nVálidas: {', '.join(ALL_CATEGORIES)}")
        return
    item = db_add_item(category, name)
    if item:
        await update.message.reply_text(f"✅ {name} → {category} (ID: {item['id']})")
    else:
        await update.message.reply_text("❌ Error al agregar.")

async def cmd_addpro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Formato:\ncategoría: nombre | marca: X | color: X | modelo: X | fit: X | notas: X\n\n"
        "Ej:\n"
        "calzado: Hoka Kawana 2 | marca: Hoka | color: negro | fit: regular\n"
        "plugs: túnel dorado | color: dorado shiny | notas: acero 10mm\n\n"
        "Solo 'categoría: nombre' es obligatorio."
    )
    context.user_data["awaiting_addpro"] = True

async def cmd_bulk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 Una prenda por línea:\n\n"
        "categoría: nombre\n"
        "O con detalle:\n"
        "categoría: nombre | marca: X | color: X\n\n"
        f"Categorías: {', '.join(ALL_CATEGORIES)}"
    )
    context.user_data["awaiting_bulk"] = True

async def cmd_status_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command = update.message.text.split()[0].replace("/", "")
    status_map = {"dirty": "dirty", "clean": "clean", "lost": "lost"}
    new_status = status_map.get(command, "clean")
    if not context.args:
        await update.message.reply_text(f"Uso: /{command} [id o nombre] [razón opcional]")
        return
    search = context.args[0]
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    item = db_find_item(search)
    if item:
        updates = {"status": new_status}
        if reason:
            updates["details"] = {**(item.get("details") or {}), "status_reason": reason}
        db_update_item(item["id"], **updates)
        emoji = {"clean": "✅", "dirty": "🧺", "lost": "❓"}.get(new_status, "📌")
        msg = f"{emoji} {item['name']} → {new_status}"
        if reason:
            msg += f" ({reason})"
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text(f"❌ No encontré '{search}'. Usa /closet para ver IDs.")

async def cmd_where(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /where [id o nombre] [ubicación]\nEj: /where 5 clóset negro, colgado")
        return
    search = context.args[0]
    location = " ".join(context.args[1:])
    item = db_find_item(search)
    if item:
        db_update_item(item["id"], location=location)
        await update.message.reply_text(f"📍 {item['name']} → {location}")
    else:
        await update.message.reply_text(f"❌ No encontré '{search}'.")

async def cmd_closet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = db_get_items()
    if not items:
        await update.message.reply_text("👔 Guardarropa vacío. Usa /add o /bulk para agregar prendas.")
        return
    lines = ["👔 TU GUARDARROPA:\n"]
    current_cat = ""
    for item in items:
        if item["category"] != current_cat:
            current_cat = item["category"]
            lines.append(f"\n📦 {current_cat.upper()}")
        emoji = {"clean": "✅", "dirty": "🧺", "lost": "❓", "damaged": "⚠️"}.get(item["status"], "❔")
        details = item.get("details") or {}
        detail_str = ""
        if details:
            parts = [f"{k}: {v}" for k, v in details.items() if k != "status_reason"]
            if parts:
                detail_str = " | " + ", ".join(parts)
        loc_str = f" 📍{item['location']}" if item.get("location") else ""
        lines.append(f"  {emoji} [{item['id']}] {item['name']}{detail_str}{loc_str}")
    text = "\n".join(lines)
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i+4000])
    else:
        await update.message.reply_text(text)

async def cmd_available(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = db_get_items(status="clean")
    if not items:
        await update.message.reply_text("😬 No tienes nada limpio. ¡A lavar!")
        return
    lines = ["✅ DISPONIBLE:\n"]
    current_cat = ""
    for item in items:
        if item["category"] != current_cat:
            current_cat = item["category"]
            lines.append(f"📦 {current_cat.upper()}")
        lines.append(f"  • [{item['id']}] {item['name']}")
    await update.message.reply_text("\n".join(lines))

async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /feedback me gustó el outfit de hoy")
        return
    db_add_feedback(" ".join(context.args))
    await update.message.reply_text("📝 Feedback guardado 💪")

async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Uso: /daily on o /daily off")
        return
    on = context.args[0].lower() == "on"
    db_update_profile(daily_enabled=on)
    if on:
        await update.message.reply_text(f"⏰ Outfit diario ON → {DAILY_HOUR}:{DAILY_MINUTE:02d}")
    else:
        await update.message.reply_text("⏰ Outfit diario OFF")

async def cmd_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        profile = db_get_profile()
        city = profile.get("city", "Saltillo, Coahuila")
        weather = get_weather(city)
        await update.message.reply_text(f"📍 Ciudad: {city}\n🌤️ {weather}\n\nCambiar: /city Monterrey")
        return
    new_city = " ".join(context.args)
    db_update_profile(city=new_city)
    weather = get_weather(new_city)
    await update.message.reply_text(f"📍 Ciudad → {new_city}\n🌤️ {weather}")

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    profile = db_get_profile()
    if not context.args:
        lines = [
            "👤 TU PERFIL:\n",
            f"📍 Ciudad: {profile.get('city', '?')}",
            f"🎂 Edad: {profile.get('age', '?')}",
            f"📏 Estatura: {profile.get('height_cm', '?')} cm",
            f"⚖️ Peso: {profile.get('weight_kg', '?')} kg",
            f"🎯 Meta: {profile.get('target_weight_kg', '?')} kg",
            f"🎨 Tono: {profile.get('skin_tone', '?')}",
            f"✨ Subtono: {profile.get('undertone', '?')}",
            f"💇 Cabello: {profile.get('hair', '?')}",
            "\n/profile [campo] [valor]",
            "Campos: peso, meta, edad, pelo, tono, subtono, estatura",
        ]
        await update.message.reply_text("\n".join(lines))
        return
    field = context.args[0].lower()
    value = " ".join(context.args[1:])
    if not value:
        await update.message.reply_text("Falta el valor. Ej: /profile peso 70")
        return
    field_map = {
        "peso": ("weight_kg", float), "weight": ("weight_kg", float),
        "meta": ("target_weight_kg", float), "target": ("target_weight_kg", float),
        "edad": ("age", int), "age": ("age", int),
        "pelo": ("hair", str), "hair": ("hair", str), "cabello": ("hair", str),
        "tono": ("skin_tone", str), "skin": ("skin_tone", str),
        "subtono": ("undertone", str), "undertone": ("undertone", str),
        "estatura": ("height_cm", float), "height": ("height_cm", float),
    }
    if field in field_map:
        key, cast = field_map[field]
        try:
            parsed = cast(value) if cast != str else value
            db_update_profile(**{key: parsed})
            await update.message.reply_text(f"✅ {key} → {parsed}")
        except ValueError:
            await update.message.reply_text("❌ Valor inválido")
    else:
        await update.message.reply_text("❌ Campos: peso, meta, edad, pelo, tono, subtono, estatura")

# --- Packing Lists ---
async def cmd_lists(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lists = db_get_lists()
    if not lists:
        await update.message.reply_text("📋 No hay listas. Crea con /listnew [nombre] [desc]")
        return
    lines = ["📋 TUS LISTAS:\n"]
    for l in lists:
        items = l.get("items") or []
        desc = l.get("description", "")
        lines.append(f"  📌 {l['name']} ({len(items)} items){' — ' + desc if desc else ''}")
    lines.append("\nVer: /list [nombre]")
    await update.message.reply_text("\n".join(lines))

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /list [nombre]\nEj: /list viaje")
        return
    name = context.args[0].lower()
    lst = db_get_list(name)
    if not lst:
        await update.message.reply_text(f"❌ Lista '{name}' no existe. Ver disponibles: /lists")
        return
    items = lst.get("items") or []
    lines = [f"📋 {name.upper()}", f"📝 {lst.get('description', '')}\n"]
    for i, item in enumerate(items):
        lines.append(f"  {i+1}. {item}")
    if not items:
        lines.append("  (vacía)")
    lines.append(f"\n/listadd {name} [item] | /listdel {name} [#]")
    await update.message.reply_text("\n".join(lines))

async def cmd_listadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /listadd [lista] [item]")
        return
    name = context.args[0].lower()
    item_text = " ".join(context.args[1:])
    lst = db_get_list(name)
    if not lst:
        await update.message.reply_text(f"❌ '{name}' no existe. Crear: /listnew {name}")
        return
    items = lst.get("items") or []
    items.append(item_text)
    db_update_list_items(name, items)
    await update.message.reply_text(f"✅ '{item_text}' → {name}")

async def cmd_listdel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /listdel [lista] [#num]")
        return
    name = context.args[0].lower()
    try:
        index = int(context.args[1].replace("#", "")) - 1
    except ValueError:
        await update.message.reply_text("❌ Necesito un número")
        return
    lst = db_get_list(name)
    if not lst:
        await update.message.reply_text(f"❌ Lista '{name}' no existe")
        return
    items = lst.get("items") or []
    if 0 <= index < len(items):
        removed = items.pop(index)
        db_update_list_items(name, items)
        await update.message.reply_text(f"🗑️ '{removed}' eliminado de {name}")
    else:
        await update.message.reply_text("❌ Número fuera de rango. Usa /list [nombre]")

async def cmd_listnew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /listnew [nombre] [descripción]")
        return
    name = context.args[0].lower()
    desc = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    result = db_create_list(name, desc)
    if result:
        await update.message.reply_text(f"✅ Lista '{name}' creada")
    else:
        await update.message.reply_text(f"⚠️ '{name}' ya existe")

async def cmd_listremove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /listremove [nombre]\n⚠️ Elimina la lista completa")
        return
    name = context.args[0].lower()
    if db_delete_list(name):
        await update.message.reply_text(f"🗑️ Lista '{name}' eliminada")
    else:
        await update.message.reply_text(f"❌ '{name}' no existe")

# --- Message Handler ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if context.user_data.get("awaiting_addpro"):
        context.user_data["awaiting_addpro"] = False
        results = _parse_detailed_lines([text.strip()])
        if results:
            await update.message.reply_text(f"✅ {results[0]}")
        else:
            await update.message.reply_text("❌ Formato incorrecto. Revisa /addpro")
        return

    if context.user_data.get("awaiting_bulk"):
        context.user_data["awaiting_bulk"] = False
        lines = text.strip().split("\n")
        results = _parse_detailed_lines(lines)
        if results:
            await update.message.reply_text(f"✅ {len(results)} prendas agregadas.")
        else:
            await update.message.reply_text("❌ No pude agregar nada. Revisa formato.")
        return

    await update.message.reply_text("🤔 Checando clóset y clima...")
    try:
        suggestion = await get_ai_suggestion(text)
        db_add_history(suggestion, text)
        await update.message.reply_text(suggestion)
    except Exception as e:
        logger.error(f"AI error: {e}")
        await update.message.reply_text("❌ Error. Intenta de nuevo.")

def _parse_detailed_lines(lines):
    results = []
    for line in lines:
        line = line.strip()
        if not line or ":" not in line:
            continue
        first_split = line.split("|")
        cat_parts = first_split[0].split(":", 1)
        category = cat_parts[0].strip().lower()
        name = cat_parts[1].strip() if len(cat_parts) > 1 else ""
        if not name or category not in ALL_CATEGORIES:
            continue
        details = {}
        for part in first_split[1:]:
            if ":" in part:
                key, val = part.split(":", 1)
                details[key.strip().lower()] = val.strip()
        item = db_add_item(category, name, details)
        if item:
            results.append(f"{name} → {category} (ID: {item['id']})")
    return results

async def send_daily_outfit(context: ContextTypes.DEFAULT_TYPE):
    profile = db_get_profile()
    if not profile.get("daily_enabled") or OWNER_CHAT_ID == 0:
        return
    try:
        suggestion = await get_ai_suggestion("outfit para ir al trabajo hoy, casual pero presentable")
        db_add_history(suggestion, "daily auto")
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=f"☀️ Buenos días! Tu outfit:\n\n{suggestion}")
    except Exception as e:
        logger.error(f"Daily outfit error: {e}")


# --- Main ---
def main():
    if not TELEGRAM_TOKEN:
        print("❌ Falta TELEGRAM_TOKEN")
        return
    if not GEMINI_API_KEY:
        print("❌ Falta GEMINI_API_KEY")
        return
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ Falta SUPABASE_URL o SUPABASE_KEY")
        return

    init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("outfit", cmd_outfit))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("addpro", cmd_addpro))
    app.add_handler(CommandHandler("bulk", cmd_bulk))
    app.add_handler(CommandHandler("dirty", cmd_status_change))
    app.add_handler(CommandHandler("clean", cmd_status_change))
    app.add_handler(CommandHandler("lost", cmd_status_change))
    app.add_handler(CommandHandler("where", cmd_where))
    app.add_handler(CommandHandler("closet", cmd_closet))
    app.add_handler(CommandHandler("available", cmd_available))
    app.add_handler(CommandHandler("feedback", cmd_feedback))
    app.add_handler(CommandHandler("daily", cmd_daily))
    app.add_handler(CommandHandler("city", cmd_city))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("lists", cmd_lists))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("listadd", cmd_listadd))
    app.add_handler(CommandHandler("listdel", cmd_listdel))
    app.add_handler(CommandHandler("listnew", cmd_listnew))
    app.add_handler(CommandHandler("listremove", cmd_listremove))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
    job_time = time(hour=DAILY_HOUR, minute=DAILY_MINUTE, tzinfo=tz)
    app.job_queue.run_daily(send_daily_outfit, time=job_time)

    RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    PORT = int(os.getenv("PORT", "10000"))
    webhook_base = WEBHOOK_URL or RENDER_URL

    if webhook_base:
        webhook_full = f"{webhook_base}/webhook"
        print(f"🤖 Outfit Bot (Supabase + webhook: {webhook_full})")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="/webhook",
            webhook_url=webhook_full,
            drop_pending_updates=True,
        )
    else:
        print("🤖 Outfit Bot (Supabase + polling local)")
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
