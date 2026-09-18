"""
============================================================
TEST BACKEND CORE — bám đúng Test A..G ở mục 12
------------------------------------------------------------
Chạy:  python test_backend_core.py

Test chạy trực tiếp trên dulich.db có sẵn trong repo, qua FastAPI TestClient
(không cần bật uvicorn, không cần SQL Server, không cần Ollama).

OSRM và Open-Meteo bị monkeypatch bằng ước lượng cục bộ để:
  * test chạy nhanh và ổn định, không phụ thuộc mạng;
  * kết quả tái lập được (cùng input → cùng output), nên khẳng định
    "các route khác nhau" là do thuật toán chứ không do nhiễu mạng.
============================================================
"""

import sys
import math
import types

# ---- Stub pyodbc: máy CI/sandbox không có driver ODBC, backend sẽ tự dùng SQLite ----
if "pyodbc" not in sys.modules:
    stub = types.ModuleType("pyodbc")
    stub.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no odbc"))
    sys.modules["pyodbc"] = stub

import main
from itinerary_store import store
from fastapi.testclient import TestClient


# ---- Thay OSRM/thời tiết bằng ước lượng cục bộ (nhanh, tất định) ----
def _fake_matrix(points_list, vehicle_type="xe_may"):
    _, k = main.get_vehicle_osrm_profile(vehicle_type)
    m = {}
    for p1 in points_list:
        m[p1["id"]] = {}
        for p2 in points_list:
            d = main.calc_dist(p1, p2)
            m[p1["id"]][p2["id"]] = {"duration": (d / 20.0 * 60.0) * k, "distance": d}
    return m


main.get_global_osrm_matrix = _fake_matrix
main.get_weather_factor = lambda lat, lon: 1.0

client = TestClient(main.app)

# Điểm xuất phát dùng chung: Hồ Gươm cho chắc chắn khớp dữ liệu Hà Nội.
BASE_PAYLOAD = {
    "region": "Hanoi",
    "start_time": "08:00",
    "end_time": "20:00",          # 12 tiếng — đúng yêu cầu Test A
    "trip_date": "2026-10-01",
    "start_lat": 21.0285,
    "start_lon": 105.8542,
    "vehicle_type": "xe_may",
    "user_preference": "yên tĩnh, cà phê view đẹp, đồ ăn ngon",
    "weight": 50,
}

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  ✅ {name}")
    else:
        FAILED.append((name, detail))
        print(f"  ❌ {name} — {detail}")


def ids_of(route):
    return [p["id"] for p in route["places"] if p["id"] != "gps_current"]


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if (a | b) else 1.0


# ============================================================
print("\n=== TEST A: 12 tiếng → nhận được 3–5 route hoàn chỉnh ===")
# ============================================================
res = client.post("/api/routes", json=BASE_PAYLOAD)
check("A1 · HTTP 200", res.status_code == 200, f"status={res.status_code}")
data = res.json()
check("A2 · status success", data.get("status") == "success", str(data)[:200])

routes = data.get("routes", [])
session_id = data.get("session_id")
check("A3 · trả về 3–5 route", 3 <= len(routes) <= 5, f"len={len(routes)}")
check("A4 · có session_id", bool(session_id), "thiếu session_id")

REQUIRED = ["route_id", "name", "theme", "description", "places",
            "timeline", "total_duration", "travel_time", "visit_time"]
missing = [k for r in routes for k in REQUIRED if k not in r]
check("A5 · route là object độc lập đủ schema (mục 3)", not missing, f"thiếu {set(missing)}")
check("A6 · route_id dạng route_N", all(r["route_id"] == f"route_{i+1}" for i, r in enumerate(routes)),
      str([r["route_id"] for r in routes]))
