"""
handlers.py
===========
معالجات أوامر ورسائل بوت تليجرام. تدير آلة الحالة لكل مستخدم
عبر context.user_data وتنسّق مع github_uploader لرفع محتوى ZIP.
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import tempfile
import time

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import github_uploader as gh
from state import (
    State,
    KEY_STATE, KEY_TOKEN, KEY_OWNER, KEY_REPO, KEY_BRANCH,
    KEY_DEFAULT_BRANCH, KEY_CANCEL, KEY_UPLOADING, KEY_LAST_FAILED,
)

# الحد الأقصى لحجم ملف ZIP (افتراضي 20MB حسب حدود Telegram Bot API القياسية)
MAX_ZIP_MB = int(os.getenv("MAX_ZIP_MB", "20"))
MAX_ZIP_SIZE = MAX_ZIP_MB * 1024 * 1024


WELCOME = (
    "👋 <b>أهلاً بك في بوت ZIP → GitHub Uploader</b>\n\n"
    "أرفع محتويات ملف ZIP (مع الحفاظ على بنية المجلدات) إلى مستودع GitHub.\n\n"
    "<b>الخطوات:</b>\n"
    "1️⃣ أرسل <b>GitHub Personal Access Token</b> (fine-grained أو classic بصلاحية <code>Contents: write</code>).\n"
    "2️⃣ أرسل المستودع بصيغة <code>owner/repo</code>.\n"
    "3️⃣ أرسل اسم الفرع، أو أرسل <code>-</code> لاستخدام <code>main</code>.\n"
    "4️⃣ أرسل ملف ZIP كمستند (Document).\n\n"
    f"⚠️ الحد الأقصى لحجم الملف: <b>{MAX_ZIP_MB}MB</b>.\n"
    "🔐 لا يُخزّن التوكن في أي ملف أو قاعدة بيانات — يبقى في الذاكرة فقط ويُمسح بعد الرفع أو عبر /reset.\n\n"
    "الأوامر: /start  /reset  /cancel  /help"
)


def _bar(pct: int, width: int = 12) -> str:
    """شريط تقدّم نصي بسيط."""
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


# ─────────────────────────────────────────────────────────────
#  الأوامر
# ─────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — ترحيب وبدء جلسة جديدة."""
    context.user_data.clear()
    context.user_data[KEY_STATE] = State.WAITING_TOKEN
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — عرض التعليمات."""
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reset — مسح بيانات الجلسة (التوكن والمستودع) والبدء من جديد."""
    ev = context.user_data.get(KEY_CANCEL)
    if ev:
        ev.set()  # أوقف أي رفع جارٍ
    context.user_data.clear()
    context.user_data[KEY_STATE] = State.IDLE
    await update.message.reply_text(
        "🧹 تم مسح بيانات الجلسة (التوكن والمستودع). أرسل /start للبدء من جديد."
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel — إلغاء أي عملية جارية."""
    ev = context.user_data.get(KEY_CANCEL)
    if context.user_data.get(KEY_UPLOADING) and ev:
        ev.set()
        await update.message.reply_text("⏹ جارٍ إلغاء عملية الرفع…")
    else:
        context.user_data[KEY_STATE] = State.IDLE
        await update.message.reply_text("تم الإلغاء. أرسل /start للبدء من جديد.")


# ─────────────────────────────────────────────────────────────
#  استقبال الرسائل النصية (حسب الحالة)
# ─────────────────────────────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get(KEY_STATE, State.IDLE)
    text = (update.message.text or "").strip()

    if state == State.WAITING_TOKEN:
        await _receive_token(update, context, text)
    elif state == State.WAITING_REPO:
        await _receive_repo(update, context, text)
    elif state == State.WAITING_BRANCH:
        await _receive_branch(update, context, text)
    elif state == State.WAITING_ZIP:
        await update.message.reply_text("📦 من فضلك أرسل ملف ZIP كمستند (Document)، وليس كنص.")
    elif state == State.UPLOADING:
        await update.message.reply_text("⏳ هناك عملية رفع جارية. استخدم /cancel لإلغائها.")
    else:
        await update.message.reply_text("أرسل /start للبدء.")


async def _receive_token(update, context, token):
    """استقبال التوكن والتحقق منه، مع حذف رسالة التوكن للخصوصية."""
    # احذف رسالة التوكن فورًا لكي لا تبقى ظاهرة في المحادثة
    try:
        await update.message.delete()
    except Exception:
        pass

    msg = await update.effective_chat.send_message("🔍 جارٍ التحقق من التوكن…")
    user = await gh.verify_token(token)
    if not user:
        await msg.edit_text("❌ التوكن غير صالح أو لا يملك الصلاحيات. أرسل توكنًا صحيحًا.")
        return
    context.user_data[KEY_TOKEN] = token
    context.user_data[KEY_STATE] = State.WAITING_REPO
    await msg.edit_text(
        f"✅ تم التحقق. مرحبًا <b>@{user.get('login')}</b>.\n\n"
        "الآن أرسل المستودع بصيغة <code>owner/repo</code>.",
        parse_mode=ParseMode.HTML,
    )


async def _receive_repo(update, context, text):
    """استقبال owner/repo والتحقق من وجوده."""
    if "/" not in text:
        await update.message.reply_text(
            "⚠️ الصيغة يجب أن تكون <code>owner/repo</code>.", parse_mode=ParseMode.HTML
        )
        return
    owner, repo = text.split("/", 1)
    owner, repo = owner.strip(), repo.strip().rstrip("/")
    if not owner or not repo:
        await update.message.reply_text(
            "⚠️ صيغة غير صحيحة. مثال: <code>octocat/hello-world</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    msg = await update.message.reply_text("🔍 جارٍ التحقق من المستودع…")
    info = await gh.get_repo_info(context.user_data[KEY_TOKEN], owner, repo)
    if not info:
        await msg.edit_text("❌ لم يتم العثور على المستودع أو لا تملك صلاحية الوصول إليه.")
        return

    context.user_data[KEY_OWNER] = owner
    context.user_data[KEY_REPO] = repo
    context.user_data[KEY_DEFAULT_BRANCH] = info.get("default_branch", "main")
    context.user_data[KEY_STATE] = State.WAITING_BRANCH
    await msg.edit_text(
        f"✅ المستودع <b>{owner}/{repo}</b> جاهز.\n"
        f"الفرع الافتراضي: <code>{info.get('default_branch', 'main')}</code>\n\n"
        "أرسل اسم الفرع المطلوب، أو أرسل <code>-</code> لاستخدام <code>main</code>.",
        parse_mode=ParseMode.HTML,
    )


async def _receive_branch(update, context, text):
    """استقبال اسم الفرع (افتراضي main)."""
    branch = text.strip()
    if branch in ("", "-"):
        branch = "main"
    context.user_data[KEY_BRANCH] = branch
    context.user_data[KEY_STATE] = State.WAITING_ZIP
    await update.message.reply_text(
        f"🌿 الفرع: <code>{branch}</code>\n\n"
        f"الآن أرسل ملف ZIP كمستند (حد أقصى {MAX_ZIP_MB}MB).",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────────────────────────────────────────
#  استقبال ملف ZIP وتنفيذ الرفع
# ─────────────────────────────────────────────────────────────
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get(KEY_STATE, State.IDLE)
    if state != State.WAITING_ZIP:
        await update.message.reply_text(
            "ℹ️ أرسل /start أولاً لإعداد التوكن والمستودع قبل إرسال ملف ZIP."
        )
        return

    doc = update.message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".zip"):
        await update.message.reply_text("⚠️ الرجاء إرسال ملف بامتداد .zip فقط.")
        return
    if doc.file_size and doc.file_size > MAX_ZIP_SIZE:
        await update.message.reply_text(
            f"⚠️ حجم الملف يتجاوز الحد المسموح ({MAX_ZIP_MB}MB)."
        )
        return

    owner = context.user_data[KEY_OWNER]
    repo = context.user_data[KEY_REPO]
    branch = context.user_data.get(KEY_BRANCH, "main")
    token = context.user_data[KEY_TOKEN]

    # مجلد عمل مؤقت لكل عملية
    workdir = tempfile.mkdtemp(prefix="ghzip_")
    zip_path = os.path.join(workdir, "upload.zip")
    extract_dir = os.path.join(workdir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    cancel_event = asyncio.Event()
    context.user_data[KEY_CANCEL] = cancel_event
    context.user_data[KEY_UPLOADING] = True
    context.user_data[KEY_STATE] = State.UPLOADING

    status = await update.message.reply_text("⬇️ جارٍ تنزيل الملف…")

    try:
        # نزّل الملف من خوادم تليجرام إلى القرص المؤقت
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(zip_path)

        # فك الضغط داخل خيط منفصل حتى لا نحجب حلقة الأحداث
        await status.edit_text("📂 جارٍ فك الضغط وتنظيف المسارات…")
        try:
            files = await asyncio.to_thread(gh.extract_zip, zip_path, extract_dir)
        except Exception as e:
            await status.edit_text(f"❌ فشل فك الضغط: {e}")
            return

        if not files:
            await status.edit_text("⚠️ لم يتم العثور على ملفات صالحة داخل الأرشيف.")
            return

        last_edit = [0.0]  # قائمة لتغليف المتغير داخل الدالة المتداخلة

        async def progress_cb(done, failed, total_):
            # حدّث الرسالة مرة كل ثانيتين على الأقل (أو عند الاكتمال) لتفادي حدود تليجرام
            now = time.monotonic()
            if now - last_edit[0] < 2.0 and (done + failed) < total_:
                return
            last_edit[0] = now
            pct = int((done + failed) / total_ * 100) if total_ else 0
            try:
                await status.edit_text(
                    f"⬆️ يرفع إلى <b>{owner}/{repo}</b> (<code>{branch}</code>)\n"
                    f"{_bar(pct)} {pct}%\n"
                    f"✅ {done}  |  ❌ {failed}  |  📦 {total_}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass  # تجاهل أخطاء التعديل (نفس المحتوى/حد المعدل)

        # نفّذ الرفع
        result = await gh.upload_files(
            files=files, token=token, owner=owner, repo=repo, branch=branch,
            progress_cb=progress_cb, cancel_event=cancel_event,
        )

        # خزّن قائمة الملفات الفاشلة للسماح بتنزيلها (لا تحتوي أسرارًا)
        context.user_data[KEY_LAST_FAILED] = result.failed_files

        await _send_report(update, context, result, owner, repo, branch)

    finally:
        # نظافة: احذف كل الملفات المؤقتة فورًا سواء نجح الرفع أو فشل
        shutil.rmtree(workdir, ignore_errors=True)
        context.user_data[KEY_UPLOADING] = False
        context.user_data.pop(KEY_CANCEL, None)
        # امسح البيانات الحساسة من الذاكرة بعد انتهاء الرفع
        context.user_data.pop(KEY_TOKEN, None)
        context.user_data.pop(KEY_OWNER, None)
        context.user_data.pop(KEY_REPO, None)
        context.user_data.pop(KEY_BRANCH, None)
        context.user_data.pop(KEY_DEFAULT_BRANCH, None)
        context.user_data[KEY_STATE] = State.IDLE


async def _send_report(update, context, result, owner, repo, branch):
    """إرسال التقرير النهائي بعد انتهاء الرفع."""
    repo_url = f"https://github.com/{owner}/{repo}"
    header = "⏹ <b>تم الإلغاء</b>" if result.cancelled else "🎉 <b>اكتمل الرفع</b>"
    text = (
        f"{header}\n\n"
        f"📦 المستودع: <a href=\"{repo_url}\">{owner}/{repo}</a>\n"
        f"🌿 الفرع: <code>{branch}</code>\n"
        f"✅ ناجحة: <b>{result.done}</b>\n"
        f"❌ فاشلة: <b>{result.failed}</b>\n"
        f"🔁 إعادة المحاولات: {result.retries}\n"
        f"⏱ المدة: {result.duration:.1f}s"
    )
    keyboard = None
    if result.failed_files:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⚠️ تنزيل قائمة الملفات الفاشلة", callback_data="dl_failed")]]
        )
    await update.effective_chat.send_message(
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await update.effective_chat.send_message("أرسل /start لرفع أرشيف آخر.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار الـ Inline (تنزيل قائمة الملفات الفاشلة)."""
    query = update.callback_query
    await query.answer()
    if query.data == "dl_failed":
        failed = context.user_data.get(KEY_LAST_FAILED) or []
        if not failed:
            await query.message.reply_text("لا توجد ملفات فاشلة.")
            return
        lines = ["ZIP → GitHub Uploader — قائمة الملفات الفاشلة", ""]
        lines += [f"{p}\t{reason}" for p, reason in failed]
        data = "\n".join(lines).encode("utf-8")
        bio = io.BytesIO(data)
        bio.name = "failed_files.txt"
        await query.message.reply_document(
            document=InputFile(bio, filename="failed_files.txt")
        )
