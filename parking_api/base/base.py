# 檔案路徑：parking_api/base/base.py
from django.db.backends.mysql.base import DatabaseWrapper as MysqlDatabaseWrapper
from django.db.backends.mysql.features import DatabaseFeatures as MysqlDatabaseFeatures

class PatchedDatabaseFeatures(MysqlDatabaseFeatures):
    """
    🎯 自訂資料庫特性：徹底封鎖所有會導致 1064 錯誤的 RETURNING 語法
    """
    # 1. 關閉大量寫入的回傳
    can_return_rows_from_bulk_insert = False
    
    # 2. 關閉單筆寫入的回傳列（新版 Django 噴錯的核心主因！）
    can_return_columns_from_insert = False
    
    # 3. 安全起見，連自動產生 ID 的回傳也一併關閉
    can_return_id_from_insert = False

class DatabaseWrapper(MysqlDatabaseWrapper):
    """
    🎯 自訂資料庫後端：換上我們全副武裝的特性檢查員
    """
    features_class = PatchedDatabaseFeatures

    def check_database_version_supported(self):
        # 依舊直接放行 MariaDB 10.6 的版本限制
        return []