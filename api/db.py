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


# Các khóa dữ liệu v2 trả ra ngoài (giao diện / lộ trình dùng). Điểm nào chưa có dữ liệu thì giá trị None / [].
V2_PUBLIC_KEYS = ("rating", "so_luot_danh_gia", "muc_gia", "gia_tu", "gia_den", "gia_ghi_chu",
                  "nhom_chinh", "trong_nha_ngoai_troi", "dia_chi", "trang_thai_du_lieu", "the", "anh",
                  "gio_tuan", "review_insight")

_LEGACY_SQL = """
    SELECT d.id, d.ten, d.vi_do, d.kinh_do, d.thoi_gian_tham_quan_phut, d.diem_gia_tri, d.loai_hinh,
           c.gio_mo_cua, c.gio_dong_cua,
           d.mo_ta, d.thong_tin_chi_tiet, d.review, d.phu_hop, d.url_hinh_anh,
           COALESCE(d.cap_do_tiep_can, 3) AS cap_do_tiep_can
    FROM DIA_DIEM d
    LEFT JOIN CUA_SO_THOI_GIAN c ON d.id = c.dia_diem_id
"""

# DB v2: có CUA_SO_THOI_GIAN.thu (giờ theo thứ) và các cột rating/giá/nhóm... của DIA_DIEM
_V2_SQL = """
    SELECT d.id, d.ten, d.vi_do, d.kinh_do, d.thoi_gian_tham_quan_phut, d.diem_gia_tri, d.loai_hinh,
           c.gio_mo_cua, c.gio_dong_cua, c.thu,
           d.mo_ta, d.thong_tin_chi_tiet, d.review, d.phu_hop, d.url_hinh_anh,
           COALESCE(d.cap_do_tiep_can, 3) AS cap_do_tiep_can,
           d.rating, d.so_luot_danh_gia, d.muc_gia, d.gia_tu, d.gia_den, d.gia_ghi_chu,
           d.nhom_chinh, d.trong_nha_ngoai_troi, d.dia_chi, d.trang_thai_du_lieu
    FROM DIA_DIEM d
    LEFT JOIN CUA_SO_THOI_GIAN c ON d.id = c.dia_diem_id
    ORDER BY d.id
"""

MAX_REVIEWS_PER_POI = 10        # số review đưa vào văn bản mô tả (cho AI / hiển thị)
MAX_REVIEWS_ANALYZED = 60       # số review dùng để phân tích đặc điểm (review_insights)
MAX_IMAGES_PER_POI = 6


def _to_scale5(v):
    """diem_gia_tri chuẩn là thang 5. DB cũ (thang 8-10) hoặc dòng chưa chuẩn hóa: > 5 thì chia 2."""
    if v is None:
        return None
    v = float(v)
    return round(v / 2.0, 1) if v > 5 else v


def _hhmm(t):
    if t is None:
        return None
    return t.strftime("%H:%M") if hasattr(t, "strftime") else str(t)[:5]


def _week_hours(wins):
    """Giờ mở cửa cả tuần: [{thu:1..7, mo:'08:00', dong:'23:00'}] hoặc {thu, nghi:True}. None nếu chưa có dữ liệu giờ."""
    if not wins:
        return None
    every = [w for w in wins if w[0] is None]
    by_day = {int(w[0]): w for w in wins if w[0] is not None}
    out = []
    for d in range(1, 8):
        w = by_day.get(d) or (every[0] if every else None)
        out.append({"thu": d, "mo": _hhmm(w[1]), "dong": _hhmm(w[2])} if w else {"thu": d, "nghi": True})
    return out


def _insight(reviews, pid):
    """Đặc điểm rút từ review (khía cạnh, điều nổi bật, lưu ý, trích dẫn). Lỗi/không có review -> dict rỗng."""
    try:
        from review_insights import analyze_reviews
        return analyze_reviews(reviews, key=str(pid))
    except Exception as exc:
        log.warning("Không phân tích được review của điểm %s: %s", pid, exc)
        return {}


def _run(sql: str):
    conn, db_type = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        return fetch_all_dict(cur, db_type)
    finally:
        conn.close()


