import discord
from discord.ext import commands
import asyncio
import datetime
import pytz
import requests
import json
import os
import time
import traceback

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
NOTION_TOKEN = "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"
NOTION_DATABASE_ID = "1b4f93ca228080128009fdeba2734fac"
DEFAULT_CHANNEL_ID = 1053793098087534673  # Default channel ID

# Set GMT-5 timezone (Peru Time)
PERU_TZ = pytz.timezone("America/Lima")

# Store channel_id in a file so it persists between restarts
CHANNEL_FILE = "channel_config.json"
# Store local cache of Notion data
CACHE_FILE = "notion_cache.json"

# Store the last reminders sent to each user
user_last_reminders = {}

# Track which reminders have already been sent to avoid duplicates
sent_reminders = {}

# Local cache of tasks from Notion
notion_tasks_cache = []
last_cache_update = 0

# Setup for commands
intents = discord.Intents.default()
intents.message_content = True  # Enable message content intent for commands
# Note: members intent requires enabling in Discord Developer Portal
# intents.members = True

async def wait_until_7am():
    """Waits until 7 AM Peru Time before checking reminders."""
    while True:
        now = datetime.datetime.now(PERU_TZ)
        next_run = now.replace(hour=7, minute=0, second=0, microsecond=0)

        # If it's already past 7 AM today, set the next run for tomorrow
        if now >= next_run:
            next_run += datetime.timedelta(days=1)

        # Sleep until 7 AM
        sleep_seconds = (next_run - now).total_seconds()
        print(f"Sleeping for {sleep_seconds / 3600:.2f} hours until 7 AM Peru Time...")
        await asyncio.sleep(sleep_seconds)

        # Once 7 AM arrives, run the reminder function
        await check_reminders()

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command('help')  # Remove the default help command

def save_channel_config(channel_id):
    """Save the channel ID to a configuration file"""
    config = {"channel_id": channel_id}
    with open(CHANNEL_FILE, "w") as f:
        json.dump(config, f)
    print(f"Channel config saved: {channel_id}")

def load_channel_config():
    """Load the channel ID from the configuration file"""
    try:
        if os.path.exists(CHANNEL_FILE):
            with open(CHANNEL_FILE, "r") as f:
                config = json.load(f)
                return config.get("channel_id", DEFAULT_CHANNEL_ID)
    except Exception as e:
        print(f"Error loading channel config: {e}")
    return DEFAULT_CHANNEL_ID

# Load channel ID on startup
CHANNEL_ID = load_channel_config()

def save_cache(tasks):
    """Save the Notion tasks to a local cache file"""
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(tasks, f, indent=2)
        print(f"Task cache saved: {len(tasks)} tasks")
    except Exception as e:
        print(f"Error saving task cache: {e}")

def load_cache():
    """Load the Notion tasks from the local cache file"""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r") as f:
                tasks = json.load(f)
                print(f"Task cache loaded: {len(tasks)} tasks")
                return tasks
    except Exception as e:
        print(f"Error loading task cache: {e}")
    return []

# Save reminder tracking data to persist between restarts
def save_reminder_tracking():
    """Save the reminder tracking data to a file"""
    try:
        tracking_data = {
            "sent_reminders": sent_reminders
        }
        with open("reminder_tracking.json", "w") as f:
            json.dump(tracking_data, f)
        print(f"Reminder tracking saved: {len(sent_reminders)} entries")
    except Exception as e:
        print(f"Error saving reminder tracking: {e}")

# Load reminder tracking data on startup
def load_reminder_tracking():
    """Load the reminder tracking data from a file"""
    global sent_reminders
    try:
        if os.path.exists("reminder_tracking.json"):
            with open("reminder_tracking.json", "r") as f:
                data = json.load(f)
                sent_reminders = data.get("sent_reminders", {})
                print(f"Reminder tracking loaded: {len(sent_reminders)} entries")
    except Exception as e:
        print(f"Error loading reminder tracking: {e}")
        sent_reminders = {}

