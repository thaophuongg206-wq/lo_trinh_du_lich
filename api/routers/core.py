"""Production endpoints used by the web frontend."""
from fastapi import File, HTTPException, UploadFile
from api.session_helpers import ItineraryUpdateRequest, RouteSelectRequest, _params_from_request, _request_from_session
from api.schemas import OptimizationRequest
from typing import Optional
from api.db import fetch_all_dict, fetch_all_points, get_db_connection
import io
import numpy as np
import pandas as pd
from api.generation import run_route_generation
from itinerary_store import store

from fastapi import APIRouter
router = APIRouter()

@router.get("/api/locations")
def get_locations():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        # Đã lấy thêm url_hinh_anh và diem_gia_tri để Frontend hiển thị
        cursor.execute("SELECT id, ten, vi_do, kinh_do, loai_hinh, diem_gia_tri, url_hinh_anh, review FROM DIA_DIEM")
        rows = fetch_all_dict(cursor, db_type)
        locations = [{
            "id": str(r["id"]), "ten": r["ten"], "lat": r["vi_do"], "lon": r["kinh_do"], 
            "loai_hinh": r["loai_hinh"], "rating": r["diem_gia_tri"], 
            "url_hinh_anh": r["url_hinh_anh"], "review": r["review"]
        } for r in rows]
        conn.close()
        return {"status": "success", "data": locations, "db_engine": db_type}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
    """
    ENDPOINT CŨ — GIỮ NGUYÊN HÀNH VI cho app.js hiện tại (mục 10: "tận dụng API
    hiện tại nếu có", mục 13: không sửa frontend ngoài phạm vi cần thiết).

    Khác biệt duy nhất: nếu request có session_id thì nó tôn trọng excluded_ids
    của phiên — nghĩa là luồng chatbot "Bỏ A rồi thêm quán ăn trưa" của frontend
    hiện tại cũng được sửa lỗi mà KHÔNG cần đổi một dòng nào ở app.js.
    """
    session = store.get_or_create(request.session_id, _params_from_request(request))
    session.exclude(request.excluded_ids or [])

    result = run_route_generation(
        request,
        excluded_ids=session.excluded_ids,
        must_visit_ids=session.pinned_ids,
        mode="multi",
    )
    result["session_id"] = session.session_id
    if result.get("status") == "success":
        session.set_routes(result["routes"])
        # Route đầu tiên là cái Screen 3 hiển thị mặc định → cũng là itinerary hiện tại.
        session.select_route(result["routes"][0]["route_id"])
    return result


@router.post("/api/routes")
async def create_routes(request: OptimizationRequest):
    """
    SCREEN 1 → BACKEND → 3–5 ROUTE HOÀN CHỈNH (mục 2).

    Trả về route ĐÃ HOÀN CHỈNH (places + timeline + tổng thời gian) để Screen 2
    render thẳng, không còn cảnh Screen 2 nhận một rổ địa điểm rồi Screen 3 mới
    tự tách route.
    """
    session = store.get_or_create(request.session_id, _params_from_request(request))
    session.exclude(request.excluded_ids or [])

    result = run_route_generation(
        request,
        excluded_ids=session.excluded_ids,
        must_visit_ids=session.pinned_ids,
        mode="multi",
    )
    if result.get("status") != "success":
        result["session_id"] = session.session_id
        return result

    session.set_routes(result["routes"])
    result["session_id"] = session.session_id
    return result


