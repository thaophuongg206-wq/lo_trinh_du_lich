import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from api.schemas import OptimizationRequest
from api.generation import run_route_generation
from ai_advisor import generate_fallback_suggestion, build_prompt

prompt = "khám phá hà nội với trải nghiệm náo nhiệt, và những quán cà phê view đẹp"

print("=== 1. KIỂM THỬ ADVISOR PROMPT & FALLBACK ===")
bp = build_prompt("context", prompt, 720)
print("Prompt quantification rule snippet:")
for line in bp.split("\n"):
    if "Người dùng" in line or "HÃY CHỌN" in line or "TUYỆT ĐỐI" in line:
        print("  ", line)

advice_text, ids, _, summary, _ = generate_fallback_suggestion([], 720, prompt)
print("\nFallback Advice Text:\n  ", advice_text)
print("\nFallback Summary:\n  ", summary)
assert "thong thả" not in advice_text.lower(), "LỖI: advice_text vẫn chứa từ 'thong thả'!"
assert "bình yên" not in advice_text.lower(), "LỖI: advice_text vẫn chứa từ 'bình yên'!"

print("\n=== 2. KIỂM THỬ SINH LỘ TRÌNH VỚI NÁO NHIỆT ===")
req = OptimizationRequest(
    region="hanoi",
    start_point="Đền Ngọc Sơn (Hồ Hoàn Kiếm)",
    start_time="08:00",
    end_time="20:00",
    vehicle_type="xe_may",
    trip_date="2026-10-22",
    user_preference=prompt,
    weight=80
)
res = run_route_generation(req)
routes = res.get("routes", [])
print(f"Sinh được {len(routes)} lộ trình.")
assert len(routes) > 0

for i, r in enumerate(routes):
    pts = [p["ten"] for p in r["optimized_route"]]
    types = [p["loai_hinh"] for p in r["optimized_route"]]
    print(f"\nRoute {chr(65+i)} (theme={r.get('theme')}): {r['route_name']}")
    print(f"   Mô tả: {r.get('route_description') or r.get('description')}")
    print(f"   Điểm: {pts}")
    print(f"   Loại hình: {types}")
    print(f"   Thời gian: {r.get('total_time_minutes')} phút")

# Kiểm tra Route A
route_a = routes[0]
assert route_a.get("theme") == "lively", f"Route A phải có theme là 'lively', nhưng là {route_a.get('theme')}"
assert "bình yên" not in route_a["route_name"].lower(), f"Route A không được có 'bình yên': {route_a['route_name']}"
assert "thong thả" not in route_a["route_name"].lower(), f"Route A không được có 'thong thả': {route_a['route_name']}"

print("\n>>> KIỂM THỬ NÁO NHIỆT THÀNH CÔNG RỰC RỠ! KHÔNG CÒN BỊ ÉP THONG THẢ HAY BÌNH YÊN! <<<")