def get_notion_data():
    """Fetches data from the Notion database."""
    global notion_tasks_cache, last_cache_update
    
    # If we've updated the cache recently, use the cached version
    current_time = time.time()
    if current_time - last_cache_update < 300:  # 5 minutes
        print("Using cached Notion data")
        return {"results": notion_tasks_cache}
    
    print("Fetching fresh data from Notion...")
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    
    try:
        # First, try to get data from Notion
        response = requests.post(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            # Save the results to our cache
            notion_tasks_cache = data.get("results", [])
            last_cache_update = current_time
            save_cache(notion_tasks_cache)
            return data
        else:
            print("Error fetching Notion data:", response.status_code, response.text)
            
            # If we can't get fresh data, use our cached data
            if notion_tasks_cache:
                print("Using cached data due to API error")
                return {"results": notion_tasks_cache}
            
            # If no cache and API failed, load from disk
            saved_cache = load_cache()
            if saved_cache:
                notion_tasks_cache = saved_cache
                return {"results": saved_cache}
                
            return None
    except Exception as e:
        print(f"Exception fetching Notion data: {e}")
        
        # If exception, try to use cache
        if notion_tasks_cache:
            print("Using cached data due to exception")
            return {"results": notion_tasks_cache}
            
        # If no memory cache, try disk cache
        saved_cache = load_cache()
        if saved_cache:
            notion_tasks_cache = saved_cache
            return {"results": saved_cache}
            
        return None

def extract_property_value(prop, property_type):
    """Extract value from a Notion property object based on its type"""
    try:
        if property_type == "title":
            if "title" in prop and len(prop["title"]) > 0:
                return prop["title"][0]["text"]["content"]
            elif "plain_text" in prop:  # Direct text value
                return prop["plain_text"]
                
        elif property_type == "rich_text":
            if "rich_text" in prop and len(prop["rich_text"]) > 0:
                return prop["rich_text"][0]["text"]["content"]
            elif "plain_text" in prop:  # Direct text value
                return prop["plain_text"]
                
        elif property_type == "date":
            if "date" in prop and prop["date"] is not None:
                return prop["date"]["start"]
        
        # For select and multi_select properties
        elif property_type == "select":
            if "select" in prop and prop["select"] is not None:
                return prop["select"]["name"]
                
        elif property_type == "multi_select":
            if "multi_select" in prop and len(prop["multi_select"]) > 0:
                return [item["name"] for item in prop["multi_select"]]
        
        # For number properties
        elif property_type == "number":
            if "number" in prop:
                return prop["number"]
        
        # For checkbox properties
        elif property_type == "checkbox":
            if "checkbox" in prop:
                return prop["checkbox"]
                
        # For people properties
        elif property_type == "people":
            if "people" in prop and len(prop["people"]) > 0:
                return [person["id"] for person in prop["people"]]
                
        # For relation properties
        elif property_type == "relation":
            if "relation" in prop and len(prop["relation"]) > 0:
                return [rel["id"] for rel in prop["relation"]]
                
        # For URL properties
        elif property_type == "url":
            if "url" in prop:
                return prop["url"]
                
        # For email properties
        elif property_type == "email":
            if "email" in prop:
                return prop["email"]
                
        # For phone_number properties
        elif property_type == "phone_number":
            if "phone_number" in prop:
                return prop["phone_number"]
                
        # For formula properties
        elif property_type == "formula":
            if "formula" in prop:
                formula_type = prop["formula"]["type"]
                return prop["formula"].get(formula_type)
        
        # If we couldn't extract a value with the given type, try some common paths
        common_paths = ["content", "name", "text", "value", "string"]
        for path in common_paths:
            if path in prop:
                return prop[path]
        
        return None
    except Exception as e:
        print(f"Error extracting property value: {str(e)}")
        return None

def get_property_value(properties, key, property_type):
    """Safely extracts property values with error handling"""
    try:
        # Try different variations of the key (Notion API might format differently)
        possible_keys = [
            key,
            key.lower().replace(" ", "_"),
            key.replace(" ", ""),
            key.lower().replace(" ", "")
        ]
        
        found_key = None
        for possible_key in possible_keys:
            if possible_key in properties:
                found_key = possible_key
                break
                
        if not found_key:
            # Try to detect if we have property with similar name
            for prop_key in properties.keys():
                # Check if the key is contained within the property key or vice versa
                if (key.lower() in prop_key.lower()) or (prop_key.lower() in key.lower()):
                    found_key = prop_key
                    break
                    
        if not found_key:
            # For debugging, print all available keys
            available_keys = list(properties.keys())
            print(f"Property '{key}' not found. Available properties: {available_keys}")
            return None
        
        # Get the property object
        prop = properties[found_key]
        
        # Extract the value based on property type
        value = extract_property_value(prop, property_type)
        
        # If we couldn't extract a value, print the property format for debugging
        if value is None:
            print(f"Property '{key}' format: {json.dumps(prop, indent=2)}")
        
        return value
    except Exception as e:
        print(f"Error extracting '{key}': {str(e)}")
        traceback.print_exc()  # Print full stack trace for debugging
        return None

def clean_old_reminders():
    """Remove reminder records that are no longer needed"""
    today = datetime.datetime.now(PERU_TZ).date().isoformat()
    keys_to_remove = []
    
    for reminder_id, sent_date in sent_reminders.items():
        if sent_date != today:
            keys_to_remove.append(reminder_id)
            
    for key in keys_to_remove:
        del sent_reminders[key]
        
    print(f"Cleaned up {len(keys_to_remove)} old reminder records")
    
    # Save the updated tracking data
    save_reminder_tracking()

async def send_reminder(channel, user_id, message_text, task, asset, date_type, date_value):
    """Send a reminder and store it in tracking systems to prevent duplicates"""
    try:
        # Create a unique identifier for this specific reminder
        today = datetime.datetime.now(PERU_TZ).date().isoformat()
        reminder_id = f"{user_id}:{task}:{asset}:{date_type}:{date_value}"
        
        # Check if we already sent this reminder today
        if reminder_id in sent_reminders and sent_reminders[reminder_id] == today:
            print(f"Skipping duplicate reminder: {reminder_id}")
            return
        
        # Send the reminder
        message = await channel.send(message_text)
        
        # Store this reminder as sent today
        sent_reminders[reminder_id] = today
        # Save tracking data after each update
        save_reminder_tracking()
        
        # Also add to the user's reminder history (keeping the existing functionality)
        if user_id not in user_last_reminders:
            user_last_reminders[user_id] = []
        
        # Add to user's reminder history with timestamp
        user_last_reminders[user_id].append({
            "message": message_text,
            "timestamp": datetime.datetime.now(PERU_TZ).isoformat(),
            "message_id": message.id,
            "reminder_id": reminder_id
        })
        
        # Keep only the last 10 reminders per user
        if len(user_last_reminders[user_id]) > 10:
            user_last_reminders[user_id] = user_last_reminders[user_id][-10:]
            
        print(f"Sent reminder: {reminder_id}")
    except Exception as e:
        print(f"Error sending reminder: {e}")

async def process_dates_for_reminders(channel, user_id, task, asset, start_date_str, correction_date_str, due_date_str, peru_now, two_days_before=None):
    """Process dates and send reminders for a task"""
    try:
        # Always include asset information in messages
        asset_text = f" for **{asset}**" if asset else ""
        
        # Convert string dates to datetime objects
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date() if start_date_str else None
        correction_date = datetime.datetime.strptime(correction_date_str, "%Y-%m-%d").date() if correction_date_str else None
        due_date = datetime.datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else None
        
        # Get today's date
        today = peru_now.date() if isinstance(peru_now, datetime.datetime) else peru_now
        
        # Send start date notifications if applicable
        if start_date:
            # Calculate days until start
            days_until_start = (start_date - today).days
            
            if days_until_start == 2:
                due_text = f" (Due on {due_date_str})" if due_date_str else ""
                msg = f"⏳ <@{user_id}>, your task **{task}**{asset_text} starts in 2 days!{due_text}"
                await send_reminder(channel, user_id, msg, task, asset, "start_2days", start_date_str)
                
            if days_until_start == 0:
                due_text = f" (Due on {due_date_str})" if due_date_str else ""
                msg = f"🚀 <@{user_id}>, your task **{task}**{asset_text} starts today!{due_text}"
                await send_reminder(channel, user_id, msg, task, asset, "start_today", start_date_str)
        
        # Process due date if it exists - send reminder 2 days before due date
        if due_date:
            # Calculate days until due
            days_until_due = (due_date - today).days
            
            if days_until_due == 2:
                msg = f"⚠️ <@{user_id}>, reminder: Your task **{task}**{asset_text} is due in 2 days!"
                await send_reminder(channel, user_id, msg, task, asset, "due_2days", due_date_str)
                
            if days_until_due == 0:
                msg = f"⏳ <@{user_id}>, last call! Your task **{task}**{asset_text} is due today!"
                await send_reminder(channel, user_id, msg, task, asset, "due_today", due_date_str)
                
        # Add correction date notifications - send reminder 1 day before correction date
        if correction_date:
            # Calculate days until correction
            days_until_correction = (correction_date - today).days
            
            if days_until_correction == 1:
                msg = f"📝 <@{user_id}>, correction for your task **{task}**{asset_text} is tomorrow!"
                await send_reminder(channel, user_id, msg, task, asset, "correction_1day", correction_date_str)
                
            if days_until_correction == 0:
                msg = f"📝 <@{user_id}>, correction for your task **{task}**{asset_text} is today!"
                await send_reminder(channel, user_id, msg, task, asset, "correction_today", correction_date_str)
                
    except ValueError as e:
        print(f"Date parsing error for task '{task}': {str(e)}")
    except Exception as e:
        print(f"Error processing reminders: {str(e)}")
        traceback.print_exc()  # Print full stack trace for debugging

async def check_reminders():
    """Checks the Notion database and sends reminders."""
    global CHANNEL_ID
    
    # Get the channel to send reminders to
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f"Warning: Channel with ID {CHANNEL_ID} not found.")
        return
        
    # Get current Peru time
    peru_now = datetime.datetime.now(PERU_TZ)
    
    notion_data = get_notion_data()
    if not notion_data:
        print("No Notion data received")
        return
        
    print(f"Processing {len(notion_data.get('results', []))} items from Notion")
    print(f"Current date in Peru: {peru_now.date().isoformat()}")
    
    # Track rows we've processed
    processed_rows = 0
    skipped_rows = 0
    
    # Debug the first row to understand structure
    if notion_data.get("results") and len(notion_data["results"]) > 0:
        sample_row = notion_data["results"][0]
        print("Sample row data structure:")
        print(f"ID: {sample_row.get('id')}")
        print(f"Property keys: {list(sample_row.get('properties', {}).keys())}")

    # Process rows from Notion
    for page in notion_data.get("results", []):
        try:
            properties = page.get("properties", {})
            
            # Extract fields with error handling - dynamically extract from available properties
            user_id = get_property_value(properties, "User", "title")
            task = get_property_value(properties, "Task", "rich_text")
            asset = get_property_value(properties, "Asset", "rich_text")
            start_date_str = get_property_value(properties, "Start Date", "date")
            correction_date_str = get_property_value(properties, "Correction Date", "date")
            due_date_str = get_property_value(properties, "Due Date", "date")
            
            # Debug output for data extraction
            print(f"Extracted data: User={user_id}, Task={task}, Asset={asset}, Start={start_date_str}")
            
            # Skip this item if critical fields are missing
            if not all([user_id, task, start_date_str]):
                print(f"Skipping item due to missing critical fields")
                skipped_rows += 1
                continue
                
            processed_rows += 1
            
            # Process dates - note the added two_days_before parameter with default value
            try:
                await process_dates_for_reminders(
                    channel, user_id, task, asset, 
                    start_date_str, correction_date_str, due_date_str, 
                    peru_now, two_days_before=peru_now.date() - datetime.timedelta(days=2)
                )
            except Exception as e:
                print(f"Error processing reminders for {task} - {asset}: {e}")
                traceback.print_exc()  # Print full stack trace for debugging
                # Continue processing other tasks even if one fails
                continue
        except Exception as e:
            print(f"Error processing Notion row: {e}")
            traceback.print_exc()  # Print full stack trace for debugging
    
    print(f"Reminder check completed. Processed {processed_rows} rows, skipped {skipped_rows} rows.")

