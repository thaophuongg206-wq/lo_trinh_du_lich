"""kiem_tra_du_lieu.py - Task 4: kiem tra chat luong toan bo du lieu DB v2 (sai / thieu / mau thuan).
Chay:  python data_v2/kiem_tra_du_lieu.py [dulich.db]      (exit 1 neu co loi muc LOI)"""
import sqlite3, sys, math, collections
DB = sys.argv[1] if len(sys.argv) > 1 else "dulich.db"
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
q = lambda s, *a: c.execute(s, a).fetchall()
LOI, CANH_BAO, OK = [], [], 0
def chk(ten, bad, muc="LOI"):
    global OK
    if not bad: OK += 1; print("  OK  ", ten); return
    (LOI if muc == "LOI" else CANH_BAO).append((ten, bad)); print(f"  {muc:<6}{ten}: {str(bad[:6])}{' ...' if len(bad) > 6 else ''}")
D = {r["id"]: r for r in q("SELECT * FROM DIA_DIEM")}
print(f"Kiem tra {DB}: {len(D)} dia diem")
chk("Khong trung ten", [n for n, k in collections.Counter(r["ten"] for r in D.values()).items() if k > 1])
chk("Toa do nam trong Ha Noi (20.9-21.2 / 105.7-105.95)", [i for i, r in D.items() if not (20.9 <= r["vi_do"] <= 21.2 and 105.7 <= r["kinh_do"] <= 105.95)])
chk("Khong trung toa do (< 30 m)", [(a, b) for a in D for b in D if a < b and math.hypot((D[a]["vi_do"]-D[b]["vi_do"])*111000, (D[a]["kinh_do"]-D[b]["kinh_do"])*104000) < 30])
for col in ("dia_chi", "nhom_chinh", "trong_nha_ngoai_troi", "rating", "so_luot_danh_gia", "gia_tu", "gia_den", "muc_gia", "thoi_gian_tham_quan_phut", "url_hinh_anh", "trang_thai_du_lieu"):
    chk(f"Khong thieu {col}", [i for i, r in D.items() if r[col] in (None, "")])
chk("rating trong 1..5 va = diem_gia_tri (thang 5)", [i for i, r in D.items() if not (1 <= (r["rating"] or 0) <= 5) or r["rating"] != r["diem_gia_tri"]])
chk("gia_tu <= gia_den", [i for i, r in D.items() if r["gia_tu"] is not None and r["gia_den"] is not None and r["gia_tu"] > r["gia_den"]])
def muc(r):
    m = ((r["gia_tu"] or 0) + (r["gia_den"] or 0)) / 2
    return 1 if m < 50000 else 2 if m < 150000 else 3 if m < 400000 else 4
chk("muc_gia khop khoang gia (nguong 50K/150K/400K)", [(i, r["muc_gia"], muc(r)) for i, r in D.items() if r["muc_gia"] is not None and r["gia_den"] and muc(r) != r["muc_gia"]], "CANH BAO")
chk("Thoi gian tham quan 10..240 phut", [i for i, r in D.items() if not (10 <= (r["thoi_gian_tham_quan_phut"] or 0) <= 240)])
chk("trong_nha_ngoai_troi hop le", [i for i, r in D.items() if r["trong_nha_ngoai_troi"] not in ("trong_nha", "ngoai_troi", "ca_hai")])
W = collections.defaultdict(list)
for r in q("SELECT * FROM CUA_SO_THOI_GIAN"): W[r["dia_diem_id"]].append(r)
chk("Moi diem co gio mo cua", [i for i in D if i not in W])
chk("gio mo < gio dong", [(r["dia_diem_id"], r["gio_mo_cua"], r["gio_dong_cua"]) for rs in W.values() for r in rs if r["gio_mo_cua"] and r["gio_dong_cua"] and r["gio_mo_cua"] >= r["gio_dong_cua"]])
chk("thu hop le (NULL hoac 1..7), khong lap thu, khong tron NULL voi thu", [i for i, rs in W.items() if any(r["thu"] is not None and not 1 <= r["thu"] <= 7 for r in rs) or len({r["thu"] for r in rs}) != len(rs) or (len(rs) > 1 and any(r["thu"] is None for r in rs))])
chk("Gio mo cua da xac minh", [r["dia_diem_id"] for rs in W.values() for r in rs if not r["da_xac_minh"]], "CANH BAO")
T = collections.defaultdict(lambda: collections.defaultdict(set))
for r in q("SELECT x.dia_diem_id d, t.ma, t.nhom FROM DIA_DIEM_THE x JOIN THE t ON t.id = x.the_id"): T[r["d"]][r["nhom"]].add(r["ma"])
chk("Moi diem co >= 1 the hoat dong", [i for i in D if not T[i]["hoat_dong"]])
chk("Moi diem co the loai va the khong gian", [i for i in D if not T[i]["loai"] or not T[i]["khong_gian"]])
chk("The khong gian khop cot trong_nha_ngoai_troi", [i for i, r in D.items() if r["trong_nha_ngoai_troi"] != "ca_hai" and (("ngoai_troi" in T[i]["khong_gian"]) != (r["trong_nha_ngoai_troi"] == "ngoai_troi"))], "CANH BAO")
chk("Khong vua 'yen_tinh' vua the loai mua sam - giai tri", [i for i in D if "yen_tinh" in T[i]["phong_cach"] and "mua_sam_giai_tri" in T[i]["loai"]], "CANH BAO")
chk("Moi the da duoc duyet", [(r["dia_diem_id"], r["the_id"]) for r in q("SELECT * FROM DIA_DIEM_THE WHERE da_duyet = 0")], "CANH BAO")
A = collections.Counter(r["dia_diem_id"] for r in q("SELECT dia_diem_id FROM DIA_DIEM_ANH"))
chk("Moi diem co >= 1 anh va dung 1 anh bia", [i for i in D if A[i] < 1 or len(q("SELECT 1 FROM DIA_DIEM_ANH WHERE dia_diem_id=? AND la_anh_bia=1", i)) != 1])
chk("Co >= 2 anh de hien thu vien", [i for i in D if A[i] < 2], "CANH BAO")
R = collections.Counter(r["dia_diem_id"] for r in q("SELECT dia_diem_id FROM DIA_DIEM_REVIEW WHERE dung_cho_ai=1"))
chk("Moi diem co >= 3 review dung cho AI", [i for i in D if R[i] < 3], "CANH BAO")
chk("Review dung_cho_ai khong qua ngan (< 20 ky tu)", [r["id"] for r in q("SELECT id FROM DIA_DIEM_REVIEW WHERE dung_cho_ai=1 AND length(noi_dung) < 20")])
chk("so_sao review trong 1..5", [r["id"] for r in q("SELECT id FROM DIA_DIEM_REVIEW WHERE so_sao IS NOT NULL AND (so_sao<1 OR so_sao>5)")])
chk("Diem da dong cua (giu lam diem mau)", [i for i, r in D.items() if r["trang_thai_du_lieu"] == "da_dong_cua"], "CANH BAO")
chk("Khong con diem nghi_van / chua_xac_minh", [i for i, r in D.items() if r["trang_thai_du_lieu"] in ("nghi_van", "chua_xac_minh")])
print(f"\nKET QUA: {OK} OK, {len(CANH_BAO)} canh bao, {len(LOI)} loi")
sys.exit(1 if LOI else 0)
