import os
from django.apps import AppConfig

class ParkingApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'parking_api'

    # 🚀 展前必備防禦：利用 Django 的 ready() 生命週期，在專案啟動時順便叫醒爬蟲定時器！
    def ready(self):
        # 🛡️ 資工核心防禦：
        # Django 在本地開發模式下會啟動兩個進程（Autoreloader 一直在偵測程式碼有沒有改），
        # 為了防止爬蟲計時器在背景同時被啟動兩次、導致兩隻爬蟲在資料庫打架，
        # 我們用環境變數 RUN_MAIN 或雲端的 AZURE_DEPLOYMENT 來確保它在全宇宙只會精確啟動一次！
        if os.environ.get('RUN_MAIN') == 'true' or os.environ.get('AZURE_DEPLOYMENT') == 'true':
            from .updater import start_timer
            start_timer()
