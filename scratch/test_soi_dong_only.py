import sys
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from api.schemas import OptimizationRequest
from api.generation import run_route_generation

req = OptimizationRequest(
    region='hanoi',
    start_point='Đền Ngọc Sơn (Hồ Hoàn Kiếm)',
    start_time='08:00',
    end_time='16:06',
    vehicle_type='xe_may',
    trip_date='2026-10-23',
    user_preference='sôi động',
    weight=80
)
res = run_route_generation(req)
print('status:', res.get('status'))
if res.get('status') == 'success':
    routes = res.get('routes', [])
    print(f'Sinh thành công {len(routes)} lộ trình!')
    for i, r in enumerate(routes):
        pts = [p['ten'] for p in r['optimized_route']]
        theme = r.get('theme')
        name = r['route_name']
        print(f'Route {chr(65+i)} ({theme}): {name}')
        print(f'   Điểm ({len(pts)}): {pts}')
        print(f'   Thời gian: {r.get("total_time_minutes")} phút')
else:
    print('Error message:', res.get('message'))
