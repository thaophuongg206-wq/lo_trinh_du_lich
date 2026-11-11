from fastapi import APIRouter, File, HTTPException, UploadFile
from typing import Optional, List
from pydantic import BaseModel
import io
import numpy as np
import pandas as pd

from api.session_helpers import ItineraryUpdateRequest, RouteSelectRequest, _params_from_request, _request_from_session
from api.schemas import OptimizationRequest
from api.db import fetch_all_dict, fetch_all_points, get_db_connection, V2_PUBLIC_KEYS
from api.generation import run_route_generation
from itinerary_store import store

router = APIRouter()

@router.get("/api/locations")
def get_locations(vehicle_type: str = "xe_may", trip_date: Optional[str] = None):
    """Trả về danh sách toàn bộ địa điểm kèm đầy đủ metadata v2 (giá, giờ mở cửa, hoạt động, review, ảnh)."""
    try:
        points = fetch_all_points(vehicle_type=vehicle_type, trip_date=trip_date)
        locations = []
        for p in points:
            open_t = p.get("open_time")
            close_t = p.get("close_time")
            loc_item = {
                "id": str(p["id"]),
                "ten": p.get("ten", ""),
                "lat": p.get("lat") or p.get("vi_do", 0),
                "lon": p.get("lon") or p.get("kinh_do", 0),
                "loai_hinh": p.get("loai_hinh", ""),
                "rating": p.get("rating") if p.get("rating") is not None else p.get("diem_gia_tri"),
                "diem_gia_tri": p.get("diem_gia_tri"),
                "so_luot_danh_gia": p.get("so_luot_danh_gia"),
                "url_hinh_anh": p.get("url_hinh_anh", ""),
                "review": p.get("review", ""),
                "mo_ta": p.get("mo_ta", ""),
                "thong_tin_chi_tiet": p.get("thong_tin_chi_tiet", ""),
                "phu_hop": p.get("phu_hop", ""),
                "gio_mo_cua": open_t.strftime("%H:%M") if hasattr(open_t, "strftime") else str(open_t or "00:00")[:5],
                "gio_dong_cua": close_t.strftime("%H:%M") if hasattr(close_t, "strftime") else str(close_t or "23:59")[:5],
                "visit_time": p.get("time") or p.get("thoi_gian_tham_quan_phut") or 45,
                **{k: p.get(k) for k in V2_PUBLIC_KEYS if k != "rating"},
            }
            locations.append(loc_item)
        return {"status": "success", "data": locations}
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
    if result["routes"]:
        session.select_route(result["routes"][0]["route_id"])
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


class ChatMessageRecordRequest(BaseModel):
    session_id: str
    role: str
    content: str
    options: Optional[List[dict]] = None
    target_place_id: Optional[str] = None
    target_place_name: Optional[str] = None
    meta: Optional[dict] = None


@router.get("/api/chat-history")
async def get_chat_history(session_id: str):
    """Lấy toàn bộ lịch sử trò chuyện multi-turn của phiên."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    return {
        "status": "success",
        "session_id": session.session_id,
        "chat_history": session.get_chat_history(),
    }


@router.post("/api/chat-history")
async def record_chat_message(req: ChatMessageRecordRequest):
    """Ghi nhận tin nhắn mới vào lịch sử chat của phiên."""
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    msg = session.add_chat_message(
        role=req.role,
        content=req.content,
        options=req.options,
        target_place_id=req.target_place_id,
        target_place_name=req.target_place_name,
        meta=req.meta,
    )
    return {"status": "success", "message": msg}


@router.delete("/api/chat-history")
async def delete_chat_history(session_id: str):
    """Xóa toàn bộ lịch sử trò chuyện trong phiên."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    session.clear_chat_history()
    return {"status": "success", "session_id": session.session_id, "message": "Đã xóa lịch sử trò chuyện."}


class UserProfileUpdateRequest(BaseModel):
    user_id: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    default_preference: Optional[str] = None
    avatar_url: Optional[str] = None


class UserFavoriteToggleRequest(BaseModel):
    user_id: Optional[str] = None
    place_id: str


class UserLoginRequest(BaseModel):
    email: str
    password: Optional[str] = None


