"""Database access (SQL Server first, bundled SQLite as fallback) + POI loading."""
from api.factors import VEHICLE_ACCESS_LEVEL
import logging
import sqlite3
from datetime import datetime

from config import CFG

try:
    import pyodbc
except ImportError:          # no ODBC driver installed -> SQLite only
    pyodbc = None

log = logging.getLogger("api.db")
SERVER, DATABASE, SQLITE_DB = CFG.db.server, CFG.db.name, CFG.db.sqlite_path

def _note_sqlite_fallback(exc) -> None:
    from core import audit
    audit.record("db", "sqlserver_unavailable", fallback="sqlite", detail=str(exc))


def get_db_connection():
    """
    Thử kết nối SQL Server trước.
    Nếu thất bại (máy bạn bè chưa cài SQL Server), tự động dùng SQLite dulich.db có sẵn trong Git.
    """
    try:
        if not CFG.db.server:
            raise RuntimeError("DB_SERVER chưa được đặt (.env) — dùng SQLite")
        if pyodbc is None:
            raise RuntimeError("pyodbc chưa được cài — dùng SQLite")
        conn = pyodbc.connect(
            f'DRIVER={{{CFG.db.odbc_driver}}};'
            f'SERVER={CFG.db.server};'
            f'DATABASE={CFG.db.name};'
            f'Trusted_Connection=yes;'
            f'TrustServerCertificate=yes;',
            timeout=CFG.db.timeout_s
        )
        return conn, "sqlserver"
    except Exception as exc:
        # never silent: say WHICH database the numbers of this run come from
        _note_sqlite_fallback(exc)
        conn = sqlite3.connect(SQLITE_DB)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def fetch_all_dict(cursor, db_type):
    if db_type == "sqlite":
        return [dict(r) for r in cursor.fetchall()]
    else:
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def fetch_all_points(vehicle_type: str = None):
    """
    Lấy toàn bộ địa điểm từ DB (dùng chung cho /api/optimize-route và /api/ai-suggest,
    tránh lặp lại logic parse giờ mở/đóng cửa ở nhiều nơi).
    Nếu truyền vehicle_type, lọc luôn theo khả năng tiếp cận của phương tiện.
    """
    conn, db_type = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT d.id, d.ten, d.vi_do, d.kinh_do, d.thoi_gian_tham_quan_phut, d.diem_gia_tri, d.loai_hinh,
               c.gio_mo_cua, c.gio_dong_cua,
               d.mo_ta, d.thong_tin_chi_tiet, d.review, d.phu_hop, d.url_hinh_anh,
               COALESCE(d.cap_do_tiep_can, 3) AS cap_do_tiep_can
        FROM DIA_DIEM d
        LEFT JOIN CUA_SO_THOI_GIAN c ON d.id = c.dia_diem_id
    """
    cursor.execute(query)
    rows = fetch_all_dict(cursor, db_type)
    conn.close()

    all_points = []
    default_open = datetime.strptime("00:00", "%H:%M").time()
    default_close = datetime.strptime("23:59", "%H:%M").time()

    for r in rows:
        open_time = r["gio_mo_cua"] if r["gio_mo_cua"] else default_open
        close_time = r["gio_dong_cua"] if r["gio_dong_cua"] else default_close

        if isinstance(open_time, str): open_time = datetime.strptime(open_time[:5], "%H:%M").time()
        if isinstance(close_time, str): close_time = datetime.strptime(close_time[:5], "%H:%M").time()

        point_data = {
            "id": str(r["id"]), "ten": r["ten"], "lat": r["vi_do"], "lon": r["kinh_do"],
            "time": r["thoi_gian_tham_quan_phut"], "score": r["diem_gia_tri"], "loai_hinh": r["loai_hinh"],
            "open_time": open_time, "close_time": close_time,
            "mo_ta": r["mo_ta"] or "",
            "thong_tin_chi_tiet": r["thong_tin_chi_tiet"] or "",
            "review": r["review"] or "",
            "phu_hop": r["phu_hop"] or "",
            "url_hinh_anh": r["url_hinh_anh"] or "",
            "cap_do_tiep_can": r["cap_do_tiep_can"] if r["cap_do_tiep_can"] is not None else 3,
        }
        all_points.append(point_data)

    if vehicle_type:
        max_access = VEHICLE_ACCESS_LEVEL.get(vehicle_type, 3)
        all_points = [p for p in all_points if p["cap_do_tiep_can"] <= max_access]

    return all_points