@router.get("/api/routes")
async def list_routes(session_id: str):
    """Lấy lại đúng tập route đã sinh. KHÔNG sinh lại (mục 10)."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    return {
        "status": "success",
        "session_id": session.session_id,
        "routes": session.list_routes(),
        "selected_route_id": session.selected_route_id,
        "excluded_ids": sorted(session.excluded_ids),
    }


@router.post("/api/routes/select")
async def select_route(req: RouteSelectRequest):
    """
    SCREEN 2 CHỌN ROUTE → SCREEN 3 (mục 10).

    Chọn route_3 thì trả về ĐÚNG route_3 đã lưu — không chạy lại thuật toán,
    nên Screen 3 không bao giờ hiển thị một lộ trình khác với cái người dùng
    vừa nhìn thấy ở Screen 2.
    """
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    route = session.select_route(req.route_id)
    if route is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy {req.route_id} trong phiên này. Các route hiện có: {list(session.route_order)}",
        )
    return {
        "status": "success",
        "session_id": session.session_id,
        "route_id": route["route_id"],
        "itinerary": route,
        "excluded_ids": sorted(session.excluded_ids),
        "routes": [route],
    }


@router.get("/api/itinerary")
async def get_itinerary(session_id: str):
    """
    SOURCE OF TRUTH (mục 11): map data, timeline, route summary, AI context đều
    lấy từ đây nên không thể xảy ra cảnh Map = route A còn Timeline = route B.
    """
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    itinerary = session.current_itinerary
    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "itinerary": itinerary,
        "map_data": [
            {
                "id": p["id"], "ten": p["ten"], "lat": p["lat"], "lon": p["lon"],
                "loai_hinh": p.get("loai_hinh", ""), "arrive_time": p.get("arrive_time"),
                "depart_time": p.get("depart_time"),
            }
            for p in (itinerary or {}).get("places", [])
        ],
        "timeline": (itinerary or {}).get("timeline", []),
        "summary": {
            "name": (itinerary or {}).get("name"),
            "theme": (itinerary or {}).get("theme"),
            "total_duration": (itinerary or {}).get("total_duration"),
            "travel_time": (itinerary or {}).get("travel_time"),
            "visit_time": (itinerary or {}).get("visit_time"),
            "place_count": (itinerary or {}).get("place_count"),
        },
        "ai_context_ids": session.current_place_ids(),
        "excluded_ids": sorted(session.excluded_ids),
        "pinned_ids": sorted(session.pinned_ids),
    }


@router.post("/api/itinerary/update")
async def update_itinerary(req: ItineraryUpdateRequest):
    """
    XOÁ ĐIỂM / THÊM ĐIỂM RỒI TÍNH LẠI (mục 8, 9).

    A → B → C → D → E, xoá C thì kết quả là A → B → D → E (hoặc A → B → X → D → E
    nếu Greedy fill-up được điểm mới) — nhưng C không bao giờ quay lại, kể cả ở
    những lần cập nhật sau như "thêm quán ăn trưa", vì C nằm trong excluded_ids
    của phiên chứ không phải chỉ bị bỏ khỏi một mảng tạm.
    """
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")

    session.restore(req.restore_ids or [])
    newly_excluded = session.exclude(req.remove_ids or [])

    # Giữ lại các điểm đang có trong itinerary (trừ điểm vừa xoá) làm must-visit.
    forced = {str(i) for i in (req.add_ids or [])} - session.excluded_ids
    keep = set(session.current_place_ids()) - session.excluded_ids
    keep |= forced
    session.pinned_ids = keep

    opt_request = _request_from_session(session)
    result = run_route_generation(
        opt_request,
        excluded_ids=session.excluded_ids,
        must_visit_ids=keep,
        force_visit_ids=forced,
        mode="single",
    )
    if result.get("status") != "success":
        result["session_id"] = session.session_id
        result["excluded_ids"] = sorted(session.excluded_ids)
        return result

    session.set_current_itinerary(result["itinerary"])
    result.update({
        "session_id": session.session_id,
        "removed_ids": newly_excluded,
        "excluded_ids": sorted(session.excluded_ids),
        "selected_route_id": session.selected_route_id,
    })
    return result


@router.get("/api/session")
async def get_session_state(session_id: str):
    """Trạng thái thô của phiên — hữu ích cho debug và cho test tự động."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    return {"status": "success", **session.snapshot()}


@router.post("/api/admin/import-excel")
async def import_excel_tool(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        # 1. Hỗ trợ đọc file CSV (từ tool cào) và Excel
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents), encoding='utf-8-sig').replace({np.nan: None})
        else:
            df = pd.read_excel(io.BytesIO(contents)).replace({np.nan: None})
        
        # 2. Kết nối tự động vào đúng SQL Server
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        
        updated_count = 0
        for index, row in df.iterrows():
            # Lấy tên địa điểm (Hỗ trợ cả header 'ten_dia_diem' hoặc 'ten')
            ten = str(row.get('ten_dia_diem', row.get('ten', ''))).strip()
            if not ten or ten == 'None':
                continue
            
            # Chỉ lấy link ảnh đầu tiên nếu có nhiều ảnh
            anh_raw = row.get('anh', row.get('url_hinh_anh'))
            url_hinh_anh = str(anh_raw).split(" ||| ")[0] if anh_raw and str(anh_raw) not in ['None', 'nan', ''] else None
            
            diem_gia_tri = row.get('rating', row.get('diem_gia_tri'))
            diem_gia_tri = diem_gia_tri if not pd.isna(diem_gia_tri) and str(diem_gia_tri) != 'None' else None

            # Cập nhật vào DB
            cursor.execute("SELECT id FROM DIA_DIEM WHERE ten = ?", (ten,))
            if cursor.fetchone() and url_hinh_anh:
                cursor.execute("""
                    UPDATE DIA_DIEM 
                    SET url_hinh_anh=?, diem_gia_tri=ISNULL(?, diem_gia_tri)
                    WHERE ten=?
                """, (url_hinh_anh, diem_gia_tri, ten))
                updated_count += 1
                
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Hoàn tất! Đã cập nhật ảnh và rating cho {updated_count} địa điểm."}
    except Exception as e:
        return {"status": "error", "message": f"Lỗi đọc file: {str(e)}"}

@router.get("/api/places/suggest")
async def places_suggest(q: str, limit: int = 5, lat: Optional[float] = None, lon: Optional[float] = None):
    """Search-box suggestions: local POIs first, then Nominatim (rate-limited + cached, see services/nominatim.py)."""
    from services.nominatim import get_nominatim
    return get_nominatim().suggest(q, limit=limit, lat=lat, lon=lon, local_points=fetch_all_points())
