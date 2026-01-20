import os
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError
import yt_dlp
import asyncio
from dotenv import load_dotenv
import re
import subprocess
import glob
import json
from datetime import datetime
import shutil
import instaloader
from functools import wraps
import time

# تحميل المتغيرات من ملف .env
load_dotenv()

# ============================================
# 📋 ثوابت التكوين (Configuration Constants)
# ============================================
DEFAULT_TIMEOUT = 30  # الحد الأقصى لانتظار الرد
MAX_FILE_SIZE_VIDEO = 50 * 1024 * 1024  # 50 MB
MAX_FILE_SIZE_IMAGE = 10 * 1024 * 1024  # 10 MB
MAX_SEARCH_RESULTS = 5  # الحد الأقصى لنتائج البحث
RETRY_ATTEMPTS = 3  # عدد محاولات إعادة المحاولة
RETRY_DELAY = 2  # التأخير بين المحاولات

# إعداد نظام السجلات بشكل محسّن
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# جلب التوكن من ملف .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# التحقق من وجود التوكن مع رسالة خطأ واضحة
if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ FATAL: TELEGRAM_BOT_TOKEN غير موجود")
    exit(1)

logger.info("✅ التوكن جاهز")

# معرف القناة المطلوب الاشتراك بها (بدون @)
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "forca91")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/forca91")

# مجلد مؤقت للتحميلات
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# ملف الإحصائيات
STATS_FILE = "bot_stats.json"

# معرف المطور (ضع معرفك هنا)
DEVELOPER_ID = int(os.getenv("DEVELOPER_ID", "0"))  # ضع معرفك في .env
USERNAME_FOR_DEVELOPER = os.getenv("USERNAME_FOR_DEVELOPER", "")  # معرف مستخدم التلجرام للمطور

# ============================================
# 💾 نظام إدارة الحالة (State Management)
# ============================================
user_states = {}  # تخزين حالة المستخدم
search_results = {}  # تخزين نتائج البحث
user_timeouts = {}  # تتبع أوقات العمليات
recent_user_actions = {}  # تتبع الإجراءات لمنع التكرار السريع
active_user_actions = set()  # منع تنفيذ نفس الطلب بالتوازي

# دالة مساعدة لتنظيف الحالات المنتهية
async def cleanup_user_states():
    """تنظيف حالات المستخدمين المنتهية الصلاحية"""
    try:
        current_time = time.time()
        expired_users = []
        
        for user_id, timeout in user_timeouts.items():
            if current_time - timeout > 3600:  # ساعة واحدة
                expired_users.append(user_id)
        
        for user_id in expired_users:
            if user_id in user_states:
                del user_states[user_id]
            del user_timeouts[user_id]
            logger.debug(f"تنظيف المستخدم {user_id}")
    except Exception as e:
        logger.error(f"خطأ في التنظيف: {e}")

# ============================================
# 🔧 دوال مساعدة (Helper Functions)
# ============================================

def retry_on_error(max_attempts=RETRY_ATTEMPTS, delay=RETRY_DELAY):
    """ديكوريتر لإعادة محاولة العمليات"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        logger.warning(f"محاولة {attempt + 1}/{max_attempts}: {str(e)[:50]}")
                        await asyncio.sleep(delay)
            raise last_error
        
        def sync_wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        logger.warning(f"محاولة {attempt + 1}/{max_attempts}: {str(e)[:50]}")
                        time.sleep(delay)
            raise last_error
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator

def is_duplicate_action(user_id, action_key, window_seconds=12):
    now = time.time()
    user_actions = recent_user_actions.setdefault(user_id, {})
    expired = [key for key, ts in user_actions.items() if now - ts > window_seconds]
    for key in expired:
        del user_actions[key]
    if action_key in user_actions:
        return True
    user_actions[action_key] = now
    return False

def begin_action(user_id, action_key):
    token = (user_id, action_key)
    if token in active_user_actions:
        return False
    active_user_actions.add(token)
    return True

def end_action(user_id, action_key):
    token = (user_id, action_key)
    if token in active_user_actions:
        active_user_actions.remove(token)

def split_message(text, max_length=3800):
    parts = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_length:
            if current:
                parts.append(current)
                current = ""
        if len(line) > max_length:
            for i in range(0, len(line), max_length):
                parts.append(line[i:i + max_length])
            continue
        current += line
    if current:
        parts.append(current)
    return parts

# دالة البحث عن ffmpeg مع تحسينات
def find_ffmpeg():
    """البحث عن ffmpeg في النظام"""
    # المسارات المحتملة لـ ffmpeg
    possible_paths = [
        '/usr/bin/ffmpeg',
        '/usr/local/bin/ffmpeg',
        '/bin/ffmpeg',
        shutil.which('ffmpeg'),
        os.path.join(os.getcwd(), 'ffmpeg'),
    ]
    
    for path in possible_paths:
        if path and os.path.exists(path):
            logger.info(f"✅ تم العثور على ffmpeg في: {path}")
            return path
    
    # محاولة استخدام which
    try:
        result = subprocess.run(['which', 'ffmpeg'], capture_output=True, text=True)
        if result.returncode == 0:
            path = result.stdout.strip()
            logger.info(f"✅ تم العثور على ffmpeg عبر which: {path}")
            return path
    except:
        pass
    
    logger.warning("⚠️ لم يتم العثور على ffmpeg")
    return None

# البحث عن ffmpeg
FFMPEG_PATH = find_ffmpeg()

# ============================================
# 📊 نظام الإحصائيات المتقدم (Advanced Stats System)
# ============================================

class AdvancedBotStats:
    """نظام إحصائيات شامل ومتقدم للبوت"""
    
    def __init__(self):
        self.stats_file = STATS_FILE
        self.load_stats()
    
    def load_stats(self):
        """تحميل الإحصائيات من الملف"""
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                    # تحديث البنية إذا لزم الأمر
                    self._ensure_structure()
            except Exception as e:
                logger.warning(f"خطأ في تحميل الإحصائيات: {e}")
                self.data = self.create_new_stats()
        else:
            self.data = self.create_new_stats()
    
    def _ensure_structure(self):
        """التأكد من وجود جميع الحقول المطلوبة"""
        required_keys = [
            'total_users', 'active_users_today', 'active_users_week', 'active_users_month', 'users',
            'total_downloads', 'successful_downloads', 'failed_downloads', 'downloads_by_type',
            'total_searches', 'search_terms', 'start_date', 'last_update', 'daily_stats',
            'daily_stats_version', 'platforms', 'premium_features', 'bot_version',
            'total_errors', 'average_download_time'
        ]
        for key in required_keys:
            if key not in self.data:
                if key == 'total_users':
                    self.data[key] = len(self.data.get('users', {}))
                elif key == 'active_users_today':
                    self.data[key] = 0
                elif key == 'active_users_week':
                    self.data[key] = 0
                elif key == 'active_users_month':
                    self.data[key] = 0
                elif key == 'users':
                    self.data[key] = {}
                elif key == 'successful_downloads':
                    self.data[key] = 0
                elif key == 'downloads_by_type':
                    self.data[key] = {'image': 0, 'video': 0, 'audio': 0, 'search': 0, 'story': 0}
                elif key == 'search_terms':
                    self.data[key] = {}
                elif key == 'last_update':
                    self.data[key] = datetime.now().isoformat()
                elif key == 'daily_stats':
                    self.data[key] = {}
                elif key == 'daily_stats_version':
                    self.data[key] = 1
                elif key == 'platforms':
                    self.data[key] = {
                        'youtube': 0,
                        'instagram': 0,
                        'tiktok': 0,
                        'twitter': 0,
                        'facebook': 0,
                        'other': 0
                    }
                elif key == 'premium_features':
                    self.data[key] = []
                elif key == 'bot_version':
                    self.data[key] = '2.5'
                elif key == 'total_errors':
                    self.data[key] = 0
                elif key == 'average_download_time':
                    self.data[key] = 0
                elif key not in self.data:
                    self.data[key] = 0 if key != 'start_date' else datetime.now().isoformat()
        self._normalize_daily_stats()
        if self.data.get('daily_stats_version', 1) < 2:
            self.data['daily_stats'] = {}
            self.data['daily_stats_version'] = 2
        self._normalize_user_records()

    def _normalize_daily_stats(self):
        daily_stats = self.data.get('daily_stats')
        if not isinstance(daily_stats, dict):
            self.data['daily_stats'] = {}
            return

        for date_key, stats in daily_stats.items():
            if not isinstance(stats, dict):
                daily_stats[date_key] = {}
            entry = daily_stats[date_key]
            entry.setdefault('downloads', 0)
            entry.setdefault('searches', 0)
            entry.setdefault('new_users', 0)
            entry.setdefault('active_users', 0)
            entry.setdefault('failed', 0)
            if not isinstance(entry.get('active_user_ids'), list):
                entry['active_user_ids'] = []
            if entry['active_users'] < len(entry['active_user_ids']):
                entry['active_users'] = len(entry['active_user_ids'])

    def _normalize_user_records(self):
        users = self.data.get('users')
        if not isinstance(users, dict):
            self.data['users'] = {}
            return

        now_iso = datetime.now().isoformat()
        today_iso = datetime.now().date().isoformat()
        for user_id, user_data in users.items():
            if not isinstance(user_data, dict):
                users[user_id] = {}
            record = users[user_id]
            record.setdefault('name', 'غير معروف')
            record.setdefault('username', 'بدون معرف')
            record.setdefault('first_seen', now_iso)
            record.setdefault('join_date', today_iso)
            record.setdefault('last_seen', now_iso)
            record.setdefault('usage_count', 0)
            record.setdefault('download_count', 0)
            record.setdefault('search_count', 0)
            record.setdefault('failed_count', 0)
            record.setdefault('is_active', False)
    
    def create_new_stats(self):
        """إنشاء إحصائيات جديدة بنية محسّنة"""
        return {
            'bot_version': '2.5',
            'start_date': datetime.now().isoformat(),
            'last_update': datetime.now().isoformat(),
            
            # إحصائيات المستخدمين
            'total_users': 0,
            'active_users_today': 0,
            'active_users_week': 0,
            'active_users_month': 0,
            'users': {},
            
            # إحصائيات التحميلات
            'total_downloads': 0,
            'successful_downloads': 0,
            'failed_downloads': 0,
            'downloads_by_type': {
                'image': 0,
                'video': 0,
                'audio': 0,
                'search': 0,
                'story': 0
            },
            
            # إحصائيات البحث
            'total_searches': 0,
            'search_terms': {},
            
            # إحصائيات يومية
            'daily_stats': {},
            'daily_stats_version': 2,
            
            # المنصات الأكثر استخداماً
            'platforms': {
                'youtube': 0,
                'instagram': 0,
                'tiktok': 0,
                'twitter': 0,
                'facebook': 0,
                'other': 0
            },
            
            # ميزات متقدمة
            'premium_features': [],
            'total_errors': 0,
            'average_download_time': 0
        }
    
    def save_stats(self):
        """حفظ الإحصائيات إلى الملف"""
        try:
            self.data['last_update'] = datetime.now().isoformat()
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            logger.debug("✅ تم حفظ الإحصائيات")
        except Exception as e:
            logger.error(f"❌ خطأ في حفظ الإحصائيات: {e}")
    
    def add_user(self, user_id, name, username):
        """إضافة أو تحديث بيانات المستخدم"""
        user_id_str = str(user_id)
        now = datetime.now().isoformat()
        today = datetime.now().date().isoformat()
        
        is_new_user = user_id_str not in self.data['users']
        
        if is_new_user:
            self.data['total_users'] += 1
            self.data['users'][user_id_str] = {
                'name': name,
                'username': username or 'بدون معرف',
                'first_seen': now,
                'join_date': today,
                'last_seen': now,
                'usage_count': 0,
                'download_count': 0,
                'search_count': 0,
                'failed_count': 0,
                'is_active': True
            }
        else:
            self.data['users'][user_id_str]['last_seen'] = now
            self.data['users'][user_id_str]['is_active'] = True

        daily_entry = self._ensure_daily_entry(today)
        if is_new_user:
            daily_entry['new_users'] += 1
        self._track_daily_active_user(user_id, daily_entry)
        
        # تحديث إحصائيات المستخدمين النشطين اليوم
        self._update_active_users()
        self.save_stats()
        
        return is_new_user
    
    def add_usage(self, user_id):
        """تسجيل استخدام للمستخدم"""
        user_id_str = str(user_id)
        now = datetime.now().isoformat()
        
        if user_id_str in self.data['users']:
            self.data['users'][user_id_str]['usage_count'] += 1
            self.data['users'][user_id_str]['last_seen'] = now

        self._track_daily_active_user(user_id)
        
        self._update_active_users()
        self.save_stats()
    
    def add_download(self, download_type, user_id=None, platform=None):
        """تسجيل تحميل ناجح"""
        self.data['total_downloads'] += 1
        self.data['successful_downloads'] += 1
        
        if download_type in self.data['downloads_by_type']:
            self.data['downloads_by_type'][download_type] += 1
        
        if user_id:
            user_id_str = str(user_id)
            if user_id_str in self.data['users']:
                self.data['users'][user_id_str]['download_count'] += 1
        
        if platform and platform in self.data['platforms']:
            self.data['platforms'][platform] += 1
        elif platform:
            self.data['platforms']['other'] += 1
        
        daily_entry = self._ensure_daily_entry()
        daily_entry['downloads'] += 1
        self.save_stats()
    
    def add_search(self, user_id=None, search_term=None):
        """تسجيل بحث"""
        self.data['total_searches'] += 1
        
        if search_term:
            if search_term not in self.data['search_terms']:
                self.data['search_terms'][search_term] = 0
            self.data['search_terms'][search_term] += 1
        
        if user_id:
            user_id_str = str(user_id)
            if user_id_str in self.data['users']:
                self.data['users'][user_id_str]['search_count'] += 1
        
        daily_entry = self._ensure_daily_entry()
        daily_entry['searches'] += 1
        self.save_stats()
    
    def add_failed_download(self, user_id=None):
        """تسجيل تحميل فاشل"""
        self.data['total_errors'] += 1
        self.data['failed_downloads'] += 1
        
        if user_id:
            user_id_str = str(user_id)
            if user_id_str in self.data['users']:
                self.data['users'][user_id_str]['failed_count'] += 1

        daily_entry = self._ensure_daily_entry()
        daily_entry['failed'] += 1
        
        self.save_stats()
    
    def _update_active_users(self):
        """تحديث إحصائيات المستخدمين النشطين"""
        from datetime import datetime, timedelta
        
        now = datetime.now()
        today = now.date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        
        active_today = 0
        active_week = 0
        active_month = 0
        
        for user_data in self.data['users'].values():
            last_seen = datetime.fromisoformat(user_data['last_seen']).date()
            
            if last_seen == today:
                active_today += 1
            if last_seen >= week_ago:
                active_week += 1
            if last_seen >= month_ago:
                active_month += 1
        
        self.data['active_users_today'] = active_today
        self.data['active_users_week'] = active_week
        self.data['active_users_month'] = active_month

    def _ensure_daily_entry(self, date_str=None):
        today = date_str or datetime.now().date().isoformat()
        if today not in self.data['daily_stats']:
            self.data['daily_stats'][today] = {
                'downloads': 0,
                'searches': 0,
                'new_users': 0,
                'active_users': 0,
                'failed': 0,
                'active_user_ids': []
            }
        else:
            self._normalize_daily_stats()
        return self.data['daily_stats'][today]

    def _track_daily_active_user(self, user_id, daily_entry=None):
        entry = daily_entry or self._ensure_daily_entry()
        user_id_str = str(user_id)
        if user_id_str not in entry['active_user_ids']:
            entry['active_user_ids'].append(user_id_str)
            entry['active_users'] = len(entry['active_user_ids'])
    
    def _update_daily_stats(self):
        """تحديث الإحصائيات اليومية"""
        today = datetime.now().date().isoformat()
        
        if today not in self.data['daily_stats']:
            self.data['daily_stats'][today] = {
                'downloads': 0,
                'searches': 0,
                'new_users': 0,
                'active_users': 0,
                'failed': 0
            }
        
        entry = self._ensure_daily_entry(today)
        entry['active_users'] = max(entry['active_users'], self.data.get('active_users_today', 0))
    
    def get_user_rank(self, user_id):
        """الحصول على ترتيب المستخدم من حيث النشاط"""
        user_id_str = str(user_id)
        
        sorted_users = sorted(
            self.data['users'].items(),
            key=lambda x: x[1]['usage_count'],
            reverse=True
        )
        
        for rank, (uid, _) in enumerate(sorted_users, 1):
            if uid == user_id_str:
                return rank
        
        return None
    
    def get_stats_text(self):
        """الحصول على نص الإحصائيات المفصل"""
        from datetime import datetime
        
        self._update_active_users()
        
        # أكثر المستخدمين نشاطاً
        top_users = sorted(
            self.data['users'].items(),
            key=lambda x: x[1]['usage_count'],
            reverse=True
        )[:5]
        
        top_users_text = "\n".join([
            f"  {i+1}. {user[1]['name']} (@{user[1]['username']}) - {user[1]['usage_count']} استخدام"
            for i, user in enumerate(top_users)
        ]) if top_users else "  لا يوجد مستخدمين بعد"
        
        # أكثر الأغاني بحثاً
        top_searches = sorted(
            self.data['search_terms'].items(),
            key=lambda x: x[1],
            reverse=True
        )[:3]
        
        top_searches_text = "\n".join([
            f"  {i+1}. {term} ({count} مرات)"
            for i, (term, count) in enumerate(top_searches)
        ]) if top_searches else "  لا توجد عمليات بحث بعد"
        
        # المنصات الأكثر استخداماً
        platforms_sorted = sorted(
            self.data['platforms'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        platforms_text = "\n".join([
            f"  📍 {platform.upper()}: {count}"
            for platform, count in platforms_sorted if count > 0
        ]) if any(count > 0 for _, count in platforms_sorted) else "  لا توجد تحميلات بعد"
        
        # حساب معدل النجاح
        success_rate = 100 if self.data['total_downloads'] == 0 else (
            (self.data['successful_downloads'] / self.data['total_downloads']) * 100
        )
        
        # حساب أيام التشغيل
        start_date = datetime.fromisoformat(self.data['start_date'])
        days_running = (datetime.now() - start_date).days + 1
        
        stats_text = f"""
