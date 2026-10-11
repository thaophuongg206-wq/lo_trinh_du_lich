import sqlite3

def update_db():
    conn = sqlite3.connect('dulich.db')
    cur = conn.cursor()

    # 1. Update existing TTTM names to clearly show Cinemas
    cur.execute("UPDATE DIA_DIEM SET ten = 'Vincom Center Bà Triệu (Rạp CGV)' WHERE id = 2")
    cur.execute("UPDATE DIA_DIEM SET ten = 'Lotte Center Liễu Giai (Rạp Lotte Cinema)' WHERE id = 1")
    cur.execute("UPDATE DIA_DIEM SET ten = 'Vincom Mega Mall Royal City (Rạp CGV)' WHERE id = 4")
    cur.execute("UPDATE DIA_DIEM SET ten = 'Aeon Mall Long Biên (Rạp CGV)' WHERE id = 5")
    cur.execute("UPDATE DIA_DIEM SET ten = 'Tràng Tiền Plaza (Rạp CGV)' WHERE id = 3")

    # 2. Add Trung tam Chieu phim Quoc Gia if not exists
    cur.execute("SELECT id FROM DIA_DIEM WHERE id = 31 OR ten LIKE '%Chiếu phim Quốc Gia%'")
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO DIA_DIEM (id, ten, vi_do, kinh_do, loai_hinh, diem_gia_tri, thoi_gian_tham_quan_phut, mo_ta, thong_tin_chi_tiet, review, phu_hop, cap_do_tiep_can, url_hinh_anh)
            VALUES (31, 'Trung tâm Chiếu phim Quốc Gia (Rạp chiếu phim)', 21.0167, 105.8155, 'TTTM', 9.2, 150, 
                    '87 Láng Hạ, Chợ Dừa, Ba Đình, Hà Nội',
                    'Rạp chiếu phim quốc gia biểu tượng lớn nhất Hà Nội với hệ thống phòng chiếu hiện đại 2D, 3D, phòng VIP, khu ẩm thực và giải trí phong phú.',
                    'Rạp phim quen thuộc của người Hà Nội, giá vé hợp lý, nhiều suất chiếu đa dạng và không gian sảnh chờ rộng rãi.',
                    'Xem phim, giải trí, hẹn hò, tụ họp bạn bè.',
                    1,
                    'https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=800')
        """)
        cur.execute("INSERT OR REPLACE INTO CUA_SO_THOI_GIAN (id, dia_diem_id, gio_mo_cua, gio_dong_cua, ngay_ap_dung) VALUES (31, 31, '08:00:00', '23:30:00', 'Tất cả')")

    conn.commit()
    conn.close()
    print("Database updated successfully!")

if __name__ == "__main__":
    update_db()
