import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from services.parser import parse_prompt
from api.schemas import OptimizationRequest
from api.generation import run_route_generation

print("=== 1. KIỂM THỬ PARSER & BADGE TEXT ===")
prompts = [
    ("khám phá hà nội với trải nghiệm nhộn nhịp, có quán cafe và view đẹp",
     ["lively", "cafe", "photo"]),
    ("muốn đi uống cafe giảng và ăn phở thìn bờ hồ, ngắm hồ gươm",
     ["cafe", "food", "nature"]),
    ("đi dạo phố cổ yên tĩnh nhẹ nhàng không đông người, thong thả uống trà sen ngắm tranh triển lãm",
     ["quiet", "culture"]),
]

for text, exp_tags in prompts:
    res = parse_prompt(text, context={"start_time": "08:00", "end_time": "20:00", "vehicle_type": "xe_may", "start_point": "Hồ Gươm"})
    badge = res.get("badge_text", "")
    summary = res.get("understood_summary", "")
    tags = res["fields"]["preferred_tags"]
    places = res["fields"]["must_visit"]
    print(f"\nPrompt: {text}")
    print(f"  -> Preferred tags: {tags}")
    print(f"  -> Must visit: {places}")
    print(f"  -> Badge Text: {badge}")
    print(f"  -> Understood Summary: {summary}")
    for exp in exp_tags:
        assert exp in tags, f"Missing expected tag {exp} in {tags}"

print("\n=== 2. KIỂM THỬ SINH LỘ TRÌNH VỚI NHỘN NHỊP + CAFE + VIEW ĐẸP ===")
req = OptimizationRequest(
    region="hanoi",
    start_point="Đền Ngọc Sơn (Hồ Hoàn Kiếm)",
    start_time="08:00",
    end_time="20:00",
    vehicle_type="xe_may",
    trip_date="2026-10-22",
    user_preference="khám phá hà nội với trải nghiệm nhộn nhịp, có quán cafe và view đẹp",
    weight=80
)
routes_res = run_route_generation(req)
routes = routes_res.get("routes", [])
print(f"Sinh được {len(routes)} lộ trình.")
assert len(routes) > 0, "Không sinh được lộ trình nào!"

for i, r in enumerate(routes):
    pts = [p["ten"] for p in r["optimized_route"]]
    types = [p["loai_hinh"] for p in r["optimized_route"]]
    print(f"Route {i+1}: {r['route_name']}")
    print(f"   Điểm: {pts}")
    print(f"   Loại hình: {types}")
    print(f"   Avg Preference Score: {r.get('avg_preference_score')}")
    # Không được có TTTM vì người dùng không yêu cầu mua sắm/xem phim
    assert "TTTM" not in types, f"Lộ trình không được chứa TTTM khi chỉ yêu cầu nhộn nhịp + cafe + view đẹp: {types}"

print("\n>>> TẤT CẢ KIỂM THỬ THÀNH CÔNG RỰC RỠ! <<<")
