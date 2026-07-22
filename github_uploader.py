"""
github_uploader.py
==================
منطق فك ضغط ملف ZIP، وتنظيف المسارات، ورفع الملفات إلى GitHub
عبر GitHub REST API (Contents API) بشكل غير متزامن باستخدام httpx.

مرجع المنطق مأخوذ (للفهم لا للنسخ) من أداة الويب الأصلية
"ZIP to GitHub Uploader Pro": ترميز base64، تنظيف المسارات،
معالجة حد المعدل (rate limiting)، وإعادة المحاولة.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
import zipfile
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional, Tuple
from urllib.parse import quote

import httpx

GITHUB_API = "https://api.github.com"
MAX_RETRIES = 3          # حد أقصى لعدد المحاولات لكل ملف
RATE_LIMIT_CAP = 90      # أقصى مدة انتظار (بالثواني) عند بلوغ حد المعدل


def _headers(token: str) -> dict:
    """ترويسات موحّدة لكل طلبات GitHub API."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "zip-to-github-telegram-bot",
    }


def _encode_path(rel_path: str) -> str:
    """ترميز أجزاء المسار للـ URL مع الحفاظ على الشرطات المائلة."""
    return "/".join(quote(seg) for seg in rel_path.split("/"))


# ─────────────────────────────────────────────────────────────
#  تنظيف المسارات (path sanitization)
# ─────────────────────────────────────────────────────────────
def sanitize_path(path: str) -> Optional[str]:
    """
    تنظيف مسار عنصر داخل الأرشيف:
      • توحيد الفواصل إلى '/'
      • تجاهل مجلدات __MACOSX وملفات .DS_Store
      • منع أي مسار يحتوي على '..' (هجمات path traversal)
      • حذف الشرطات المائلة الأمامية الزائدة
    يعيد None عندما يجب تجاهل هذا العنصر.
    """
    if not path:
        return None
    p = path.replace("\\", "/").replace("\0", "")
    # تجاهل ملفات نظام ماك
    if p.startswith("__MACOSX") or "__MACOSX/" in p:
        return None
    if p.rsplit("/", 1)[-1] == ".DS_Store":
        return None
    # منع path traversal: أي جزء يساوي '..'
    if ".." in p.split("/"):
        return None
    # حذف الشرطات الأمامية الزائدة
    p = p.lstrip("/")
    return p or None


def extract_zip(zip_path: str, extract_dir: str) -> List[Tuple[str, str]]:
    """
    يفك ضغط الأرشيف إلى extract_dir مع تنظيف المسارات.
    يعيد قائمة من (المسار_النسبي_النظيف, المسار_المطلق_على_القرص).

    ملاحظة: دالة متزامنة (blocking) — استدعِها عبر asyncio.to_thread
    حتى لا تحجب حلقة الأحداث الرئيسية.
    """
    result: List[Tuple[str, str]] = []
    base = os.path.abspath(extract_dir)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            clean = sanitize_path(info.filename)
            if not clean:
                continue
            dest = os.path.abspath(os.path.join(base, clean))
            # حماية إضافية: تأكد أن الوجهة داخل مجلد فك الضغط
            if dest != base and not dest.startswith(base + os.sep):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                out.write(src.read())
            result.append((clean, dest))
    result.sort(key=lambda x: x[0])
    return result


# ─────────────────────────────────────────────────────────────
#  نتيجة الرفع
# ─────────────────────────────────────────────────────────────
@dataclass
class UploadResult:
    total: int = 0
    done: int = 0
    failed: int = 0
    retries: int = 0
    rate_limits: int = 0
    cancelled: bool = False
    duration: float = 0.0
    failed_files: List[Tuple[str, str]] = field(default_factory=list)  # (path, reason)


# ─────────────────────────────────────────────────────────────
#  التحقق من التوكن والمستودع
# ─────────────────────────────────────────────────────────────
async def verify_token(token: str) -> Optional[dict]:
    """يتحقق من صلاحية التوكن ويعيد بيانات المستخدم أو None."""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(f"{GITHUB_API}/user", headers=_headers(token))
        except httpx.RequestError:
            return None
    return r.json() if r.status_code == 200 else None


async def get_repo_info(token: str, owner: str, repo: str) -> Optional[dict]:
    """يعيد معلومات المستودع (بما فيها الفرع الافتراضي) أو None عند عدم الوجود/الصلاحية."""
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.get(
                f"{GITHUB_API}/repos/{owner}/{repo}", headers=_headers(token)
            )
        except httpx.RequestError:
            return None
    return r.json() if r.status_code == 200 else None