╔════════════════════════════════════════════════════════════════╗
║              📊 إحصائيات البوت الشاملة (v{self.data['bot_version']})          ║
╚════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👥 **إحصائيات المستخدمين**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  👨‍💼 إجمالي المستخدمين: {self.data['total_users']}
  🟢 النشطين اليوم: {self.data['active_users_today']}
  🟠 النشطين هذا الأسبوع: {self.data['active_users_week']}
  🟡 النشطين هذا الشهر: {self.data['active_users_month']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📥 **إحصائيات التحميلات**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📊 إجمالي التحميلات: {self.data['total_downloads']}
  ✅ التحميلات الناجحة: {self.data['successful_downloads']}
  ❌ التحميلات الفاشلة: {self.data['failed_downloads']}
  
  **التفصيل:**
    🎬 فيديوهات: {self.data['downloads_by_type']['video']}
    🎵 موسيقى: {self.data['downloads_by_type']['audio']}
    📸 صور: {self.data['downloads_by_type']['image']}
    🎶 أغاني (بحث): {self.data['downloads_by_type']['search']}
    📹 قصص: {self.data['downloads_by_type']['story']}
  
  📈 معدل النجاح: {success_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 **إحصائيات البحث**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  🔎 إجمالي عمليات البحث: {self.data['total_searches']}
  
  **أكثر الأغاني بحثاً:**
{top_searches_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 **المنصات الأكثر استخداماً**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{platforms_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 **أكثر المستخدمين نشاطاً**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{top_users_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚙️ **معلومات عامة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  📅 تاريخ البدء: {self.data['start_date'][:10]}
  ⏰ أيام التشغيل: {days_running} يوم
  🔧 آخر تحديث: {self.data['last_update'][11:19]}

╚════════════════════════════════════════════════════════════════╝
        """
        
        return stats_text
    
    def get_user_stats(self, user_id):
        """الحصول على إحصائيات المستخدم الفردي"""
        user_id_str = str(user_id)
        
        if user_id_str not in self.data['users']:
            return None
        
        user = self.data['users'][user_id_str]
        rank = self.get_user_rank(user_id)
        
        user_stats = f"""
╔════════════════════════════════════════════════════════════════╗
║               👤 إحصائياتك الشخصية             ║
╚════════════════════════════════════════════════════════════════╝

  📝 الاسم: {user['name']}
  👻 المعرف: @{user['username']}
  
  🔢 الإحصائيات:
    • إجمالي الاستخدامات: {user['usage_count']}
    • التحميلات: {user['download_count']}
    • عمليات البحث: {user['search_count']}
    • الأخطاء: {user['failed_count']}
  
  📊 الترتيب: #{rank if rank else 'غير محدد'} من بين {self.data['total_users']} مستخدم
  📅 الانضمام: {user['join_date']}
  ⏰ آخر نشاط: {user['last_seen'][11:19]}

╚════════════════════════════════════════════════════════════════╝
        """
        
        return user_stats

# إنشاء كائن الإحصائيات المتقدم
stats = AdvancedBotStats()

class SocialMediaDownloader:
    """فئة لتحميل المحتوى من مواقع التواصل الاجتماعي"""
    
    def __init__(self):
        # User-Agent strings للتجنب من اكتشاف البوت
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        ]
        import random
        self.user_agent = random.choice(user_agents)
        
        # إعدادات أساسية محسنة
        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'user_agent': self.user_agent,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'player_skip': ['webpage', 'configs'],
                }
            },
            'http_headers': {
                'User-Agent': self.user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            },
            'retries': 3,
            'fragment_retries': 3,
            'ignoreerrors': False,
            'no_color': True,
        }
        # تسجيل إصدار yt-dlp
        try:
            logger.info(f"✅ yt-dlp version: {yt_dlp.__version__}")
        except Exception:
            logger.warning("⚠️ لم يتم التعرف على إصدار yt-dlp")

        # Logger class for yt-dlp (debugging)
        class YTDLLogger:
            def __init__(self, path=None):
                self.path = path or os.path.join(DOWNLOAD_FOLDER, 'yt_dlp_debug.log')

            def debug(self, msg):
                try:
                    with open(self.path, 'a', encoding='utf-8') as f:
                        f.write(f"DEBUG: {msg}\n")
                except Exception:
                    pass

            def info(self, msg):
                try:
                    with open(self.path, 'a', encoding='utf-8') as f:
                        f.write(f"INFO: {msg}\n")
                except Exception:
                    pass

            def warning(self, msg):
                try:
                    with open(self.path, 'a', encoding='utf-8') as f:
                        f.write(f"WARNING: {msg}\n")
                except Exception:
                    pass

            def error(self, msg):
                try:
                    with open(self.path, 'a', encoding='utf-8') as f:
                        f.write(f"ERROR: {msg}\n")
                except Exception:
                    pass

        # If debugging enabled via env, attach logger and make yt-dlp verbose
        if os.getenv('DEBUG_YTDLP', '0') == '1':
            base_opts['quiet'] = False
            base_opts['no_warnings'] = False
            base_opts['logger'] = YTDLLogger()
        
        # محاولة تحميل ملف cookies إذا كان موجوداً
        cookies_file = os.getenv('YOUTUBE_COOKIES_FILE', 'cookies.txt')
        if os.path.exists(cookies_file):
            base_opts['cookiefile'] = cookies_file
            logger.info(f"✅ تم تحميل ملف cookies من: {cookies_file}")
        else:
            logger.warning("⚠️ ملف cookies غير موجود - سيتم استخدام طرق بديلة")
        
        # إضافة مسار ffmpeg إذا كان متاحاً
        if FFMPEG_PATH:
            base_opts['ffmpeg_location'] = os.path.dirname(FFMPEG_PATH)
        
        # إعدادات تحميل الفيديو
        self.ydl_opts_video = {
            **base_opts,
            'format': 'best[ext=mp4]/best[height<=1080]/best',
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
            'prefer_ffmpeg': True,
            'merge_output_format': 'mp4',
        }
        
        # إعدادات تحميل الصوت بدون تحويل (إذا لم يكن ffmpeg متاحاً)
        if FFMPEG_PATH:
            self.ydl_opts_audio = {
                **base_opts,
                'format': 'bestaudio/best',
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        else:
            # بدون تحويل - تحميل الصوت مباشرة
            logger.warning("⚠️ ffmpeg غير متاح - سيتم تحميل الصوت بصيغته الأصلية")
            self.ydl_opts_audio = {
                **base_opts,
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
            }

    def _write_debug(self, context_name, exc):
        try:
            import traceback
            path = os.path.join(DOWNLOAD_FOLDER, 'yt_dlp_debug.log')
            with open(path, 'a', encoding='utf-8') as f:
                f.write(f"\n--- {datetime.now().isoformat()} - {context_name} ---\n")
                f.write(f"Exception: {repr(exc)}\n")
                traceback.print_exc(file=f)
        except Exception:
            pass
    
    def download_image(self, url):
        """تحميل صورة من الرابط - مع طرق متعددة"""
        logger.info(f"محاولة تحميل صورة من: {url}")
        
        try:
            logger.info("استخدام Web Scraping...")
            return self._download_with_scraping(url)
        except Exception as e:
            logger.warning(f"فشل Web Scraping: {e}")
        
        try:
            logger.info("محاولة التحميل المباشر...")
            return self._download_direct(url)
        except Exception as e:
            logger.warning(f"فشل التحميل المباشر: {e}")
        
        logger.error("فشلت جميع الطرق")
        raise Exception("فشل تحميل الصورة. تأكد من أن الرابط يحتوي على صورة عامة")
    
    def _download_with_scraping(self, url):
        """تحميل صورة باستخدام Web Scraping"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            html = response.text
            
            image_patterns = [
                r'"display_url":"(https://[^"]+)"',
                r'property="og:image" content="([^"]+)"',
                r'"contentUrl":"(https://[^"]+)"',
                r'<img[^>]+src="([^"]+)"[^>]*>',
            ]
            
            image_url = None
            for pattern in image_patterns:
                matches = re.findall(pattern, html)
                if matches:
                    for match in matches:
                        if any(ext in match.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']) or 'fbcdn' in match or 'cdninstagram' in match:
                            image_url = match.replace('\\u0026', '&')
                            break
                    if image_url:
                        break
            
            if not image_url:
                raise Exception("لم يتم العثور على رابط صورة في الصفحة")
            
            logger.info(f"تم العثور على رابط الصورة: {image_url[:100]}...")
            
            img_response = requests.get(image_url, headers=headers, timeout=30)
            img_response.raise_for_status()
            
            content_type = img_response.headers.get('content-type', '').lower()
            if 'jpeg' in content_type or 'jpg' in content_type:
                ext = 'jpg'
            elif 'png' in content_type:
                ext = 'png'
            elif 'webp' in content_type:
                ext = 'webp'
            else:
                ext = 'jpg'
            
            filename = f"{DOWNLOAD_FOLDER}/scraped_image.{ext}"
            
            with open(filename, 'wb') as f:
                f.write(img_response.content)
            
            return filename, "صورة"
            
        except Exception as e:
            raise Exception(f"فشل Web Scraping: {str(e)}")
    
    def _download_direct(self, url):
        """تحميل مباشر للروابط المباشرة"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        content_type = response.headers.get('content-type', '').lower()
        if 'jpeg' in content_type or 'jpg' in content_type:
            ext = 'jpg'
        elif 'png' in content_type:
            ext = 'png'
        elif 'gif' in content_type:
            ext = 'gif'
        elif 'webp' in content_type:
            ext = 'webp'
        elif 'image' in content_type:
            ext = 'jpg'
        else:
            ext = url.split('.')[-1].split('?')[0].lower()
            if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                ext = 'jpg'
        
        filename = f"{DOWNLOAD_FOLDER}/direct_image.{ext}"
        
        with open(filename, 'wb') as f:
            f.write(response.content)
        
        return filename, "صورة"
    
    def download_instagram_story(self, url):
        """تحميل قصة Instagram"""
        try:
            logger.info(f"محاولة تحميل قصة Instagram من: {url}")
            
            # استخراج معرف المستخدم من الرابط
            username_match = re.search(r'instagram\.com/stories/([^/?]+)', url)
            if not username_match:
                raise Exception("❌ رابط غير صحيح. يجب أن يكون رابط قصة Instagram")
            
            username = username_match.group(1)
            logger.info(f"اسم المستخدم: {username}")
            
            # إعدادات yt-dlp مع دعم الكوكيز
            story_opts = {
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                },
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
            }
            
            # محاولة التحميل مع yt-dlp
            try:
                with yt_dlp.YoutubeDL(story_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    
                    # تحديد نوع الملف (صورة أو فيديو)
                    if info.get('vcodec') != 'none':
                        # فيديو
                        if not filename.endswith('.mp4'):
                            base = os.path.splitext(filename)[0]
                            new_filename = f"{base}.mp4"
                            if os.path.exists(new_filename):
                                filename = new_filename
                        return filename, f"قصة {username} (فيديو)"
                    else:
                        # صورة
                        content_type = info.get('ext', 'jpg')
                        if not filename.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                            base = os.path.splitext(filename)[0]
                            for ext in ['.jpg', '.jpeg', '.png', '.webp']:
                                test_file = f"{base}{ext}"
                                if os.path.exists(test_file):
                                    filename = test_file
                                    break
                        return filename, f"قصة {username} (صورة)"
                        
            except yt_dlp.utils.DownloadError as e:
                self._write_debug('download_instagram_story', e)
                error_msg = str(e).lower()
                if 'private' in error_msg or 'not available' in error_msg:
                    raise Exception("❌ القصة غير متاحة أو خاصة. تأكد من أن القصة عامة.")
                elif 'login' in error_msg or 'authentication' in error_msg or 'sign in' in error_msg or 'cookies' in error_msg:
                    raise Exception("❌ قصص Instagram تتطلب تسجيل الدخول. لتحميل القصص:\n\n1. سجل دخولك إلى Instagram في المتصفح\n2. احصل على كوكيز المتصفح\n3. أو استخدم ميزة 'جميع القصص' مع اسم المستخدم بدلاً من رابط واحد\n\n💡 جرب: /story username (بدون رابط)")
                raise Exception(f"❌ خطأ في تحميل القصة: {str(e)}")
        except Exception as e:
            logger.error(f"خطأ في تحميل قصة Instagram: {e}")
            raise Exception(f"❌ خطأ في تحميل القصة: {str(e)}")
    
    def download_instagram_stories(self, username):
        """تحميل جميع قصص Instagram للمستخدم"""
        try:
            logger.info(f"محاولة تحميل قصص Instagram للمستخدم: {username}")
            
            # إنشاء كائن Instaloader
            L = instaloader.Instaloader(
                dirname_pattern=DOWNLOAD_FOLDER,
                filename_pattern='{shortcode}',
                download_videos=True,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                post_metadata_txt_pattern='',
            )
            
            # محاولة تحميل القصص
            try:
                profile = instaloader.Profile.from_username(L.context, username)
                logger.info(f"تم العثور على الملف الشخصي: {profile.username}")
                
                # الحصول على القصص
                stories = profile.get_stories()
                story_list = list(stories)
                
                if not story_list:
                    raise Exception("❌ لا توجد قصص متاحة لهذا المستخدم أو أن الحساب خاص")
                
                logger.info(f"تم العثور على {len(story_list)} قصة")
                
                downloaded_files = []
                
                for story in story_list:
                    try:
                        logger.info(f"تحميل القصة: {story.shortcode}")
                        
                        # تحميل القصة
                        L.download_storyitem(story, target=DOWNLOAD_FOLDER)
                        
                        # البحث عن الملف المحمل
                        pattern = f"{DOWNLOAD_FOLDER}/{story.shortcode}*"
                        files = glob.glob(pattern)
                        
                        if files:
                            # اختيار الملف الأول (الصورة أو الفيديو)
                            filename = files[0]
                            
                            # التحقق من حجم الملف
                            if os.path.getsize(filename) > 0:
                                downloaded_files.append((filename, f"قصة {username}"))
                                logger.info(f"تم تحميل: {filename}")
                            else:
                                logger.warning(f"ملف فارغ: {filename}")
                                os.remove(filename)
                        else:
                            logger.warning(f"لم يتم العثور على ملف للقصة: {story.shortcode}")
                            
                    except Exception as e:
                        logger.warning(f"فشل تحميل القصة {story.shortcode}: {e}")
                        continue
                
                if not downloaded_files:
                    raise Exception("❌ فشل تحميل أي قصة")
                
                logger.info(f"تم تحميل {len(downloaded_files)} قصة بنجاح")
                return downloaded_files
                
            except instaloader.exceptions.ProfileNotExistsException:
                raise Exception("❌ الملف الشخصي غير موجود")
            except instaloader.exceptions.PrivateProfileNotFollowedException:
                raise Exception("❌ الحساب خاص ولا يمكن الوصول إليه")
            except instaloader.exceptions.LoginRequiredException:
                raise Exception("❌ قصص Instagram تتطلب تسجيل الدخول إلى Instagram.\n\nلتحميل القصص تحتاج إلى:\n1. تسجيل الدخول إلى Instagram\n2. أو استخدام حساب آخر\n\n💡 بدلاً من ذلك، جرب تحميل المنشورات العامة أو الهايلايتس")
            except Exception as e:
                logger.error(f"خطأ في Instaloader: {e}")
                error_msg = str(e).lower()
                if 'login' in error_msg or 'authentication' in error_msg:
                    raise Exception("❌ قصص Instagram تتطلب تسجيل الدخول.\n\n💡 جرب تحميل المنشورات بدلاً من القصص: /image [رابط منشور]")
                raise Exception(f"❌ خطأ في الوصول إلى القصص: {str(e)}")
                
        except Exception as e:
            logger.error(f"خطأ في تحميل قصص Instagram: {e}")
            raise Exception(f"❌ خطأ في تحميل القصص: {str(e)}")
    
    def download_video(self, url, max_retries=3):
        """تحميل فيديو من الرابط مع آلية إعادة المحاولة"""
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # تحديث User-Agent في كل محاولة
                import random
                user_agents = [
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]
                opts = self.ydl_opts_video.copy()
                opts['user_agent'] = random.choice(user_agents)
                opts['http_headers']['User-Agent'] = opts['user_agent']
                
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    
                    if not filename.endswith('.mp4'):
                        base = os.path.splitext(filename)[0]
                        new_filename = f"{base}.mp4"
                        if os.path.exists(new_filename):
                            filename = new_filename
                    
                    return filename, info.get('title', 'فيديو')
                    
            except yt_dlp.utils.DownloadError as e:
                self._write_debug('download_video', e)
                last_error = str(e)
                error_msg = last_error.lower()

                # حاول دائماً محاولة بديلة واحدة بإزالة extractor_args وتخفيف القيود
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ محاولة {attempt + 1}/{max_retries}: خطأ yt-dlp: {last_error} — محاولة بديلة بدون extractor_args...")
                    import time
                    time.sleep(2)
                    # جرب بدون extractor_args وبخيارات أبسط
                    opts = self.ydl_opts_video.copy()
                    opts.pop('extractor_args', None)
                    opts['user_agent'] = random.choice(user_agents)
                    opts.setdefault('allow_unplayable_formats', True)
                    opts.setdefault('ignore_no_formats_error', True)
                    opts['http_headers']['User-Agent'] = opts['user_agent']
                    try:
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            info = ydl.extract_info(url, download=True)
                            filename = ydl.prepare_filename(info)
                            if not filename.endswith('.mp4'):
                                base = os.path.splitext(filename)[0]
                                new_filename = f"{base}.mp4"
                                if os.path.exists(new_filename):
                                    filename = new_filename
                            return filename, info.get('title', 'فيديو')
                    except Exception:
                        # دع الحلقة الرئيسية تتابع المحاولات العادية
                        continue
                # إذا لم تنجح البدائل، أعد الخطأ الأصلي بصيغة مفهومة
                if 'sign in' in error_msg or 'authentication' in error_msg or 'cookies' in error_msg or 'private' in error_msg:
                    raise Exception("❌ YouTube يطلب المصادقة. الرجاء المحاولة لاحقاً أو استخدام رابط مختلف.")
                raise Exception(f"خطأ في تحميل الفيديو: {str(e)}")
                    
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ محاولة {attempt + 1}/{max_retries}: {str(e)}")
                    import time
                    time.sleep(2)
                    continue
                else:
                    raise Exception(f"خطأ في تحميل الفيديو: {str(e)}")
        
        raise Exception(f"فشل تحميل الفيديو بعد {max_retries} محاولات: {last_error}")
    
    def download_audio(self, url, max_retries=3):
        """تحميل الصوت من الرابط مع آلية إعادة المحاولة"""
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # تحديث User-Agent في كل محاولة
                import random
                user_agents = [
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                ]
                opts = self.ydl_opts_audio.copy()
                opts['user_agent'] = random.choice(user_agents)
                opts['http_headers']['User-Agent'] = opts['user_agent']
                
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    
                    # إذا كان ffmpeg متاحاً، ابحث عن ملف mp3
                    if FFMPEG_PATH:
                        audio_filename = filename.rsplit('.', 1)[0] + '.mp3'
                        if not os.path.exists(audio_filename):
                            base = os.path.splitext(filename)[0]
                            for ext in ['.mp3', '.m4a', '.opus', '.webm']:
                                test_file = f"{base}{ext}"
                                if os.path.exists(test_file):
                                    audio_filename = test_file
                                    break
                    else:
                        # بدون ffmpeg، استخدم الملف كما هو
                        audio_filename = filename
                        # تأكد من وجود الملف بامتدادات مختلفة
                        if not os.path.exists(audio_filename):
                            base = os.path.splitext(filename)[0]
                            for ext in ['.m4a', '.webm', '.opus', '.mp3']:
                                test_file = f"{base}{ext}"
                                if os.path.exists(test_file):
                                    audio_filename = test_file
                                    break
                    
                    return audio_filename, info.get('title', 'صوت')
                    
            except yt_dlp.utils.DownloadError as e:
                self._write_debug('download_audio', e)
                last_error = str(e)
                error_msg = last_error.lower()

                # حاول دائماً محاولة بديلة واحدة بإزالة extractor_args وتخفيف القيود
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ محاولة {attempt + 1}/{max_retries}: خطأ yt-dlp: {last_error} — محاولة بديلة بدون extractor_args...")
                    import time
                    time.sleep(2)
                    opts = self.ydl_opts_audio.copy()
                    opts.pop('extractor_args', None)
                    opts['user_agent'] = random.choice(user_agents)
                    opts.setdefault('allow_unplayable_formats', True)
                    opts.setdefault('ignore_no_formats_error', True)
                    opts['http_headers']['User-Agent'] = opts['user_agent']
                    try:
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            info = ydl.extract_info(url, download=True)
                            filename = ydl.prepare_filename(info)
                            if FFMPEG_PATH:
                                audio_filename = filename.rsplit('.', 1)[0] + '.mp3'
                                if not os.path.exists(audio_filename):
                                    base = os.path.splitext(filename)[0]
                                    for ext in ['.mp3', '.m4a', '.opus', '.webm']:
                                        test_file = f"{base}{ext}"
                                        if os.path.exists(test_file):
                                            audio_filename = test_file
                                            break
                            else:
                                audio_filename = filename
                                if not os.path.exists(audio_filename):
                                    base = os.path.splitext(filename)[0]
                                    for ext in ['.m4a', '.webm', '.opus', '.mp3']:
                                        test_file = f"{base}{ext}"
                                        if os.path.exists(test_file):
                                            audio_filename = test_file
                                            break
                            return audio_filename, info.get('title', 'صوت')
                    except Exception:
                        continue

                if 'sign in' in error_msg or 'authentication' in error_msg or 'cookies' in error_msg or 'private' in error_msg:
                    raise Exception("❌ YouTube يطلب المصادقة. الرجاء المحاولة لاحقاً أو استخدام رابط مختلف.")
                raise Exception(f"خطأ في تحميل الصوت: {str(e)}")
                    
            except Exception as e:
                last_error = str(e)
                error_msg = str(e).lower()
                if 'ffmpeg' in error_msg or 'ffprobe' in error_msg:
                    raise Exception("لا يمكن معالجة الصوت حالياً. جرب رابطاً مختلفاً أو تواصل مع المطور.")
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ محاولة {attempt + 1}/{max_retries}: {str(e)}")
                    import time
                    time.sleep(2)
                    continue
                else:
                    raise Exception(f"خطأ في تحميل الصوت: {str(e)}")
        
        raise Exception(f"فشل تحميل الصوت بعد {max_retries} محاولات: {last_error}")
    
    def get_info(self, url):
        """الحصول على معلومات مفصلة عن الرابط"""
        try:
            import random
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            ]
            
            opts = {
                'quiet': True, 
                'no_warnings': True,
                'nocheckcertificate': True,
                'extract_flat': False,
                'user_agent': random.choice(user_agents),
                'http_headers': {
                    'User-Agent': random.choice(user_agents),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                },
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'web'],
                    }
                },
            }
            
            # محاولة تحميل ملف cookies إذا كان موجوداً
            cookies_file = os.getenv('YOUTUBE_COOKIES_FILE', 'cookies.txt')
            if os.path.exists(cookies_file):
                opts['cookiefile'] = cookies_file
            
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if not info:
                    raise Exception("لم يتم العثور على معلومات")

                return info

        except yt_dlp.utils.DownloadError as e:
            self._write_debug('get_info', e)
            logger.error(f"خطأ yt-dlp: {e}")
            # محاولة بديلة بدون extractor_args
            try:
                opts_alt = opts.copy()
                opts_alt.pop('extractor_args', None)
                opts_alt.setdefault('allow_unplayable_formats', True)
                opts_alt.setdefault('ignore_no_formats_error', True)
                if os.path.exists(os.getenv('YOUTUBE_COOKIES_FILE', 'cookies.txt')):
                    opts_alt['cookiefile'] = os.getenv('YOUTUBE_COOKIES_FILE', 'cookies.txt')
                with yt_dlp.YoutubeDL(opts_alt) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        return info
            except Exception as e2:
                logger.error(f"محاولة بديلة فشلت في get_info: {e2}")

            error_msg = str(e).lower()
            if 'sign in' in error_msg or 'bot' in error_msg or 'authentication' in error_msg:
                raise Exception("❌ YouTube يطلب المصادقة. الرجاء المحاولة لاحقاً.")
            raise Exception("لا يمكن الوصول إلى هذا المحتوى")
        except Exception as e:
            logger.error(f"خطأ عام في get_info: {e}")
            raise Exception(f"خطأ في جلب المعلومات: {str(e)}")
    
    def search_youtube(self, query, max_results=5):
        """البحث في YouTube عن أغنية"""
        try:
            logger.info(f"البحث في YouTube: {query}")
            
            import random
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            ]
            
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'nocheckcertificate': True,
                'user_agent': random.choice(user_agents),
                'http_headers': {
                    'User-Agent': random.choice(user_agents),
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                },
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android', 'web'],
                    }
                },
            }
            
            # محاولة تحميل ملف cookies إذا كان موجوداً
            cookies_file = os.getenv('YOUTUBE_COOKIES_FILE', 'cookies.txt')
            if os.path.exists(cookies_file):
                ydl_opts['cookiefile'] = cookies_file
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                search_query = f"ytsearch{max_results}:{query}"
                result = ydl.extract_info(search_query, download=False)

                if not result or 'entries' not in result:
                    raise Exception("لم يتم العثور على نتائج")

                videos = []
                for entry in result['entries']:
                    if entry:
                        videos.append({
                            'id': entry.get('id'),
                            'title': entry.get('title'),
                            'url': f"https://www.youtube.com/watch?v={entry.get('id')}",
                            'duration': entry.get('duration', 0),
                            'channel': entry.get('uploader', entry.get('channel', 'Unknown'))
                        })

                logger.info(f"تم العثور على {len(videos)} نتيجة")
                return videos

        except Exception as e:
            self._write_debug('search_youtube', e)
            logger.error(f"خطأ في البحث: {e}")
            # محاولة بديلة بدون extractor_args
            try:
                ydl_opts_alt = ydl_opts.copy()
                ydl_opts_alt.pop('extractor_args', None)
                ydl_opts_alt.setdefault('allow_unplayable_formats', True)
                ydl_opts_alt.setdefault('ignore_no_formats_error', True)
                if os.path.exists(os.getenv('YOUTUBE_COOKIES_FILE', 'cookies.txt')):
                    ydl_opts_alt['cookiefile'] = os.getenv('YOUTUBE_COOKIES_FILE', 'cookies.txt')
                with yt_dlp.YoutubeDL(ydl_opts_alt) as ydl:
                    search_query = f"ytsearch{max_results}:{query}"
                    result = ydl.extract_info(search_query, download=False)
                    if result and 'entries' in result:
                        videos = []
                        for entry in result['entries']:
                            if entry:
                                videos.append({
                                    'id': entry.get('id'),
                                    'title': entry.get('title'),
                                    'url': f"https://www.youtube.com/watch?v={entry.get('id')}",
                                    'duration': entry.get('duration', 0),
                                    'channel': entry.get('uploader', entry.get('channel', 'Unknown'))
                                })
                        return videos
            except Exception as e2:
                logger.error(f"محاولة بديلة فشلت في search_youtube: {e2}")

            error_msg = str(e).lower()
            if 'bot' in error_msg or 'sign in' in error_msg or 'authentication' in error_msg:
                raise Exception("❌ YouTube يطلب المصادقة. الرجاء المحاولة لاحقاً.")
            raise Exception(f"فشل البحث: {str(e)}")

# إنشاء كائن التحميل
downloader = SocialMediaDownloader()

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """التحقق من اشتراك المستخدم في القناة المطلوبة"""
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=f"@{REQUIRED_CHANNEL}", user_id=user_id)
        return member.status in ['creator', 'administrator', 'member']
    except TelegramError as e:
        logger.error(f"خطأ في التحقق من الاشتراك: {e}")
        return False

async def subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة طلب الاشتراك في القناة - نسخة احترافية"""
    keyboard = [
        [InlineKeyboardButton("📢 اشترك في القناة", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="check_subscription")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = """
╔════════════════════════════════════════════════╗
║    🔒 مطلوب الاشتراك في القناة أولاً          ║
╚════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📢 **خطوات سهلة:**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣ اضغط الزر "📢 اشترك في القناة"
2️⃣ اشترك في القناة الرسمية
3️⃣ ارجع وأضغط "✅ تحققت من الاشتراك"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 **لماذا الاشتراك؟**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✨ احصل على أحدث التحديثات
✨ تابع أخبار البوت والتطويرات
✨ شارك تجربتك مع المجتمع
✨ حصري: محتوى وميزات إضافية

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⏱️ استغرق دقيقة واحدة فقط!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    if update.callback_query:
        await update.callback_query.message.edit_text(message, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message, reply_markup=reply_markup)

async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج التحقق من الاشتراك عبر الزر"""
    query = update.callback_query
    await query.answer()
    
    if await check_subscription(update, context):
        await query.message.edit_text("✅ تم التحقق من الاشتراك بنجاح.\nيمكنك الآن استخدام البوت.\n\nيرجى إرسال /start لبدء الاستخدام.")
    else:
        await subscription_required(update, context)

def get_type_selection_keyboard():
    """إنشاء لوحة مفاتيح اختيار نوع المحتوى - نسخة احترافية"""
    keyboard = [
        [
            InlineKeyboardButton("🎬 فيديو", callback_data="type_video"),
            InlineKeyboardButton("🎵 موسيقى", callback_data="type_audio")
        ],
        [
            InlineKeyboardButton("📊 معلومات", callback_data="type_info"),
            InlineKeyboardButton("🔍 بحث أغنية", callback_data="type_search")
        ],
        [
            InlineKeyboardButton("❓ مساعدة", callback_data="type_help")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_developer_keyboard():
    """إنشاء لوحة مفاتيح خاصة بالمطور - نسخة احترافية"""
    keyboard = [
        [
            InlineKeyboardButton("🎬 فيديو", callback_data="type_video"),
            InlineKeyboardButton("🎵 موسيقى", callback_data="type_audio")
        ],
        [
            InlineKeyboardButton("📊 معلومات", callback_data="type_info"),
            InlineKeyboardButton("🔍 بحث أغنية", callback_data="type_search")
        ],
        [
            InlineKeyboardButton("📈 الإحصائيات", callback_data="stats_view"),
            InlineKeyboardButton("📢 إرسال رسالة", callback_data="broadcast_view")
        ],
        [
            InlineKeyboardButton("❓ مساعدة", callback_data="type_help")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def is_developer(user_id, username=""):
    """التحقق من أن المستخدم هو المطور"""
    username_str = f"@{username}" if username else ""
    return (user_id == DEVELOPER_ID) or (USERNAME_FOR_DEVELOPER and username_str == USERNAME_FOR_DEVELOPER)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب مع أزرار الاختيار"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    user = update.effective_user
    stats.add_user(user.id, user.full_name, user.username or "بدون معرف")
    stats.add_usage(user.id)  # عد استخدام عند الضغط على start
    
    welcome_message = """
╔════════════════════════════════════════════════╗
║   🎉 نرحب بكم في بوت تحميل المحتوى الذكي   ║
╚════════════════════════════════════════════════╝

👋 نحن هنا لمساعدتك في تحميل محتواك المفضل من جميع المنصات بسهولة وسرعة!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📥 **ما الذي يمكن تحميله عبر البوت؟**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎥 الفيديوهات      🎵 الموسيقى      📸 الصور
📹 قصص Instagram   📊 المعلومات    🎶 الأغاني

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 **المنصات المدعومة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔴 YouTube      📸 Instagram    🎵 TikTok
🐦 Twitter/X    👍 Facebook     📌 Pinterest
🎵 SoundCloud   وغيرها الكثير...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚀 **كيفية الاستخدام**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**الطريقة 1️⃣ (الأزرار - سهلة):**
اضغط على الزر → أرسل الرابط ✓

**الطريقة 2️⃣ (الأوامر - سريعة):**
/video [الرابط] - لتحميل فيديو
/audio [الرابط] - لتحميل موسيقى
/search [اسم الأغنية] - للبحث
/info [الرابط] - لعرض المعلومات
/story [اسم المستخدم] - لقصص Instagram

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 **نصائح مهمة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✨ للملفات الكبيرة (50MB+) يرجى استخدام /audio
✨ تأكد من نسخ الرابط بشكل صحيح
✨ البحث يدعم جميع أغاني YouTube
✨ اضغط ❓ المساعدة لمزيد من التفاصيل

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 **يرجى اختيار الخدمة المطلوبة:**
"""
    
    user = update.effective_user
    
    # تحديد لوحة المفاتيح بناءً على ما إذا كان المستخدم مطور
    keyboard = None
    if is_developer(user.id, user.username):
        keyboard = get_developer_keyboard()
    else:
        keyboard = get_type_selection_keyboard()
    
    await update.message.reply_text(
        welcome_message,
        reply_markup=keyboard
    )

async def type_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج اختيار نوع المحتوى من الأزرار"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    download_type = query.data.replace("type_", "")
    
    # معالجة زر المساعدة
    if download_type == 'help':
        help_text = """
╔════════════════════════════════════════════════╗
║      📚 دليل استخدام بوت تحميل المحتوى       ║
╚════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **خطوات الاستخدام البسيطة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣ يرجى اختيار نوع المحتوى من الأزرار
2️⃣ أرسل رابط المحتوى
3️⃣ انتظر قليلاً... ✓
4️⃣ احصل على ملفك!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ **الأوامر السريعة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/video [الرابط]  - فيديو
/audio [الرابط]  - موسيقى
/search [اسم]     - بحث
/info [الرابط]    - معلومات
/story [اسم]      - قصص Instagram

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 **نصائح مهمة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

• للملفات الكبيرة (50MB+) يرجى استخدام /audio
• تأكد من نسخ الرابط كاملاً
• تأكد أن الحساب عام وليس خاص
• لمزيد من المعلومات يرجى استخدام /help

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 **المنصات**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

YouTube  •  Instagram  •  TikTok  •  Twitter
Facebook  •  Pinterest  •  SoundCloud + المزيد

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

اضغط "العودة" للرجوع للقائمة الرئيسية ↓
"""
        keyboard = [
            [InlineKeyboardButton("↩️ العودة للقائمة", callback_data="back_to_menu")]
        ]
        await query.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    user_states[user_id] = download_type
    
    type_emoji = {
        'image': '📸',
        'video': '🎬',
        'audio': '🎵',
        'info': '📊',
        'search': '🔍',
        'story': '📸'
    }
    
    type_name = {
        'image': 'صورة',
        'video': 'فيديو',
        'audio': 'موسيقى',
        'info': 'معلومات',
        'search': 'بحث أغنية',
        'story': 'قصص Instagram'
    }
    
    if download_type == 'search':
        await query.message.edit_text(
            f"""
🎵 **بحث عن الأغاني**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
أرسل اسم الأغنية التي تريد البحث عنها...

💡 **أمثلة:**
• Imagine Dragons Believer
• The Weeknd Blinding Lights
• Dua Lipa Break My Heart

🔍 سيظهر 5 نتائج للاختيار من بينها!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💭 أو اختر خياراً آخر:
            """,
            reply_markup=get_type_selection_keyboard()
        )
    elif download_type == 'story':
        await query.message.edit_text(
            """
📸 **تحميل قصص Instagram**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

أرسل اسم مستخدم Instagram...

💡 **ملاحظات:**
• استخدم الاسم بدون @ (مثال: username)
• يجب أن يكون الحساب عاماً وليس خاصاً
• تتطلب مصادقة حساب Instagram

📌 **مثال:**
أرسل: username

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💭 أو اختر خياراً آخر:
            """,
            reply_markup=get_type_selection_keyboard()
        )
    else:
        await query.message.edit_text(
            f"""
{type_emoji[download_type]} **تم اختيار: {type_name[download_type]}**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 **الخطوة التالية:**
أرسل لي رابط المحتوى من المنصة

🔗 **يمكنك نسخ الرابط من:**
• شريط عنوان المتصفح (URL)
• اضغط "نسخ الرابط" من قائمة المحتوى

⏱️ **المهلة الزمنية:** 30 ثانية

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💭 أو اختر خياراً آخر:
            """,
            reply_markup=get_type_selection_keyboard()
        )

async def stats_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الإحصائيات الشاملة للجميع مع خيارات متقدمة"""
    query = update.callback_query
    user = update.effective_user
    
    await query.answer()
    
    is_dev = is_developer(user.id, user.username)
    
    # إنشاء أزرار الإحصائيات
    keyboard = []
    
    if is_dev:
        # أزرار خاصة بالمطور
        keyboard = [
            [
                InlineKeyboardButton("📊 إحصائيات عامة", callback_data="stats_general"),
                InlineKeyboardButton("👤 إحصائياتي", callback_data="stats_personal")
            ],
            [
                InlineKeyboardButton("🏆 أكثر المستخدمين", callback_data="stats_top_users"),
                InlineKeyboardButton("📈 الرسوم البيانية", callback_data="stats_charts")
            ],
            [
                InlineKeyboardButton("↩️ عودة للقائمة", callback_data="back_to_menu")
            ]
        ]
    else:
        # أزرار عامة للجميع
        keyboard = [
            [
                InlineKeyboardButton("📊 إحصائيات عامة", callback_data="stats_general"),
                InlineKeyboardButton("👤 إحصائياتي", callback_data="stats_personal")
            ],
            [
                InlineKeyboardButton("↩️ عودة للقائمة", callback_data="back_to_menu")
            ]
        ]
    
    intro_text = """
╔════════════════════════════════════════════════════════════════╗
║                  📊 مركز الإحصائيات              ║
╚════════════════════════════════════════════════════════════════╝

اختر نوع الإحصائيات التي تريد مشاهدتها:

📊 **الإحصائيات العامة** - معلومات شاملة عن البوت
👤 **إحصائياتي** - معلوماتك الشخصية والترتيب
"""
    
    if is_dev:
        intro_text += """
🏆 **أكثر المستخدمين** - ترتيب المستخدمين النشطين
📈 **الرسوم البيانية** - تحليل تفصيلي وإحصائيات متقدمة
"""
    
    await query.message.edit_text(intro_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def stats_general_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الإحصائيات العامة للبوت"""
    query = update.callback_query
    await query.answer()
    
    stats_text = stats.get_stats_text()
    parts = split_message(stats_text)
    
    keyboard = [
        [InlineKeyboardButton("📊 القائمة الرئيسية", callback_data="stats_view")],
        [InlineKeyboardButton("↩️ عودة للقائمة", callback_data="back_to_menu")]
    ]
    
    await query.message.edit_text(parts[0], reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    for part in parts[1:]:
        await query.message.reply_text(part, parse_mode='Markdown')

async def stats_personal_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات المستخدم الشخصية"""
    query = update.callback_query
    user = update.effective_user
    
    await query.answer()
    
    user_stats = stats.get_user_stats(user.id)
    
    if not user_stats:
        stats.add_user(user.id, user.full_name, user.username or "بدون معرف")
        stats.add_usage(user.id)
        user_stats = stats.get_user_stats(user.id)

    if not user_stats:
        user_stats = """
╔════════════════════════════════════════════════════════════════╗
║               👤 إحصائياتك الشخصية             ║
╚════════════════════════════════════════════════════════════════╝

  لم تقم باستخدام البوت بعد!
  ابدأ الآن واستكشف جميع الميزات الرائعة 🚀
"""
    
    keyboard = [
        [InlineKeyboardButton("📊 القائمة الرئيسية", callback_data="stats_view")],
        [InlineKeyboardButton("↩️ عودة للقائمة", callback_data="back_to_menu")]
    ]
    
    await query.message.edit_text(user_stats, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def stats_top_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض أكثر المستخدمين نشاطاً - خاص بالمطور"""
    query = update.callback_query
    user = update.effective_user
    
    # التحقق من المطور
    if not is_developer(user.id, user.username):
        await query.answer("⛔ هذا الخيار متاح للمطور فقط", show_alert=True)
        return
    
    await query.answer()
    
    # الحصول على أكثر المستخدمين نشاطاً
    top_users = sorted(
        stats.data['users'].items(),
        key=lambda x: x[1]['usage_count'],
        reverse=True
    )[:10]
    
    top_users_text = "\n".join([
        f"{i+1}. {user[1]['name']} (@{user[1]['username']})\n   • الاستخدامات: {user[1]['usage_count']}\n   • التحميلات: {user[1]['download_count']}\n   • البحث: {user[1]['search_count']}"
        for i, user in enumerate(top_users)
    ]) if top_users else "لا يوجد مستخدمين بعد"
    
    top_users_msg = f"""
╔════════════════════════════════════════════════════════════════╗
║              🏆 أكثر 10 مستخدمين نشاطاً             ║
╚════════════════════════════════════════════════════════════════╝

{top_users_text}
    """
    
    keyboard = [
        [InlineKeyboardButton("📊 القائمة الرئيسية", callback_data="stats_view")],
        [InlineKeyboardButton("↩️ عودة للقائمة", callback_data="back_to_menu")]
    ]
    
    await query.message.edit_text(top_users_msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def stats_charts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تحليل متقدم وإحصائيات - خاص بالمطور"""
    query = update.callback_query
    user = update.effective_user
    
    # التحقق من المطور
    if not is_developer(user.id, user.username):
        await query.answer("⛔ هذا الخيار متاح للمطور فقط", show_alert=True)
        return
    
    await query.answer()
    
    from datetime import datetime, timedelta
    
    # حساب الإحصائيات المتقدمة
    total_users = stats.data['total_users']
    total_downloads = stats.data['total_downloads']
    total_searches = stats.data['total_searches']
    
    # معدل النجاح
    success_rate = 100 if total_downloads == 0 else (
        (stats.data['successful_downloads'] / total_downloads) * 100
    )
    
    # متوسط الاستخدام لكل مستخدم
    total_usage = sum(u.get('usage_count', 0) for u in stats.data['users'].values())
    avg_usage = total_usage / total_users if total_users > 0 else 0
    
    # حساب النمو اليومي
    now = datetime.now()
    today = now.date().isoformat()
    yesterday = (now - timedelta(days=1)).date().isoformat()
    
    today_downloads = stats.data['daily_stats'].get(today, {}).get('downloads', 0)
    yesterday_downloads = stats.data['daily_stats'].get(yesterday, {}).get('downloads', 0)
    
    growth = 0
    if yesterday_downloads > 0:
        growth = ((today_downloads - yesterday_downloads) / yesterday_downloads) * 100
    
    # أيام التشغيل
    start_date = datetime.fromisoformat(stats.data['start_date'])
    days_running = (datetime.now() - start_date).days + 1
    
    # متوسط التحميلات اليومية
    avg_daily_downloads = total_downloads / days_running if days_running > 0 else 0
    
    # أكثر أنواع التحميلات
    downloads_by_type = stats.data['downloads_by_type']
    top_type = max(downloads_by_type, key=downloads_by_type.get) if downloads_by_type else "لا يوجد"
    
    # أكثر المنصات استخداماً
    platforms = stats.data['platforms']
    top_platform = max(platforms, key=platforms.get) if platforms else "لا يوجد"
    
    charts_msg = f"""
╔════════════════════════════════════════════════════════════════╗
║           📈 التحليل المتقدم والرسوم البيانية           ║
╚════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 **الإحصائيات الأساسية**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  إجمالي المستخدمين: {total_users:,}
  إجمالي التحميلات: {total_downloads:,}
  إجمالي عمليات البحث: {total_searches:,}
  أيام التشغيل: {days_running}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 **معدلات الأداء**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  معدل النجاح: {success_rate:.1f}%
  متوسط التحميلات اليومية: {avg_daily_downloads:.1f}
  متوسط الاستخدام للمستخدم: {avg_usage:.1f}
  نمو التحميلات (اليوم): {growth:+.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **التصنيفات الأكثر شيوعاً**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  أكثر نوع تحميل: {top_type.upper() if top_type != 'لا يوجد' else top_type}
  أكثر منصة: {top_platform.upper() if top_platform != 'لا يوجد' else top_platform}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 **معلومات التاريخ**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  تاريخ البدء: {stats.data['start_date'][:10]}
  آخر تحديث: {stats.data['last_update']}

╚════════════════════════════════════════════════════════════════╝
    """
    
    keyboard = [
        [InlineKeyboardButton("📊 القائمة الرئيسية", callback_data="stats_view")],
        [InlineKeyboardButton("↩️ عودة للقائمة", callback_data="back_to_menu")]
    ]
    
    await query.message.edit_text(charts_msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def broadcast_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إظهار واجهة الإرسال للمطور من الزر"""
    query = update.callback_query
    user = update.effective_user
    
    # التحقق من أن المستخدم هو المطور
    if not is_developer(user.id, user.username):
        await query.answer("⛔ هذا الخيار متاح للمطور فقط", show_alert=True)
        return
    
    await query.answer()
    
    broadcast_message = """
📢 وضع الإرسال الجماعي

الآن يمكنك إرسال رسالة لجميع المستخدمين.

اكتب رسالتك في الشات الآن:
(سيتم إرسالها لجميع مستخدمي البوت)

💡 لاحظ: الرسالة يجب أن تكون نصية فقط (بدون صور أو ملفات)
    """
    
    keyboard = [
        [InlineKeyboardButton("↩️ إلغاء", callback_data="cancel_broadcast")]
    ]
    
    user_states[user.id] = 'broadcast_mode'
    
    await query.message.edit_text(broadcast_message, reply_markup=InlineKeyboardMarkup(keyboard))

async def cancel_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء وضع الإرسال"""
    query = update.callback_query
    user = update.effective_user
    
    # التحقق من أن المستخدم هو المطور
    if not is_developer(user.id, user.username):
        await query.answer("⛔ هذا الخيار متاح للمطور فقط", show_alert=True)
        return
    
    await query.answer()
    
    if user.id in user_states and user_states[user.id] == 'broadcast_mode':
        del user_states[user.id]
    
    keyboard = get_developer_keyboard()
    
    await query.message.edit_text("✅ تم إلغاء الإرسال", reply_markup=keyboard)

async def back_to_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """العودة للقائمة الرئيسية"""
    query = update.callback_query
    user = update.effective_user
    
    await query.answer()
    
    menu_message = """
📋 القائمة الرئيسية

اختر ما تريد:
    """
    
    keyboard = None
    if is_developer(user.id, user.username):
        keyboard = get_developer_keyboard()
    else:
        keyboard = get_type_selection_keyboard()
    
    await query.message.edit_text(menu_message, reply_markup=keyboard)

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل صورة مباشرة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    stats.add_usage(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال رابط مع الأمر\nمثال: /image https://instagram.com/...")
        return
    
    url = context.args[0]
    await download_image_handler(update, context, url)

async def story_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل قصص Instagram للمستخدم"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    stats.add_usage(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال اسم مستخدم Instagram مع الأمر\nمثال: /story username")
        return
    
    username = context.args[0].strip('@')  # إزالة @ إذا كان موجوداً
    await download_stories_handler(update, context, username)

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل فيديو مباشرة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    stats.add_usage(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال رابط مع الأمر\nمثال: /video https://tiktok.com/...")
        return
    
    url = context.args[0]
    await download_video_handler(update, context, url)

async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل موسيقى مباشرة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return

    user = update.effective_user
    stats.add_usage(user.id)

    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال رابط مع الأمر\nمثال: /audio https://youtube.com/...")
        return
    
    url = context.args[0]
    action_key = f"audio:{url}"
    if is_duplicate_action(user.id, action_key) or not begin_action(user.id, action_key):
        await update.message.reply_text("⏳ يوجد طلب مماثل قيد المعالجة. يرجى الانتظار.")
        return
    message = await update.message.reply_text("🎵 جاري تحميل الموسيقى...")
    
    try:
        loop = asyncio.get_running_loop()
        filename, title = await loop.run_in_executor(None, downloader.download_audio, url)

        await message.edit_text("📤 جاري إرسال الملف...")

        with open(filename, 'rb') as audio_file:
            await update.message.reply_audio(
                audio=audio_file,
                title=title,
                caption=f"🎵 {title}"
            )

        stats.add_download('audio', user.id, 'youtube')
        os.remove(filename)
        await message.delete()

    except Exception as e:
        stats.add_failed_download()
        await message.edit_text(f"❌ خطأ: {str(e)}")

    finally:
        end_action(user.id, action_key)

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض معلومات مفصلة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    stats.add_usage(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال رابط مع الأمر\nمثال: /info https://youtube.com/...")
        return
    
    url = context.args[0]
    message = await update.message.reply_text("🔍 جاري جلب المعلومات...")
    
    try:
        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, downloader.get_info, url)
        
        if not info:
            await message.edit_text("❌ لم يتم العثور على معلومات")
            return
        
        title = info.get('title', info.get('webpage_title', 'غير متوفر'))
        uploader = info.get('uploader', info.get('channel', info.get('creator', 'غير متوفر')))
        duration = info.get('duration', 0)
        view_count = info.get('view_count', 0)
        like_count = info.get('like_count', 0)
        
        if duration and duration > 0:
            hours = duration // 3600
            minutes = (duration % 3600) // 60
            seconds = duration % 60
            if hours > 0:
                duration_str = f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                duration_str = f"{minutes}:{seconds:02d}"
        else:
            duration_str = "غير متوفر"
        
        view_str = f"{view_count:,}" if view_count else "غير متوفر"
        like_str = f"{like_count:,}" if like_count else "غير متوفر"
        
        info_text = f"""
📊 معلومات المحتوى:

📌 العنوان: {title}

👤 الناشر: {uploader}

⏱️ المدة: {duration_str}

👁️ المشاهدات: {view_str}
❤️ الإعجابات: {like_str}
        """
        
        await message.edit_text(info_text)
        
    except Exception as e:
        await message.edit_text(f"❌ خطأ: {str(e)}")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """البحث عن أغنية في YouTube"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    stats.add_usage(update.effective_user.id)
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ الرجاء كتابة اسم الأغنية بعد الأمر\n"
            "مثال: /search Imagine Dragons Believer"
        )
        return
    
    query = ' '.join(context.args)
    message = await update.message.reply_text(f"🔍 جاري البحث عن: {query}...")
    
    stats.add_search()
    
    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, downloader.search_youtube, query, 5)
        
        if not results:
            await message.edit_text("❌ لم يتم العثور على نتائج")
            return
        
        user_id = update.effective_user.id
        search_results[user_id] = results
        
        keyboard = []
        for i, video in enumerate(results):
            duration = video['duration']
            if duration and duration > 0:
                minutes = int(duration) // 60
                seconds = int(duration) % 60
                duration_str = f"{minutes}:{seconds:02d}"
            else:
                duration_str = "?"
            
            button_text = f"🎵 {video['title'][:45]}... ({duration_str})"
            keyboard.append([InlineKeyboardButton(
                button_text, 
                callback_data=f"download_song_{i}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await message.edit_text(
            f"🎵 نتائج البحث عن: {query}\n\n"
            f"اختر الأغنية التي تريد تحميلها:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        await message.edit_text(f"❌ خطأ في البحث: {str(e)}")
        logger.error(f"خطأ في search_command: {e}")

async def download_song_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل الأغنية المختارة"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if user_id not in search_results:
        await query.message.edit_text("❌ انتهت صلاحية البحث. الرجاء البحث مرة أخرى")
        return
    
    try:
        song_index = int(query.data.split('_')[-1])
        video = search_results[user_id][song_index]
    except (IndexError, ValueError):
        await query.message.edit_text("❌ خطأ في اختيار الأغنية")
        return
    
    await query.message.edit_text(f"🎵 جاري تحميل: {video['title'][:50]}...")
    
    try:
        loop = asyncio.get_running_loop()
        filename, title = await loop.run_in_executor(None, downloader.download_audio, video['url'])
        
        stats.add_download('search', user_id, 'youtube')
        
        await query.message.edit_text("📤 جاري إرسال الأغنية...")
        
        with open(filename, 'rb') as audio_file:
            await query.message.reply_audio(
                audio=audio_file,
                title=title,
                performer=video['channel'],
                caption=f"🎵 {title}\n👤 {video['channel']}"
            )
        
        os.remove(filename)
        await query.message.delete()
        
        if user_id in search_results:
            del search_results[user_id]
        
    except Exception as e:
        stats.add_failed_download()
        await query.message.edit_text(f"❌ خطأ في التحميل: {str(e)}")
        logger.error(f"خطأ في download_song_callback: {e}")

async def download_song_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل نتيجة محددة من البحث وإرسالها كموسيقى"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    if user_id not in search_results:
        await query.message.edit_text("❌ لا توجد نتائج بحث حالية. يرجى تنفيذ البحث مرة أخرى.")
        return

    try:
        song_index = int(query.data.split('_')[-1])
        video = search_results[user_id][song_index]
    except (IndexError, ValueError):
        await query.message.edit_text("❌ تعذر تحديد الاختيار. يرجى إعادة البحث.")
        return

    action_key = f"search_audio:{video.get('url')}"
    if is_duplicate_action(user_id, action_key) or not begin_action(user_id, action_key):
        await query.answer("⏳ الطلب قيد المعالجة.", show_alert=True)
        return

    await query.message.edit_text(f"🎵 جاري تحميل: {video['title'][:50]}...")

    try:
        loop = asyncio.get_running_loop()
        filename, title = await loop.run_in_executor(None, downloader.download_audio, video['url'])

        stats.add_download('search', user_id, 'youtube')

        await query.message.edit_text("جار ارسال الاغنية ....")

        with open(filename, 'rb') as audio_file:
            await query.message.reply_audio(
                audio=audio_file,
                title=title,
                performer=video.get('channel', ''),
                caption=f"🎵 {title}\n🎤 {video.get('channel', '')}"
            )

        os.remove(filename)
        await query.message.delete()

        if user_id in search_results:
            del search_results[user_id]

    except Exception as e:
        stats.add_failed_download(user_id)
        await query.message.edit_text(f"❌ خطأ في تحميل الصوت: {str(e)}")
        logger.error(f"خطأ في download_song_callback: {e}")
    finally:
        end_action(user_id, action_key)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الإحصائيات الشاملة - متاح للجميع"""
    user_id = update.effective_user.id
    user_username = update.effective_user.username or ""
    
    # تسجيل المستخدم
    stats.add_user(user_id, update.effective_user.full_name, user_username)
    stats.add_usage(user_id)
    
    # إنشاء لوحة مفاتيح الإحصائيات
    keyboard = []
    
    is_dev = is_developer(user_id, user_username)
    
    if is_dev:
        keyboard = [
            [
                InlineKeyboardButton("📊 إحصائيات عامة", callback_data="stats_general"),
                InlineKeyboardButton("👤 إحصائياتي", callback_data="stats_personal")
            ],
            [
                InlineKeyboardButton("🏆 أكثر المستخدمين", callback_data="stats_top_users"),
                InlineKeyboardButton("📈 الرسوم البيانية", callback_data="stats_charts")
            ]
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton("📊 إحصائيات عامة", callback_data="stats_general"),
                InlineKeyboardButton("👤 إحصائياتي", callback_data="stats_personal")
            ]
        ]
    
    intro_text = """
╔════════════════════════════════════════════════════════════════╗
║                  📊 مركز الإحصائيات              ║
╚════════════════════════════════════════════════════════════════╝

اختر نوع الإحصائيات التي تريد مشاهدتها:

📊 **الإحصائيات العامة** - معلومات شاملة عن البوت
👤 **إحصائياتي** - معلوماتك الشخصية والترتيب
"""
    
    if is_dev:
        intro_text += """
🏆 **أكثر المستخدمين** - ترتيب المستخدمين النشطين
📈 **الرسوم البيانية** - تحليل تفصيلي وإحصائيات متقدمة
"""
    
    await update.message.reply_text(intro_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال رسالة لجميع المستخدمين (للمطور فقط)"""
    user_id = update.effective_user.id
    user_username = update.effective_user.username or ""
    
    # التحقق من أن المستخدم هو المطور (عبر ID أو Username)
    is_developer = (user_id == DEVELOPER_ID) or (USERNAME_FOR_DEVELOPER and f"@{user_username}" == USERNAME_FOR_DEVELOPER)
    
    if not is_developer:
        await update.message.reply_text("⛔ هذا الأمر متاح للمطور فقط")
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ الرجاء كتابة الرسالة بعد الأمر\n"
            "مثال: /broadcast مرحباً بالجميع!"
        )
        return
    
    broadcast_text = ' '.join(context.args)
    message = await update.message.reply_text("📤 جاري إرسال الرسالة...")
    
    success_count = 0
    fail_count = 0
    
    for user_id_str in stats.data['users'].keys():
        try:
            await context.bot.send_message(
                chat_id=int(user_id_str),
                text=f"رسالة من المطور 👨‍💻\n\n{broadcast_text}"
            )
            success_count += 1
        except Exception as e:
            fail_count += 1
            logger.error(f"فشل إرسال رسالة إلى {user_id_str}: {e}")
    
    await message.edit_text(
        f"✅ تم إرسال الرسالة\n\n"
        f"✅ نجح: {success_count}\n"
        f"❌ فشل: {fail_count}"
    )

async def dump_debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال ملف السجلات للمطور (للمطور فقط)"""
    user_id = update.effective_user.id
    user_username = update.effective_user.username or ""
    
    # التحقق من أن المستخدم هو المطور (عبر ID أو Username)
    is_developer = (user_id == DEVELOPER_ID) or (USERNAME_FOR_DEVELOPER and f"@{user_username}" == USERNAME_FOR_DEVELOPER)
    
    if not is_developer:
        await update.message.reply_text("⛔ هذا الأمر متاح للمطور فقط")
        return
    
    debug_file = os.path.join(DOWNLOAD_FOLDER, 'yt_dlp_debug.log')
    
    if not os.path.exists(debug_file):
        await update.message.reply_text("❌ ملف السجلات غير موجود")
        return
    
    try:
        with open(debug_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # أخذ آخر 200 سطر
        last_lines = lines[-200:] if len(lines) > 200 else lines
        debug_content = ''.join(last_lines)
        
        # إذا كان المحتوى طويلاً جداً، قسمه
        if len(debug_content) > 4000:
            debug_content = debug_content[-4000:]
        
        await update.message.reply_text(
            f"📄 آخر 200 سطر من ملف السجلات:\n\n```\n{debug_content}\n```"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في قراءة ملف السجلات: {str(e)}")

async def download_image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """معالج تحميل الصور مع معالجة أخطاء محسّنة"""
    message = await update.message.reply_text("📸 جاري التحميل...")
    
    user_id = update.effective_user.id
    # تحديد المنصة من الرابط
    platform = 'instagram' if 'instagram' in url.lower() else 'other'
    
    filename = None
    try:
        logger.info(f"تحميل صورة من: {url[:50]}...")
        
        loop = asyncio.get_running_loop()
        filename, title = await asyncio.wait_for(
            loop.run_in_executor(None, downloader.download_image, url),
            timeout=DEFAULT_TIMEOUT
        )
        
        if not os.path.exists(filename):
            await message.edit_text("❌ الملف غير موجود")
            return
        
        file_size = os.path.getsize(filename)
        
        if file_size == 0:
            await message.edit_text("❌ الملف فارغ")
            os.remove(filename)
            return
        
        if file_size > MAX_FILE_SIZE_IMAGE:
            await message.edit_text(
                f"⚠️ كبير ({file_size // (1024*1024)} MB)\n"
                f"الحد الأقصى: {MAX_FILE_SIZE_IMAGE // (1024*1024)} MB"
            )
            os.remove(filename)
            return
        
        await message.edit_text("📤 جاري الإرسال...")
        
        with open(filename, 'rb') as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=f"📸 {title[:200]}"
            )
        stats.add_download('image', user_id, platform)
        await message.delete()
        
    except asyncio.TimeoutError:
        stats.add_failed_download(user_id)
        await message.edit_text("⏱️ انتهت المهلة")
    except Exception as e:
        stats.add_failed_download()
        await message.edit_text(f"❌ خطأ: {str(e)[:100]}")
        logger.error(f"فشل تحميل الصورة: {e}")
    finally:
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass
        end_action(user_id, action_key)

async def download_video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """معالج تحميل الفيديوهات مع معالجة أخطاء محسّنة"""
    message = await update.message.reply_text("🎬 جاري التحميل...")
    
    user_id = update.effective_user.id
    # تحديد المنصة من الرابط
    if 'youtube' in url.lower():
        platform = 'youtube'
    elif 'tiktok' in url.lower() or 'vm.tiktok' in url.lower():
        platform = 'tiktok'
    elif 'instagram' in url.lower():
        platform = 'instagram'
    elif 'twitter' in url.lower() or 'x.com' in url.lower():
        platform = 'twitter'
    elif 'facebook' in url.lower() or 'fb.com' in url.lower():
        platform = 'facebook'
    else:
        platform = 'other'
    
    filename = None
    try:
        loop = asyncio.get_running_loop()
        
        # تحديد مهلة زمنية لتجنب التعليق
        filename, title = await asyncio.wait_for(
            loop.run_in_executor(None, downloader.download_video, url),
            timeout=DEFAULT_TIMEOUT + 30  # 60 ثانية
        )
        
        file_size = os.path.getsize(filename)
        
        if file_size > MAX_FILE_SIZE_VIDEO:
            stats.add_failed_download(user_id)
            await message.edit_text(
                f"⚠️ الملف كبير جداً ({file_size // (1024*1024)} MB)\n"
                f"الحد الأقصى: {MAX_FILE_SIZE_VIDEO // (1024*1024)} MB\n\n"
                f"💡 جرب: /audio {url}"
            )
            os.remove(filename)
            return
        
        await message.edit_text("📤 جاري الإرسال...")
        
        with open(filename, 'rb') as video:
            await update.message.reply_video(
                video=video,
                caption=f"🎬 {title[:200]}",
                supports_streaming=True
            )
        
        stats.add_download('video', user_id, platform)
        os.remove(filename)
        await message.delete()
        
    except asyncio.TimeoutError:
        stats.add_failed_download(user_id)
        await message.edit_text("⏱️ انتهت المهلة - الملف قد يكون كبير جداً")
    except FileNotFoundError:
        stats.add_failed_download(user_id)
        await message.edit_text("❌ الملف غير موجود")
    except Exception as e:
        stats.add_failed_download()
        error_msg = str(e)[:150]
        await message.edit_text(f"❌ خطأ: {error_msg}")
        logger.error(f"فشل تحميل الفيديو: {e}")
    finally:
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass
        end_action(user_id, action_key)

async def download_story_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """معالج تحميل قصص Instagram"""
    message = await update.message.reply_text("📸 جاري تحميل قصة Instagram...")
    
    filename = None
    try:
        loop = asyncio.get_running_loop()
        filename, title = await loop.run_in_executor(None, downloader.download_instagram_story, url)
        
        if not os.path.exists(filename):
            await message.edit_text("❌ الملف غير موجود")
            return
        
        file_size = os.path.getsize(filename)
        if file_size == 0:
            await message.edit_text("❌ الملف فارغ")
            if os.path.exists(filename):
                os.remove(filename)
            return
        
        await message.edit_text("📤 جاري الإرسال...")
        
        # تحديد نوع الملف (صورة أو فيديو)
        file_ext = os.path.splitext(filename)[1].lower()
        
        if file_ext in ['.mp4', '.mov', '.webm']:
            # فيديو
            max_size = 50 * 1024 * 1024
            if file_size > max_size:
                await message.edit_text(
                    f"⚠️ الفيديو كبير جداً ({file_size // (1024*1024)} MB)\n"
                    f"الحد الأقصى: 50 MB"
                )
                os.remove(filename)
                return
            
            with open(filename, 'rb') as video:
                await update.message.reply_video(
                    video=video,
                    caption=f"📸 {title}",
                    supports_streaming=True
                )
            stats.add_download('video')
        else:
            # صورة
            max_size = 10 * 1024 * 1024
            if file_size > max_size:
                await message.edit_text(
                    f"⚠️ الصورة كبيرة جداً ({file_size // (1024*1024)} MB)\n"
                    f"الحد الأقصى: 10 MB"
                )
                os.remove(filename)
                return
            
            with open(filename, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"📸 {title}"
                )
            stats.add_download('image')
        
        os.remove(filename)
        await message.delete()
        
    except Exception as e:
        stats.add_failed_download()
        error_msg = f"❌ خطأ: {str(e)[:200]}"
        await message.edit_text(error_msg)
        logger.error(f"خطأ في download_story_handler: {e}")
        
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass

async def download_stories_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, username: str):
    """معالج تحميل قصص Instagram للمستخدم"""
    message = await update.message.reply_text(f"📸 جاري تحميل قصص Instagram للمستخدم: {username}...")
    
    try:
        loop = asyncio.get_running_loop()
        stories = await loop.run_in_executor(None, downloader.download_instagram_stories, username)
        
        if not stories:
            await message.edit_text("❌ لم يتم العثور على قصص متاحة")
            return
        
        await message.edit_text(f"✅ تم العثور على {len(stories)} قصة. جاري الإرسال...")
        
        sent_count = 0
        for filename, title in stories:
            try:
                if not os.path.exists(filename):
                    logger.warning(f"الملف غير موجود: {filename}")
                    continue
                
                file_size = os.path.getsize(filename)
                if file_size == 0:
                    logger.warning(f"الملف فارغ: {filename}")
                    os.remove(filename)
                    continue
                
                # تحديد نوع الملف (صورة أو فيديو)
                file_ext = os.path.splitext(filename)[1].lower()
                
                if file_ext in ['.mp4', '.mov', '.webm']:
                    # فيديو
                    max_size = 50 * 1024 * 1024
                    if file_size > max_size:
                        logger.warning(f"الفيديو كبير جداً: {filename}")
                        os.remove(filename)
                        continue
                    
                    with open(filename, 'rb') as video:
                        await update.message.reply_video(
                            video=video,
                            caption=f"📸 {title} ({sent_count + 1}/{len(stories)})",
                            supports_streaming=True
                        )
                    stats.add_download('video')
                else:
                    # صورة
                    max_size = 10 * 1024 * 1024
                    if file_size > max_size:
                        logger.warning(f"الصورة كبيرة جداً: {filename}")
                        os.remove(filename)
                        continue
                    
                    with open(filename, 'rb') as photo:
                        await update.message.reply_photo(
                            photo=photo,
                            caption=f"📸 {title} ({sent_count + 1}/{len(stories)})"
                        )
                    stats.add_download('image')
                
                sent_count += 1
                os.remove(filename)
                
                # انتظار قصير بين الإرسالات لتجنب الحظر
                if sent_count < len(stories):
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"فشل إرسال القصة {filename}: {e}")
                if os.path.exists(filename):
                    os.remove(filename)
                continue
        
        await message.delete()
        
        if sent_count == 0:
            await update.message.reply_text("❌ فشل إرسال أي قصة")
        else:
            await update.message.reply_text(f"✅ تم إرسال {sent_count} قصة بنجاح")
        
    except Exception as e:
        stats.add_failed_download()
        error_msg = f"❌ خطأ: {str(e)[:200]}"
        await message.edit_text(error_msg)
        logger.error(f"خطأ في download_stories_handler: {e}")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الروابط أو البحث حسب اختيار المستخدم"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    try:
        text = update.message.text
        user_id = update.effective_user.id
        user = update.effective_user
        
        # تحديث آخر وقت نشاط للمستخدم
        user_timeouts[user_id] = time.time()
        
        # معالجة وضع الإرسال الجماعي للمطور
        if user_id in user_states and user_states[user_id] == 'broadcast_mode':
            if is_developer(user.id, user.username):
                broadcast_text = text
                message = await update.message.reply_text("📤 جاري الإرسال...")
                
                success_count = 0
                fail_count = 0
                
                for user_id_str in stats.data['users'].keys():
                    try:
                        await context.bot.send_message(
                            chat_id=int(user_id_str),
                            text=f"رسالة من المطور 👨‍💻\n\n{broadcast_text}"
                        )
                        success_count += 1
                    except Exception as e:
                        fail_count += 1
                        logger.error(f"فشل الإرسال إلى {user_id_str}")
                
                del user_states[user_id]
                
                keyboard = get_developer_keyboard()
                
                await message.edit_text(
                    f"✅ تم الإرسال\n\n"
                    f"✅ نجح: {success_count}\n"
                    f"❌ فشل: {fail_count}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ عودة", callback_data="back_to_menu")]])
                )
                return
        
        # عد الاستخدام
        stats.add_usage(user_id)
        
        # معالجة البحث
        if user_id in user_states and user_states[user_id] == 'search':
            message = await update.message.reply_text(f"🔍 جاري البحث...")
            
            stats.add_search()
            
            try:
                loop = asyncio.get_running_loop()
                results = await loop.run_in_executor(None, downloader.search_youtube, text, MAX_SEARCH_RESULTS)
                
                if not results:
                    await message.edit_text("❌ لا توجد نتائج")
                    return
                
                search_results[user_id] = results
                
                keyboard = []
                for i, video in enumerate(results):
                    duration = video.get('duration', 0)
                    if duration and duration > 0:
                        minutes = int(duration) // 60
                        seconds = int(duration) % 60
                        duration_str = f"{minutes}:{seconds:02d}"
                    else:
                        duration_str = "؟"
                    
                    button_text = f"🎵 {video['title'][:40]}... ({duration_str})"
                    keyboard.append([InlineKeyboardButton(button_text, callback_data=f"download_song_{i}")])
                
                await message.edit_text(
                    f"🎵 النتائج:\n\n{text}\n\n"
                    f"اختر:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except asyncio.TimeoutError:
                await message.edit_text("⏱️ انتهت مهلة البحث")
            except Exception as e:
                await message.edit_text(f"❌ خطأ: {str(e)[:100]}")
                logger.error(f"خطأ البحث: {e}")
            
            return
        
        # التحقق من الروابط
        if not text.startswith(('http://', 'https://')):
            return
        
        if user_id not in user_states:
            await update.message.reply_text(
                "⚠️ اختر النوع أولاً",
                reply_markup=get_type_selection_keyboard()
            )
            return
        
        download_type = user_states[user_id]
        
        # معالجة أنواع التحميل المختلفة
        if download_type == 'image':
            await download_image_handler(update, context, text)
        elif download_type == 'video':
            await download_video_handler(update, context, text)
        elif download_type == 'audio':
            action_key = f"audio:{text}"
            if is_duplicate_action(user_id, action_key) or not begin_action(user_id, action_key):
                await update.message.reply_text("⏳ يوجد طلب مماثل قيد المعالجة. يرجى الانتظار.")
                return
            message = await update.message.reply_text("🎵 جاري...")
            
            # تحديد المنصة من الرابط
            if 'youtube' in text.lower():
                platform = 'youtube'
            elif 'tiktok' in text.lower() or 'vm.tiktok' in text.lower():
                platform = 'tiktok'
            elif 'instagram' in text.lower():
                platform = 'instagram'
            elif 'twitter' in text.lower() or 'x.com' in text.lower():
                platform = 'twitter'
            elif 'soundcloud' in text.lower():
                platform = 'soundcloud'
            else:
                platform = 'other'
            
            try:
                loop = asyncio.get_running_loop()
                filename, title = await asyncio.wait_for(
                    loop.run_in_executor(None, downloader.download_audio, text),
                    timeout=DEFAULT_TIMEOUT
                )
                
                await message.edit_text("📤 جاري الإرسال...")
                
                with open(filename, 'rb') as audio_file:
                    await update.message.reply_audio(
                        audio=audio_file,
                        title=title,
                        caption=f"🎵 {title}"
                    )
                
                stats.add_download('audio', user_id, platform)
                os.remove(filename)
                await message.delete()
            except asyncio.TimeoutError:
                stats.add_failed_download(user_id)
                await message.edit_text("⏱️ انتهت المهلة")
            except Exception as e:
                stats.add_failed_download(user_id)
                await message.edit_text(f"❌ خطأ: {str(e)[:100]}")
            finally:
                end_action(user_id, action_key)
        elif download_type == 'story':
            username = text.strip('@')
            await download_stories_handler(update, context, username)
        elif download_type == 'info':
            context.args = [text]
            await info_command(update, context)
    
    except Exception as e:
        logger.error(f"خطأ عام: {e}")
        try:
            await update.message.reply_text(f"❌ خطأ: {str(e)[:100]}")
        except:
            pass
        return
        if False:
            success_count = 0
            fail_count = 0
            
            for user_id_str in stats.data['users'].keys():
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id_str),
                        text=f"رسالة من المطور 👨‍💻\n\n{broadcast_text}"
                    )
                    success_count += 1
                except Exception as e:
                    fail_count += 1
                    logger.error(f"فشل إرسال رسالة إلى {user_id_str}: {e}")
            
            del user_states[user_id]
            
            keyboard = get_developer_keyboard()
            
            await message.edit_text(
                f"✅ تم إرسال الرسالة\n\n"
                f"✅ نجح: {success_count}\n"
                f"❌ فشل: {fail_count}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ عودة", callback_data="back_to_menu")]])
            )
            return
    
    # عد الاستخدام عند إرسال أي رسالة
    stats.add_usage(user_id)
    
    if user_id in user_states and user_states[user_id] == 'search':
        message = await update.message.reply_text(f"🔍 جاري البحث عن: {text}...")
        
        stats.add_search()
        
        try:
            results = downloader.search_youtube(text, max_results=5)
            
            if not results:
                await message.edit_text("❌ لم يتم العثور على نتائج")
                return
            
            search_results[user_id] = results
            
            keyboard = []
            for i, video in enumerate(results):
                duration = video['duration']
                if duration and duration > 0:
                    minutes = int(duration) // 60
                    seconds = int(duration) % 60
                    duration_str = f"{minutes}:{seconds:02d}"
                else:
                    duration_str = "?"
                
                button_text = f"🎵 {video['title'][:45]}... ({duration_str})"
                keyboard.append([InlineKeyboardButton(
                    button_text, 
                    callback_data=f"download_song_{i}"
                )])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await message.edit_text(
                f"🎵 نتائج البحث عن: {text}\n\n"
                f"اختر الأغنية التي تريد تحميلها:",
                reply_markup=reply_markup
            )
            
        except Exception as e:
            await message.edit_text(f"❌ خطأ في البحث: {str(e)}")
            logger.error(f"خطأ في البحث: {e}")
        
        return
    
    if not text.startswith(('http://', 'https://')):
        return
    
    if user_id not in user_states:
        await update.message.reply_text(
            "⚠️ الرجاء اختيار نوع المحتوى أولاً:\n\n"
            "استخدم /start لعرض الخيارات",
            reply_markup=get_type_selection_keyboard()
        )
        return
    
    download_type = user_states[user_id]
    
    if download_type == 'image':
        await download_image_handler(update, context, text)
    elif download_type == 'video':
        await download_video_handler(update, context, text)
    elif download_type == 'audio':
        message = await update.message.reply_text("🎵 جاري تحميل الموسيقى...")
        try:
            filename, title = downloader.download_audio(text)
            await message.edit_text("📤 جاري إرسال الملف...")
            
            with open(filename, 'rb') as audio_file:
                await update.message.reply_audio(
                    audio=audio_file,
                    title=title,
                    caption=f"🎵 {title}"
                )
            
            stats.add_download('audio')
            os.remove(filename)
            await message.delete()
        except Exception as e:
            stats.add_failed_download()
            await message.edit_text(f"❌ خطأ: {str(e)}")
    elif download_type == 'story':
        username = text.strip('@')  # إزالة @ إذا كان موجوداً
        await download_stories_handler(update, context, username)
    elif download_type == 'info':
        # إنشاء context.args مؤقت للاستخدام مع info_command
        context.args = [text]
        await info_command(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة المساعدة احترافية وشاملة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    help_text = """
╔════════════════════════════════════════════════╗
║      📚 دليل استخدام بوت تحميل المحتوى       ║
╚════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 **الطريقة الأولى - استخدام الأزرار**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1️⃣ اضغط على /start
2️⃣ اختر نوع المحتوى (فيديو، موسيقى، إلخ)
3️⃣ أرسل الرابط
4️⃣ انتظر التحميل... ✓

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚡ **الطريقة الثانية - استخدام الأوامر**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎬 `/video [الرابط]`
   لتحميل الفيديوهات من جميع المنصات

🎵 `/audio [الرابط]`
   لتحميل الموسيقى والأصوات عالية الجودة

🔍 `/search [اسم الأغنية]`
   للبحث عن الأغاني على YouTube

📊 `/info [الرابط]`
   لعرض معلومات المحتوى (المدة، المشاهدات، إلخ)

📸 `/story [اسم_المستخدم]`
   لتحميل قصص Instagram (بدون @)

❓ `/help`
   لعرض هذه الرسالة

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌐 **المنصات المدعومة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ YouTube       ✅ Instagram     ✅ TikTok
✅ Twitter/X     ✅ Facebook      ✅ Pinterest
✅ SoundCloud    ✅ Reddit        ✅ وغيرها...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 **نصائح وحيل مفيدة**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💎 للملفات الكبيرة (50MB+):
   استخدم /audio بدلاً من /video

💎 لتجنب الأخطاء:
   • تأكد من نسخ الرابط بشكل صحيح
   • تأكد من أن الحساب عام وليس خاص
   • جرب رابط آخر إذا فشل

💎 البحث عن الأغاني:
   • اكتب اسم الفنان والأغنية
   • مثال: /search Imagine Dragons Believer
   • سيظهر 5 نتائج للاختيار من بينها

💎 قصص Instagram:
   • تأكد من عدم إغلاق الحساب
   • استخدم اسم المستخدم بدون @
   • مثال: /story username

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ **المشاكل الشائعة والحلول**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❌ "الملف كبير جداً"
✅ الحل: استخدم /audio بدلاً من /video

❌ "الرابط غير صحيح"
✅ الحل: تأكد من نسخ الرابط كاملاً

❌ "انتهت المهلة الزمنية"
✅ الحل: جرب الرابط مرة أخرى أو اختر ملف أصغر

❌ "الحساب خاص"
✅ الحل: استخدم حساب عام أو احفظ الرابط من حساب عام

❌ "الفيديو محذوف"
✅ الحل: تأكد من توفر المحتوى على المنصة

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📧 **نحتاج رأيك!**
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

إذا واجهت مشكلة أو لديك اقتراح:
أرسل رسالة إلى @husTh1 على التلجرام

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✨ استمتع باستخدام البوت! 🚀
"""
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

def main():
    """تشغيل البوت مع تحسينات"""
    # طباعة معلومات البدء
    logger.info("=" * 50)
    logger.info("🚀 جاري تشغيل البوت...")
    logger.info("=" * 50)
    
    if FFMPEG_PATH:
        logger.info(f"✅ ffmpeg: {FFMPEG_PATH}")
    else:
        logger.warning("⚠️ ffmpeg غير متوفر")
    
    logger.info(f"✅ قناة الاشتراك: {REQUIRED_CHANNEL}")
    logger.info(f"✅ معرف المطور: {DEVELOPER_ID}")
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # تسجيل معالجات Callback
    callback_handlers = [
        ("check_subscription", check_subscription_callback),
        ("type_", type_selection_callback),
        ("download_song_", download_song_callback),
        ("stats_view", stats_view_callback),
        ("stats_general", stats_general_callback),
        ("stats_personal", stats_personal_callback),
        ("stats_top_users", stats_top_users_callback),
        ("stats_charts", stats_charts_callback),
        ("broadcast_view", broadcast_view_callback),
        ("cancel_broadcast", cancel_broadcast_callback),
        ("back_to_menu", back_to_menu_callback),
    ]
    
    for pattern, handler in callback_handlers:
        application.add_handler(CallbackQueryHandler(handler, pattern=pattern))
        logger.debug(f"تسجيل: {pattern}")
    
    # تسجيل معالجات الأوامر
    command_handlers = [
        ("start", start),
        ("help", help_command),
        ("image", image_command),
        ("video", video_command),
        ("audio", audio_command),
        ("story", story_command),
        ("info", info_command),
        ("search", search_command),
        ("stats", stats_command),
        ("broadcast", broadcast_command),
        ("dump_debug", dump_debug_command),
    ]
    
    for command, handler in command_handlers:
        application.add_handler(CommandHandler(command, handler))
        logger.debug(f"تسجيل: /{command}")
    
    # معالج الرسائل النصية
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    logger.info("✅ جميع المعالجات مسجلة")
    logger.info("=" * 50)
    logger.info("✅ البوت يعمل الآن...")
    logger.info("=" * 50)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("🛑 تم إيقاف البوت من قبل المستخدم")
    except Exception as e:
        logger.critical(f"❌ خطأ حرج: {e}")
        raise
