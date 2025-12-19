"""
سكريبت لاختبار تحميل الصور من Instagram
شغّله لفحص المشكلة: python test_download.py
"""

import os
import sys

# إنشاء مجلد التحميل
DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

print("=" * 50)
print("🧪 اختبار تحميل الصور - متقدم")
print("=" * 50)

# اختبار 1: instaloader
print("\n1️⃣ اختبار instaloader...")
try:
    import instaloader
    print("✅ instaloader مثبت")
    
    # ضع هنا رابط Instagram حقيقي للاختبار
    test_url = input("\n📝 أدخل رابط Instagram صورة للاختبار:\n(أو اضغط Enter لتخطي): ")
    
    if test_url.strip():
        import re
        import glob
        
        shortcode_match = re.search(r'/p/([A-Za-z0-9_-]+)', test_url)
        if not shortcode_match:
            shortcode_match = re.search(r'/reel/([A-Za-z0-9_-]+)', test_url)
            
        if shortcode_match:
            shortcode = shortcode_match.group(1)
            print(f"📌 Shortcode: {shortcode}")
            
            L = instaloader.Instaloader(
                dirname_pattern=DOWNLOAD_FOLDER,
                filename_pattern='{shortcode}',
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                post_metadata_txt_pattern='',
            )
            
            try:
                print("🔄 جاري تحميل المنشور...")
                post = instaloader.Post.from_shortcode(L.context, shortcode)
                print(f"✅ تم جلب معلومات المنشور")
                print(f"   - فيديو؟ {post.is_video}")
                
                # التحميل
                L.download_post(post, target=DOWNLOAD_FOLDER)
                print("✅ تم التحميل")
                
                # الانتظار قليلاً
                import time
                time.sleep(1)
                
                # فحص الملفات
                pattern = f"{DOWNLOAD_FOLDER}/{shortcode}*"
                files = glob.glob(pattern)
                print(f"\n📁 جميع الملفات: {files}")
                
                image_files = [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')) and not f.endswith('.txt')]
                print(f"📷 ملفات الصور: {image_files}")
                
                # فحص كل ملف صورة
                for img_file in image_files:
                    if os.path.exists(img_file):
                        size = os.path.getsize(img_file)
                        print(f"\n🔍 فحص: {img_file}")
                        print(f"   - الحجم: {size} بايت ({size/1024:.2f} KB)")
                        
                        if size > 0:
                            # قراءة أول 20 بايت
                            with open(img_file, 'rb') as f:
                                first_bytes = f.read(20)
                            print(f"   - أول 20 بايت: {first_bytes}")
                            
                            # فحص نوع الملف
                            if first_bytes[:2] == b'\xff\xd8':
                                print("   - النوع: JPEG ✅")
                            elif first_bytes[:8] == b'\x89PNG\r\n\x1a\n':
                                print("   - النوع: PNG ✅")
                            elif first_bytes[:4] == b'RIFF' and first_bytes[8:12] == b'WEBP':
                                print("   - النوع: WEBP ✅")
                            else:
                                print(f"   - النوع: غير معروف ⚠️")
                        else:
                            print("   - ⚠️ الملف فارغ!")
                
                if not image_files:
                    print("❌ لم يتم العثور على ملفات صور")
                    
            except Exception as e:
                print(f"❌ خطأ في التحميل: {e}")
                import traceback
                print(traceback.format_exc())
        else:
            print("⚠️ لم يتم العثور على shortcode في الرابط")
    else:
        print("⏭️ تم تخطي الاختبار")
        
except ImportError:
    print("❌ instaloader غير مثبت")
    print("   لتثبيته: pip install instaloader")
except Exception as e:
    print(f"❌ خطأ: {e}")

print("\n" + "=" * 50)
print("✅ انتهى الاختبار")
print("=" * 50)

print("\n💡 تعليمات:")
print("1. إذا كانت الملفات فارغة (0 بايت)، المشكلة في instaloader")
print("2. إذا كانت الملفات موجودة ولها حجم، أرسل سجل Terminal للبوت")
print("3. تحقق من مجلد downloads/ وافتح الصور يدوياً")