@bot.command(name="aqui")
async def set_channel_here(ctx):
    """Command to set the current channel as the one to interact with"""
    global CHANNEL_ID
    
    # Update the channel ID
    CHANNEL_ID = ctx.channel.id
    
    # Save the configuration
    save_channel_config(CHANNEL_ID)
    
    # Send confirmation
    await ctx.send(f"✅ Este canal ha sido configurado para recibir recordatorios.")

@bot.command(name="test")
async def test_reminder(ctx, user_id_or_mention):
    """Test reminders for a specific user by ID or mention"""
    # Extract user ID from mention or use as is
    user_id = user_id_or_mention
    
    # If it's a mention, extract the ID
    if user_id_or_mention.startswith('<@') and user_id_or_mention.endswith('>'):
        user_id = user_id_or_mention[2:-1]
        # Remove ! if it's a nickname mention
        if user_id.startswith('!'):
            user_id = user_id[1:]
    
    # Print for debugging
    print(f"Testing user with ID: {user_id}")
    
    # Try to fetch the user
    try:
        user = await bot.fetch_user(int(user_id))
        user_tag = f"{user.name}#{user.discriminator}" if hasattr(user, 'discriminator') else user.name
        print(f"Found user: {user_tag}")
    except Exception as e:
        print(f"Error fetching user: {e}")
        user = None
    
    # Check if we have any reminders for this user
    if user_id in user_last_reminders and user_last_reminders[user_id]:
        # Get the last reminder for this user
        last_reminder = user_last_reminders[user_id][-1]
        
        # Send a test reminder to the current channel
        if user:
            test_message = f"**Prueba de recordatorio para {user.mention}**\n\n📝 Último recordatorio enviado: \n```\n{last_reminder['message']}\n```\n⏰ Enviado: {last_reminder['timestamp']}"
        else:
            test_message = f"**Prueba de recordatorio para <@{user_id}>**\n\n📝 Último recordatorio enviado: \n```\n{last_reminder['message']}\n```\n⏰ Enviado: {last_reminder['timestamp']}"
            
        await ctx.send(test_message)
        
        # Send a direct message to the user if we found them
        if user:
            try:
                await user.send(f"¡Hola {user.mention}! Este es un mensaje de prueba del bot de recordatorios.")
                await ctx.send(f"✅ Mensaje de prueba enviado a <@{user_id}>")
            except discord.Forbidden:
                await ctx.send(f"⚠️ No se pudo enviar un mensaje directo a <@{user_id}>. Es posible que tenga los mensajes directos desactivados.")
    else:
        if user:
            await ctx.send(f"ℹ️ No hay recordatorios previos para {user.mention}")
            
            # Still try to send a test message
            try:
                await user.send(f"¡Hola {user.mention}! Este es un mensaje de prueba del bot de recordatorios.")
                await ctx.send(f"✅ Mensaje de prueba enviado a {user.mention}")
            except discord.Forbidden:
                await ctx.send(f"⚠️ No se pudo enviar un mensaje directo a {user.mention}. Es posible que tenga los mensajes directos desactivados.")
        else:
            await ctx.send(f"ℹ️ No hay recordatorios previos para el usuario con ID <@{user_id}>")
            await ctx.send("⚠️ No se pudo encontrar al usuario para enviar un mensaje directo.")