@router.get("/api/user/profile")
async def get_user_profile(user_id: Optional[str] = None):
    """Lấy thông tin tài khoản và số lượng địa điểm yêu thích."""
    uid = (user_id or "").strip()
    if not uid or uid.lower() in ("guest", "null", "undefined", "anonymous"):
        return {"status": "unauthenticated", "data": None}
    from api.user_store import user_store
    return {"status": "success", "data": user_store.get_user(uid)}


@router.post("/api/user/profile")
async def update_user_profile(req: UserProfileUpdateRequest):
    """Cập nhật thông tin tài khoản và gu du lịch mặc định."""
    uid = (req.user_id or "").strip()
    if not uid or uid.lower() in ("guest", "null", "undefined", "anonymous"):
        raise HTTPException(status_code=401, detail="Vui lòng đăng nhập để cập nhật hồ sơ.")
    from api.user_store import user_store
    updated = user_store.update_profile(uid, req.model_dump(exclude_unset=True))
    return {"status": "success", "data": updated}


@router.get("/api/user/favorites")
async def get_user_favorites(user_id: Optional[str] = None):
    """Lấy danh sách chi tiết các địa điểm đã lưu yêu thích của người dùng."""
    uid = (user_id or "").strip()
    if not uid or uid.lower() in ("guest", "null", "undefined", "anonymous"):
        return {"status": "unauthenticated", "user_id": None, "favorites_count": 0, "data": []}
    from api.user_store import user_store
    fav_ids = set(user_store.get_favorites(uid))
    all_pts = fetch_all_points()
    fav_places = []
    for p in all_pts:
        if str(p["id"]) in fav_ids:
            open_t = p.get("open_time")
            close_t = p.get("close_time")
            fav_places.append({
                "id": str(p["id"]),
                "ten": p.get("ten", ""),
                "lat": p.get("lat") or p.get("vi_do", 0),
                "lon": p.get("lon") or p.get("kinh_do", 0),
                "loai_hinh": p.get("loai_hinh", ""),
                "rating": p.get("rating") if p.get("rating") is not None else p.get("diem_gia_tri"),
                "so_luot_danh_gia": p.get("so_luot_danh_gia"),
                "url_hinh_anh": p.get("url_hinh_anh", ""),
                "dia_chi": p.get("dia_chi", ""),
                "mo_ta": p.get("mo_ta", ""),
                "gio_mo_cua": open_t.strftime("%H:%M") if hasattr(open_t, "strftime") else str(open_t or "00:00")[:5],
                "gio_dong_cua": close_t.strftime("%H:%M") if hasattr(close_t, "strftime") else str(close_t or "23:59")[:5],
            })
    return {"status": "success", "user_id": uid, "favorites_count": len(fav_places), "data": fav_places}


@router.post("/api/user/favorites/toggle")
async def toggle_user_favorite(req: UserFavoriteToggleRequest):
    """Thêm hoặc gỡ một địa điểm khỏi danh sách yêu thích. Bắt buộc đăng nhập."""
    uid = (req.user_id or "").strip()
    if not uid or uid.lower() in ("guest", "null", "undefined", "anonymous"):
        raise HTTPException(
            status_code=401,
            detail="Vui lòng đăng nhập để lưu địa điểm yêu thích vào tài khoản của bạn."
        )
    from api.user_store import user_store
    is_fav, fav_ids = user_store.toggle_favorite(uid, req.place_id)
    return {
        "status": "success",
        "user_id": uid,
        "place_id": str(req.place_id),
        "is_favorite": is_fav,
        "favorites": fav_ids,
        "favorites_count": len(fav_ids),
    }


@router.post("/api/user/login")
async def user_login(req: UserLoginRequest):
    """Đăng nhập hoặc chuyển tài khoản người dùng đơn giản."""
    from api.user_store import user_store
    user_id = req.email.strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="Vui lòng nhập email hoặc tên đăng nhập.")
    profile = user_store.get_user(user_id)
    return {"status": "success", "message": "Đăng nhập thành công", "data": profile}


@router.post("/api/user/logout")
async def user_logout():
    """Đăng xuất người dùng."""
    return {"status": "success", "message": "Đã đăng xuất thành công."}


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