def _load_extras() -> dict:
    """Thẻ, ảnh, review của DB v2 -> {"the": {id: [...]}, "anh": {id: [...]}, "review": {id: [...]}}.
    Bảng nào thiếu (DB cũ) thì bỏ qua, không làm hỏng việc đọc địa điểm."""
    out = {"the": {}, "anh": {}, "review": {}}
    try:
        for r in _run("SELECT x.dia_diem_id AS d, t.ma AS ma, t.ten AS ten, t.nhom AS nhom "
                      "FROM DIA_DIEM_THE x JOIN THE t ON t.id = x.the_id ORDER BY x.dia_diem_id, t.id"):
            out["the"].setdefault(r["d"], []).append({"ma": r["ma"], "ten": r["ten"], "nhom": r["nhom"]})
        for r in _run("SELECT dia_diem_id AS d, url FROM DIA_DIEM_ANH "
                      "ORDER BY dia_diem_id, la_anh_bia DESC, thu_tu"):
            lst = out["anh"].setdefault(r["d"], [])
            if r["url"] and len(lst) < MAX_IMAGES_PER_POI:
                lst.append(r["url"])
        for r in _run("SELECT dia_diem_id AS d, noi_dung, so_sao, tuoi_review_thang, ngon_ngu FROM DIA_DIEM_REVIEW "
                      "WHERE dung_cho_ai = 1 ORDER BY dia_diem_id, tuoi_review_thang"):
            lst = out["review"].setdefault(r["d"], [])
            if r["noi_dung"] and len(lst) < MAX_REVIEWS_ANALYZED:
                lst.append({"noi_dung": str(r["noi_dung"]).strip(), "so_sao": r["so_sao"],
                            "tuoi_review_thang": r["tuoi_review_thang"], "ngon_ngu": r["ngon_ngu"]})
    except Exception as exc:
        log.warning("Không đọc được bảng thẻ/ảnh/review của DB v2: %s", exc)
    return out


