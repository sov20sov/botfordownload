import os
from bot import downloader

# Change the URL below to one that fails for you if needed
TEST_URL = os.environ.get('TEST_URL', 'https://www.youtube.com/watch?v=dQw4w9WgXcQ')

print('Testing get_info for:', TEST_URL)
try:
    info = downloader.get_info(TEST_URL)
    print('Success: title=', info.get('title'))
except Exception as e:
    import traceback
    print('Error:', e)
    traceback.print_exc()
