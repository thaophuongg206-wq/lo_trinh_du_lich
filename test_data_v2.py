"""Test DB v2 (Task 2): giờ mở cửa theo thứ, review/rating/thẻ/ảnh từ DB, tương thích DB cũ.
Chạy:  python test_data_v2.py      (dùng dulich.db trong thư mục; SQLITE_DB=... để thử DB khác)
OSRM/thời tiết được thay bằng ước lượng cục bộ như test_backend_core.py."""
import sys, types, os, sqlite3
if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
if "pyodbc" not in sys.modules:
    stub = types.ModuleType("pyodbc"); stub.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no odbc"))
    sys.modules["pyodbc"] = stub
import main, api.osrm_matrix, api.factors
from api.db import fetch_all_points, SQLITE_DB
from fastapi.testclient import TestClient

def _fake_matrix(pts, vehicle_type="xe_may"):
    _, k = main.get_vehicle_osrm_profile(vehicle_type)
    return {a["id"]: {b["id"]: {"duration": (main.calc_dist(a, b) / 20.0 * 60.0) * k, "distance": main.calc_dist(a, b)} for b in pts} for a in pts}
api.osrm_matrix.get_global_osrm_matrix = _fake_matrix
api.factors.get_weather_factor = lambda lat, lon: 1.0
client = TestClient(main.app)

ok = fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond: ok += 1; print("  PASS", name)
    else: fail += 1; print("  FAIL", name, extra)

db = sqlite3.connect(SQLITE_DB)
IS_V2 = any(r[1] == "thu" for r in db.execute("pragma table_info(CUA_SO_THOI_GIAN)"))
N = db.execute("select count(*) from DIA_DIEM").fetchone()[0]
print(f"Dang test tren: {SQLITE_DB} | DB v2: {IS_V2} | {N} dia diem")