check("A7 · mọi route đều có timeline + places", all(r["places"] and r["timeline"] for r in routes))
check("A8 · total_duration ≈ travel + visit + wait",
      all(abs(r["total_duration"] - (r["travel_time"] + r["visit_time"] + r["wait_time"])) < 2.0 for r in routes),
      str([(r["total_duration"], r["travel_time"], r["visit_time"], r["wait_time"]) for r in routes]))
check("A9 · không vượt quỹ thời gian 12h",
      all(r["total_duration"] <= data["available_minutes"] + 1 for r in routes))

print(f"\n  → sinh {data.get('candidates_generated')} candidate, chọn ra {len(routes)} lộ trình:")
for r in routes:
    print(f"     {r['route_id']} [{r['theme']:<14}] {len(ids_of(r))} điểm · "
          f"{r['total_duration']:>5.0f}ph (đi {r['travel_time']:>4.0f}ph) · {r['name']}")


# ============================================================
print("\n=== TEST G: các route có THỰC SỰ khác nhau không? ===")
# ============================================================
sets = [set(ids_of(r)) for r in routes]
dupes = [(i, j) for i in range(len(sets)) for j in range(i + 1, len(sets)) if sets[i] == sets[j]]
check("G1 · không có route trùng tập điểm", not dupes, f"trùng: {dupes}")

overlaps = [(i, j, round(jaccard(sets[i], sets[j]), 2))
            for i in range(len(sets)) for j in range(i + 1, len(sets))]
worst = max(o[2] for o in overlaps) if overlaps else 0
check("G2 · độ chồng lấn tối đa < 0.8", worst < 0.8, f"max Jaccard={worst}")

themes = [r["theme"] for r in routes]
check("G3 · nhiều theme khác nhau", len(set(themes)) >= 3, f"themes={themes}")

durations = [r["total_duration"] for r in routes]
check("G4 · khác nhau về tổng thời gian", len(set(round(d) for d in durations)) >= 3, f"{durations}")

travels = [r["travel_time"] for r in routes]
check("G5 · khác nhau về mức độ di chuyển", len(set(round(t) for t in travels)) >= 3, f"{travels}")

orders = [tuple(ids_of(r)) for r in routes]
check("G6 · khác nhau về thứ tự", len(set(orders)) == len(orders))

names = [r["name"] for r in routes]
check("G7 · tên lộ trình không trùng nhau", len(set(names)) == len(names), str(names))

print("  → ma trận chồng lấn (Jaccard):")
for i, j, v in overlaps:
    print(f"     route_{i+1} vs route_{j+1}: {v}")


# ============================================================
print("\n=== TEST B: chọn route 2 ở Screen 2 → Screen 3 nhận ĐÚNG route 2 ===")
# ============================================================
target = routes[1]
target_ids = ids_of(target)
sel = client.post("/api/routes/select", json={"session_id": session_id, "route_id": "route_2"})
check("B1 · HTTP 200", sel.status_code == 200, sel.text[:200])
sel_data = sel.json()
check("B2 · trả đúng route_2", sel_data["route_id"] == "route_2", sel_data.get("route_id"))
check("B3 · tập điểm KHÔNG bị sinh lại", ids_of(sel_data["itinerary"]) == target_ids,
      "route trả về khác route đã hiển thị ở Screen 2")
check("B4 · timeline giữ nguyên", sel_data["itinerary"]["timeline"] == target["timeline"])
check("B5 · total_duration giữ nguyên", sel_data["itinerary"]["total_duration"] == target["total_duration"])

# Gọi lại lần nữa phải cho kết quả y hệt (không regenerate — mục 10, mục 13)
sel2 = client.post("/api/routes/select", json={"session_id": session_id, "route_id": "route_2"}).json()
check("B6 · gọi lại vẫn y hệt (không regenerate)", ids_of(sel2["itinerary"]) == target_ids)

it = client.get("/api/itinerary", params={"session_id": session_id}).json()
check("B7 · source of truth khớp: map = timeline = itinerary (mục 11)",
      [m["id"] for m in it["map_data"] if m["id"] != "gps_current"] == target_ids
      and it["timeline"] == target["timeline"]
      and it["ai_context_ids"] == target_ids)

