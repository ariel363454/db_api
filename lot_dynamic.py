import os
import requests
import pymysql
import time
from datetime import datetime
from dateutil import parser

# ==========================================
# 1. 配置區域 (這裡填入所有會變動的設定)
# ==========================================
API_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json" 

DB_CONFIG = {
    "host": os.environ.get('DB_HOST', 'db-tp-sv.mysql.database.azure.com'),
    "user": os.environ.get('DB_USER', 'ariel'),
    "password": os.environ.get('DB_PASSWORD', '@ariel6636'),
    "database": os.environ.get('DB_NAME', 'parking_clean'),
    "charset": "utf8mb4",
    "ssl": {"ssl_mode": "REQUIRED"}
}


UPDATE_INTERVAL = 300 

# ==========================================
# 2. ETL 邏輯函數區域 (Functions)
# ==========================================
def log_to_history(status, note, update_time=None):
    if not update_time:
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = """
            INSERT INTO update_history (
                data_type, source_name, status, note, update_time
            ) VALUES (%s, %s, %s, %s, %s)
            """
            log_data = (
                "停車場動態資料",
                "臺北市政府路外停車快易通",
                status,
                note,
                update_time
            )
            cursor.execute(sql, log_data)
        conn.commit()
    except Exception as e:
        print(f"[{datetime.now()}] 無法寫入歷史紀錄表: {e}")
    finally:
        conn.close()


def fetch_data():
    try:
        response = requests.get(API_URL, timeout=10) 
        if response.status_code == 200:
            return response.json()
        error_msg = f"API抓取失敗，狀態碼：{response.status_code}"
        print(f"[{datetime.now()}] {error_msg}")
        log_to_history(status='failed', note=error_msg)
    except Exception as e:
        error_msg = f"連線 API 發生異常: {str(e)[:100]}"
        print(f"[{datetime.now()}] {error_msg}")
        log_to_history(status='failed', note=error_msg)
    return None


def transform_data(raw_json):
    processed_list = []
    update_time_str = raw_json.get('data', {}).get('UPDATETIME', None)
    parks = raw_json.get('data', {}).get('park', []) 
    try:
        if update_time_str:
            parsed_time = parser.parse(update_time_str)
            update_time = parsed_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"❌ 時間解析依然失敗 ({e})，改用本地當前時間")
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for item in parks:
        lot_id = str(item.get('id', ''))
        if not lot_id.startswith('TPE'):
            continue
        raw_car = int(item.get('availablecar') or 0)
        handicap = int(item.get('Handicap_Available') or 0)
        pregnancy = int(item.get('Pregnancy_Available') or 0)
        if raw_car is None or raw_car < 0:
            continue

        clean_raw_car = max(raw_car, 0)
        ava_handicap = max(handicap, 0)
        ava_pregnancy = max(pregnancy, 0)
        
        ava_car = max(clean_raw_car - ava_handicap - ava_pregnancy, 0)
        row = (
            ava_car,
            ava_handicap,
            ava_pregnancy,
            update_time,
            lot_id,
        )
        processed_list.append(row)
        
    return processed_list, update_time


def update_database(data_list, update_time):
    if not data_list:
        print(f"[{datetime.now()}] 更新失敗，沒有資料可寫入！")
        return
        
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = """
            UPDATE parking_lot_status 
            SET 
                ava_car = %s,
                ava_handicap = %s,
                ava_pregnancy = %s,
                update_time = %s
            WHERE lot_id = %s
            """
            cursor.executemany(sql, data_list)
        conn.commit()
        success_msg = f"成功更新 {len(data_list)} 筆資料"
        print(f"[{datetime.now()}] {success_msg}")
        log_to_history(status="success", note=success_msg, update_time=update_time)     
    except Exception as e:
        conn.rollback()
        error_msg = f"資料庫更新失敗: {str(e)[:100]}"
        print(f"[{datetime.now()}] {error_msg}")
        log_to_history(status="failed", note=error_msg, update_time=update_time)
    finally:
        conn.close()

# ==========================================
# 3. 執行入口 (無窮迴圈常駐排程)
# ==========================================
def run_lot():
    """ 
    這就是被 Django Timer 呼叫的主程式核心！
    它只專心跑完「一輪」ETL 就結束，不再用 sleep 霸佔執行緒。
    """
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n--- [Timer 驅動] 開始執行第一隻停車場更新排程 [{current_time}] ---")
    
    raw_data = fetch_data()
    if raw_data:
        clean_data, update_time = transform_data(raw_data)
        update_database(clean_data, update_time)

if __name__ == "__main__":
    print("🚀 常駐定時排程程式已成功啟動...")
    
    while True:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n--- 開始執行新一輪更新排程 [{current_time}] ---")
        
        raw_data = fetch_data()
        
        if raw_data:
            clean_data, update_time = transform_data(raw_data)
            update_database(clean_data, update_time)
            
        print(f"⏳ 本輪執行完畢。程式將暫停 {UPDATE_INTERVAL} 秒，時間到後自動執行下一輪...")
        
        time.sleep(UPDATE_INTERVAL)