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

# تحميل المتغيرات من ملف .env
load_dotenv()

# إعداد نظام السجلات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# جلب التوكن من ملف .env
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# التحقق من وجود التوكن
if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ خطأ: لم يتم العثور على TELEGRAM_BOT_TOKEN في ملف .env")
    exit(1)

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

# تخزين حالة المستخدم (نوع التحميل المطلوب)
user_states = {}
# تخزين نتائج البحث
search_results = {}

# دالة للعثور على ffmpeg
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

# نظام الإحصائيات
class BotStats:
    def __init__(self):
        self.stats_file = STATS_FILE
        self.load_stats()
    
    def load_stats(self):
        """تحميل الإحصائيات من الملف"""
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
            except:
                self.data = self.create_new_stats()
        else:
            self.data = self.create_new_stats()
    
    def create_new_stats(self):
        """إنشاء إحصائيات جديدة"""
        return {
            'total_users': 0,
            'users': {},
            'total_downloads': 0,
            'downloads_by_type': {
                'image': 0,
                'video': 0,
                'audio': 0,
                'search': 0
            },
            'total_searches': 0,
            'failed_downloads': 0,
            'start_date': datetime.now().isoformat()
        }
    
    def save_stats(self):
        """حفظ الإحصائيات إلى الملف"""
        try:
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"خطأ في حفظ الإحصائيات: {e}")
    
    def add_user(self, user_id, name, username):
        """إضافة مستخدم جديد"""
        user_id_str = str(user_id)
        now = datetime.now().isoformat()
        
        if user_id_str not in self.data['users']:
            self.data['total_users'] += 1
            self.data['users'][user_id_str] = {
                'name': name,
                'username': username,
                'first_seen': now,
                'last_seen': now,
                'usage_count': 1
            }
        else:
            self.data['users'][user_id_str]['last_seen'] = now
            self.data['users'][user_id_str]['usage_count'] += 1
        
        self.save_stats()
    
    def add_download(self, download_type):
        """تسجيل تحميل"""
        self.data['total_downloads'] += 1
        if download_type in self.data['downloads_by_type']:
            self.data['downloads_by_type'][download_type] += 1
        self.save_stats()
    
    def add_search(self):
        """تسجيل بحث"""
        self.data['total_searches'] += 1
        self.save_stats()
    
    def add_failed_download(self):
        """تسجيل تحميل فاشل"""
        self.data['failed_downloads'] += 1
        self.save_stats()
    
    def get_stats_text(self):
        """الحصول على نص الإحصائيات"""
        from datetime import datetime, timedelta
        now = datetime.now()
        active_users = 0
        
        for user_data in self.data['users'].values():
            last_seen = datetime.fromisoformat(user_data['last_seen'])
            if (now - last_seen).days == 0:
                active_users += 1
        
        top_users = sorted(
            self.data['users'].items(),
            key=lambda x: x[1]['usage_count'],
            reverse=True
        )[:5]
        
        top_users_text = "\n".join([
            f"  {i+1}. {user[1]['name']} (@{user[1]['username']}) - {user[1]['usage_count']} استخدام"
            for i, user in enumerate(top_users)
        ])
        
        stats_text = f"""
📊 إحصائيات البوت الشاملة

👥 المستخدمين:
• إجمالي المستخدمين: {self.data['total_users']}
• المستخدمين النشطين اليوم: {active_users}

📥 التحميلات:
• إجمالي التحميلات: {self.data['total_downloads']}
  - صور: {self.data['downloads_by_type']['image']}
  - فيديوهات: {self.data['downloads_by_type']['video']}
  - موسيقى: {self.data['downloads_by_type']['audio']}
  - أغاني (بحث): {self.data['downloads_by_type']['search']}

🔍 البحث:
• إجمالي عمليات البحث: {self.data['total_searches']}

❌ الفشل:
• التحميلات الفاشلة: {self.data['failed_downloads']}

🏆 أكثر المستخدمين نشاطاً:
{top_users_text if top_users_text else "  لا يوجد"}

📅 تاريخ البدء: {self.data['start_date'][:10]}
        """
        
        return stats_text