async def _ensure_branch(client, token, owner, repo, branch, default_branch):
    """ينشئ الفرع المطلوب من الفرع الافتراضي إن لم يكن موجودًا."""
    if not branch or branch == default_branch:
        return
    base = f"{GITHUB_API}/repos/{owner}/{repo}"
    # هل الفرع موجود بالفعل؟
    r = await client.get(f"{base}/git/ref/heads/{branch}", headers=_headers(token))
    if r.status_code == 200:
        return
    # احصل على sha للفرع الافتراضي ثم أنشئ المرجع الجديد
    r = await client.get(
        f"{base}/git/ref/heads/{default_branch}", headers=_headers(token)
    )
    if r.status_code != 200:
        return
    sha = r.json()["object"]["sha"]
    await client.post(
        f"{base}/git/refs",
        headers=_headers(token),
        json={"ref": f"refs/heads/{branch}", "sha": sha},
    )


# ─────────────────────────────────────────────────────────────
#  رفع ملف واحد مع إعادة المحاولة ومعالجة حد المعدل
# ─────────────────────────────────────────────────────────────
async def _upload_one(
    client, token, owner, repo, branch, rel_path, abs_path,
    commit_message, result: UploadResult, cancel_event,
) -> bool:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{_encode_path(rel_path)}"

    # اقرأ محتوى الملف ورمّزه base64
    with open(abs_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")

    # احصل على sha الحالي إن كان الملف موجودًا مسبقًا (لتحديثه بدل الفشل)
    sha: Optional[str] = None
    try:
        g = await client.get(url, params={"ref": branch}, headers=_headers(token))
        if g.status_code == 200:
            sha = g.json().get("sha")
    except httpx.RequestError:
        pass

    last_reason = "خطأ غير معروف"
    attempt = 0
    while attempt < MAX_RETRIES:
        if cancel_event and cancel_event.is_set():
            return False

        body = {"message": commit_message, "content": content_b64, "branch": branch}
        if sha:
            body["sha"] = sha

        try:
            resp = await client.put(url, json=body, headers=_headers(token))
        except httpx.RequestError as e:
            last_reason = f"خطأ شبكة: {e}"
            attempt += 1
            if attempt < MAX_RETRIES:
                result.retries += 1
                await asyncio.sleep(1.5 * attempt)
            continue

        if resp.status_code in (200, 201):
            return True

        # ── معالجة حد المعدل الأساسي (rate limiting) ──
        # كود 403 مع X-RateLimit-Remaining == 0
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if resp.status_code == 403 and remaining == "0":
            reset = int(resp.headers.get("X-RateLimit-Reset", "0") or "0")
            wait = max(1.0, min(RATE_LIMIT_CAP, reset - time.time() + 1))
            result.rate_limits += 1
            await asyncio.sleep(wait)
            continue  # لا تُحتسب محاولة فاشلة

        # ── حد المعدل الثانوي (secondary rate limit) ──
        if resp.status_code in (403, 429) and "secondary rate" in resp.text.lower():
            retry_after = int(resp.headers.get("Retry-After", "10") or "10")
            result.rate_limits += 1
            await asyncio.sleep(min(RATE_LIMIT_CAP, retry_after))
            continue

        # ── أخطاء أخرى: أعد المحاولة مع مهلة تصاعدية ──
        try:
            last_reason = f"HTTP {resp.status_code}: {resp.json().get('message', '')}"
        except Exception:
            last_reason = f"HTTP {resp.status_code}"
        attempt += 1
        if attempt < MAX_RETRIES:
            result.retries += 1
            await asyncio.sleep(1.5 * attempt)

    result.failed_files.append((rel_path, last_reason))
    return False


# ─────────────────────────────────────────────────────────────
#  منسّق الرفع (تسلسلي لتفادي تعارض SHA على نفس الفرع)
# ─────────────────────────────────────────────────────────────
async def upload_files(
    files: List[Tuple[str, str]],
    token: str,
    owner: str,
    repo: str,
    branch: str = "main",
    commit_message: str = "Upload via ZIP to GitHub Uploader (Telegram bot)",
    progress_cb: Optional[Callable[[int, int, int], Awaitable[None]]] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> UploadResult:
    """
    يرفع قائمة الملفات تسلسليًا إلى GitHub مع تتبع التقدم ودعم الإلغاء.
    progress_cb(done, failed, total) تُستدعى بعد كل ملف.
    """
    result = UploadResult(total=len(files))
    start = time.time()

    async with httpx.AsyncClient(timeout=60) as client:
        # تأكد من وجود الفرع المستهدف
        info = await get_repo_info(token, owner, repo)
        default_branch = (info or {}).get("default_branch", "main")
        try:
            await _ensure_branch(client, token, owner, repo, branch, default_branch)
        except Exception:
            pass  # نتابع على أي حال؛ قد يكون الفرع موجودًا

        for rel_path, abs_path in files:
            if cancel_event and cancel_event.is_set():
                result.cancelled = True
                break
            ok = await _upload_one(
                client, token, owner, repo, branch,
                rel_path, abs_path, commit_message, result, cancel_event,
            )
            if ok:
                result.done += 1
            else:
                result.failed += 1
            if progress_cb:
                await progress_cb(result.done, result.failed, result.total)

    result.duration = time.time() - start
    return result