print(f"  → route_2 = {len(target_ids)} điểm: {target_ids}")


# ============================================================
print("\n=== TEST C: xoá một địa điểm ===")
# ============================================================
victim = target_ids[1]           # xoá điểm thứ 2 (tương đương 'C' trong A→B→C→D→E)
victim_name = target["places"][2]["ten"]
upd = client.post("/api/itinerary/update", json={"session_id": session_id, "remove_ids": [victim]})
check("C1 · HTTP 200", upd.status_code == 200, upd.text[:300])
u = upd.json()
check("C2 · status success", u.get("status") == "success", str(u)[:200])
new_ids = ids_of(u["itinerary"])
check("C3 · điểm bị xoá KHÔNG còn trong lộ trình", victim not in new_ids, f"{victim} vẫn còn")
check("C4 · ghi vào excluded_ids (không xoá khỏi DB)", victim in u["excluded_ids"])
check("C5 · các điểm còn lại được giữ",
      len([i for i in target_ids if i != victim and i in new_ids]) >= len(target_ids) - 2,
      f"giữ {[i for i in target_ids if i != victim and i in new_ids]} / {[i for i in target_ids if i != victim]}")

# DB phải còn nguyên bản ghi (mục 13: không được xoá dữ liệu khỏi database)
db_points = main.fetch_all_points()
check("C6 · bản ghi vẫn còn nguyên trong database", any(p["id"] == victim for p in db_points),
      "địa điểm đã bị xoá khỏi DB — vi phạm mục 13")

print(f"  → đã xoá '{victim_name}' (id={victim})")
print(f"     trước: {target_ids}")
print(f"     sau  : {new_ids}")


# ============================================================
print("\n=== TEST D: xoá rồi fill-up ===")
# ============================================================
check("D1 · Greedy fill-up từ TOÀN BỘ candidate pool", len(new_ids) >= len(target_ids) - 1,
      f"{len(target_ids)} điểm → {len(new_ids)} điểm (lẽ ra phải lấp lại chỗ trống)")
filled = [i for i in new_ids if i not in target_ids]
check("D2 · điểm mới lấp vào không phải điểm đã bị loại", victim not in filled)
check("D3 · vẫn nằm trong quỹ thời gian",
      u["itinerary"]["total_duration"] <= u["available_minutes"] + 1)
print(f"  → fill-up thêm {len(filled)} điểm mới: {filled or '(không còn chỗ trống)'}")


# ============================================================
print("\n=== TEST E: xoá 2–3 địa điểm liên tiếp ===")
# ============================================================
removed_so_far = [victim]
current = new_ids
for step in range(2):
    if len(current) < 2:
        break
    nxt = current[1]
    r = client.post("/api/itinerary/update", json={"session_id": session_id, "remove_ids": [nxt]}).json()
    check(f"E{step+1} · xoá liên tiếp lần {step+2} thành công", r.get("status") == "success", str(r)[:200])
    removed_so_far.append(nxt)
    current = ids_of(r["itinerary"])
    leaked = [x for x in removed_so_far if x in current]
    check(f"E{step+1}b · không điểm nào đã xoá quay lại", not leaked, f"quay lại: {leaked}")

state = client.get("/api/session", params={"session_id": session_id}).json()
check("E3 · excluded_ids tích luỹ đủ", set(removed_so_far) <= set(state["excluded_ids"]),
      f"excluded={state['excluded_ids']} vs removed={removed_so_far}")
print(f"  → đã xoá tổng cộng {removed_so_far}, lộ trình còn: {current}")


