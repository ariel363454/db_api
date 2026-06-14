import os
import requests
import json
import pymysql
import time
from datetime import datetime

# ==========================================
# 1. 配置區域 (Configuration)
# ==========================================
CLIENT_ID = "b1329023-4b484569-b70c-4511"
CLIENT_SECRET = "fffcfb8e-7380-47af-9963-b99ac5b4ef9e"

DB_CONFIG = {
    "host": os.environ.get('DB_HOST', 'db-tp-sv.mysql.database.azure.com'),
    "user": os.environ.get('DB_USER', 'ariel'),
    "password": os.environ.get('DB_PASSWORD', '@ariel6636'),
    "database": os.environ.get('DB_NAME', 'parking_clean'),
    "charset": "utf8mb4",
    "ssl": {"ssl_mode": "REQUIRED"}
}

TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
URL_DYNAMIC = "https://tdx.transportdata.tw/api/basic/v1/Parking/OnStreet/ParkingSegmentAvailability/City/Taipei?%24format=JSON"

UPDATE_INTERVAL = 300

# ==========================================
# 2. ETL 邏輯函數區域 (Functions)
# ==========================================

def get_token():
    payload = {'grant_type': 'client_credentials', 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET}
    try:
        res = requests.post(TOKEN_URL, data=payload, headers={'content-type': 'application/x-www-form-urlencoded'}, timeout=10)
        return res.json().get('access_token') if res.status_code == 200 else None
    except Exception as e:
        print(f"[{datetime.now()}] Token 取得異常: {e}")
        return None


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
                "路邊停車動態資料",
                "交通部TDX平臺",
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


# 📥 [Extract] 抓取 TDX API 資料
def fetch_data():
    token = get_token()
    if not token:
        error_msg = "無法取得有效 Token，取消本輪抓取"
        print(f"[{datetime.now()}] {error_msg}")
        log_to_history(status='failed', note=error_msg)
        return None

    headers = {'Authorization': f'Bearer {token}', 'Accept-Encoding': 'gzip'}
    try:
        res = requests.get(URL_DYNAMIC, headers=headers, timeout=20)
        if res.status_code == 200:
            return res.json()
        error_msg = f"API 抓取失敗，狀態碼：{res.status_code}"
        print(f"[{datetime.now()}] {error_msg}")
        log_to_history(status='failed', note=error_msg)
    except Exception as e:
        error_msg = f"連線 TDX API 發生異常: {str(e)[:100]}"
        print(f"[{datetime.now()}] {error_msg}")
        log_to_history(status='failed', note=error_msg)
    return None


def transform_data(raw_json):
    processed_list = []
    
    src_time_str = raw_json.get('SrcUpdateTime')
    if src_time_str:
        update_time = src_time_str.replace('T', ' ').split('+')[0]
    else:
        update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    availabilities = raw_json.get('CurbParkingSegmentAvailabilities', [])

    for seg in availabilities:
        road_id = seg.get('ParkingSegmentID')
        if not road_id:
            continue
        ava_car = 0
        ava_handicap = 0
        ava_pregnancy = 0

        for ava in seg.get('Availabilities', []):
            stype = str(ava.get('SpaceType'))
            raw_val = ava.get('AvailableSpaces', -1)

            count = 0 if raw_val < 0 else raw_val

            if stype == '1':
                ava_car = count
            elif stype == '2':
                ava_handicap = count
            elif stype == '3':
                ava_pregnancy = count

        row = (
            ava_car,
            ava_handicap,
            ava_pregnancy,
            update_time,
            road_id
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
            UPDATE road_side_status
            SET 
                ava_car = %s,
                ava_handicap = %s,
                ava_pregnancy = %s,
                update_time = %s
            WHERE road_id = %s
            """
            cursor.executemany(sql, data_list)
        conn.commit()
        success_msg = f"成功更新 {len(data_list)} 筆路段即時資料"
        print(f"[{datetime.now()}] {success_msg}")
        log_to_history(status="success", note=success_msg, update_time=update_time)     
    except Exception as e:
        conn.rollback()
        error_msg = f"資料庫動態更新失敗: {str(e)[:100]}"
        print(f"[{datetime.now()}] {error_msg}")
        log_to_history(status="failed", note=error_msg, update_time=update_time)
    finally:
        conn.close()

# ==========================================
# 3. 執行入口 (無窮迴圈常駐排程)
# ==========================================
def run_road_side():
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
    print("🚀 路邊停車動態定時排程程式已成功啟動...")
    
    while True:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n--- 開始執行新一輪路邊停車更新排程 [{current_time}] ---")
        
        raw_data = fetch_data()
        
        if raw_data:
            clean_data, update_time = transform_data(raw_data)
            update_database(clean_data, update_time)
            
        print(f"⏳ 本輪執行完畢。程式將暫停 {UPDATE_INTERVAL} 秒，時間到後自動執行下一輪...")
        
        time.sleep(UPDATE_INTERVAL)