# إنشاء كائن الإحصائيات
stats = BotStats()

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
            
            # استخدام yt-dlp لتحميل القصة
            story_opts = {
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                },
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
            }
            
            # إضافة مسار ffmpeg إذا كان متاحاً
            if FFMPEG_PATH:
                story_opts['ffmpeg_location'] = os.path.dirname(FFMPEG_PATH)
            
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
            error_msg = str(e).lower()
            if 'private' in error_msg or 'not available' in error_msg:
                raise Exception("❌ القصة غير متاحة أو خاصة. تأكد من أن القصة عامة.")
            raise Exception(f"❌ خطأ في تحميل القصة: {str(e)}")
        except Exception as e:
            logger.error(f"خطأ في تحميل قصة Instagram: {e}")
            raise Exception(f"❌ خطأ في تحميل القصة: {str(e)}")
    
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
                last_error = str(e)
                error_msg = last_error.lower()
                
                # إذا كان الخطأ متعلق بالبوت، جرب طريقة بديلة
                if 'bot' in error_msg or 'sign in' in error_msg or 'cookies' in error_msg:
                    if attempt < max_retries - 1:
                        logger.warning(f"⚠️ محاولة {attempt + 1}/{max_retries}: خطأ في المصادقة، جاري المحاولة بطريقة بديلة...")
                        import time
                        time.sleep(2)  # انتظار قصير قبل إعادة المحاولة
                        # جرب بدون extractor_args
                        opts = self.ydl_opts_video.copy()
                        opts.pop('extractor_args', None)
                        opts['user_agent'] = random.choice(user_agents)
                        opts['http_headers']['User-Agent'] = opts['user_agent']
                        continue
                    else:
                        raise Exception("❌ YouTube يطلب المصادقة. الرجاء المحاولة لاحقاً أو استخدام رابط مختلف.")
                else:
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
                last_error = str(e)
                error_msg = last_error.lower()
                
                # إذا كان الخطأ متعلق بالبوت، جرب طريقة بديلة
                if 'bot' in error_msg or 'sign in' in error_msg or 'cookies' in error_msg:
                    if attempt < max_retries - 1:
                        logger.warning(f"⚠️ محاولة {attempt + 1}/{max_retries}: خطأ في المصادقة، جاري المحاولة بطريقة بديلة...")
                        import time
                        time.sleep(2)
                        # جرب بدون extractor_args
                        opts = self.ydl_opts_audio.copy()
                        opts.pop('extractor_args', None)
                        opts['user_agent'] = random.choice(user_agents)
                        opts['http_headers']['User-Agent'] = opts['user_agent']
                        continue
                    else:
                        raise Exception("❌ YouTube يطلب المصادقة. الرجاء المحاولة لاحقاً أو استخدام رابط مختلف.")
                else:
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
            logger.error(f"خطأ yt-dlp: {e}")
            error_msg = str(e).lower()
            if 'bot' in error_msg or 'sign in' in error_msg:
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
            logger.error(f"خطأ في البحث: {e}")
            error_msg = str(e).lower()
            if 'bot' in error_msg or 'sign in' in error_msg:
                raise Exception("❌ YouTube يطلب المصادقة. الرجاء المحاولة لاحقاً.")
            raise Exception(f"فشل البحث: {str(e)}")

# إنشاء كائن التحميل
downloader = SocialMediaDownloader()

async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """التحقق من اشتراك المستخدم في القناة"""
    user_id = update.effective_user.id
    
    try:
        member = await context.bot.get_chat_member(chat_id=f"@{REQUIRED_CHANNEL}", user_id=user_id)
        if member.status in ['creator', 'administrator', 'member']:
            return True
        else:
            return False
    except TelegramError as e:
        logger.error(f"خطأ في التحقق من الاشتراك: {e}")
        return False

