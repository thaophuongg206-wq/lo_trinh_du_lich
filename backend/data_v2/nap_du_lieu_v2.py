"""
nap_du_lieu_v2.py - Nap GIO MO CUA, GIA, SDT/WEBSITE vao dulich_v2.db (Task 2 - Thanh)

Chay:  python nap_du_lieu_v2.py dulich_v2.db [places_output_v2.json] [can_xac_minh_thu_cong.csv]
Nguon (uu tien tu thap -> cao):  crawl v2 (JSON)  <  nhap tay (CSV)   -> nhap tay luon de len crawl.
Chay lai nhieu lan an toan. Nen COPY dulich_v2.db ra ban sao truoc khi chay lan dau.

QUY UOC GIO MO CUA (bang CUA_SO_THOI_GIAN):
  * Moi ngay gio nhu nhau, khong ngay nghi  -> 1 dong, thu = NULL          (main.py cu van chay)
  * Gio khac nhau theo ngay / co ngay nghi  -> moi ngay MO CUA 1 dong, thu = 1..7 (ISO: 1=Thu Hai ... 7=Chu Nhat). Trong CSV nhap tay: ngay_nghi ghi 2=Thu Hai ... 7=Thu Bay, CN=Chu Nhat;
    ngay NGHI = khong co dong.  Truong hop nay main.py cu se bi LAP dia diem -> can sua fetch_all_points
    (loc theo thu cua ngay di) truoc/cung luc. Script se in danh sach cac dia diem thuoc truong hop nay.
  * Gio khong doc duoc (vd nghi trua: 2 khung gio/ngay) -> KHONG ghi, de nguyen dong cu, dua vao danh sach "can nhap tay".
"""
import sys, re, csv, json, sqlite3
from datetime import datetime
from crawl_serpapi_maps_v2 import hours_rows   # dung lai bo doc gio da test

DB   = sys.argv[1] if len(sys.argv) > 1 else "dulich_v2.db"
JSON = sys.argv[2] if len(sys.argv) > 2 else None
CSV  = sys.argv[3] if len(sys.argv) > 3 else None
NOW  = datetime.now().strftime("%Y-%m-%d")
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row; cur = db.cursor()
names = {r["ten"]: r["id"] for r in cur.execute("SELECT id, ten FROM DIA_DIEM")}
# Develop doi ten 5 TTTM them "(Rap CGV)"/"(Rap Lotte Cinema)": cho phep khop ca ten khong hau to (JSON crawl cu dung ten cu)
for _t, _i in list(names.items()):
    names.setdefault(re.sub(r"\s*\((?:Rạp|Rap)[^)]*\)\s*$", "", _t).strip(), _i)
LOAI_HOP_LE = {   # tu khoa loai Google cho phep theo nhom_chinh (khong khop -> nghi khop nham, khong nap)
 "mua_sam_giai_tri": ["mua sắm", "siêu thị", "cửa hàng", "công trình", "giải trí"],
 "an_uong": ["nhà hàng", "quán", "ăn", "phở", "bún", "bánh", "xôi", "cá", "mì", "cà phê"],
 "cafe": ["cà phê", "cafe", "nhà hàng", "quán", "bữa"],
 "van_hoa": ["thờ", "lịch sử", "điểm mốc", "thu hút", "nhà thờ", "chùa", "đền", "bảo tàng", "lăng", "di tích"],
 "check_in": ["thờ", "lịch sử", "thu hút", "nhà thờ", "công trình", "địa điểm"],
}
nhom = {r["id"]: r["nhom_chinh"] for r in cur.execute("SELECT id, nhom_chinh FROM DIA_DIEM")}
coords = {r["id"]: (r["vi_do"], r["kinh_do"]) for r in cur.execute("SELECT id, vi_do, kinh_do FROM DIA_DIEM")}
audit = []
import math
def dist_m(a1, o1, a2, o2):
    p = math.pi / 180
    x = math.sin((a2 - a1) * p / 2) ** 2 + math.cos(a1 * p) * math.cos(a2 * p) * math.sin((o2 - o1) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(x))
rep = {"gio_loi": [], "gio_qua_dem": [], "gia_0_bo_qua": [], "khop_nham_loai": [], "gio_crawl": [], "gio_tay": [], "can_nhap_tay": [], "per_day": [], "loi_crawl": [], "gia": [], "khong_khop": []}

