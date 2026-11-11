"""
crawl_serpapi_maps.py
-----------------------
Script cao du lieu "sach" tu Google Maps (thong qua SerpApi) cho danh sach
dia diem trong du an Du Lich Thong Minh (lo_trinh_du_lich).

Khac voi ban dung Google Places API truc tiep, script nay goi qua SerpApi
(https://serpapi.com) - mot dich vu trung gian da "cao ho" du lieu that
tu Google Maps. Uu diem: KHONG can tao tai khoan Google Cloud, KHONG can
the thanh toan, chi can dang ky email tai serpapi.com la co key dung ngay.

Voi moi dia diem, script lay:
  - Rating that (diem trung binh + tong so luot danh gia)
  - Toi da REVIEWS_PER_PLACE review moi nhat (noi dung + nguoi viet + thoi gian + so sao)
  - Toi da PHOTOS_PER_PLACE link anh net (tu Google Maps Photos)

Luong goi API cho moi dia diem:
  1. engine=google_maps         -> tim dia diem theo ten, lay data_id + rating
  2. engine=google_maps_reviews -> dung data_id de lay danh sach review (co phan trang)
  3. engine=google_maps_photos  -> dung data_id de lay danh sach anh

Cai dat thu vien:
    pip install requests

Cau hinh:
    Dang ky mien phi tai https://serpapi.com/users/sign_up (khong can the)
    Lay API key tai https://serpapi.com/manage-api-key
    Dat key vao bien moi truong (Windows PowerShell):
        setx SERPAPI_KEY "your_key_here"
    hoac sua truc tiep bien API_KEY ben duoi (khong khuyen khich khi commit len GitHub).

Luu y quota:
    - Goi mien phi: 250 luot tim/thang.
    - Moi dia diem ton: 1 luot (maps) + ~1-2 luot (reviews, co the can them trang de du 10)
      + 1 luot (photos) => ~3-4 luot/dia diem => 30 dia diem ~ 100-120 luot,
      van con du trong han muc mien phi.
    - Script co sleep(SLEEP_SECONDS) giua cac lan goi de tranh rate limit.
"""

import os
import csv
import json
import time
import requests

# ----------------------- CAU HINH -----------------------
API_KEY = os.environ.get("SERPAPI_KEY", "DAN_API_KEY_SERPAPI_CUA_BAN_VAO_DAY")

SERPAPI_URL = "https://serpapi.com/search.json"
SLEEP_SECONDS = 1.0
REVIEWS_PER_PLACE = 10
PHOTOS_PER_PLACE = 5
INPUT_CSV = "locations_input.csv"
OUTPUT_JSON = "places_output.json"
OUTPUT_CSV = "places_output.csv"


def search_place(ten_dia_diem: str, dia_chi: str = "") -> dict | None:
    """Buoc 1: Tim dia diem tren Google Maps, lay data_id + rating co ban."""
    query = f"{ten_dia_diem} {dia_chi}".strip()
    params = {
        "engine": "google_maps",
        "q": query,
        "hl": "vi",
        "type": "search",
        "api_key": API_KEY,
    }
    resp = requests.get(SERPAPI_URL, params=params, timeout=20)
    data = resp.json()

    if "error" in data:
        print(f"  [!] Loi SerpApi khi tim '{query}': {data['error']}")
        return None

    # Truong hop Google tra ve thang 1 dia diem duy nhat (place_results)
    if data.get("place_results"):
        return data["place_results"]

    # Truong hop Google tra ve danh sach nhieu ket qua (local_results) -> lay ket qua dau
    local_results = data.get("local_results") or []
    if local_results:
        return local_results[0]

    print(f"  [!] Khong tim thay dia diem cho: {query}")
    return None


def get_reviews(data_id: str, limit: int = REVIEWS_PER_PLACE) -> list[dict]:
    """Buoc 2: Lay toi da `limit` review moi nhat, tu dong phan trang neu can."""
    reviews_out = []
    if not data_id:
        return reviews_out

    params = {
        "engine": "google_maps_reviews",
        "data_id": data_id,
        "hl": "vi",
        "sort_by": "newestFirst",
        "api_key": API_KEY,
    }

    next_page_token = None
    while len(reviews_out) < limit:
        if next_page_token:
            params["next_page_token"] = next_page_token

        resp = requests.get(SERPAPI_URL, params=params, timeout=20)
        data = resp.json()

        if "error" in data:
            print(f"  [!] Loi khi lay review (data_id={data_id}): {data['error']}")
            break

        page_reviews = data.get("reviews") or []
        if not page_reviews:
            break

        for r in page_reviews:
            reviews_out.append(
                {
                    "noi_dung": r.get("snippet"),
                    "nguoi_viet": (r.get("user") or {}).get("name"),
                    "so_sao": r.get("rating"),
                    "thoi_gian": r.get("date"),
                }
            )
            if len(reviews_out) >= limit:
                break

        next_page_token = (data.get("serpapi_pagination") or {}).get("next_page_token")
        if not next_page_token:
            break

        time.sleep(SLEEP_SECONDS)

    return reviews_out[:limit]


