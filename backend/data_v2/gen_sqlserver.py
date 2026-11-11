"""Sinh migration_v2_sqlserver.sql + DuLichThongMinh_v2_FULL_co_du_lieu.sql tu dulich.db (hoac dulich_v2.db) (schema + du lieu) de chay tren SQL Server DuLichThongMinh."""
import sqlite3
import os, sys
# CSDL nguon: tham so thu 2 neu co; neu khong thi tu tim dulich.db (trong repo) roi dulich_v2.db
DB_PATH = sys.argv[2] if len(sys.argv) > 2 else next((p for p in ("dulich.db", "dulich_v2.db") if os.path.exists(p) and os.path.getsize(p) > 0), None)
if not DB_PATH or not os.path.exists(DB_PATH):
    sys.exit("Khong thay dulich.db / dulich_v2.db trong thu muc hien tai. Chay: python gen_sqlserver.py DuLichThongMinh.sql duong_dan_toi_file.db")
print("Doc du lieu tu:", DB_PATH)
db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True); db.row_factory = sqlite3.Row
def q(v):
    if v is None: return "NULL"
    if isinstance(v, (int, float)): return repr(v)
    return "N'" + str(v).replace("'", "''") + "'"
o = []
o.append("""-- ============================================================
-- MIGRATION V2 (Thanh - Data/Database) cho SQL Server
-- Chay SAU DuLichThongMinh.sql. KHONG xoa cot/bang cu -> main.py van chay.
-- Chay lai nhieu lan an toan (IF COL_LENGTH / IF OBJECT_ID).
-- ============================================================
USE DuLichThongMinh;
GO
""")
cols = [("dia_chi","NVARCHAR(500)"),("nhom_chinh","NVARCHAR(30)"),("trong_nha_ngoai_troi","NVARCHAR(20)"),
 ("rating","FLOAT"),("so_luot_danh_gia","INT"),("muc_gia","INT"),("gia_tu","INT"),("gia_den","INT"),("gia_ghi_chu","NVARCHAR(255)"),
 ("google_data_id","NVARCHAR(100)"),("sdt","VARCHAR(30)"),("website","NVARCHAR(500)"),
 ("thoi_gian_tham_quan_nguon","NVARCHAR(50) DEFAULT N'uoc_luong_chua_xac_minh'"),
 ("trang_thai_du_lieu","NVARCHAR(30) DEFAULT N'chua_xac_minh'"),("ghi_chu_kiem_tra","NVARCHAR(MAX)"),
 ("nguon_du_lieu","NVARCHAR(100)"),("ngay_cap_nhat","DATE")]
for n, t in cols: o.append(f"IF COL_LENGTH('DIA_DIEM','{n}') IS NULL ALTER TABLE DIA_DIEM ADD {n} {t};")
for n, t in [("thu","INT"),("da_xac_minh","BIT DEFAULT 0"),("nguon","NVARCHAR(50)")]:
    o.append(f"IF COL_LENGTH('CUA_SO_THOI_GIAN','{n}') IS NULL ALTER TABLE CUA_SO_THOI_GIAN ADD {n} {t};")
o.append("GO\n")
o.append("""IF OBJECT_ID('DIA_DIEM_ANH') IS NULL CREATE TABLE DIA_DIEM_ANH (
    id INT IDENTITY(1,1) PRIMARY KEY, dia_diem_id INT NOT NULL, url NVARCHAR(MAX) NOT NULL,
    thu_tu INT DEFAULT 0, la_anh_bia BIT DEFAULT 0, nguon NVARCHAR(50),
    CONSTRAINT FK_Anh_DiaDiem FOREIGN KEY (dia_diem_id) REFERENCES DIA_DIEM(id));
IF OBJECT_ID('DIA_DIEM_REVIEW') IS NULL CREATE TABLE DIA_DIEM_REVIEW (
    id INT IDENTITY(1,1) PRIMARY KEY, dia_diem_id INT NOT NULL, noi_dung NVARCHAR(MAX) NOT NULL, so_sao FLOAT,
    nguoi_viet NVARCHAR(150), thoi_gian_goc NVARCHAR(50), tuoi_review_thang FLOAT, ngon_ngu NVARCHAR(10),
    so_ky_tu INT, dung_cho_ai BIT DEFAULT 0, nguon NVARCHAR(50),
    CONSTRAINT FK_Review_DiaDiem FOREIGN KEY (dia_diem_id) REFERENCES DIA_DIEM(id));
IF OBJECT_ID('THE') IS NULL CREATE TABLE THE (
    id INT IDENTITY(1,1) PRIMARY KEY, ma VARCHAR(40) NOT NULL UNIQUE, ten NVARCHAR(100) NOT NULL, nhom NVARCHAR(30) NOT NULL);
IF OBJECT_ID('DIA_DIEM_THE') IS NULL CREATE TABLE DIA_DIEM_THE (
    dia_diem_id INT NOT NULL, the_id INT NOT NULL, nguon NVARCHAR(30) DEFAULT N'luat_tu_dong', da_duyet BIT DEFAULT 0,
    CONSTRAINT PK_DiaDiemThe PRIMARY KEY (dia_diem_id, the_id),
    CONSTRAINT FK_DDThe_DD FOREIGN KEY (dia_diem_id) REFERENCES DIA_DIEM(id),
    CONSTRAINT FK_DDThe_The FOREIGN KEY (the_id) REFERENCES THE(id));
GO
-- Nap lai du lieu moi (xoa du lieu bang phu truoc de chay lai duoc)
DELETE FROM DIA_DIEM_THE; DELETE FROM THE; DELETE FROM DIA_DIEM_REVIEW; DELETE FROM DIA_DIEM_ANH;
GO
""")
d_cols = ["diem_gia_tri","vi_do","kinh_do","url_hinh_anh","dia_chi","nhom_chinh","trong_nha_ngoai_troi","rating","so_luot_danh_gia","muc_gia","gia_tu","gia_den","gia_ghi_chu","google_data_id","sdt","website","thoi_gian_tham_quan_phut","thoi_gian_tham_quan_nguon","trang_thai_du_lieu","ghi_chu_kiem_tra","nguon_du_lieu","ngay_cap_nhat"]
for r in db.execute("SELECT * FROM DIA_DIEM ORDER BY id"):
    o.append(f"UPDATE DIA_DIEM SET " + ", ".join(f"{c}={q(r[c])}" for c in d_cols) + f" WHERE id={r['id']};")
