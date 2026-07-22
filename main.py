"""
main.py
=======
نقطة تشغيل بوت تليجرام. تُحمّل متغيرات البيئة، تُهيّئ التطبيق،
وتُسجّل المعالجات ثم تبدأ الاستطلاع (polling).
"""

import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from handlers import (
    cancel,
    handle_callback,
    handle_document,
    handle_text,
    help_command,
    reset,
    start,
)

# حمّل متغيرات البيئة من ملف .env إن وجد
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# قلّل ضجيج مكتبة httpx
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "❌ BOT_TOKEN غير مضبوط. انسخ .env.example إلى .env وضع توكن البوت."
        )

    app = Application.builder().token(token).build()

    # الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("cancel", cancel))

    # أزرار Inline
    app.add_handler(CallbackQueryHandler(handle_callback))

    # المستندات (ملفات ZIP) والنصوص
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("🤖 البوت يعمل الآن… (Ctrl+C للإيقاف)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
