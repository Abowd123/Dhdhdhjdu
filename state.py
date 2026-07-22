"""
state.py
========
تعريف حالات آلة الحالة (state machine) الخاصة بكل مستخدم،
ومفاتيح التخزين داخل context.user_data.

التدفق: WAITING_TOKEN → WAITING_REPO → WAITING_BRANCH → WAITING_ZIP → UPLOADING
"""

from enum import Enum, auto


class State(Enum):
    """حالات الجلسة لكل مستخدم على حدة."""
    IDLE = auto()            # لا توجد جلسة نشطة
    WAITING_TOKEN = auto()   # بانتظار GitHub Personal Access Token
    WAITING_REPO = auto()    # بانتظار owner/repo
    WAITING_BRANCH = auto()  # بانتظار اسم الفرع
    WAITING_ZIP = auto()     # بانتظار ملف ZIP
    UPLOADING = auto()       # عملية رفع جارية


# ─── مفاتيح context.user_data ────────────────────────────────
KEY_STATE = "state"              # الحالة الحالية (State)
KEY_TOKEN = "gh_token"          # GitHub PAT (في الذاكرة فقط)
KEY_OWNER = "owner"            # مالك المستودع
KEY_REPO = "repo"             # اسم المستودع
KEY_BRANCH = "branch"          # الفرع المستهدف
KEY_DEFAULT_BRANCH = "default_branch"  # الفرع الافتراضي للمستودع
KEY_CANCEL = "cancel_event"    # asyncio.Event للإلغاء
KEY_UPLOADING = "is_uploading"  # هل يوجد رفع جارٍ؟
KEY_LAST_FAILED = "last_failed"  # آخر قائمة ملفات فاشلة (لتنزيلها)
