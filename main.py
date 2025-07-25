import os
import uuid
import json
import logging
from typing import Dict, List, Any
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# Load from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PRIVATE_CHANNEL_ID = int(os.getenv("PRIVATE_CHANNEL_ID", 0))
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")
STORAGE_FILE = "storage.json"
DEFAULT_DELETE_SECONDS = int(os.getenv("DELETE_TIMER", 600))

# Logger setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# In-memory storage
video_storage: Dict[str, List[int]] = {}
delete_timer: Dict[str, int] = {"timer": DEFAULT_DELETE_SECONDS}
batch_sessions: Dict[int, List[Any]] = {}

# Storage helpers
def load_storage() -> None:
    if os.path.exists(STORAGE_FILE):
        with open(STORAGE_FILE, "r") as f:
            data = json.load(f)
            video_storage.update(data.get("storage", {}))
            delete_timer.update(data.get("timer", {"timer": DEFAULT_DELETE_SECONDS}))

def save_storage() -> None:
    with open(STORAGE_FILE, "w") as f:
        json.dump({"storage": video_storage, "timer": delete_timer}, f)

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    chat_id = update.effective_chat.id

    if args:
        token = args[0]
        message_ids = video_storage.get(token)
        if message_ids:
            try:
                for msg_id in message_ids:
                    await context.bot.copy_message(
                        chat_id=chat_id,
                        from_chat_id=PRIVATE_CHANNEL_ID,
                        message_id=msg_id,
                        protect_content=True
                    )
                await update.message.reply_text("✅ Media delivered (Forwarding protected).")
            except Exception as e:
                logger.error(f"Forward error: {e}")
                await update.message.reply_text("❌ Error: Could not deliver media.")
        else:
            await update.message.reply_text("❌ Invalid or expired link.")
    else:
        await update.message.reply_text(
            f"👋 Welcome to @{BOT_USERNAME}!\n"
            f"🔒 Only admin can upload content.\n"
            f"⏱️ Auto-delete timer is set to {delete_timer['timer']} seconds."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Only admin can access this command.")
        return

    help_text = (
        "📋 *Admin Commands:*\n"
        "/start <token> - Get stored media by token\n"
        "/settimer <seconds> - Set auto-delete timer\n"
        "/batch - Start batch upload\n"
        "/done - Finish batch and get link\n"
        "/help - Show this help menu"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def settimer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Only the admin can set timer.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /settimer <seconds>")
        return

    seconds = int(context.args[0])
    delete_timer["timer"] = seconds
    save_storage()
    await update.message.reply_text(f"✅ Auto-delete timer updated to {seconds} seconds.")

# Media handlers
async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id

    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Only the admin can upload media.")
        return

    # Batch mode
    if user.id in batch_sessions:
        batch_sessions[user.id].append(update.message)
        await update.message.reply_text("📥 Added to batch. Send /done to finish.")
        return

    try:
        forwarded = await context.bot.copy_message(
            chat_id=PRIVATE_CHANNEL_ID,
            from_chat_id=chat_id,
            message_id=update.message.message_id,
            protect_content=True
        )

        token = str(uuid.uuid4())
        video_storage[token] = [forwarded.message_id]
        save_storage()

        link = f"https://t.me/{BOT_USERNAME}?start={token}"
        await update.message.reply_text(f"✅ Media stored!\n🔗 Link: {link}")

        # Schedule deletion
        context.job_queue.run_once(
            delete_from_channel,
            when=delete_timer["timer"],
            name=token,
            data={"message_ids": [forwarded.message_id]}
        )
    except Exception as e:
        logger.error(f"Media handling error: {e}")
        await update.message.reply_text("❌ Error while storing media.")

async def batch_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Only admin can start batch upload.")
        return

    if user.id in batch_sessions:
        await update.message.reply_text("⚠️ You already started a batch.")
        return

    batch_sessions[user.id] = []
    await update.message.reply_text("📦 Batch upload started. Send media now.")

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Only admin can finish batch.")
        return

    if user.id not in batch_sessions or not batch_sessions[user.id]:
        await update.message.reply_text("⚠️ No active batch or no media sent.")
        return

    messages = batch_sessions.pop(user.id)
    message_ids = []

    try:
        for msg in messages:
            forwarded = await context.bot.copy_message(
                chat_id=PRIVATE_CHANNEL_ID,
                from_chat_id=msg.chat_id,
                message_id=msg.message_id,
                protect_content=True
            )
            message_ids.append(forwarded.message_id)

        token = str(uuid.uuid4())
        video_storage[token] = message_ids
        save_storage()

        link = f"https://t.me/{BOT_USERNAME}?start={token}"
        await update.message.reply_text(f"✅ Batch stored!\n🔗 Link: {link}")

        context.job_queue.run_once(
            delete_from_channel,
            when=delete_timer["timer"],
            name=token,
            data={"message_ids": message_ids}
        )
    except Exception as e:
        logger.error(f"Batch error: {e}")
        await update.message.reply_text("❌ Error during batch upload.")

# Job to delete messages from channel and remove token
async def delete_from_channel(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    token = context.job.name
    message_ids = data.get("message_ids", [])

    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=PRIVATE_CHANNEL_ID, message_id=msg_id)
        except Exception as e:
            logger.warning(f"Delete error: {e}")

    if token in video_storage:
        del video_storage[token]
        save_storage()

# Global error handler
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Error: {context.error}")

# Main function
def main() -> None:
    load_storage()

    application = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("settimer", settimer))
    application.add_handler(CommandHandler("batch", batch_command))
    application.add_handler(CommandHandler("done", done_command))

    application.add_handler(MessageHandler(
        (filters.VIDEO | filters.PHOTO) & ~filters.COMMAND,
        handle_media
    ))

    application.add_error_handler(error_handler)

    # Run the bot
    application.run_polling(allowed_updates=None)

if __name__ == "__main__":
    main()