@bot.command(name="reload")
async def reload_data(ctx):
    """Force reload data from Notion"""
    global last_cache_update
    await ctx.send("🔄 Recargando datos de Notion...")
    
    # Reset the cache timer to force a refresh
    last_cache_update = 0
    
    try:
        # Run the reminder check which will fetch fresh data
        await check_reminders()
        await ctx.send("✅ Datos recargados exitosamente.")
    except Exception as e:
        await ctx.send(f"❌ Error al recargar datos: {str(e)}")

@bot.command(name="dumpdata")
async def dump_data(ctx):
    """Dump the first 5 rows of data for debugging"""
    global notion_tasks_cache
    
    # Get the cached data or fetch new data
    notion_data = get_notion_data()
    if not notion_data or not notion_data.get("results"):
        await ctx.send("❌ No hay datos disponibles para mostrar.")
        return
        
    # Print just a summary
    total_rows = len(notion_data.get("results", []))
    await ctx.send(f"📊 Total de filas en la base de datos: {total_rows}")
    
    # Show the structure of the first row if available
    if total_rows > 0:
        first_row = notion_data["results"][0]
        # Create a simplified version to avoid too long messages
        simplified = {
            "id": first_row.get("id", "N/A"),
            "properties_keys": list(first_row.get("properties", {}).keys())
        }
        
        # Print in code blocks for better formatting
        await ctx.send(f"📋 Estructura de la primera fila:\n```json\n{json.dumps(simplified, indent=2)}\n```")
    
    # Show reminder tracking stats
    await ctx.send(f"🔔 Recordatorios enviados hoy: {len(sent_reminders)}")
    
    # Show user reminder stats
    reminder_counts = {user_id: len(reminders) for user_id, reminders in user_last_reminders.items()}
    if reminder_counts:
        await ctx.send(f"🔔 Historial de recordatorios por usuario: {reminder_counts}")
    else:
        await ctx.send("ℹ️ No hay recordatorios guardados todavía.")