def fetch_all_points(vehicle_type: str = None, trip_date=None, bo_diem_dong_cua: bool = False):
    """
    Lấy toàn bộ địa điểm từ DB (dùng chung cho /api/optimize-route và /api/ai-suggest,
    tránh lặp lại logic parse giờ mở/đóng cửa ở nhiều nơi).

    vehicle_type     : lọc theo khả năng tiếp cận của phương tiện.
    trip_date        : date | "YYYY-MM-DD" | None.
        * Có ngày đi: chọn khung giờ đúng THỨ của ngày đó (CUA_SO_THOI_GIAN.thu: 1=Thứ Hai ... 7=Chủ nhật,
          NULL = áp dụng mọi ngày). Điểm có dữ liệu giờ nhưng không có khung nào cho thứ đó (nghỉ hôm đó) bị LOẠI.
        * Không có: mỗi điểm vẫn chỉ ra 1 dòng, lấy khung giờ phổ biến nhất.
    bo_diem_dong_cua : True thì bỏ điểm trang_thai_du_lieu = 'da_dong_cua' (quán đã đóng vĩnh viễn).

    Tương thích ngược: nếu DB chưa nâng cấp v2 (thiếu cột thu, rating...) thì chạy câu SELECT cũ (1 dòng/điểm).
    """
    from collections import Counter

    weekday = None
    if trip_date:
        if isinstance(trip_date, str):
            try:
                trip_date = datetime.strptime(trip_date[:10], "%Y-%m-%d").date()
            except Exception:
                trip_date = None
        if trip_date:
            weekday = trip_date.isoweekday()

    try:
        rows, is_v2 = _run(_V2_SQL), True
    except Exception as exc:                 # DB chưa có cột v2 -> câu SELECT cũ
        try:
            from core import audit
            audit.record("db", "schema_v1_legacy_query", fallback="legacy_select", detail=str(exc))
        except Exception:
            pass
        rows, is_v2 = _run(_LEGACY_SQL), False

    extras = _load_extras() if is_v2 else {"the": {}, "anh": {}, "review": {}}

    # Gom nhiều dòng giờ của cùng một địa điểm
    grouped = {}
    for r in rows:
        g = grouped.setdefault(r["id"], {"row": r, "windows": []})
        g["windows"].append((r.get("thu"), r["gio_mo_cua"], r["gio_dong_cua"]))

    all_points = []
    default_open = datetime.strptime("00:00", "%H:%M").time()
    default_close = datetime.strptime("23:59", "%H:%M").time()

    for pid, g in grouped.items():
        r, wins = g["row"], g["windows"]
        if bo_diem_dong_cua and r.get("trang_thai_du_lieu") == "da_dong_cua":
            continue

        wins = [w for w in wins if w[1] or w[2]]          # bỏ dòng LEFT JOIN rỗng
        every_day = [w for w in wins if w[0] is None]
        per_day = [w for w in wins if w[0] is not None]
        if not wins:
            o, c = None, None                              # chưa có dữ liệu giờ -> mở cả ngày
        elif weekday is not None:
            match = [w for w in per_day if int(w[0]) == weekday] or every_day
            if not match:
                continue                                   # hôm đó nghỉ -> loại khỏi tập ứng viên
            o, c = match[0][1], match[0][2]
        else:
            (o, c), _ = Counter((w[1], w[2]) for w in (every_day or per_day)).most_common(1)[0]

        open_time = o if o else default_open
        close_time = c if c else default_close
        if isinstance(open_time, str): open_time = datetime.strptime(open_time[:5], "%H:%M").time()
        if isinstance(close_time, str): close_time = datetime.strptime(close_time[:5], "%H:%M").time()

        point_data = {
            "id": str(r["id"]), "ten": r["ten"], "lat": r["vi_do"], "lon": r["kinh_do"],
            "time": r["thoi_gian_tham_quan_phut"], "score": _to_scale5(r["diem_gia_tri"]), "loai_hinh": r["loai_hinh"],
            "open_time": open_time, "close_time": close_time,
            "mo_ta": r["mo_ta"] or "",
            "thong_tin_chi_tiet": r["thong_tin_chi_tiet"] or "",
            "review": r["review"] or "",
            "phu_hop": r["phu_hop"] or "",
            "url_hinh_anh": r["url_hinh_anh"] or "",
            "cap_do_tiep_can": r["cap_do_tiep_can"] if r["cap_do_tiep_can"] is not None else 3,
        }
        if is_v2:
            # score (diem_gia_tri) và rating đều thang 5 (rating = điểm Google thật)
            point_data.update({
                "rating": r.get("rating"), "so_luot_danh_gia": r.get("so_luot_danh_gia"),
                "muc_gia": r.get("muc_gia"), "gia_tu": r.get("gia_tu"), "gia_den": r.get("gia_den"),
                "gia_ghi_chu": r.get("gia_ghi_chu"), "nhom_chinh": r.get("nhom_chinh"),
                "trong_nha_ngoai_troi": r.get("trong_nha_ngoai_troi"), "dia_chi": r.get("dia_chi"),
                "trang_thai_du_lieu": r.get("trang_thai_du_lieu"),
                "the": extras["the"].get(pid, []), "anh": extras["anh"].get(pid, []),
                "gio_tuan": _week_hours(wins),
            })
            rev_rows = extras["review"].get(pid)
            if rev_rows:      # review thật (đã làm sạch) lấy từ DB v2, thay cho places_output.json
                revs = [x["noi_dung"] for x in rev_rows[:MAX_REVIEWS_PER_POI]]
                text = " ".join(revs)
                base = point_data["review"].strip()
                point_data["serpapi_reviews"] = revs
                point_data["serpapi_rating"] = r.get("rating")
                point_data["serpapi_review_text"] = text
                point_data["review"] = f"{base}. Đánh giá thực tế từ khách hàng: {text}" if base else text
                point_data["review_insight"] = _insight(rev_rows, pid)
                all_points.append(point_data)
                continue
        try:     # chưa có review trong DB -> dự phòng: places_output.json như cũ
            from api.serpapi_cache import enrich_point_with_serpapi
            point_data = enrich_point_with_serpapi(point_data)
        except Exception:
            pass
        if is_v2:      # chưa có review trong DB: phân tích từ review dự phòng (places_output.json) nếu có
            point_data["review_insight"] = _insight(point_data.get("serpapi_reviews") or [], pid)
        all_points.append(point_data)

    if vehicle_type:
        max_access = VEHICLE_ACCESS_LEVEL.get(vehicle_type, 3)
        all_points = [p for p in all_points if p["cap_do_tiep_can"] <= max_access]

    return all_points