pts = fetch_all_points()
check("khong ngay: moi diem 1 dong, khong lap", len(pts) == N == len({p['id'] for p in pts}), f"{len(pts)} vs {N}")
mon = {p["id"] for p in fetch_all_points(trip_date="2026-10-05")}     # Thu Hai
tue = {p["id"] for p in fetch_all_points(trip_date="2026-10-06")}     # Thu Ba
fri = {p["id"] for p in fetch_all_points(trip_date="2026-10-09")}     # Thu Sau
if IS_V2:
    check("Thu Hai: Lang (27) nghi -> bi loai", "27" not in mon and len(mon) == N - 1, f"{len(mon)}")
    check("Thu Ba: Lang (27) mo, du diem, khong lap", "27" in tue and len(tue) == N)
    check("Thu Sau: Lang (27) nghi", "27" not in fri)
    sat = {p["id"]: p for p in fetch_all_points(trip_date="2026-10-10")}
    wk = {p["id"]: p for p in fetch_all_points(trip_date="2026-10-06")}
    check("Aeon (5): cuoi tuan mo som hon ngay thuong", sat["5"]["open_time"] < wk["5"]["open_time"])
    check("bo_diem_dong_cua=True bo 24 va 25", not ({"24", "25"} & {p["id"] for p in fetch_all_points(bo_diem_dong_cua=True)}))
    check("mac dinh van giu 24, 25 (diem mau)", {"24", "25"} <= {p["id"] for p in pts})
    p4 = next(p for p in pts if p["id"] == "4")
    check("rating that (thang 5) + so danh gia", 0 < (p4["rating"] or 0) <= 5 and (p4["so_luot_danh_gia"] or 0) > 100, str(p4["rating"]))
    check("score (diem_gia_tri) thang 5, = rating that", all(0 < (p["score"] or 0) <= 5 for p in pts) and all(p["score"] == p["rating"] for p in pts if p["rating"]))
    check("co the + anh cho moi diem", all(p["the"] and p["anh"] for p in pts))
    p2 = next(p for p in pts if p["id"] == "2")
    check("review that lay tu DB (diem 2)", len(p2.get("serpapi_reviews", [])) >= 3 and "Đánh giá thực tế" in p2["review"])
    p1 = next(p for p in pts if p["id"] == "1")
    check("Lotte (1) chua co review trong DB -> du phong places_output.json", len(p1.get("serpapi_reviews", [])) >= 3)
    check("moi diem co open_time/close_time hop le", all(p["open_time"] and p["close_time"] for p in pts))
    # API: ngay Thu Hai -> khong lo trinh nao chua Lang; dia diem tra ve co rating that + the + anh
    body = {"region": "Hanoi", "start_time": "08:00", "end_time": "20:00", "trip_date": "2026-10-05", "start_lat": 21.0285,
            "start_lon": 105.8542, "vehicle_type": "xe_may", "weight": 50, "user_preference": "van hoa lich su Lang Chu tich Van Mieu"}
    r = client.post("/api/routes", json=body).json()
    routes = r.get("routes") or r.get("data", {}).get("routes") or []
    ids = {pl["id"] for rt in routes for pl in rt.get("places", rt.get("route", []))}
    check("API /api/routes Thu Hai: co lo trinh, khong co Lang", bool(routes) and "27" not in ids, f"{len(routes)} routes")
    pl = next((pl for rt in routes for pl in rt.get("places", rt.get("route", [])) if pl["id"] != "gps_current"), {})
    check("API tra rating that + the + anh trong tung diem", 0 < (pl.get("rating") or 99) <= 5 and "the" in pl and "anh" in pl, str({k: pl.get(k) for k in ('ten','rating')}))
    loc = client.get("/api/locations").json()["data"]
    check("/api/locations: rating that (<=5)", all((l["rating"] or 0) <= 5 or l["id"] == "31" for l in loc))

    # ---- Task 3: gio ca tuan, review insight, the -> diem so thich, ABSA tu review DB ----
    from api.preferences import calculate_preference_score
    from absa_service import enrich_points
    lang = next(p for p in fetch_all_points() if p["id"] == "27")
    check("gio_tuan: Lang nghi T2 va T6", lang["gio_tuan"][0].get("nghi") and lang["gio_tuan"][4].get("nghi") and lang["gio_tuan"][1].get("mo"))
    with_ins = [p for p in pts if p["review_insight"].get("so_review")]
    check("review_insight: >= 25 diem co phan tich tu review that", len(with_ins) >= 25, str(len(with_ins)))
    check("review_insight: co noi_bat / trich_dan", sum(1 for p in with_ins if p["review_insight"]["noi_bat"]) >= 20 and all(p["review_insight"]["trich_dan"] for p in with_ins))
    cafe_pt = next(p for p in pts if p["id"] == "22"); mall = next(p for p in pts if p["id"] == "4")
    check("diem so thich: tag 'cafe' dung the DB (cafe > TTTM)", calculate_preference_score(cafe_pt, [], ["cafe"]) > calculate_preference_score(mall, [], ["cafe"]))
    outdoor = next(p for p in pts if p.get("trong_nha_ngoai_troi") == "ngoai_troi"); indoor = next(p for p in pts if p.get("trong_nha_ngoai_troi") == "trong_nha")
    check("diem so thich: ngoai troi dung cot trong_nha_ngoai_troi", calculate_preference_score(outdoor, ["x"], None, "ngoai_troi") > calculate_preference_score(indoor, ["x"], None, "ngoai_troi"))
    lowp = next(p for p in pts if p.get("muc_gia") == 1); highp = next((p for p in pts if (p.get("muc_gia") or 0) >= 3), None)
    check("diem so thich: ngan sach dung muc_gia", highp is not None and calculate_preference_score(lowp, ["x"], None, None, 1) > calculate_preference_score(highp, ["x"], None, None, 1))
    ab = enrich_points([p for p in pts if p["id"] in ("2", "22", "31")], use_ollama=False)
    check("ABSA doc review DB (khong can Ollama)", all(a["absa_source"] == "review_db" for a in ab if a["id"] in ("2", "22")) and all(0 <= a["sabsa_score"] <= 1 for a in ab))
    api_pl = next((pl for rt in routes for pl in rt.get("places", rt.get("route", [])) if pl["id"] != "gps_current"), {})
    check("API tra gio_tuan + review_insight cho tung diem", "gio_tuan" in api_pl and "review_insight" in api_pl)
    # ---- Hotfix sau khi chay thu tren trinh duyet: gio mo cua gui xuong UI + trich dan uu tien tieng Viet ----
    check("API tra gio_mo_cua/gio_dong_cua (HH:MM) cho giao dien", all(isinstance(pl.get("gio_mo_cua"), str) and len(pl["gio_mo_cua"]) == 5 and len(pl.get("gio_dong_cua", "")) == 5 for rt in routes for pl in rt.get("places", rt.get("route", [])) if pl["id"] != "gps_current"))
    from review_insights import analyze_reviews
    ri = analyze_reviews([{"noi_dung": "He said, come visit me, we have to check in here, it is beautiful and lovely", "so_sao": 5, "tuoi_review_thang": 1},
                          {"noi_dung": "Quán rất đẹp, cà phê ngon, nhân viên thân thiện, sẽ quay lại lần sau", "so_sao": 5, "tuoi_review_thang": 5, "ngon_ngu": "khong_ro"}], key="t")
    check("trich dan uu tien review tieng Viet (>=4 sao)", "Quán rất đẹp" in ri["trich_dan"][0]["noi_dung"], str(ri["trich_dan"][:1]))

# DB cu (khong co cot v2) van chay
old = os.environ.get("TEST_OLD_DB")
if old:
    import api.db as adb; adb.SQLITE_DB = old
    legacy = fetch_all_points()
    check("DB cu: van doc duoc bang cau SELECT cu", len(legacy) > 0 and "rating" not in legacy[0])

print(f"\nKET QUA: {ok} PASS, {fail} FAIL")
sys.exit(1 if fail else 0)
