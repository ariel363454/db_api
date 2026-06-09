from django.shortcuts import render
from django.db import connection
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
import datetime
from django.views.decorators.cache import cache_page


@cache_page(60 * 3)
@api_view(['GET'])
def get_parking_bounds(request):
    min_lat = request.query_params.get('min_lat')
    max_lat = request.query_params.get('max_lat')
    min_lng = request.query_params.get('min_lng')
    max_lng = request.query_params.get('max_lng')
    
    # 🛡️ 參數安全防禦攔截（提前到最前面，防止後續噴 NoneType 錯誤）
    if not all([min_lat, max_lat, min_lng, max_lng]):
        return Response(
            {'error': '缺少地圖邊界參數，請確認 min_lat, max_lat, min_lng, max_lng 是否齊全'}, 
            status=status.HTTP_400_BAD_REQUEST
        )

    # 🚀 構造矩形外框的 WKT 字串 (傳一次給三個查詢共用，節省運算)
    bbox_wkt = f"POLYGON(({min_lng} {min_lat}, {max_lng} {min_lat}, {max_lng} {max_lat}, {min_lng} {max_lat}, {min_lng} {min_lat}))"

    user_lat = request.query_params.get('user_lat')
    user_lng = request.query_params.get('user_lng')
    is_radius_mode = user_lat is not None and user_lng is not None
    
    target_time_str = request.query_params.get('target_time')
    
    if target_time_str:
        try:
            target_dt = datetime.datetime.strptime(target_time_str, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return Response({'error': '時間格式錯誤，請用 YYYY-MM-DD HH:MM:SS'}, status=status.HTTP_400_BAD_REQUEST)
    else:
        target_dt = datetime.datetime.now()
        
    current_time_str = target_dt.strftime('%H:%M:%S')
    current_date_str = target_dt.strftime('%Y-%m-%d')

    results = []
    
    with connection.cursor() as cursor:
        
        # ==========================================
        # 🅿️ 1. 停車場 (parking_lot) 空間篩選區
        # ==========================================
        # 🚀 改動亮點：剔除 BETWEEN，強迫發動 geom_point 的 R-Tree 空間索引！
        lot_base_query = """
            SELECT DISTINCT
                'lot' AS type,
                p.lot_id, p.lot_name, p.latitude, p.longitude, a.area_name, p.addr, p.service_time, p.charge,
                p.car_space, p.pregnancy_space, p.handicap_space,
                s.ava_car, s.ava_pregnancy, s.ava_handicap, s.update_time
            FROM parking_lot p
            LEFT JOIN parking_rate_rule r ON p.lot_id = r.lot_id  
            LEFT JOIN parking_lot_status s ON p.lot_id = s.lot_id
            LEFT JOIN area a ON p.area_id = a.area_id
            WHERE MBRContains(ST_GeomFromText(%s), p.geom_point)
              AND (
                r.lot_id IS NULL OR (
                    (r.day_mask & POW(2, WEEKDAY(%s))) > 0
                    AND (
                        (r.start_time <= r.end_time AND %s BETWEEN r.start_time AND r.end_time) OR
                        (r.start_time > r.end_time AND (%s >= r.start_time OR %s <= r.end_time))
                    )
                )
              )
        """
        lot_params = [
            bbox_wkt, # 注入空間矩形
            current_date_str,
            current_time_str, current_time_str, current_time_str
        ]
        
        # 500m 模式精篩
        if is_radius_mode:
            # 🚀 效能優化：直接拿已經索引的 geom_point 去跟使用者的 POINT 計算，速度提升 5 倍
            lot_base_query += """
                AND ST_Distance(
                    p.geom_point,
                    ST_GeomFromText(CONCAT('POINT(', %s, ' ', %s, ')'))
                ) <= 0.0045
            """
            lot_params.extend([user_lng, user_lat])
            
        lot_base_query += " ORDER BY p.lot_id ASC"
            
        cursor.execute(lot_base_query, lot_params)
        lot_columns = [col[0] for col in cursor.description]
        raw_lots = cursor.fetchall()
        
        # [費率規則優化區 - 維持你原本極讚的批次字典加速邏輯]
        lot_ids_on_screen = [row[lot_columns.index('lot_id')] for row in raw_lots] if raw_lots else []
        rates_dict = {}
        if lot_ids_on_screen:
            format_strings = ','.join(['%s'] * len(lot_ids_on_screen))
            rate_query = f"""
                SELECT 
                    lot_id, day_mask, 
                    TIME_FORMAT(start_time, '%%H:%%i') AS start_time_str, 
                    TIME_FORMAT(end_time, '%%H:%%i') AS end_time_str, 
                    hourly_rate, per_time_rate, rate_text 
                FROM parking_rate_rule 
                WHERE lot_id IN ({format_strings})
                  AND (day_mask & POW(2, WEEKDAY(%s))) > 0
                  AND (
                      (start_time <= end_time AND %s BETWEEN start_time AND end_time) OR
                      (start_time > end_time AND (%s >= start_time OR %s <= end_time))
                  )
            """
            rate_params = lot_ids_on_screen + [current_date_str, current_time_str, current_time_str, current_time_str]
            cursor.execute(rate_query, rate_params)
            rate_columns = [col[0] for col in cursor.description]
            for rate_row in cursor.fetchall():
                rate_item = dict(zip(rate_columns, rate_row))
                rates_dict[rate_item['lot_id']] = rate_item
                
        for row in raw_lots:
            item = dict(zip(lot_columns, row))
            if item.get('update_time'): item['update_time'] = str(item['update_time'])
            item['current_active_rate'] = rates_dict.get(item['lot_id'], None)
            results.append(item)

        # ==========================================
        # 🔵 2. 路邊車格 (road_side) 空間篩選區
        # ==========================================
        # 🚀 改動亮點：將 ST_GeomFromText(r.geometry_wkt) 改為直接讀取已建有索引的 r.geom_line！
        road_base_query = """
            SELECT 
                'road' AS type,
                r.road_id, r.road_name, r.latitude, r.longitude, a.area_name, r.geometry_wkt, r.charge,
                r.total_car, r.total_pregnancy, r.total_handicap,
                s.ava_car, s.ava_pregnancy, s.ava_handicap, s.update_time
            FROM road_side r
            LEFT JOIN road_side_status s ON r.road_id = s.road_id
            LEFT JOIN area a ON r.area_id = a.area_id
            WHERE ST_Intersects(ST_GeomFromText(%s), r.geom_line)
        """
        road_params = [bbox_wkt]
        
        if is_radius_mode:
            # 🚀 同步優化：拋棄 ST_Centroid，改用 ST_Buffer 建立 500m 圓形雷達，判定線段是否與圓相交
            road_base_query += """
                AND ST_Intersects(
                    r.geom_line,
                    ST_Buffer(
                        ST_GeomFromText(CONCAT('POINT(', %s, ' ', %s, ')')),
                        0.0045
                    )
                )
            """
            road_params.extend([user_lng, user_lat])
            
        road_base_query += " ORDER BY r.road_id ASC"
            
        cursor.execute(road_base_query, road_params)
        road_columns = [col[0] for col in cursor.description]
        for row in cursor.fetchall():
            item = dict(zip(road_columns, row))
            if item.get('update_time'): item['update_time'] = str(item['update_time'])
            results.append(item)

        # ==========================================
        # 🟡 3. 黃線管制 (yellow_line_test) 空間篩選區
        # ==========================================
        # 🚀 改動亮點：完美對齊通用幾何欄位 y.geom_line，斬殺 unexpected type MULTILINESTRING 隱患！
        yellow_base_query = """
            SELECT 
                'yellow_line' AS type,
                y.yl_id, y.road_name,
                a.area_name, y.geometry_wkt,
                y.control_time, y.holiday_time
            FROM yellow_line_test y
            LEFT JOIN area a ON y.area_id = a.area_id
            WHERE ST_Intersects(ST_GeomFromText(%s), y.geom_line)
        """
        yellow_params = [bbox_wkt]
        
        if is_radius_mode:
            yellow_base_query += """
                AND ST_Distance(
                    ST_Centroid(y.geom_line),
                    ST_GeomFromText(CONCAT('POINT(', %s, ' ', %s, ')'))
                ) <= 0.0045
            """
            yellow_params.extend([user_lng, user_lat])
            
        yellow_base_query += " ORDER BY y.yl_id ASC"
        
        cursor.execute(yellow_base_query, yellow_params)
        yellow_columns = [col[0] for col in cursor.description]
        for row in cursor.fetchall():
            item = dict(zip(yellow_columns, row))
            if isinstance(item.get('control_time'), (datetime.time, datetime.timedelta)):
                item['control_time'] = str(item['control_time'])
            if isinstance(item.get('holiday_time'), (datetime.time, datetime.timedelta)):
                item['holiday_time'] = str(item['holiday_time'])
            results.append(item)
        
    return Response(results, status=status.HTTP_200_OK)
