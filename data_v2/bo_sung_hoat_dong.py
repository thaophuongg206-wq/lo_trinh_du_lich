"""bo_sung_hoat_dong.py - Task 4: bo sung the HOAT DONG con thieu (moi diem >= 1 hoat dong). Chay lai nhieu lan an toan.
Chay:  python data_v2/bo_sung_hoat_dong.py dulich.db
The do luat gan (nguon='luat_tu_dong'), da_duyet = 0 -> nhom xem lai roi duyet."""
import sqlite3, sys
db = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else "dulich.db"); cur = db.cursor()
cur.execute("INSERT OR IGNORE INTO THE (ma, ten, nhom) VALUES ('tham_quan', 'Tham quan - tìm hiểu', 'hoat_dong')")
ADD = {9: ["an_trua", "an_toi"], 19: ["tro_chuyen", "chup_anh"], 23: ["tro_chuyen", "lam_viec"]}
# moi diem thuoc nhom van hoa -> hoat dong "tham quan"
for (i,) in cur.execute("SELECT x.dia_diem_id FROM DIA_DIEM_THE x JOIN THE t ON t.id=x.the_id WHERE t.ma='van_hoa'").fetchall():
    ADD.setdefault(i, []).append("tham_quan")
n = 0
for pid, tags in ADD.items():
    for ma in tags:
        tid = cur.execute("SELECT id FROM THE WHERE ma=?", (ma,)).fetchone()[0]
        n += cur.execute("INSERT OR IGNORE INTO DIA_DIEM_THE (dia_diem_id, the_id, nguon, da_duyet) VALUES (?,?,'luat_tu_dong',0)", (pid, tid)).rowcount
db.commit()
print("Da them", n, "lien ket the hoat dong | THE:", cur.execute("SELECT COUNT(*) FROM THE").fetchone()[0], "| DIA_DIEM_THE:", cur.execute("SELECT COUNT(*) FROM DIA_DIEM_THE").fetchone()[0])