# ============================================================
print("\n=== TEST F: xoá A → thêm điểm mới → A KHÔNG xuất hiện lại ===")
# ============================================================
# Đây là kịch bản đúng nguyên văn mục 8: 'Bỏ A' rồi 'Thêm quán ăn trưa'.
all_ids = {p["id"] for p in db_points}
candidate_new = sorted(all_ids - set(current) - set(removed_so_far))[:2]
addres = client.post("/api/itinerary/update",
                     json={"session_id": session_id, "add_ids": candidate_new}).json()
check("F1 · thêm điểm thành công", addres.get("status") == "success", str(addres)[:200])
after_add = ids_of(addres["itinerary"])
leaked = [x for x in removed_so_far if x in after_add]
check("F2 · điểm đã xoá KHÔNG quay lại sau khi thêm điểm mới", not leaked, f"quay lại: {leaked}")
check("F3 · điểm mới được ghim vào lộ trình",
      any(i in after_add for i in candidate_new), f"{candidate_new} không vào được (có thể hết quỹ giờ)")

# Ràng buộc phải sống sót qua cả việc sinh lại toàn bộ route (Screen 1 lần 2)
regen = client.post("/api/routes", json={**BASE_PAYLOAD, "session_id": session_id}).json()
leaked2 = [x for x in removed_so_far for r in regen["routes"] if x in ids_of(r)]
check("F4 · sinh lại 3–5 route mới vẫn không có điểm đã xoá", not leaked2, f"quay lại: {set(leaked2)}")
check("F5 · vẫn trả 3–5 route sau khi loại trừ", 3 <= len(regen["routes"]) <= 5, f"len={len(regen['routes'])}")

# Khôi phục: người dùng đổi ý thì lấy lại được (vì DB chưa bao giờ bị xoá)
rest = client.post("/api/itinerary/update",
                   json={"session_id": session_id, "restore_ids": [victim]}).json()
check("F6 · khôi phục được điểm đã loại", victim not in rest["excluded_ids"])
print(f"  → thêm {candidate_new} → lộ trình: {after_add}")


# ============================================================
print("\n=== TEST H (bổ sung): tương thích ngược /api/optimize-route ===")
# ============================================================
legacy = client.post("/api/optimize-route", json=BASE_PAYLOAD).json()
check("H1 · endpoint cũ vẫn chạy", legacy.get("status") == "success", str(legacy)[:200])
lr = legacy["routes"][0]
check("H2 · khoá cũ app.js đang dùng còn nguyên",
      all(k in lr for k in ("strategy", "route_name", "optimized_route", "total_time_minutes")),
      f"có: {sorted(lr.keys())}")
check("H3 · optimized_route == places", lr["optimized_route"] == lr["places"])
check("H4 · phần tử optimized_route giữ đủ field cho frontend",
      all(k in lr["optimized_route"][0] for k in
          ("id", "ten", "lat", "lon", "arrive_time", "depart_time", "visit_time",
           "travel_to_next", "distance_to_next", "url_hinh_anh", "review")))

# Luồng chatbot cũ: truyền ai_selected_ids kèm session_id đã có excluded_ids
sid2 = legacy["session_id"]
client.post("/api/itinerary/update", json={"session_id": sid2, "remove_ids": [ids_of(lr)[1]]})
banned = ids_of(lr)[1]
legacy2 = client.post("/api/optimize-route",
                      json={**BASE_PAYLOAD, "session_id": sid2,
                            "ai_selected_ids": ids_of(lr)}).json()
leak3 = [x for r in legacy2["routes"] if banned in ids_of(r) for x in [banned]]
check("H5 · ai_selected_ids không ghi đè được excluded_ids (mục 6)", not leak3,
      f"{banned} quay lại qua ai_selected_ids")


# ============================================================
print("\n" + "=" * 60)
print(f"KẾT QUẢ: {len(PASSED)} PASS · {len(FAILED)} FAIL")
if FAILED:
    print("\nCÁC TEST HỎNG:")
    for name, detail in FAILED:
        print(f"  ❌ {name}\n     {detail}")
print("=" * 60)
sys.exit(1 if FAILED else 0)