def parse_price(s):
    """'$$' -> muc 2 ; '100–200 N ₫' / '₫100K–200K' -> (100000,200000). Khong doan neu khong chac."""
    if not s: return (None, None, None)
    s = str(s); muc = s.count("$") if set(s.strip()) <= {"$"} else None
    lo = hi = None
    unit = re.search(r"(triệu|tr\b|M\b)|(N\b|K\b|nghìn)", s, re.I)
    nums = [float(x.replace(".", "").replace(",", "")) if re.fullmatch(r"\d{1,3}([.,]\d{3})+", x) else float(x.replace(",", "."))
            for x in re.findall(r"\d+(?:[.,]\d+)*", s)]
    if nums and unit:
        mult = 1_000_000 if unit.group(1) else 1000
        lo, hi = int(nums[0] * mult), int(nums[-1] * mult)
    elif nums and any(n >= 1000 for n in nums):
        lo, hi = int(nums[0]), int(nums[-1])
    if lo is not None and hi is not None and lo < 1000 <= hi:   # Google ghi "1-100.000 d" = "duoi 100K": bo can duoi gia
        lo = None
    return (muc, lo, hi)

def hhmm(x, closing=False):
    """'9:30' / '9h30' / '9:30:00' -> '09:30'. Gio DONG 0:00 / 24:00 -> '23:59'. Khong doc duoc -> None."""
    m = re.match(r"\s*(\d{1,2})\s*[:hH.]?\s*(\d{2})?", x or "")
    if not m: return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if closing and h in (0, 24) and mi == 0: return "23:59"
    return f"{h:02d}:{mi:02d}" if h <= 23 and mi <= 59 else None

def set_hours(pid, by_day, nguon):
    """by_day: {thu: (gio_mo, gio_dong)} chi gom ngay MO CUA."""
    cur.execute("DELETE FROM CUA_SO_THOI_GIAN WHERE dia_diem_id=?", (pid,))
    vals = set(by_day.values())
    if len(by_day) == 7 and len(vals) == 1:
        o, c = next(iter(vals))
        cur.execute("INSERT INTO CUA_SO_THOI_GIAN (dia_diem_id,gio_mo_cua,gio_dong_cua,ngay_ap_dung,thu,da_xac_minh,nguon) VALUES (?,?,?,?,?,?,?)",
                    (pid, o, c, "Tất cả các ngày", None, 1, nguon))
    else:
        for thu, (o, c) in sorted(by_day.items()):
            cur.execute("INSERT INTO CUA_SO_THOI_GIAN (dia_diem_id,gio_mo_cua,gio_dong_cua,ngay_ap_dung,thu,da_xac_minh,nguon) VALUES (?,?,?,?,?,?,?)",
                        (pid, o, c, f"Thứ {thu}" if thu < 7 else "Chủ nhật", thu, 1, nguon))
        rep["per_day"].append(pid)

def mark(pid, note):
    r = cur.execute("SELECT trang_thai_du_lieu, ghi_chu_kiem_tra FROM DIA_DIEM WHERE id=?", (pid,)).fetchone()
    cur.execute("UPDATE DIA_DIEM SET ngay_cap_nhat=?, ghi_chu_kiem_tra=? WHERE id=?",
                (NOW, ((r["ghi_chu_kiem_tra"] or "") + " | " + note).strip(" |"), pid))