async def subscription_required(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة طلب الاشتراك في القناة"""
    keyboard = [
        [InlineKeyboardButton("📢 اشترك في القناة", url=CHANNEL_LINK)],
        [InlineKeyboardButton("✅ تحققت من الاشتراك", callback_data="check_subscription")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = """
🔒 للاستفادة من البوت يجب عليك الاشتراك في قناتنا أولاً!

📢 اضغط على الزر أدناه للاشتراك في القناة
ثم اضغط "✅ تحققت من الاشتراك"
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
        await query.message.edit_text("✅ رائع! أنت مشترك في القناة\nيمكنك الآن استخدام البوت 🎉\n\nأرسل /start لبدء الاستخدام")
    else:
        await subscription_required(update, context)

def get_type_selection_keyboard():
    """إنشاء لوحة مفاتيح اختيار نوع المحتوى"""
    keyboard = [
        [
            InlineKeyboardButton("🎬 فيديو", callback_data="type_video")
        ],
        [
            InlineKeyboardButton("🎵 موسيقى", callback_data="type_audio"),
            InlineKeyboardButton("📊 معلومات", callback_data="type_info")
        ],
        [
            InlineKeyboardButton("📸 قصة Instagram", callback_data="type_story"),
            InlineKeyboardButton("🔍 بحث أغنية", callback_data="type_search")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب مع أزرار الاختيار"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    user = update.effective_user
    stats.add_user(user.id, user.full_name, user.username or "بدون معرف")
    
    welcome_message = """ 

🎉 أهلاً وسهلاً بك في بوت تحميل المحتوى من مواقع التواصل الاجتماعي! 👋
مع هذا البوت يمكنك بسهولة تنزيل كل ما تحتاجه من صور، فيديوهات، موسيقى، بالإضافة إلى عرض المعلومات الخاصة بالمحتوى.

📥 ما الذي يمكنك تحميله؟

🎥 الفيديوهات
🎵 الموسيقى
📸 قصص Instagram
🖼 الصور (سيتم تفعيلها قريبًا بعد انتهاء الصيانة)

🌐 المنصات المدعومة

YouTube – Instagram – TikTok – Facebook – Twitter/X – Pinterest – SoundCloud
والمزيد من المنصات الأخرى!

📝 كيفية الاستخدام

1️⃣ أرسل رابط الفيديو أو موسيقى مباشرة ليتم تحميلها
2️⃣ أو استخدم الاموامر

/video [رابط] لتحميل فيديو

/audio [رابط] لتحميل موسيقى فقط

/story [رابط قصة Instagram] لتحميل قصة Instagram

/info [رابط] لعرض معلومات المحتوى

/search [اسم الأغنية] للبحث عن أغنية

🔗 تم تطوير البوت بواسطة إدارة قناة ساخر | عالم برشلونة

✨ استمتع بتجربتك! 😄

    """
    
    await update.message.reply_text(
        welcome_message,
        reply_markup=get_type_selection_keyboard()
    )

async def type_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالج اختيار نوع المحتوى من الأزرار"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    download_type = query.data.replace("type_", "")
    
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
        'story': 'قصة Instagram'
    }
    
    if download_type == 'search':
        await query.message.edit_text(
            f"{type_emoji[download_type]} {type_name[download_type]}\n\n"
            f"أرسل اسم الأغنية التي تريد البحث عنها...\n\n"
            f"مثال: Imagine Dragons Believer\n\n"
            f"💡 أو اختر نوع آخر:",
            reply_markup=get_type_selection_keyboard()
        )
    elif download_type == 'story':
        await query.message.edit_text(
            f"{type_emoji[download_type]} {type_name[download_type]}\n\n"
            f"أرسل رابط قصة Instagram...\n\n"
            f"مثال: https://www.instagram.com/stories/username/1234567890/\n\n"
            f"💡 أو اختر نوع آخر:",
            reply_markup=get_type_selection_keyboard()
        )
    else:
        await query.message.edit_text(
            f"{type_emoji[download_type]} تم اختيار: {type_name[download_type]}\n\n"
            f"الآن أرسل الرابط...\n\n"
            f"💡 أو اختر نوع آخر:",
            reply_markup=get_type_selection_keyboard()
        )

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل صورة مباشرة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال رابط مع الأمر\nمثال: /image https://instagram.com/...")
        return
    
    url = context.args[0]
    await download_image_handler(update, context, url)

async def story_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل قصة Instagram مباشرة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال رابط قصة Instagram مع الأمر\nمثال: /story https://instagram.com/stories/username/1234567890/")
        return
    
    url = context.args[0]
    await download_story_handler(update, context, url)

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحميل فيديو مباشرة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
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
    
    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال رابط مع الأمر\nمثال: /audio https://youtube.com/...")
        return
    
    url = context.args[0]
    message = await update.message.reply_text("🎵 جاري تحميل الموسيقى...")
    
    try:
        filename, title = downloader.download_audio(url)
        
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

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض معلومات مفصلة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    if not context.args:
        await update.message.reply_text("⚠️ الرجاء إرسال رابط مع الأمر\nمثال: /info https://youtube.com/...")
        return
    
    url = context.args[0]
    message = await update.message.reply_text("🔍 جاري جلب المعلومات...")
    
    try:
        info = downloader.get_info(url)
        
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
        results = downloader.search_youtube(query, max_results=5)
        
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
        filename, title = downloader.download_audio(video['url'])
        
        stats.add_download('search')
        
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

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات البوت (للمطور فقط)"""
    user_id = update.effective_user.id
    
    if user_id != DEVELOPER_ID:
        await update.message.reply_text("⛔ هذا الأمر متاح للمطور فقط")
        return
    
    stats_text = stats.get_stats_text()
    await update.message.reply_text(stats_text)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال رسالة لجميع المستخدمين (للمطور فقط)"""
    user_id = update.effective_user.id
    
    if user_id != DEVELOPER_ID:
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
                text=f"📢 رسالة من المطور:\n\n{broadcast_text}"
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

async def download_image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """معالج تحميل الصور"""
    message = await update.message.reply_text("📸 جاري تحميل الصورة...")
    
    filename = None
    try:
        logger.info(f"=== بدء تحميل الصورة ===")
        logger.info(f"الرابط: {url}")
        
        filename, title = downloader.download_image(url)
        
        logger.info(f"اسم الملف: {filename}")
        logger.info(f"هل الملف موجود: {os.path.exists(filename)}")
        
        if not os.path.exists(filename):
            await message.edit_text("❌ الملف غير موجود")
            return
        
        file_size = os.path.getsize(filename)
        logger.info(f"حجم الملف: {file_size} بايت ({file_size / (1024*1024):.2f} MB)")
        
        if file_size == 0:
            await message.edit_text("❌ الملف فارغ")
            os.remove(filename)
            return
        
        if file_size > 10 * 1024 * 1024:
            await message.edit_text(f"⚠️ الصورة كبيرة جداً ({file_size // (1024*1024)} MB)")
            os.remove(filename)
            return
        
        await message.edit_text("📤 جاري الإرسال...")
        
        try:
            await update.message.reply_photo(
                photo=open(filename, 'rb'),
                caption=f"📸 {title[:200]}"
            )
            stats.add_download('image')
            await message.delete()
            
        except Exception as send_error:
            logger.error(f"فشل إرسال الصورة: {send_error}")
            await message.edit_text(f"❌ فشل الإرسال: {str(send_error)[:100]}")
        
        if os.path.exists(filename):
            os.remove(filename)
            
    except Exception as e:
        stats.add_failed_download()
        error_msg = f"❌ خطأ: {str(e)[:200]}"
        await message.edit_text(error_msg)
        logger.error(f"خطأ في download_image_handler: {e}")
        
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except:
                pass

async def download_video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """معالج تحميل الفيديوهات"""
    message = await update.message.reply_text("🎬 جاري تحميل الفيديو...")
    
    try:
        filename, title = downloader.download_video(url)
        
        file_size = os.path.getsize(filename)
        max_size = 50 * 1024 * 1024
        
        if file_size > max_size:
            stats.add_failed_download()
            await message.edit_text(
                f"⚠️ الملف كبير جداً ({file_size // (1024*1024)} MB)\n"
                f"الحد الأقصى: 50 MB\n\n"
                f"💡 جرب: /audio {url}"
            )
            os.remove(filename)
            return
        
        await message.edit_text("📤 جاري إرسال الفيديو...")
        
        with open(filename, 'rb') as video:
            await update.message.reply_video(
                video=video,
                caption=f"🎬 {title}",
                supports_streaming=True
            )
        
        stats.add_download('video')
        os.remove(filename)
        await message.delete()
        
    except Exception as e:
        stats.add_failed_download()
        await message.edit_text(f"❌ خطأ: {str(e)}")

async def download_story_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """معالج تحميل قصص Instagram"""
    message = await update.message.reply_text("📸 جاري تحميل قصة Instagram...")
    
    filename = None
    try:
        filename, title = downloader.download_instagram_story(url)
        
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

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الروابط أو البحث حسب اختيار المستخدم"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    text = update.message.text
    user_id = update.effective_user.id
    
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
        await download_story_handler(update, context, text)
    elif download_type == 'info':
        # إنشاء context.args مؤقت للاستخدام مع info_command
        context.args = [text]
        await info_command(update, context)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة المساعدة"""
    if not await check_subscription(update, context):
        await subscription_required(update, context)
        return
    
    help_text = """
📚 دليل استخدام البوت:

🎯 طريقتان للاستخدام:

1️⃣ الأزرار (سهلة):
• أرسل /start
• اختر نوع المحتوى
• أرسل الرابط أو اسم الأغنية

2️⃣ الأوامر (سريعة):
/video [رابط] - فيديو
/audio [رابط] - موسيقى
/story [رابط قصة Instagram] - قصة Instagram
/info [رابط] - معلومات
/search [اسم الأغنية] - بحث أغنية

🎵 ميزة البحث عن الأغاني:
• اضغط زر "🔍 بحث أغنية"
• أو استخدم: /search Believer
• اختر من النتائج
• احصل على الأغنية مباشرة!

📱 المنصات المدعومة:
Instagram, TikTok, YouTube, Twitter, Facebook, Pinterest, SoundCloud

💡 نصائح:
• اختر النوع الصحيح للحصول على أفضل نتيجة
• للفيديوهات الكبيرة (+50MB)، استخدم /audio
• البحث يعمل مع جميع أغاني YouTube
    """
    
    await update.message.reply_text(help_text)

def main():
    """تشغيل البوت"""
    # طباعة معلومات ffmpeg
    if FFMPEG_PATH:
        logger.info(f"✅ ffmpeg متاح في: {FFMPEG_PATH}")
    else:
        logger.warning("⚠️ ffmpeg غير متاح - سيتم تحميل الصوت بصيغته الأصلية")
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="check_subscription"))
    application.add_handler(CallbackQueryHandler(type_selection_callback, pattern="^type_"))
    application.add_handler(CallbackQueryHandler(download_song_callback, pattern="^download_song_"))
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("image", image_command))
    application.add_handler(CommandHandler("video", video_command))
    application.add_handler(CommandHandler("audio", audio_command))
    application.add_handler(CommandHandler("story", story_command))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("search", search_command))
    
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    
    logger.info("🚀 البوت يعمل الآن...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()