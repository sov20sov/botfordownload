#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
اختبار نظام متابعة الاستخدامات - Usage Tracking System Test

هذا الملف يختبر جميع وظائف نظام متابعة الاستخدامات
بدون الحاجة لتشغيل البوت الكامل
"""

import json
import os
from datetime import datetime
import tempfile
import shutil

# قائمة بنتائج الاختبارات
test_results = []

class BotStatsTest:
    """فئة للاختبار - نسخة مبسطة من BotStats"""
    
    def __init__(self, stats_file):
        self.stats_file = stats_file
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
            return True
        except Exception as e:
            print(f"❌ خطأ في الحفظ: {e}")
            return False
    
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
                'usage_count': 0
            }
        
        self.data['users'][user_id_str]['last_seen'] = now
        self.save_stats()
    
    def add_usage(self, user_id):
        """تسجيل استخدام للمستخدم"""
        user_id_str = str(user_id)
        now = datetime.now().isoformat()
        
        if user_id_str in self.data['users']:
            self.data['users'][user_id_str]['usage_count'] += 1
            self.data['users'][user_id_str]['last_seen'] = now
        else:
            self.data['users'][user_id_str] = {
                'name': 'Unknown',
                'username': 'unknown',
                'first_seen': now,
                'last_seen': now,
                'usage_count': 1
            }
        
        self.save_stats()
    
    def add_download(self, download_type):
        """تسجيل تحميل"""
        self.data['total_downloads'] += 1
        if download_type in self.data['downloads_by_type']:
            self.data['downloads_by_type'][download_type] += 1
        self.save_stats()


def test_1_create_new_user():
    """الاختبار 1: إنشاء مستخدم جديد"""
    print("\n🧪 الاختبار 1: إنشاء مستخدم جديد")
    print("-" * 50)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = os.path.join(tmpdir, 'test_stats.json')
        stats = BotStatsTest(stats_file)
        
        # إضافة مستخدم
        stats.add_user(123456789, "أحمد محمد", "ahmad_user")
        
        # التحقق
        assert stats.data['total_users'] == 1, "يجب أن يكون هناك مستخدم واحد"
        assert '123456789' in stats.data['users'], "يجب تسجيل المستخدم"
        assert stats.data['users']['123456789']['usage_count'] == 0, "الاستخدام يجب أن يكون 0"
        
        print("✅ تم إنشاء المستخدم بنجاح")
        print(f"   - المستخدم: {stats.data['users']['123456789']['name']}")
        print(f"   - الاستخدامات: {stats.data['users']['123456789']['usage_count']}")
        test_results.append(("✅ الاختبار 1: إنشاء مستخدم جديد", True))


def test_2_add_usage():
    """الاختبار 2: إضافة استخدام"""
    print("\n🧪 الاختبار 2: إضافة استخدام")
    print("-" * 50)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = os.path.join(tmpdir, 'test_stats.json')
        stats = BotStatsTest(stats_file)
        
        # إضافة مستخدم
        stats.add_user(123456789, "أحمد محمد", "ahmad_user")
        
        # إضافة استخدامات
        stats.add_usage(123456789)
        stats.add_usage(123456789)
        stats.add_usage(123456789)
        
        # التحقق
        assert stats.data['users']['123456789']['usage_count'] == 3, "يجب أن تكون 3 استخدامات"
        
        print("✅ تم إضافة الاستخدامات بنجاح")
        print(f"   - عدد الاستخدامات: {stats.data['users']['123456789']['usage_count']}")
        test_results.append(("✅ الاختبار 2: إضافة استخدام", True))


def test_3_add_downloads():
    """الاختبار 3: تسجيل التحميلات"""
    print("\n🧪 الاختبار 3: تسجيل التحميلات")
    print("-" * 50)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = os.path.join(tmpdir, 'test_stats.json')
        stats = BotStatsTest(stats_file)
        
        # تسجيل تحميلات
        stats.add_download('video')
        stats.add_download('video')
        stats.add_download('audio')
        stats.add_download('audio')
        stats.add_download('audio')
        stats.add_download('image')
        
        # التحقق
        assert stats.data['total_downloads'] == 6, "يجب أن تكون 6 تحميلات"
        assert stats.data['downloads_by_type']['video'] == 2, "يجب أن تكون فيديوهات 2"
        assert stats.data['downloads_by_type']['audio'] == 3, "يجب أن تكون موسيقى 3"
        assert stats.data['downloads_by_type']['image'] == 1, "يجب أن تكون صور 1"
        
        print("✅ تم تسجيل التحميلات بنجاح")
        print(f"   - إجمالي التحميلات: {stats.data['total_downloads']}")
        print(f"   - الفيديوهات: {stats.data['downloads_by_type']['video']}")
        print(f"   - الموسيقى: {stats.data['downloads_by_type']['audio']}")
        print(f"   - الصور: {stats.data['downloads_by_type']['image']}")
        test_results.append(("✅ الاختبار 3: تسجيل التحميلات", True))


def test_4_multiple_users():
    """الاختبار 4: مستخدمون متعددون"""
    print("\n🧪 الاختبار 4: مستخدمون متعددون")
    print("-" * 50)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = os.path.join(tmpdir, 'test_stats.json')
        stats = BotStatsTest(stats_file)
        
        # إضافة عدة مستخدمين
        stats.add_user(111111111, "أحمد محمد", "ahmad")
        stats.add_user(222222222, "محمد علي", "ali")
        stats.add_user(333333333, "فاطمة أحمد", "fatima")
        
        # إضافة استخدامات
        for i in range(5):
            stats.add_usage(111111111)
        for i in range(3):
            stats.add_usage(222222222)
        for i in range(2):
            stats.add_usage(333333333)
        
        # التحقق
        assert stats.data['total_users'] == 3, "يجب أن يكون 3 مستخدمين"
        assert stats.data['users']['111111111']['usage_count'] == 5, "المستخدم الأول: 5 استخدامات"
        assert stats.data['users']['222222222']['usage_count'] == 3, "المستخدم الثاني: 3 استخدامات"
        assert stats.data['users']['333333333']['usage_count'] == 2, "المستخدم الثالث: 2 استخدامات"
        
        print("✅ تم إضافة مستخدمين متعددين بنجاح")
        print(f"   - إجمالي المستخدمين: {stats.data['total_users']}")
        for user_id, user_data in stats.data['users'].items():
            print(f"   - {user_data['name']}: {user_data['usage_count']} استخدامات")
        test_results.append(("✅ الاختبار 4: مستخدمون متعددون", True))


def test_5_file_persistence():
    """الاختبار 5: استمرارية البيانات"""
    print("\n🧪 الاختبار 5: استمرارية البيانات في الملف")
    print("-" * 50)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = os.path.join(tmpdir, 'test_stats.json')
        
        # الكتابة
        stats = BotStatsTest(stats_file)
        stats.add_user(123456789, "أحمد محمد", "ahmad_user")
        stats.add_usage(123456789)
        stats.add_download('video')
        
        # قراءة مجددة
        stats2 = BotStatsTest(stats_file)
        
        # التحقق
        assert stats2.data['total_users'] == 1, "يجب أن تكون البيانات محفوظة"
        assert stats2.data['users']['123456789']['usage_count'] == 1
        assert stats2.data['total_downloads'] == 1
        
        print("✅ تم الحفظ والاسترجاع بنجاح")
        print(f"   - المستخدمون المحفوظة: {stats2.data['total_users']}")
        print(f"   - التحميلات المحفوظة: {stats2.data['total_downloads']}")
        test_results.append(("✅ الاختبار 5: استمرارية البيانات", True))


def test_6_json_format():
    """الاختبار 6: صيغة JSON الصحيحة"""
    print("\n🧪 الاختبار 6: صيغة JSON الصحيحة")
    print("-" * 50)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        stats_file = os.path.join(tmpdir, 'test_stats.json')
        stats = BotStatsTest(stats_file)
        stats.add_user(123456789, "أحمد محمد", "ahmad_user")
        
        # فحص الملف
        with open(stats_file, 'r', encoding='utf-8') as f:
            data = json.load(f)  # سيرمي استثناء إذا لم تكن الصيغة صحيحة
        
        print("✅ صيغة JSON صحيحة")
        print(f"   - الحقول: {list(data.keys())}")
        test_results.append(("✅ الاختبار 6: صيغة JSON", True))


def print_summary():
    """طباعة ملخص النتائج"""
    print("\n" + "=" * 50)
    print("📊 ملخص النتائج")
    print("=" * 50)
    
    success_count = sum(1 for _, result in test_results if result)
    total_count = len(test_results)
    
    for test_name, result in test_results:
        print(test_name)
    
    print("\n" + "-" * 50)
    print(f"✅ النجاح: {success_count}/{total_count}")
    
    if success_count == total_count:
        print("\n🎉 جميع الاختبارات نجحت!")
    else:
        print(f"\n⚠️ هناك {total_count - success_count} اختبار فشل")


def main():
    """الدالة الرئيسية"""
    print("=" * 50)
    print("🧪 اختبار نظام متابعة الاستخدامات")
    print("=" * 50)
    
    try:
        test_1_create_new_user()
        test_2_add_usage()
        test_3_add_downloads()
        test_4_multiple_users()
        test_5_file_persistence()
        test_6_json_format()
        
        print_summary()
        
    except AssertionError as e:
        print(f"\n❌ فشل الاختبار: {e}")
        test_results.append((f"❌ اختبار فشل: {e}", False))
        print_summary()
    except Exception as e:
        print(f"\n❌ خطأ: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