def get_photos(data_id: str, limit: int = PHOTOS_PER_PLACE) -> list[str]:
    """Buoc 3: Lay toi da `limit` link anh net tu Google Maps Photos."""
    photos_out = []
    if not data_id:
        return photos_out

    params = {
        "engine": "google_maps_photos",
        "data_id": data_id,
        "hl": "vi",
        "api_key": API_KEY,
    }
    resp = requests.get(SERPAPI_URL, params=params, timeout=20)
    data = resp.json()

    if "error" in data:
        print(f"  [!] Loi khi lay anh (data_id={data_id}): {data['error']}")
        return photos_out

    photos = data.get("photos") or []
    for p in photos[:limit]:
        url = p.get("image") or p.get("thumbnail")
        if url:
            photos_out.append(url)

    return photos_out


def crawl_one_location(ten_dia_diem: str, dia_chi: str = "") -> dict:
    """Cao du lieu sach cho 1 dia diem, tra ve dict chuan hoa."""
    print(f"-> Dang xu ly: {ten_dia_diem}")
    result = {
        "ten_dia_diem": ten_dia_diem,
        "dia_chi_goc": dia_chi,
        "rating": None,
        "so_luot_danh_gia": None,
        "reviews": [],   # danh sach toi da REVIEWS_PER_PLACE review moi nhat
        "anh": [],       # danh sach toi da PHOTOS_PER_PLACE link anh
    }

    place = search_place(ten_dia_diem, dia_chi)
    if not place:
        return result

    data_id = place.get("data_id")
    result["rating"] = place.get("rating")
    result["so_luot_danh_gia"] = place.get("reviews")

    time.sleep(SLEEP_SECONDS)
    result["reviews"] = get_reviews(data_id, REVIEWS_PER_PLACE)

    time.sleep(SLEEP_SECONDS)
    result["anh"] = get_photos(data_id, PHOTOS_PER_PLACE)

    return result


def load_locations_from_csv(path: str) -> list[dict]:
    """Doc danh sach dia diem tu file CSV (ten_dia_diem, dia_chi)."""
    locations = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            locations.append(
                {
                    "ten_dia_diem": row.get("ten_dia_diem", "").strip(),
                    "dia_chi": row.get("dia_chi", "").strip(),
                }
            )
    return locations


def save_results(results: list[dict]) -> None:
    # File JSON: giu nguyen cau truc long nhau (danh sach review/anh) -> giau nhat cho AI doc
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # File CSV: de nhap vao Excel/SQL Server, cac gia tri list duoc noi bang " ||| "
    flat_rows = []
    for item in results:
        review_texts = [
            f"{r.get('noi_dung') or ''} (⭐{r.get('so_sao')}, {r.get('nguoi_viet')}, {r.get('thoi_gian')})"
            for r in item["reviews"]
        ]
        flat_rows.append(
            {
                "ten_dia_diem": item["ten_dia_diem"],
                "dia_chi_goc": item["dia_chi_goc"],
                "rating": item["rating"],
                "so_luot_danh_gia": item["so_luot_danh_gia"],
                "so_review_lay_duoc": len(item["reviews"]),
                "reviews": " ||| ".join(review_texts),
                "so_anh_lay_duoc": len(item["anh"]),
                "anh": " ||| ".join(item["anh"]),
            }
        )

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(flat_rows[0].keys()))
        writer.writeheader()
        writer.writerows(flat_rows)

    print(f"\nDa luu {len(results)} dia diem vao:")
    print(f"  - {OUTPUT_JSON}  (day du, dang long nhau - nap cho AI hoc/phan tich)")
    print(f"  - {OUTPUT_CSV}   (dang phang, de xem Excel / import lai DB)")


def main():
    if API_KEY.startswith("DAN_API_KEY"):
        print("[!] Chua cau hinh SERPAPI_KEY. Dat bien moi truong truoc khi chay.")
        print("    setx SERPAPI_KEY \"your_key_here\"  (roi mo lai terminal)")
        return

    if not os.path.exists(INPUT_CSV):
        print(f"[!] Khong thay file {INPUT_CSV}. Hay tao file nay voi 2 cot: ten_dia_diem, dia_chi")
        return

    locations = load_locations_from_csv(INPUT_CSV)
    if not locations:
        print("[!] Khong co dia diem nao de xu ly.")
        return

    print(f"Tong so dia diem can xu ly: {len(locations)}")
    print(f"Moi dia diem se lay toi da {REVIEWS_PER_PLACE} review va {PHOTOS_PER_PLACE} anh.")

    results = []
    for loc in locations:
        item = crawl_one_location(loc["ten_dia_diem"], loc.get("dia_chi", ""))
        results.append(item)
        time.sleep(SLEEP_SECONDS)

    save_results(results)


if __name__ == "__main__":
    main()