@bot.command(name="showtable")
async def show_table_structure(ctx, rows_to_show=3):
    """Show the structure of the Notion table for debugging"""
    notion_data = get_notion_data()
    if not notion_data or not notion_data.get("results"):
        await ctx.send("❌ No hay datos disponibles para mostrar.")
        return
    
    results = notion_data.get("results", [])
    properties_structure = {}
    
    # Extract property structure from up to rows_to_show rows
    for i, page in enumerate(results[:int(rows_to_show)]):
        properties = page.get("properties", {})
        
        for prop_name, prop_value in properties.items():
            if prop_name not in properties_structure:
                properties_structure[prop_name] = []
            
            # Extract property type
            prop_type = prop_value.get("type", "unknown")
            properties_structure[prop_name].append(prop_type)
    
    # Create a report
    report = "📊 **Notion Table Structure**\n\n"
    for prop_name, types in properties_structure.items():
        # Get the most common type
        if types:
            most_common_type = max(set(types), key=types.count)
            report += f"- **{prop_name}**: {most_common_type}\n"
    
    # Send the report
    await ctx.send(report)

@bot.command(name="resetreminders")
async def reset_reminders(ctx):
    """Reset the reminder tracking to force reminders to be sent again"""
    global sent_reminders
    
    # Save the count for the confirmation message
    old_count = len(sent_reminders)
    
    # Clear the reminder tracking
    sent_reminders = {}
    save_reminder_tracking()
    
    await ctx.send(f"✅ Tracking de recordatorios reiniciado. Se borraron {old_count} registros.")

