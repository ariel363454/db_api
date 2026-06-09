import os
import sys
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(current_dir))

if project_root not in sys.path:
    sys.path.append(project_root)
from lot_dynamic import run_lot 
from TDX_dynamic import run_road_side

def crawl_job():
    print(f"\n⏰ [{datetime.now()}] ⚙️ 雲端背景 Timer 觸發：開始執行雙軌爬蟲同步任務...")
    try:
        print("📡 正在執行：路外停車場爬蟲 (lot_dynamic)...")
        run_lot()
        print("✅ [Timer] 路外停車場資料更新成功！")
    except Exception as e:
        print(f"❌ [Timer 失敗] 路外停車場任務發生崩潰: {str(e)}")
    try:
        print("📡 正在執行：TDX 路邊停車格爬蟲 (TDX_dynamic)...")
        run_road_side()
        print("✅ [Timer] TDX 路邊停車格資料更新成功！")
    except Exception as e:
        print(f"❌ [Timer 失敗] TDX 路邊停車格任務發生崩潰: {str(e)}")

def start_timer():
    scheduler = BackgroundScheduler()
    
    # ⚡ 獲取當下時間，作為開機立刻執行的觸發訊號
    now = datetime.now()
    
    scheduler.add_job(
        crawl_job, 
        'interval', 
        minutes=5, 
        id='taipei_parking_timer', 
        replace_existing=True,
        next_run_time=now  # 🚀 關鍵核心：強迫 Azure 開機開箱的第一秒，立刻執行一次 crawl_job！
    )
    
    scheduler.start()
    print("🚀 [資工後端防禦] 背景計時器 Timer 已成功通電，且已觸發首發冷啟動任務！")