# ---------- 1. JSON crawl v2 ----------
if JSON:
    for e in json.load(open(JSON, encoding="utf-8")):
        pid = names.get(e["ten_dia_diem"])
        if pid is None: rep["khong_khop"].append(e["ten_dia_diem"]); continue
        if e.get("loi"):
            rep["loi_crawl"].append((pid, e["ten_dia_diem"], e["loi"])); rep["can_nhap_tay"].append(pid)
            cur.execute("UPDATE DIA_DIEM SET trang_thai_du_lieu='nghi_van' WHERE id=? AND trang_thai_du_lieu<>'da_dong_cua'", (pid,)); mark(pid, "crawl v2 bi loai: " + e["loi"][:300]); continue
        loai_txt = " ".join(e["loai"]) if isinstance(e.get("loai"), list) else str(e.get("loai") or "")
        if not any(k in loai_txt.lower() for k in LOAI_HOP_LE.get(nhom.get(pid), [])):
            rep["khop_nham_loai"].append((pid, e["ten_dia_diem"], loai_txt)); rep["can_nhap_tay"].append(pid)
            cur.execute("UPDATE DIA_DIEM SET trang_thai_du_lieu='nghi_van' WHERE id=?", (pid,))
            mark(pid, f"crawl v2 khop nham: Google tra ve loai '{loai_txt}' khong hop voi nhom '{nhom.get(pid)}' -> khong nap rating/review/gio"); continue
        g = e.get("toa_do") or {}
        if g.get("latitude") and coords.get(pid) and coords[pid][0]:
            audit.append((pid, e["ten_dia_diem"], coords[pid][0], coords[pid][1], g["latitude"], g["longitude"],
                          round(dist_m(coords[pid][0], coords[pid][1], g["latitude"], g["longitude"]))))
        muc, lo, hi = parse_price(e.get("gia"))
        cur.execute("""UPDATE DIA_DIEM SET rating=COALESCE(?,rating), so_luot_danh_gia=COALESCE(?,so_luot_danh_gia),
                       google_data_id=COALESCE(?,google_data_id), sdt=COALESCE(?,sdt), website=COALESCE(?,website),
                       muc_gia=COALESCE(?,muc_gia), gia_tu=COALESCE(?,gia_tu), gia_den=COALESCE(?,gia_den),
                       gia_ghi_chu=COALESCE(?,gia_ghi_chu), nguon_du_lieu='SerpApi Google Maps v2' WHERE id=?""",
                    (e.get("rating"), e.get("so_luot_danh_gia"), e.get("data_id"), e.get("sdt"), e.get("website"),
                     muc, lo, hi, (f"Google: {e['gia']}" if e.get("gia") else None), pid))
        if e.get("gia"): rep["gia"].append(pid)
        rows = e.get("gio_mo_cua") or []
        ok = {r["thu"]: (r["gio_mo"], r["gio_dong"]) for r in rows if r["trang_thai"] in ("ok", "24h")}
        unreadable = [r for r in rows if r["trang_thai"] == "khong_ro"]
        if ok and not unreadable:
            set_hours(pid, ok, "SerpApi"); rep["gio_crawl"].append(pid)
            cur.execute("UPDATE DIA_DIEM SET trang_thai_du_lieu=CASE WHEN trang_thai_du_lieu='nghi_van' THEN 'chua_xac_minh' ELSE trang_thai_du_lieu END WHERE id=? AND ?", (pid, 1 if e.get("rating") else 0))
            mark(pid, f"{NOW}: gio mo cua tu SerpApi")
        else:
            rep["can_nhap_tay"].append(pid)

# ---------- 2. CSV nhap tay (de len JSON) ----------
def num(x):
    x = (x or "").strip().replace(".", "").replace(",", "")
    return int(x) if x.isdigit() else None
