# ZIP → GitHub Uploader — Telegram Bot 🤖

بوت تليجرام بلغة Python يأخذ ملف **ZIP** ويرفع محتوياته (مع الحفاظ على بنية المجلدات) إلى مستودع **GitHub** عبر GitHub REST API باستخدام **Personal Access Token**. هذا هو مكافئ أداة الويب "ZIP to GitHub Uploader Pro" على شكل بوت تليجرام.

---

## ✨ المزايا

- `/start` — ترحيب وشرح خطوات الاستخدام.
- تدفّق تفاعلي مبني على آلة حالة: **التوكن → المستودع → الفرع → ملف ZIP → الرفع**.
- فك الضغط بمكتبة `zipfile` مع **تنظيف المسارات**:
  - تجاهل مجلدات `__MACOSX` وملفات `.DS_Store`.
  - منع أي مسار يحتوي على `../` (حماية من path traversal).
  - حذف الشرطات المائلة الأمامية الزائدة.
- رفع كل ملف عبر `PUT /repos/{owner}/{repo}/contents/{path}` بترميز `base64` وترويسة `Authorization: Bearer TOKEN`.
- **رسالة تقدّم واحدة** يتم تعديلها (`edit_message_text`) بدل إرسال رسالة لكل ملف.
- معالجة **rate limiting** الخاص بـ GitHub (كود 403 مع `X-RateLimit-Remaining: 0`، وأيضًا secondary rate limit) بالانتظار المناسب، مع **حد أقصى 3 محاولات** لكل ملف.
- تقرير نهائي: رابط المستودع، عدد الملفات الناجحة/الفاشلة، المدة، وزر لتنزيل **قائمة الملفات الفاشلة** كملف نصي.
- `/cancel` لإلغاء أي عملية جارية، و`/reset` لمسح بيانات الجلسة (التوكن والمستودع).
- اتصال غير متزامن بالكامل عبر `httpx`، وفك الضغط في خيط منفصل حتى لا تُحجب الحلقة الرئيسية.
- **لا يُخزّن التوكن** في أي ملف أو قاعدة بيانات — يبقى في الذاكرة (`context.user_data`) فقط، ويُمسح عند `/reset` أو بعد انتهاء الرفع.
- حد أقصى قابل للتهيئة لحجم ملف ZIP (افتراضي **20MB** حسب حدود Telegram Bot API القياسية).

---

## 📁 بنية المشروع

```
zip_to_github_bot/
├── main.py              # نقطة التشغيل وتسجيل المعالجات وبدء polling
├── handlers.py          # معالجات الأوامر والرسائل وآلة الحالة
├── github_uploader.py   # فك الضغط + تنظيف المسارات + الرفع لـ GitHub API
├── state.py             # تعريف الحالات ومفاتيح user_data
├── requirements.txt     # الاعتماديات
├── .env.example         # مثال متغيرات البيئة
├── Procfile             # لتشغيل عامل (worker) على Railway/Heroku
├── Dockerfile           # للنشر عبر حاوية
└── .gitignore
```

---

## 🚀 التشغيل المحلي

### 1) المتطلبات
- Python 3.10 أو أحدث.
- توكن بوت تليجرام من [@BotFather](https://t.me/BotFather).

### 2) الإعداد

```bash
# استنساخ/فك المشروع ثم الدخول إلى مجلده
cd zip_to_github_bot

# إنشاء بيئة افتراضية (مستحسن)
python -m venv .venv
source .venv/bin/activate      # على ويندوز: .venv\Scripts\activate

# تثبيت الاعتماديات
pip install -r requirements.txt

# تجهيز متغيرات البيئة
cp .env.example .env
# افتح .env وضع قيمة BOT_TOKEN الحقيقية
```

### 3) التشغيل

```bash
python main.py
```

إذا ظهرت رسالة "🤖 البوت يعمل الآن…" فالبوت جاهز. افتح محادثته على تليجرام وأرسل `/start`.

---

## 🔑 كيفية استخدام البوت

1. أرسل `/start`.
2. أرسل **GitHub Personal Access Token**:
   - **fine-grained**: صلاحية `Contents: Read and write` على المستودع المستهدف.
   - أو **classic**: نطاق `repo`.
   - (يُحذف تلقائيًا من المحادثة فور استلامه للخصوصية.)
3. أرسل المستودع بصيغة `owner/repo` (مثال: `octocat/hello-world`).
4. أرسل اسم الفرع، أو `-` لاستخدام `main` (يُنشأ الفرع تلقائيًا إن لم يكن موجودًا).
5. أرسل ملف **ZIP** كمستند (Document).
6. تابع رسالة التقدّم حتى يظهر التقرير النهائي.

---

## ☁️ النشر

### الخيار أ: Railway

1. أنشئ مستودعًا على GitHub وارفع إليه ملفات المشروع.
2. على [railway.app](https://railway.app): **New Project → Deploy from GitHub repo**.
3. من تبويب **Variables** أضف المتغير:
   - `BOT_TOKEN` = توكن البوت.
   - (اختياري) `MAX_ZIP_MB` = 20.
4. Railway سيكتشف `Procfile` ويشغّل `worker: python main.py`.
   - إن طلب أمر تشغيل، استخدم `python main.py`.
5. تحقق من السجلّات (Logs) لظهور رسالة عمل البوت.

> ملاحظة: هذا البوت يعمل بنمط **polling** (وليس webhook)، لذا لا يحتاج منفذًا مفتوحًا؛ شغّله كـ **worker** لا كخدمة ويب.

### الخيار ب: VPS (Ubuntu مع systemd)

```bash
# على الخادم
sudo apt update && sudo apt install -y python3 python3-venv git
git clone <your-repo-url> /opt/zip_to_github_bot
cd /opt/zip_to_github_bot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env   # ضع BOT_TOKEN
```

أنشئ خدمة systemd في `/etc/systemd/system/zipbot.service`:

```ini
[Unit]
Description=ZIP to GitHub Telegram Bot
After=network.target

[Service]
WorkingDirectory=/opt/zip_to_github_bot
EnvironmentFile=/opt/zip_to_github_bot/.env
ExecStart=/opt/zip_to_github_bot/.venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

ثم:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zipbot
sudo systemctl status zipbot     # للتحقق
sudo journalctl -u zipbot -f     # لمتابعة السجلّات
```

### الخيار ج: Docker

```bash
docker build -t zip-github-bot .
docker run -d --name zipbot --restart unless-stopped \
  -e BOT_TOKEN="ضع_التوكن_هنا" \
  -e MAX_ZIP_MB=20 \
  zip-github-bot
```

---

## ⚠️ ملاحظات وحدود

- **حد حجم الملف**: Telegram Bot API القياسي يسمح للبوتات بتنزيل ملفات حتى ~20MB. لملفات أكبر تحتاج إعداد Local Bot API Server.
- **حجم الملف الواحد في GitHub Contents API**: يُفضّل أن يكون أقل من ~1MB لكل ملف (حد الـ API نحو 100MB مع قيود على base64).
- الرفع **تسلسلي** عمدًا لتفادي تعارض SHA على نفس الفرع؛ هذا أكثر أمانًا مع Contents API.
- التوكن لا يُسجَّل أبدًا في اللوجات ولا يُخزَّن على القرص.
