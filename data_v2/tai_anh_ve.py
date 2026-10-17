"""tai_anh_ve.py - Tai anh dia diem ve thu muc images/ (ben canh index.html) de khong phu thuoc link Google het han.

Chay (o thu muc goc repo, may co mang):
    python data_v2/tai_anh_ve.py dulich.db                 # tai tu cac link dang co trong DB
    python data_v2/tai_anh_ve.py dulich.db --serpapi       # diem nao link da chet: lay link moi qua SerpApi (can SERPAPI_KEY, ~1 luot/diem)

Moi diem: images/<id>_0.jpg (anh bia), images/<id>_1.jpg ... (toi da 4 anh).  DB duoc cap nhat:
    DIA_DIEM.url_hinh_anh = 'images/<id>_0.jpg'   va   DIA_DIEM_ANH = danh sach duong dan cuc bo.
Diem nao KHONG tai duoc anh nao thi giu nguyen link cu (giao dien tu dung anh mac dinh). Chay lai an toan.
Sau khi chay: dua thu muc images/ len GitHub cung index.html va chay lai gen_sqlserver.py de SQL Server co duong dan moi."""
import os, sys, sqlite3, json, mimetypes
import requests

DB = next((a for a in sys.argv[1:] if not a.startswith("--")), "dulich.db")
USE_SERPAPI = "--serpapi" in sys.argv
OUT_DIR = os.environ.get("IMAGES_DIR", "images")
SERPAPI_URL = os.environ.get("SERPAPI_URL", "https://serpapi.com/search.json")
MAX_PER_PLACE = 4
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
           "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}   # khong gui Referer


def download(url, dest_base):
    """Tai 1 anh. Thanh cong -> tra ve duong dan file da luu; that bai -> None."""
    if not url or not url.startswith("http"):
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except Exception:
        return None
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if r.status_code != 200 or not ctype.startswith("image/") or len(r.content) < 2000:
        return None
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ctype) or mimetypes.guess_extension(ctype) or ".jpg"
    path = dest_base + ext
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def serpapi_photos(data_id):
    key = os.environ.get("SERPAPI_KEY")
    if not (key and data_id):
        return []
    try:
        r = requests.get(SERPAPI_URL, params={"engine": "google_maps_photos", "data_id": data_id, "api_key": key}, timeout=30)
        js = r.json()
    except Exception as exc:
        print("    SerpApi loi:", exc)
        return []
    return [p.get("image") or p.get("thumbnail") for p in (js.get("photos") or []) if p.get("image") or p.get("thumbnail")]


os.makedirs(OUT_DIR, exist_ok=True)
db = sqlite3.connect(DB); db.row_factory = sqlite3.Row; cur = db.cursor()
ok_places, fail_places = [], []
for r in cur.execute("SELECT id, ten, url_hinh_anh, google_data_id FROM DIA_DIEM ORDER BY id").fetchall():
    pid = r["id"]
    candidates = [r["url_hinh_anh"]] + [x["url"] for x in cur.execute(
        "SELECT url FROM DIA_DIEM_ANH WHERE dia_diem_id=? ORDER BY la_anh_bia DESC, thu_tu", (pid,))]
    seen, saved = set(), []
    def try_urls(urls):
        for u in urls:
            if len(saved) >= MAX_PER_PLACE: break
            if not u or u in seen or u.startswith(OUT_DIR + "/"): continue
            seen.add(u)
            p = download(u, os.path.join(OUT_DIR, f"{pid}_{len(saved)}"))
            if p: saved.append(p.replace("\\", "/"))
    # da la duong dan cuc bo hop le thi bo qua (chay lai khong lam hong)
    if (r["url_hinh_anh"] or "").startswith(OUT_DIR + "/") and os.path.exists(r["url_hinh_anh"]):
        ok_places.append(pid); continue
    try_urls(candidates)
    if not saved and USE_SERPAPI:
        try_urls(serpapi_photos(r["google_data_id"]))
    if not saved:
        fail_places.append((pid, r["ten"])); print(f"  [X] {pid:>2} {r['ten'][:34]}: khong tai duoc anh nao -> giu link cu"); continue
    cur.execute("UPDATE DIA_DIEM SET url_hinh_anh=? WHERE id=?", (saved[0], pid))
    cur.execute("DELETE FROM DIA_DIEM_ANH WHERE dia_diem_id=?", (pid,))
    for i, p in enumerate(saved):
        cur.execute("INSERT INTO DIA_DIEM_ANH (dia_diem_id,url,thu_tu,la_anh_bia,nguon) VALUES (?,?,?,?,?)", (pid, p, i, 1 if i == 0 else 0, "tai_ve_cuc_bo"))
    ok_places.append(pid); print(f"  [OK] {pid:>2} {r['ten'][:34]}: {len(saved)} anh")
db.commit()
print(f"\nXong: {len(ok_places)} diem co anh cuc bo | khong tai duoc: {[p for p, _ in fail_places]}")
if fail_places and not USE_SERPAPI:
    print("Diem loi: link Google/anh cu da het han. Chay lai voi --serpapi (can set SERPAPI_KEY) de lay link moi.")