o.append("DELETE FROM CUA_SO_THOI_GIAN;")
for r in db.execute("SELECT * FROM CUA_SO_THOI_GIAN ORDER BY dia_diem_id, COALESCE(thu,0)"):
    o.append(f"INSERT INTO CUA_SO_THOI_GIAN (dia_diem_id,gio_mo_cua,gio_dong_cua,ngay_ap_dung,thu,da_xac_minh,nguon) VALUES ({r['dia_diem_id']},{q(r['gio_mo_cua'])},{q(r['gio_dong_cua'])},{q(r['ngay_ap_dung'])},{q(r['thu'])},{r['da_xac_minh'] or 0},{q(r['nguon'])});")
o.append("GO")
o.append("SET IDENTITY_INSERT THE ON;")
for r in db.execute("SELECT * FROM THE ORDER BY id"): o.append(f"INSERT INTO THE (id,ma,ten,nhom) VALUES ({r['id']},{q(r['ma'])},{q(r['ten'])},{q(r['nhom'])});")
o.append("SET IDENTITY_INSERT THE OFF;\nGO")
for r in db.execute("SELECT * FROM DIA_DIEM_THE"): o.append(f"INSERT INTO DIA_DIEM_THE (dia_diem_id,the_id,nguon,da_duyet) VALUES ({r['dia_diem_id']},{r['the_id']},{q(r['nguon'])},{r['da_duyet']});")
o.append("GO")
for r in db.execute("SELECT * FROM DIA_DIEM_ANH ORDER BY id"): o.append(f"INSERT INTO DIA_DIEM_ANH (dia_diem_id,url,thu_tu,la_anh_bia,nguon) VALUES ({r['dia_diem_id']},{q(r['url'])},{r['thu_tu']},{r['la_anh_bia']},{q(r['nguon'])});")
o.append("GO")
for r in db.execute("SELECT * FROM DIA_DIEM_REVIEW ORDER BY id"):
    o.append("INSERT INTO DIA_DIEM_REVIEW (dia_diem_id,noi_dung,so_sao,nguoi_viet,thoi_gian_goc,tuoi_review_thang,ngon_ngu,so_ky_tu,dung_cho_ai,nguon) VALUES (" +
             ",".join(q(r[c]) for c in ["dia_diem_id","noi_dung","so_sao","nguoi_viet","thoi_gian_goc","tuoi_review_thang","ngon_ngu","so_ky_tu","dung_cho_ai","nguon"]) + ");")
o.append("GO\nPRINT N'Migration V2 hoan tat';")
open("migration_v2_sqlserver.sql", "w", encoding="utf-8-sig").write("\n".join(o) + "\n")
print(len(o), "dong")

orig = sys.argv[1] if len(sys.argv) > 1 else "DuLichThongMinh.sql"
if os.path.exists(orig):
    base = open(orig, encoding="utf-8", errors="replace").read().replace("\r", "")
    mig = open("migration_v2_sqlserver.sql", encoding="utf-8-sig").read()
    open("DuLichThongMinh_v2_FULL_co_du_lieu.sql", "w", encoding="utf-8-sig").write(base + "\nGO\n\n" + mig)
    print("Da tao DuLichThongMinh_v2_FULL_co_du_lieu.sql")
else:
    print("Khong thay", orig, "-> chi tao migration_v2_sqlserver.sql (de tao ban FULL: python gen_sqlserver.py duong_dan/DuLichThongMinh.sql)")
