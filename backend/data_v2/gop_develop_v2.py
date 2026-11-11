"""gop_develop_v2.py - Gop DB v2 (Task 2) voi thay doi cua nhanh develop. Chay lai nhieu lan an toan.

Develop (scratch/update_cinemas_db.py) da: (1) doi ten 5 TTTM them "(Rap CGV)" / "(Rap Lotte Cinema)",
(2) them diem id=31 "Trung tam Chieu phim Quoc Gia". Script nay lam dung 2 viec do tren dulich.db v2 va
dien san cac cot v2 cho diem 31 (rating/so luot danh gia de TRONG vi chua crawl duoc -> dien tay bang CSV).

Cuoi cung chuan hoa diem_gia_tri ve THANG 5 (= rating Google; chua co rating thi lay diem cu / 2).

Chay:  python gop_develop_v2.py dulich.db
"""
import sqlite3, sys
from datetime import datetime

DB = sys.argv[1] if len(sys.argv) > 1 else "dulich.db"
NOW = datetime.now().strftime("%Y-%m-%d")
db = sqlite3.connect(DB); cur = db.cursor()

RENAME = {1: "Lotte Center Liễu Giai (Rạp Lotte Cinema)", 2: "Vincom Center Bà Triệu (Rạp CGV)",
          3: "Tràng Tiền Plaza (Rạp CGV)", 4: "Vincom Mega Mall Royal City (Rạp CGV)", 5: "Aeon Mall Long Biên (Rạp CGV)"}
for i, ten in RENAME.items():
    cur.execute("UPDATE DIA_DIEM SET ten=? WHERE id=?", (ten, i))

if not cur.execute("SELECT 1 FROM DIA_DIEM WHERE id=31").fetchone():
    anh = "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=800"
    cur.execute("""INSERT INTO DIA_DIEM (id, ten, vi_do, kinh_do, loai_hinh, diem_gia_tri, thoi_gian_tham_quan_phut,
        mo_ta, thong_tin_chi_tiet, review, phu_hop, cap_do_tiep_can, url_hinh_anh,
        dia_chi, nhom_chinh, trong_nha_ngoai_troi, rating, so_luot_danh_gia, muc_gia, gia_tu, gia_den, gia_ghi_chu,
        thoi_gian_tham_quan_nguon, trang_thai_du_lieu, ghi_chu_kiem_tra, nguon_du_lieu, ngay_cap_nhat)
        VALUES (31, 'Trung tâm Chiếu phim Quốc Gia (Rạp chiếu phim)', 21.0167, 105.8155, 'TTTM', 9.2, 150,
        '87 Láng Hạ, Chợ Dừa, Ba Đình, Hà Nội',
        'Rạp chiếu phim quốc gia biểu tượng lớn nhất Hà Nội với hệ thống phòng chiếu hiện đại 2D, 3D, phòng VIP, khu ẩm thực và giải trí phong phú.',
        'Rạp phim quen thuộc của người Hà Nội, giá vé hợp lý, nhiều suất chiếu đa dạng và không gian sảnh chờ rộng rãi.',
        'Xem phim, giải trí, hẹn hò, tụ họp bạn bè.', 1, ?,
        '87 Láng Hạ, Chợ Dừa, Ba Đình, Hà Nội', 'mua_sam_giai_tri', 'trong_nha', NULL, NULL, 2, 70000, 120000,
        'Uoc luong ve xem phim (Claude), chua xac minh', 'uoc_luong_theo_loai', 'chua_xac_minh',
        'Diem do nhanh develop them (scratch/update_cinemas_db.py). Chua crawl: rating, so danh gia, gio theo thu, toa do can xac minh.',
        'develop (update_cinemas_db.py)', ?)""", (anh, NOW))
    # gio: lay nguyen cua develop (08:00-23:30 moi ngay), danh dau CHUA xac minh
    cur.execute("INSERT INTO CUA_SO_THOI_GIAN (dia_diem_id,gio_mo_cua,gio_dong_cua,ngay_ap_dung,thu,da_xac_minh,nguon) VALUES (31,'08:00:00','23:30:00','Tất cả các ngày',NULL,0,'develop_chua_xac_minh')")
    cur.execute("INSERT INTO DIA_DIEM_ANH (dia_diem_id,url,thu_tu,la_anh_bia,nguon) VALUES (31,?,0,1,'unsplash (develop)')", (anh,))
    for ma in ("mua_sam_giai_tri", "trong_nha", "vui_choi", "hen_ho", "ban_be_nhom"):
        tid = cur.execute("SELECT id FROM THE WHERE ma=?", (ma,)).fetchone()[0]
        cur.execute("INSERT OR IGNORE INTO DIA_DIEM_THE (dia_diem_id,the_id,nguon,da_duyet) VALUES (31,?,'luat_tu_dong',0)", (tid,))
# Chuan hoa THANG 5: diem_gia_tri = rating Google that; diem chua co rating (vd 31) thi diem cu (thang 10) / 2
cur.execute("UPDATE DIA_DIEM SET diem_gia_tri = rating WHERE rating IS NOT NULL")
cur.execute("UPDATE DIA_DIEM SET diem_gia_tri = ROUND(diem_gia_tri / 2.0, 1) WHERE rating IS NULL AND diem_gia_tri > 5")
db.commit()
n = cur.execute("SELECT COUNT(*) FROM DIA_DIEM").fetchone()[0]
print("DIA_DIEM:", n, "dong | CUA_SO:", cur.execute("SELECT COUNT(*) FROM CUA_SO_THOI_GIAN").fetchone()[0],
      "| ANH:", cur.execute("SELECT COUNT(*) FROM DIA_DIEM_ANH").fetchone()[0],
      "| THE lien ket:", cur.execute("SELECT COUNT(*) FROM DIA_DIEM_THE").fetchone()[0])
db.close()