@bot.command(name="help")
async def custom_help(ctx):
    """Shows help information for bot commands"""
    help_embed = discord.Embed(
        title="📋 Comandos del Bot de Recordatorios",
        description="Aquí están los comandos disponibles para interactuar con el bot:",
        color=discord.Color.blue()
    )
    
    help_embed.add_field(
        name="!aqui", 
        value="Establece el canal actual como el canal de recordatorios. Todos los recordatorios se enviarán a este canal.", 
        inline=False
    )
    
    help_embed.add_field(
        name="!test <ID or @mention>", 
        value="Prueba los recordatorios para un usuario específico. Puedes usar su ID de Discord (como aparece en la tabla de Notion) o mencionarlo directamente. Muestra el último recordatorio enviado y envía un mensaje de prueba.", 
        inline=False
    )
    
    help_embed.add_field(
        name="!reload", 
        value="Fuerza una recarga de los datos desde Notion, ignorando la caché.", 
        inline=False
    )
    
    help_embed.add_field(
        name="!dumpdata", 
        value="Muestra información de diagnóstico sobre los datos cargados en el bot.", 
        inline=False
    )
    
    help_embed.add_field(
        name="!showtable", 
        value="Muestra la estructura de la tabla de Notion para depuración.", 
        inline=False
    )
    
    help_embed.add_field(
        name="!resetreminders", 
        value="Reinicia el tracking de recordatorios enviados. Útil si necesitas que los recordatorios se envíen de nuevo.", 
        inline=False
    )
    
    help_embed.add_field(
        name="!help", 
        value="Muestra este mensaje de ayuda con la descripción de todos los comandos disponibles.", 
        inline=False
    )
    
    help_embed.set_footer(text="Bot de Recordatorios Notion | Desarrollado para Ari & Rumi")
    
    await ctx.send(embed=help_embed)

@bot.event
async def on_message(message):
    """Process messages for custom commands"""
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # Process commands through the command handler
    await bot.process_commands(message)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    # Load reminder tracking data
    load_reminder_tracking()
    # Start the scheduled task
    bot.loop.create_task(scheduled_check())
    print(f"Using channel ID: {CHANNEL_ID}")

# Run bot periodically
async def scheduled_check():
    await bot.wait_until_ready()  # Wait until the bot is ready
    while not bot.is_closed():
        try:
            # Clean up old reminders at the start of each check
            clean_old_reminders()
            
            # Check reminders as usual
            await check_reminders()
            print("Reminder check completed")
        except Exception as e:
            print(f"Error in scheduled check: {str(e)}")
            traceback.print_exc()  # Print full stack trace for debugging
        # Check every 50 minutes as requested
        await asyncio.sleep(3000)  # 50 minutes = 3000 seconds

# Run the bot
bot.run(TOKEN)