def read_csv(path):
    """Excel o may Viet hay luu CSV dang Tab + bang ma ANSI (cp1258) -> tu nhan dien, khong bat nguoi dung luu lai."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1258", "cp1252"):
        try: text = raw.decode(enc); break
        except UnicodeError: continue
    first = text.splitlines()[0] if text.strip() else ""
    delim = max((",", ";", "\t"), key=first.count)
    import io
    return [r for r in csv.DictReader(io.StringIO(text), delimiter=delim) if (r.get("id") or "").strip().isdigit()]

if CSV:
    for r in read_csv(CSV):
        pid = int(r["id"]); g = lambda k: (r.get(k) or "").strip()
        o, c = g("gio_mo_that"), g("gio_dong_that")
        if o and c:
            o, c = hhmm(o), hhmm(c, True)
            if o is None or c is None:
                rep["gio_loi"].append(pid); o = c = None
            elif c <= o and c != "23:59":     # qua nua dem (vd 18:00-02:00): he thong chua ho tro -> cat o 23:59
                rep["gio_qua_dem"].append(pid); c = "23:59"
        if o and c:
            # CSV dung cach goi thong thuong: 2=Thu Hai ... 7=Thu Bay, CN=Chu Nhat. DB luu thu ISO: 1=Thu Hai ... 7=Chu Nhat.
            nghi = {7 if t in ("CN", "8") else int(t) - 1 for t in re.findall(r"CN|[2-8]", g("ngay_nghi(vd: 2,6)").upper())}
            by_day = {d: (o, c) for d in range(1, 8) if d not in nghi}
            set_hours(pid, by_day, "nhap_tay"); rep["gio_tay"].append(pid)
            if pid in rep["can_nhap_tay"]: rep["can_nhap_tay"].remove(pid)
        tg = num(g("thoi_gian_tham_quan_that_phut"))
        if tg: cur.execute("UPDATE DIA_DIEM SET thoi_gian_tham_quan_phut=?, thoi_gian_tham_quan_nguon='nhap_tay_da_xac_minh' WHERE id=?", (tg, pid))
        else:
            dx = num(g("de_xuat_thoi_gian_phut"))
            if dx: cur.execute("UPDATE DIA_DIEM SET thoi_gian_tham_quan_phut=?, thoi_gian_tham_quan_nguon='uoc_luong_theo_loai' WHERE id=?", (dx, pid))
        rt = g("rating_that").replace(",", ".")
        if re.fullmatch(r"\d(\.\d)?", rt): cur.execute("UPDATE DIA_DIEM SET rating=? WHERE id=?", (float(rt), pid))
        nrv = num(g("so_luot_danh_gia_that"))
        if nrv: cur.execute("UPDATE DIA_DIEM SET so_luot_danh_gia=? WHERE id=?", (nrv, pid))
        tu, den, muc = num(g("gia_tu_VND")), num(g("gia_den_VND")), num(g("muc_gia(1-4)"))
        mien_phi = re.search(r"mi.n ph.", g("ghi_chu_nguon_xac_minh"), re.I) is not None
        if tu == 0 and den == 0 and not mien_phi:   # 0/0 chi duoc tin khi ghi chu co chu "mien phi"
            rep["gia_0_bo_qua"].append(pid); tu = den = None
        if tu is not None or den is not None or muc:
            cur.execute("UPDATE DIA_DIEM SET gia_tu=COALESCE(?,gia_tu), gia_den=COALESCE(?,gia_den), muc_gia=COALESCE(?,muc_gia), gia_ghi_chu=COALESCE(?,gia_ghi_chu) WHERE id=?",
                        (tu, den, muc, g("ghi_chu_nguon_xac_minh") or None, pid)); rep["gia"].append(pid)
        if o or tg or tu is not None or rt or nrv:
            mark(pid, f"{NOW}: nhap tay ({g('ghi_chu_nguon_xac_minh') or 'khong ghi nguon'})")
cur.execute("UPDATE DIA_DIEM SET diem_gia_tri = rating WHERE rating IS NOT NULL")   # diem_gia_tri = rating (thang 5)
db.commit()

# ---------- 3. Bao cao ----------
still = [r["id"] for r in cur.execute("SELECT d.id FROM DIA_DIEM d WHERE NOT EXISTS (SELECT 1 FROM CUA_SO_THOI_GIAN c WHERE c.dia_diem_id=d.id AND c.da_xac_minh=1)")]
print(f"Gio that tu crawl: {len(rep['gio_crawl'])} | tu nhap tay: {len(rep['gio_tay'])} | gia nap: {len(set(rep['gia']))}")
print(f"Dia diem co gio THEO NGAY (can sua main.py): {sorted(set(rep['per_day']))}")
print(f"Chua co gio that ({len(still)}): {still}")
if rep["gia_0_bo_qua"]: print("GIA 0/0 BI BO QUA (de nguyen gia cu). Neu that su MIEN PHI, ghi 'mien phi' vao cot ghi_chu_nguon_xac_minh:", rep["gia_0_bo_qua"])
if rep["gio_loi"]: print("GIO KHONG DOC DUOC (sua dang 09:30):", rep["gio_loi"])
if rep["gio_qua_dem"]: print("Gio dong <= gio mo (qua nua dem), da cat o 23:59:", rep["gio_qua_dem"])
if audit:
    with open("bao_cao_lech_toa_do.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["id", "ten", "vi_do_DB", "kinh_do_DB", "vi_do_Google", "kinh_do_Google", "lech_m"])
        w.writerows(sorted(audit, key=lambda r: -r[-1]))
    print("Toa do DB lech Google > 100 m:", [(r[0], r[-1]) for r in sorted(audit, key=lambda r: -r[-1]) if r[-1] > 100], "(chi tiet: bao_cao_lech_toa_do.csv)")
if rep["khop_nham_loai"]: print("KHOP NHAM THEO LOAI (khong nap):", rep["khop_nham_loai"])
if rep["loi_crawl"]: print("Crawl loi/nghi khop nham:", rep["loi_crawl"])
if rep["khong_khop"]: print("Khong khop ten voi DB:", rep["khong_khop"])
db.close()
