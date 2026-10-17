$(document).ready(function () {
    const API_BASE = (function () {
        if (window.location.protocol === 'file:') return 'http://127.0.0.1:8000';
        if (window.location.port === '8000') return '';
        const host = window.location.hostname ? window.location.hostname : '127.0.0.1';
        return `${window.location.protocol}//${host}:8000`;
    })();

    // Khởi tạo bản đồ nền sáng (CartoDB Voyager Light)
    const map = L.map('map', { zoomControl: true }).setView([21.0285, 105.8542], 13);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO'
    }).addTo(map);

    let mapMarkers = [], polylineRoutes = [];
    let isAiUpdating = false;
    let cachedAllLocations = null;
    let dragSrcIndex = null;

    // Bảng màu riêng biệt cho từng chặng di chuyển (1->2, 2->3, 3->4...)
    const LEG_COLORS = [
        '#3978D4', // Chặng 1 -> 2: Primary Blue
        '#0D9488', // Chặng 2 -> 3: Teal Natural
        '#F59E0B', // Chặng 3 -> 4: Warm Amber
        '#8B5CF6', // Chặng 4 -> 5: Soft Indigo
        '#EC4899', // Chặng 5 -> 6: Rose Coral
        '#10B981', // Chặng 6 -> 7: Emerald Green
    ];

    // Đọc danh sách địa điểm yêu thích (Favorite) từ localStorage
    let favoritePlaceIds = new Set();
    try {
        const savedFavs = JSON.parse(localStorage.getItem('teravue_fav_places') ? localStorage.getItem('teravue_fav_places') : '[]');
        if (Array.isArray(savedFavs)) favoritePlaceIds = new Set(savedFavs.map(String));
    } catch (e) {
        favoritePlaceIds = new Set();
    }

    function saveFavorites() {
        try {
            localStorage.setItem('teravue_fav_places', JSON.stringify(Array.from(favoritePlaceIds)));
        } catch (e) {}
    }

    // Đọc danh sách địa điểm ĐÃ ĐI (On-Trip Visited) từ localStorage
    let visitedPlaceIds = new Set();
    try {
        const savedVisited = JSON.parse(localStorage.getItem('teravue_visited_places') ? localStorage.getItem('teravue_visited_places') : '[]');
        if (Array.isArray(savedVisited)) visitedPlaceIds = new Set(savedVisited.map(String));
    } catch (e) {
        visitedPlaceIds = new Set();
    }

    function saveVisitedPlaces() {
        try {
            localStorage.setItem('teravue_visited_places', JSON.stringify(Array.from(visitedPlaceIds)));
        } catch (e) {}
    }

    // ============================================================
    // STATE MANAGEMENT
    // ============================================================
    const tripState = {
        currentLocationData: null,
        sessionId: null,
        startFingerprint: null,
        baseParams: null,
        aiSuggestedIds: [],
        currentRoutes: null,
        selectedRouteId: null,
        activeRouteIndex: 0,
        currentItinerary: null,
    };

    const DEFAULT_PLACE_IMG = 'https://images.unsplash.com/photo-1555921015-5532091f6026?auto=format&fit=crop&w=600&q=80';

    const VEHICLE_NAMES = {
        xe_may: 'Xe máy',
        o_to: 'Ô tô cá nhân',
        xe_dap: 'Xe đạp',
        di_bo: 'Đi bộ',
        xe_16_cho: 'Xe 16 chỗ',
        xe_29_cho: 'Xe 29 chỗ',
    };

    const CATEGORY_ESTIMATED_COST = {
        'Cafe': 55000,
        'Ăn uống': 85000,
        'Tham quan': 40000,
        'TTTM': 120000,
        'Checkin': 0,
        'diem_xuat_phat': 0,
    };

    const CATEGORY_ACTIVITY_LABEL = {
        'Cafe': 'Thưởng thức cà phê & thư giãn không gian quán',
        'Ăn uống': 'Thưởng thức ẩm thực địa phương & nạp năng lượng',
        'Tham quan': 'Tham quan di tích, tìm hiểu văn hóa & lịch sử',
        'TTTM': 'Vui chơi giải trí, mua sắm & xem phim trong nhà',
        'Checkin': 'Dạo phố, ngắm cảnh & chụp ảnh check-in',
    };

    function firstDefined() {
        for (let i = 0; i < arguments.length; i++) {
            const val = arguments[i];
            if (val !== undefined && val !== null && val !== '') {
                return val;
            }
        }
        return '';
    }

    // ============================================================
    // DỮ LIỆU ĐỊA ĐIỂM TỪ DB v2: giá, giờ cả tuần, rating, thẻ, ảnh, đặc điểm rút từ review
    // ============================================================
    function escHtml(s) {
        return String(s === undefined || s === null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function toNumOrNull(v) {
        if (v === null || v === undefined || v === '') return null;
        const n = Number(v);
        return isNaN(n) ? null : n;
    }
    function placePriceBounds(place) {
        return { lo: toNumOrNull(place && place.gia_tu), hi: toNumOrNull(place && place.gia_den) };
    }
    // Giá hiển thị: "30.000 – 100.000 đ", "Dưới 100.000 đ", "Miễn phí"
    function formatPlacePrice(place) {
        const pb = placePriceBounds(place);
        if (pb.lo === null && pb.hi === null) return formatCurrencyVND(estimatePlaceCost(place));
        if (pb.lo === 0 && pb.hi === 0) return 'Miễn phí';
        const f = (n) => n.toLocaleString('vi-VN');
        if (pb.lo !== null && pb.hi !== null) return pb.lo === pb.hi ? `${f(pb.hi)} đ` : `${f(pb.lo)} – ${f(pb.hi)} đ`;
        return pb.hi !== null ? `Dưới ${f(pb.hi)} đ` : `Từ ${f(pb.lo)} đ`;
    }
    function priceSourceNote(place) {
        const g = String((place && place.gia_ghi_chu) || '');
        if (/uoc luong|ước lượng/i.test(g)) return 'Giá ước lượng theo loại địa điểm';
        if (/google/i.test(g)) return 'Theo Google Maps';
        return '';
    }
    // Giờ mở cửa cả tuần, gộp ngày liên tiếp có giờ giống nhau: "T2–T5: 10:00–22:00 · T6–CN: 10:00–23:00"
    function weekHoursNote(place) {
        const days = Array.isArray(place && place.gio_tuan) ? place.gio_tuan : null;
        if (!days || days.length !== 7) return '';
        const short = ['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'];
        const txt = (d) => d.nghi ? 'Nghỉ' : `${d.mo}–${d.dong}`;
        const groups = [];
        days.forEach((d, i) => {
            const last = groups[groups.length - 1];
            if (last && last.txt === txt(d)) last.end = i; else groups.push({ start: i, end: i, txt: txt(d) });
        });
        if (groups.length === 1) return groups[0].txt === 'Nghỉ' ? '' : 'Mở cửa tất cả các ngày trong tuần';
        return groups.map(g => `${g.start === g.end ? short[g.start] : short[g.start] + '–' + short[g.end]}: ${g.txt}`).join(' · ');
    }
    function placeTagGroups(place) {
        const the = Array.isArray(place && place.the) ? place.the : [];
        const names = (nhom) => the.filter(t => t && t.nhom === nhom).map(t => t.ten);
        const space = { trong_nha: 'Trong nhà', ngoai_troi: 'Ngoài trời', ca_hai: 'Trong nhà & ngoài trời' }[place && place.trong_nha_ngoai_troi] || '';
        return { hoatDong: names('hoat_dong'), phongCach: names('phong_cach'), space };
    }
    function getPlaceRatingCount(place) {
        const n = toNumOrNull(place && place.so_luot_danh_gia);
        return n && n > 0 ? `${n.toLocaleString('vi-VN')} đánh giá` : '';
    }
    // Review nổi bật: ưu tiên trích dẫn đã chọn từ review thật (có số sao); không có thì cắt ngắn review cũ
    function reviewSnippet(place) {
        const ins = (place && place.review_insight) || {};
        const q = Array.isArray(ins.trich_dan) && ins.trich_dan.length ? ins.trich_dan[0] : null;
        if (q && q.noi_dung) return (q.sao ? `★${q.sao} · ` : '') + q.noi_dung;
        const r = String(firstDefined(place && place.review, place && place.thong_tin_chi_tiet)).trim();
        return r.length > 220 ? r.slice(0, 220).replace(/\s+\S*$/, '') + '…' : r;
    }
    // Chip "khách hay khen" / "lưu ý" / hoạt động, dùng ở Screen 2
    function buildInsightChipsHtml(place) {
        const ins = (place && place.review_insight) || {};
        const tg = placeTagGroups(place);
        const chip = (cls, t) => `<span class="pd-chip ${cls}">${escHtml(t)}</span>`;
        const parts = [];
        if (tg.space) parts.push(chip('pd-chip-space', tg.space));
        tg.hoatDong.slice(0, 4).forEach(t => parts.push(chip('pd-chip-act', t)));
        (ins.noi_bat || []).slice(0, 3).forEach(t => parts.push(chip('pd-chip-good', '👍 ' + t)));
        (ins.luu_y || []).slice(0, 2).forEach(t => parts.push(chip('pd-chip-warn', '⚠ ' + t)));
        return parts.length ? `<div class="pd-chip-row">${parts.join('')}</div>` : '';
    }

    function estimatePlaceCost(place) {
        if (!place) return 0;
        if (typeof place.chi_phi === 'number') return place.chi_phi;
        if (typeof place.cost === 'number') return place.cost;
        const pb = placePriceBounds(place);                       // giá từ DB v2 (gia_tu / gia_den)
        if (pb.lo !== null || pb.hi !== null) {
            if (pb.lo !== null && pb.hi !== null) return Math.round((pb.lo + pb.hi) / 2);
            return pb.hi !== null ? pb.hi : pb.lo;
        }
        const text = `${firstDefined(place.thong_tin_chi_tiet)} ${firstDefined(place.review)}`;
        const matchK = text.match(/(\d{2,3})\s*k\b/i);
        if (matchK) return parseInt(matchK[1], 10) * 1000;
        if (place.loai_hinh in CATEGORY_ESTIMATED_COST) {
            return CATEGORY_ESTIMATED_COST[place.loai_hinh];
        }
        return 45000;
    }

    function estimateRouteCost(places) {
        if (!Array.isArray(places)) return 0;
        return places.reduce((sum, p) => {
            if (p.loai_hinh === 'diem_xuat_phat' || p.id === 'gps_current') return sum;
            return sum + estimatePlaceCost(p);
        }, 0);
    }

    function formatCurrencyVND(amount) {
        if (!amount || amount <= 0) return 'Miễn phí';
        return amount.toLocaleString('vi-VN') + ' đ';
    }

    function computeRouteDistanceKm(route) {
        if (typeof route.distance_km === 'number' && route.distance_km > 0) {
            return route.distance_km.toFixed(1);
        }
        const places = Array.isArray(route.places) ? route.places : (Array.isArray(route.optimized_route) ? route.optimized_route : []);
        const total = places.reduce((acc, p) => acc + (parseFloat(p.distance_to_next) ? parseFloat(p.distance_to_next) : 0), 0);
        return total.toFixed(1);
    }

    function formatOpeningHours(place) {
        const formatTimeField = (val) => {
            if (!val) return null;
            const s = String(val);
            return s.length >= 5 ? s.slice(0, 5) : s;
        };
        const o = formatTimeField(firstDefined(place.open_time, place.gio_mo_cua));
        const c = formatTimeField(firstDefined(place.close_time, place.gio_dong_cua));
        if (o && c) return `${o} – ${c}`;
        return 'Chưa có dữ liệu giờ mở cửa';
    }

    function getPlaceRating(place) {
        const val = firstDefined(place.rating, place.google_rating, place.diem_danh_gia, place.score);
        if (val === '') return '—';
        const num = parseFloat(val);
        if (isNaN(num)) return String(val);
        return num > 5 ? (num / 2).toFixed(1) : num.toFixed(1);
    }

    // ============================================================
    // SINH MÔ TẢ TRẢI NGHIỆM DỰA TRÊN DỮ LIỆU & NGỮ CẢNH THỰC TẾ
    // ============================================================
    function buildContextualExperience(place) {
        const arr = firstDefined(place.arrive_time, '09:00');
        const hour = parseInt(arr.split(':')[0], 10) ? parseInt(arr.split(':')[0], 10) : 9;
        const cat = firstDefined(place.loai_hinh, 'Tham quan');
        const phuHop = String(firstDefined(place.phu_hop)).trim().replace(/\.$/, '');
        const detail = String(firstDefined(place.thong_tin_chi_tiet)).trim().replace(/^-\s*/, '');

        let timeContext = '';
        if (hour < 9) {
            if (cat === 'Ăn uống') timeContext = 'Khung giờ sáng sớm rất thích hợp để thưởng thức bữa sáng nóng hổi và nạp năng lượng đầu ngày.';
            else if (cat === 'Cafe') timeContext = 'Buổi sáng sớm tiết trời mát mẻ, yên tĩnh, lý tưởng để nhâm nhi cà phê mở đầu ngày mới.';
            else timeContext = 'Thời điểm sáng sớm không khí trong lành, vắng người, rất thuận lợi cho hoạt động tham quan ngoài trời.';
        } else if (hour < 11) {
            if (cat === 'Tham quan' || cat === 'Checkin') timeContext = 'Khung giờ buổi sáng có ánh sáng tự nhiên đẹp và chưa nắng gắt, rất phù hợp để dạo bước tham quan và chụp ảnh.';
            else timeContext = 'Khoảng thời gian giữa buổi sáng nhịp độ thư thả, thích hợp để dừng chân nghỉ ngơi và trải nghiệm không gian.';
        } else if (hour < 14) {
            if (cat === 'Ăn uống') timeContext = 'Đúng khung giờ nghỉ trưa, đây là điểm dừng hợp lý để dùng bữa trưa và nạp lại năng lượng.';
            else if (cat === 'Cafe' || cat === 'TTTM') timeContext = 'Thời điểm giữa trưa nắng ấm, ghé không gian có mái che/điều hòa để nghỉ chân và thư giãn là lựa chọn tối ưu.';
            else timeContext = 'Khung giờ trưa lượng khách thường giảm bớt, thuận tiện để bạn thong thả khám phá không gian tại đây.';
        } else if (hour < 17) {
            if (cat === 'Cafe') timeContext = 'Buổi chiều là khoảng lặng lý tưởng để dừng chân thưởng thức đồ uống, trò chuyện và nghỉ ngơi sau chặng di chuyển.';
            else if (cat === 'Tham quan' || cat === 'Checkin') timeContext = 'Thời điểm chiều nắng đã dịu dần, rất phù hợp cho hoạt động dạo phố, vãn cảnh và ghi lại những góc hình đẹp.';
            else timeContext = 'Khung giờ buổi chiều thuận lợi để kết hợp trải nghiệm văn hóa, ẩm thực và thư giãn nhẹ nhàng.';
        } else {
            if (cat === 'Ăn uống') timeContext = 'Khung giờ chiều tối mát mẻ, rất hợp để thưởng thức bữa tối đậm vị và cảm nhận nhịp sống Hà Nội khi lên đèn.';
            else if (cat === 'Checkin' || cat === 'Tham quan') timeContext = 'Thời điểm hoàng hôn và tối phố xá lên đèn rực rỡ, rất lý tưởng cho hoạt động dạo bộ ngoài trời và ngắm cảnh.';
            else timeContext = 'Buổi tối không khí thoáng đãng, phù hợp để thư giãn và khép lại hành trình trong ngày.';
        }

        const dataNote = phuHop
            ? `Theo dữ liệu điểm đến, nơi này đặc biệt phù hợp cho: ${phuHop.toLowerCase()}.`
            : (detail ? `Đặc điểm nổi bật: ${detail}` : '');

        return `<strong>${arr} – ${place.ten}:</strong> ${timeContext} ${dataNote}`.trim();
    }

    // ============================================================
    // PLACE DETAIL MODAL (Ảnh, Rating, Review, Giờ mở cửa, Giá, Hoạt động, Favorite)
    // ============================================================
    function openPlaceDetailModal(place) {
        if (!place) return;
        const pid = String(firstDefined(place.id));
        const imgUrl = firstDefined(place.url_hinh_anh, DEFAULT_PLACE_IMG);
        const catLabel = formatCategoryLabel(place.loai_hinh);
        const ratingStr = getPlaceRating(place);
        const openHoursStr = formatOpeningHours(place);
        const priceStr = formatPlacePrice(place);
        const slotStr = place.arrive_time
            ? `${place.arrive_time} – ${firstDefined(place.depart_time, '--:--')} (${firstDefined(place.visit_time, 60)}p)`
            : `Khoảng ${firstDefined(place.visit_time, place.time, 60)} phút`;
        const activityStr = firstDefined(place.phu_hop, CATEGORY_ACTIVITY_LABEL[place.loai_hinh], 'Tham quan, khám phá & trải nghiệm không gian');
        const addressStr = firstDefined(place.dia_chi, place.mo_ta, 'Hà Nội, Việt Nam');
        const reviewStr = reviewSnippet(place) || 'Địa điểm được nhiều du khách yêu thích tại Hà Nội.';
        const tg = placeTagGroups(place);
        const ins = place.review_insight || {};

        $('#pd-image').attr('src', imgUrl).attr('alt', place.ten).off('error').on('error', function () {
            $(this).attr('src', DEFAULT_PLACE_IMG);
        });
        $('#pd-category').text(catLabel);
        const cntStr = getPlaceRatingCount(place);
        $('#pd-rating').text(`★ ${ratingStr}${cntStr ? ' · ' + cntStr : ''}`);
        $('#pd-title').text(place.ten);
        $('#pd-address span').text(addressStr);
        $('#pd-hours').text(openHoursStr);
        $('#pd-hours-note').text(weekHoursNote(place));
        $('#pd-price').text(priceStr);
        $('#pd-price-note').text(priceSourceNote(place));
        $('#pd-slot').text(slotStr);
        $('#pd-activity').text(tg.hoatDong.length ? tg.hoatDong.join(' · ') : activityStr);
        $('#pd-tags').html(buildInsightChipsHtml(place));
        // Review: chip khách hay khen / lưu ý + độ tích cực + review tiêu biểu
        const sentiment = (ins.so_review && ins.diem_cam_xuc !== null && ins.diem_cam_xuc !== undefined)
            ? `Phân tích từ ${ins.so_review} review thật · cảm xúc tích cực ${Math.round(ins.diem_cam_xuc * 100)}%` : '';
        $('#pd-insight').text(sentiment);
        // Thư viện ảnh
        const gallery = Array.from(new Set([imgUrl].concat(Array.isArray(place.anh) ? place.anh : []).filter(Boolean)));
        const $gal = $('#pd-gallery').empty();
        if (gallery.length > 1) {
            gallery.slice(0, 6).forEach((u, i) => {
                $('<img class="pd-thumb' + (i === 0 ? ' active' : '') + '" loading="lazy" referrerpolicy="no-referrer">').attr('src', u).attr('alt', place.ten)
                    .on('error', function () { $(this).remove(); })
                    .on('click', function () {
                        $('#pd-image').attr('src', u);
                        $gal.find('.pd-thumb').removeClass('active'); $(this).addClass('active');
                    }).appendTo($gal);
            });
        }
        $('#pd-context-note').html(buildContextualExperience(place));
        $('#pd-review').text(`“${reviewStr}”`);

        const updateModalFavBtn = () => {
            const isFav = favoritePlaceIds.has(pid);
            $('#pd-fav-btn')
                .toggleClass('active', isFav)
                .html(isFav ? '❤️ Đã yêu thích' : '❤️ Yêu thích');
        };
        updateModalFavBtn();

        $('#pd-fav-btn').off('click').on('click', function () {
            if (favoritePlaceIds.has(pid)) {
                favoritePlaceIds.delete(pid);
            } else {
                favoritePlaceIds.add(pid);
            }
            saveFavorites();
            updateModalFavBtn();
            refreshCurrentItinerary();
        });

        const modalEl = document.getElementById('placeDetailModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }
    }

    function getActiveItinerary() {
        if (tripState.currentItinerary) return tripState.currentItinerary;
        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
            const found = tripState.currentRoutes.routes.find(r => r.route_id === tripState.selectedRouteId);
            return found ? found : tripState.currentRoutes.routes[0];
        }
        return null;
    }

    function setCurrentItinerary(itinerary) {
        if (!itinerary) return;
        tripState.currentItinerary = itinerary;

        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
            const index = tripState.currentRoutes.routes.findIndex(
                r => r.route_id === itinerary.route_id
            );
            if (index >= 0) {
                tripState.activeRouteIndex = index;
                tripState.currentRoutes.routes[index] = itinerary;
            } else if (tripState.currentRoutes.routes[tripState.activeRouteIndex]) {
                tripState.currentRoutes.routes[tripState.activeRouteIndex] = itinerary;
            }
        }
        tripState.selectedRouteId = firstDefined(itinerary.route_id, tripState.selectedRouteId);
    }

    function refreshCurrentItinerary() {
        const itinerary = getActiveItinerary();
        if (!itinerary) return;

        const vehicleType = firstDefined(tripState.baseParams && tripState.baseParams.vehicle_type, $('#vehicle_type').val());
        const availableMinutes = firstDefined(tripState.currentRoutes && tripState.currentRoutes.available_minutes, itinerary.available_minutes, 0);

        renderItinerarySummary(itinerary);
        renderTimelineAndMap(itinerary, vehicleType, availableMinutes);
    }

    function showCrudBanner(msg, isWarning = false) {
        const $banner = $('#crud-feedback-banner');
        if (!$banner.length) return;
        $banner
            .removeClass('d-none')
            .css({
                backgroundColor: isWarning ? 'var(--status-warning-bg)' : 'var(--status-success-bg)',
                color: isWarning ? 'var(--status-warning)' : 'var(--status-success)',
                borderColor: isWarning ? '#F6E0B5' : '#B7E4CE'
            })
            .html(`<i class="fa-solid ${isWarning ? 'fa-triangle-exclamation' : 'fa-circle-check'} me-1"></i> ${msg}`);
        clearTimeout($banner.data('timer'));
        const timer = setTimeout(() => $banner.addClass('d-none'), 4500);
        $banner.data('timer', timer);
    }

    // Khởi tạo ngày mặc định là hôm nay
    const today = new Date(new Date().getTime() - (new Date().getTimezoneOffset() * 60000))
        .toISOString().split('T')[0];
    $('#trip_date').val(today).attr('min', today);

    if (new URLSearchParams(window.location.search).get('admin') === '1') {
        $('.admin-trigger').removeClass('d-none');
    }

    const CATEGORY_ICON = {
        "Cafe":           { icon: "fa-mug-saucer",   color: "#3978D4" },
        "Ăn uống":        { icon: "fa-utensils",     color: "#23845B" },
        "Tham quan":      { icon: "fa-landmark",     color: "#3978D4" },
        "TTTM":           { icon: "fa-film",         color: "#2F68BD" },
        "Checkin":        { icon: "fa-camera",       color: "#3978D4" },
        "diem_xuat_phat": { icon: "fa-flag",         color: "#26364B" },
    };

    function categoryStyle(loaiHinh) {
        return CATEGORY_ICON[loaiHinh] ? CATEGORY_ICON[loaiHinh] : { icon: "fa-location-dot", color: "#3978D4" };
    }

    function formatCategoryLabel(loaiHinh) {
        if (!loaiHinh) return 'Điểm đến';
        if (loaiHinh === 'TTTM') return 'Rạp phim & TTTM';
        return loaiHinh;
    }

    function vehicleSentence(vType, distKm, mins) {
        const distStr = (Math.round((distKm ? distKm : 0) * 10) / 10).toString().replace('.', ',');
        const minsStr = Math.round(mins ? mins : 0);
        const vLabel = (VEHICLE_NAMES[vType] ? VEHICLE_NAMES[vType] : 'Xe máy').toLowerCase();
        return `Di chuyển ${distStr} km bằng ${vLabel} · khoảng ${minsStr} phút`;
    }

    function durationLabel(mins) {
        mins = Math.round(mins ? mins : 0);
        const h = Math.floor(mins / 60), m = mins % 60;
        if (h <= 0) return `${m} phút`;
        return m > 0 ? `${h}h${m < 10 ? '0' + m : m}` : `${h}h`;
    }

    // ============================================================
    // TOP NAVBAR, SIDEBAR & HIỂN THỊ GLOBAL CHATBOT WIDGET
    // ============================================================
    function setActiveNavTab(screenKey) {
        $('.navbar-center .nav-item-link').removeClass('active').removeAttr('aria-current');
        $(`.navbar-center .nav-item-link[data-screen="${screenKey}"]`)
            .addClass('active')
            .attr('aria-current', 'page');

        // Hiển thị Chatbot ở cả Screen 2 (chat) và Screen 3 (map), ẩn ở Screen 1 (hero)
        if (screenKey === 'chat' || screenKey === 'map') {
            $('#global-ai-assistant').removeClass('d-none');
        } else {
            $('#global-ai-assistant').addClass('d-none');
            $('#ai-map-chat').removeClass('open');
        }
    }

    $('#btn-toggle-sidebar').on('click', function () {
        $('#chat-history-sidebar').toggleClass('collapsed');
        setTimeout(() => map.invalidateSize(), 250);
    });

    $('#btn-new-trip, #nav-logo').on('click', function (e) {
        e.preventDefault();
        $('#chat-section, #map-section').hide();
        $('#hero-section').fadeIn(180);
        setActiveNavTab('hero');
    });

    $('.navbar-center .nav-item-link[data-screen]').on('click', function (e) {
        e.preventDefault();
        const target = $(this).data('screen');
        if (target === 'hero') {
            $('#chat-section, #map-section').hide();
            $('#hero-section').fadeIn(180);
            setActiveNavTab('hero');
        } else if (target === 'chat') {
            if (!tripState.currentRoutes) {
                alert('Hãy nhập mong muốn chuyến đi ở màn hình đầu tiên trước nhé!');
                return;
            }
            $('#hero-section, #map-section').hide();
            $('#chat-section').fadeIn(180);
            setActiveNavTab('chat');
        } else if (target === 'map') {
            if (!tripState.currentRoutes) {
                alert('Hãy tạo và chọn một lộ trình trước khi mở bản đồ nhé!');
                return;
            }
            enterMapScreen(tripState.selectedRouteId);
            setActiveNavTab('map');
        }
    });

    function addHistoryEntry(prefText, startTime, endTime, vType) {
        const $list = $('#chat-history-list');
        if (!$list.length) return;
        $list.find('.history-item').removeClass('active');
        const shortTitle = prefText.length > 28 ? prefText.slice(0, 28) + '...' : prefText;
        const vName = VEHICLE_NAMES[vType] ? VEHICLE_NAMES[vType] : 'Xe máy';
        const $item = $(`
            <li class="history-item active">
                <i class="fa-solid fa-route mt-1"></i>
                <div class="history-item-text">
                    <div class="history-title">${shortTitle}</div>
                    <div class="history-meta">${startTime} - ${endTime} · ${vName}</div>
                </div>
            </li>
        `);
        $item.on('click', function () {
            $list.find('.history-item').removeClass('active');
            $(this).addClass('active');
            if (tripState.currentRoutes) {
                $('#hero-section, #map-section').hide();
                $('#chat-section').fadeIn(180);
                setActiveNavTab('chat');
            }
        });
        $list.prepend($item);
    }

    // ============================================================
    // LIVE "AI ĐÃ HIỂU" PREVIEW TRÊN SCREEN 1
    // ============================================================
    function updateLiveUnderstoodBar(customSummary) {
        const pref = $('#user_preference').val().trim();
        const sTime = firstDefined($('#start_time').val(), '08:00');
        const eTime = firstDefined($('#end_time').val(), '20:00');
        const vType = firstDefined($('#vehicle_type').val(), 'xe_may');
        const sPoint = $('#start_point').val().trim();
        const ePoint = $('#end_point').val().trim();

        if (!pref && !customSummary) {
            $('#ai-understood-badge').addClass('d-none');
            return;
        }

        if (customSummary) {
            $('#ai-understood-text').text(customSummary);
            $('#ai-understood-badge').removeClass('d-none');
            return;
        }

        const parts = [`${sTime}–${eTime}`, firstDefined(VEHICLE_NAMES[vType], vType)];
        if (sPoint) parts.push(`Từ: ${sPoint}`);
        if (ePoint) parts.push(`Đến: ${ePoint}`);
        if (pref) {
            const cleanPref = pref.length > 42 ? pref.slice(0, 42) + '...' : pref;
            parts.push(cleanPref);
        }
        $('#ai-understood-text').text(parts.join(' | '));
        $('#ai-understood-badge').removeClass('d-none');
    }

    $('#user_preference, #start_time, #end_time, #vehicle_type, #start_point, #end_point').on('input change', function () {
        updateLiveUnderstoodBar();
    });

    // ============================================================
    // SCREEN 1 → INPUT & GPS
    // ============================================================
    $('#btn-use-location').click(function (e) {
        if (e && e.preventDefault) e.preventDefault();
        const $status = $('#location-status');
        if (!navigator.geolocation) return $status.html('<span class="text-danger">Trình duyệt không hỗ trợ GPS</span>');
        $status.html('<span class="text-secondary"><i class="fa-solid fa-spinner fa-spin"></i> Đang lấy tọa độ GPS...</span>');
        navigator.geolocation.getCurrentPosition(
            pos => {
                tripState.currentLocationData = { lat: pos.coords.latitude, lon: pos.coords.longitude };
                $('#start_point').val(`Vị trí GPS hiện tại`);
                $status.html('<span class="text-success fw-semibold"><i class="fa-solid fa-check"></i> Đã định vị GPS</span>');
                updateLiveUnderstoodBar();
            },
            () => $status.html('<span class="text-danger">Không lấy được GPS</span>'),
            { timeout: 8000, enableHighAccuracy: true }
        );
    });

    $('#start_point').on('input', function () {
        const val = $(this).val().trim();
        if (!val.includes('GPS')) {
            tripState.currentLocationData = null;
            $('#location-status').empty();
        }
    });

    function parseCoordinates(text) {
        if (!text) return null;
        const m = text.trim().match(/^(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)$/);
        if (!m) return null;
        const lat = parseFloat(m[1]);
        const lon = parseFloat(m[2]);
        if (lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
            return { lat, lon };
        }
        return null;
    }

    function resolveStartAndRun(onReady) {
        const startPointText = $('#start_point').val().trim();
        const isGpsText = startPointText.includes('GPS');
        const usingGPS = !!tripState.currentLocationData;
        const fingerprint = usingGPS
            ? `gps:${tripState.currentLocationData.lat},${tripState.currentLocationData.lon}`
            : `text:${startPointText}`;

        if (tripState.baseParams && tripState.startFingerprint === fingerprint && tripState.baseParams.start_lat != null) {
            onReady({
                start_lat: tripState.baseParams.start_lat,
                start_lon: tripState.baseParams.start_lon,
                start_point: tripState.baseParams.start_point,
            });
            return;
        }

        if (usingGPS) {
            tripState.startFingerprint = fingerprint;
            onReady({
                start_lat: tripState.currentLocationData.lat,
                start_lon: tripState.currentLocationData.lon,
                start_point: ''
            });
            return;
        }

        if (isGpsText && navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                pos => {
                    tripState.currentLocationData = { lat: pos.coords.latitude, lon: pos.coords.longitude };
                    tripState.startFingerprint = `gps:${pos.coords.latitude},${pos.coords.longitude}`;
                    onReady({ start_lat: pos.coords.latitude, start_lon: pos.coords.longitude, start_point: '' });
                },
                () => {
                    tripState.startFingerprint = fingerprint;
                    onReady({ start_lat: 21.0285, start_lon: 105.8542, start_point: 'Hồ Hoàn Kiếm, Hà Nội' });
                },
                { timeout: 4000 }
            );
            return;
        }

        if (!startPointText) {
            tripState.startFingerprint = fingerprint;
            onReady({ start_lat: 21.0285, start_lon: 105.8542, start_point: 'Hồ Hoàn Kiếm, Hà Nội' });
            return;
        }

        const coords = parseCoordinates(startPointText);
        if (coords) {
            tripState.startFingerprint = `coords:${coords.lat},${coords.lon}`;
            onReady({ start_lat: coords.lat, start_lon: coords.lon, start_point: '' });
            return;
        }

        const query = (startPointText.includes('Việt Nam') || startPointText.includes('Vietnam'))
            ? startPointText
            : startPointText + ', Hà Nội, Việt Nam';

        $.get('https://nominatim.openstreetmap.org/search', { q: query, format: 'json', limit: 1 })
            .done(res => {
                tripState.startFingerprint = fingerprint;
                if (res && res.length > 0) {
                    onReady({
                        start_lat: parseFloat(res[0].lat),
                        start_lon: parseFloat(res[0].lon),
                        start_point: startPointText
                    });
                } else {
                    onReady({ start_lat: 21.0285, start_lon: 105.8542, start_point: startPointText });
                }
            })
            .fail(() => {
                tripState.startFingerprint = fingerprint;
                onReady({ start_lat: 21.0285, start_lon: 105.8542, start_point: startPointText });
            });
    }

    function applyAdvice(res) {
        tripState.sessionId = firstDefined(res.session_id, tripState.sessionId);
        tripState.aiSuggestedIds = Array.isArray(res.suggested_ids) ? res.suggested_ids : [];
        $('#ai-advice-content').html(String(firstDefined(res.advice_text)).replace(/\n/g, '<br>'));
        renderQuickActions();
    }

    // ============================================================
    // SCREEN 2: ROUTE CARDS + TIMELINE CHI TIẾT + BẢNG SO SÁNH
    // ============================================================
    function fetchRoutes(onComplete) {
        const payload = Object.assign({}, tripState.baseParams, {
            session_id: tripState.sessionId,
            ai_selected_ids: tripState.aiSuggestedIds,
            num_routes: 5,
        });
        $.ajax({
            url: `${API_BASE}/api/routes`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(payload),
            timeout: 30000,
            success: function (res) {
                if (res.status !== 'success' || !res.routes || res.routes.length === 0) {
                    alert(firstDefined(res.message, 'Không tạo được lộ trình phù hợp, hãy thử mô tả khác.'));
                    return;
                }
                tripState.sessionId = firstDefined(res.session_id, tripState.sessionId);
                tripState.currentRoutes = res;
                const kept = res.routes.find(r => r.route_id === tripState.selectedRouteId);
                const defaultRoute = kept ? kept : res.routes[0];
                tripState.selectedRouteId = defaultRoute ? defaultRoute.route_id : null;
                if (defaultRoute) tripState.currentItinerary = defaultRoute;

                renderRouteCards(res.routes);
                renderComparisonTable(res.routes);

                $('#hero-section, #map-section').hide();
                $('#chat-section').fadeIn(180);
                setActiveNavTab('chat');
            },
            error: (xhr) => alert("Không tạo được lộ trình: " + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, "Kiểm tra Backend.")),
            complete: () => { if (onComplete) onComplete(); }
        });
    }

    function buildScreen2RouteTimeline(places, vType) {
        if (!Array.isArray(places) || places.length === 0) return '';

        const itemsHtml = places.map((p, idx) => {
            const imgUrl = firstDefined(p.url_hinh_anh, DEFAULT_PLACE_IMG);
            const timeRange = `${firstDefined(p.arrive_time, '--:--')} – ${firstDefined(p.depart_time, '--:--')}`;
            const visitMins = firstDefined(p.visit_time, 60);
            const ratingStr = getPlaceRating(p);
            const costStr = formatPlacePrice(p);
            const openHoursStr = formatOpeningHours(p);
            const _tg = placeTagGroups(p);
            const activityStr = _tg.hoatDong.length ? _tg.hoatDong.join(' · ') : firstDefined(p.phu_hop, CATEGORY_ACTIVITY_LABEL[p.loai_hinh], 'Tham quan & trải nghiệm');
            const addressStr = firstDefined(p.dia_chi, p.mo_ta, 'Hà Nội');
            const reviewStr = reviewSnippet(p);
            const _cnt = getPlaceRatingCount(p);
            const contextExpHtml = buildContextualExperience(p);
            const legColor = LEG_COLORS[idx % LEG_COLORS.length];

            const nextMoveHtml = (idx < places.length - 1 && p.travel_to_next > 0)
                ? `<div class="timeline-travel-connector mt-2">
                       <span class="leg-color-dot" style="background:${legColor};"></span>
                       <span>${vehicleSentence(vType, p.distance_to_next, p.travel_to_next)}</span>
                   </div>`
                : '';

            return `
                <div class="s2-timeline-item" data-place-idx="${idx}" title="Nhấn để xem chi tiết địa điểm">
                    <img src="${imgUrl}" alt="${p.ten}" class="s2-timeline-img" loading="lazy" referrerpolicy="no-referrer" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                    <div class="s2-timeline-content">
                        <div class="s2-timeline-top">
                            <span class="s2-time-badge">
                                <i class="fa-regular fa-clock"></i> ${timeRange} (${visitMins} phút)
                            </span>
                            <span class="s2-rating-badge" title="Điểm đánh giá">
                                ★ ${ratingStr}${_cnt ? ' <small>(' + _cnt + ')</small>' : ''}
                            </span>
                        </div>
                        <h5 class="s2-place-title">${idx + 1}. ${p.ten} <small class="text-secondary fw-normal">(${formatCategoryLabel(p.loai_hinh)})</small></h5>
                        <div class="s2-context-exp">${contextExpHtml}</div>
                        <div class="s2-meta-row">
                            <span class="s2-meta-item"><i class="fa-solid fa-person-hiking text-primary me-1"></i><strong>Hoạt động:</strong> ${activityStr}</span>
                            <span class="s2-meta-item"><i class="fa-solid fa-door-open text-primary me-1"></i><strong>Giờ mở cửa:</strong> ${openHoursStr}</span>
                            <span class="s2-meta-item"><i class="fa-solid fa-tag text-primary me-1"></i><strong>Giá dự kiến:</strong> ${costStr}</span>
                            <span class="s2-meta-item"><i class="fa-solid fa-location-dot text-primary me-1"></i><strong>Địa chỉ:</strong> ${addressStr}</span>
                        </div>
                        ${buildInsightChipsHtml(p)}
                        ${reviewStr ? `<div class="s2-review-quote">💬 Review: “${escHtml(reviewStr)}”</div>` : ''}
                        ${nextMoveHtml}
                    </div>
                </div>
            `;
        }).join('');

        return `
            <div class="route-detail-panel">
                <div class="route-detail-header">
                    <h5 class="route-detail-title"><i class="fa-solid fa-list-check me-2"></i>Chi tiết Timeline & Phân tích trải nghiệm</h5>
                    <span class="small text-secondary">Nhấn vào từng địa điểm để mở Place Detail</span>
                </div>
                <div class="s2-timeline-list">
                    ${itemsHtml}
                </div>
            </div>
        `;
    }

    function selectRouteOnScreen2(routeId) {
        tripState.selectedRouteId = routeId;
        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
            const chosen = tripState.currentRoutes.routes.find(r => r.route_id === routeId);
            if (chosen) tripState.currentItinerary = chosen;
        }

        $('#ai-routes-grid .route-card').each(function () {
            const isMatch = $(this).attr('data-route-id') === routeId;
            $(this)
                .toggleClass('selected', isMatch)
                .attr('aria-selected', isMatch ? 'true' : 'false')
                .find('.route-card-select-indicator')
                .html(isMatch
                    ? '<i class="fa-solid fa-circle-check me-1"></i> Đang xem chi tiết'
                    : 'Nhấn để xem Timeline chi tiết');
        });

        $('#route-comparison-table [data-route-id]').each(function () {
            const isMatch = $(this).attr('data-route-id') === routeId;
            $(this).toggleClass('comp-col-selected', isMatch);
        });

        // Đồng bộ lựa chọn với Backend để nếu user mở Chatbot ngay tại Screen 2 thì sửa đúng route này
        if (tripState.sessionId && routeId) {
            $.ajax({
                url: `${API_BASE}/api/routes/select`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    session_id: tripState.sessionId,
                    route_id: routeId
                }),
            });
        }
    }

    function renderRouteCards(routes) {
        const $grid = $('#ai-routes-grid').empty();
        const vType = firstDefined(tripState.baseParams && tripState.baseParams.vehicle_type, $('#vehicle_type').val());

        routes.forEach((r, idx) => {
            const isSelected = r.route_id === tripState.selectedRouteId;
            const rawPlaces = Array.isArray(r.places) ? r.places : (Array.isArray(r.optimized_route) ? r.optimized_route : []);
            const places = rawPlaces.filter(
                p => p.loai_hinh !== 'diem_xuat_phat' && p.id !== 'gps_current'
            );
            const placeCount = firstDefined(r.place_count, places.length);
            const totalDurationStr = durationLabel(firstDefined(r.total_duration, r.total_time_minutes));
            const distanceKmStr = computeRouteDistanceKm(r);
            const estCostStr = formatCurrencyVND(estimateRouteCost(places));
            const routeLetter = String.fromCharCode(65 + idx);

            let photosHtml = '';
            if (places.length > 0) {
                const photoItems = places.slice(0, 5).map((p, pIdx) => {
                    const imgUrl = firstDefined(p.url_hinh_anh, DEFAULT_PLACE_IMG);
                    const timeBadge = p.arrive_time ? `${p.arrive_time} · ` : '';
                    return `
                        <div class="route-photo-item" data-photo-idx="${pIdx}" title="Nhấn để xem chi tiết ${p.ten}">
                            <img src="${imgUrl}" alt="${firstDefined(p.ten, 'Điểm đến')}" loading="lazy" referrerpolicy="no-referrer" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                            <div class="route-photo-caption">${pIdx + 1}. ${timeBadge}${p.ten}</div>
                        </div>
                    `;
                }).join('');
                photosHtml = `<div class="route-card-photos">${photoItems}</div>`;
            }

            const detailTimelineHtml = buildScreen2RouteTimeline(places, vType);

            const $card = $('<article class="route-card" role="option"></article>')
                .toggleClass('selected', isSelected)
                .attr('aria-selected', isSelected ? 'true' : 'false')
                .attr('data-route-id', r.route_id)
                .html(`
                    <div class="route-card-top">
                        <span class="route-card-theme">Route ${routeLetter} · ${firstDefined(r.theme_label, r.strategy, 'Phương án ' + (idx + 1))}</span>
                        <span class="route-card-select-indicator">
                            ${isSelected ? '<i class="fa-solid fa-circle-check me-1"></i> Đang xem chi tiết' : 'Nhấn để xem Timeline chi tiết'}
                        </span>
                    </div>
                    <h4 class="route-card-title">${firstDefined(r.name, r.route_name, r.route_id)}</h4>
                    <p class="route-card-desc">${firstDefined(r.description)}</p>
                    ${photosHtml}
                    <div class="route-card-metrics-grid">
                        <span class="route-metric-pill">
                            <i class="fa-solid fa-location-dot"></i>
                            <span>${placeCount} điểm đến</span>
                        </span>
                        <span class="route-metric-pill">
                            <i class="fa-regular fa-clock"></i>
                            <span>Tổng: ${totalDurationStr}</span>
                        </span>
                        <span class="route-metric-pill">
                            <i class="fa-solid fa-route"></i>
                            <span>Quãng đường: ${distanceKmStr} km</span>
                        </span>
                        <span class="route-metric-pill">
                            <i class="fa-solid fa-wallet"></i>
                            <span>Chi phí: ~${estCostStr}</span>
                        </span>
                    </div>
                    ${detailTimelineHtml}
                `);

            $card.on('click', function () {
                selectRouteOnScreen2(r.route_id);
            });

            // Click vào ảnh hoặc mục địa điểm trong Timeline ở Screen 2 -> mở Place Detail Modal
            $card.find('.route-photo-item').on('click', function (e) {
                e.stopPropagation();
                selectRouteOnScreen2(r.route_id);
                const pIdx = parseInt($(this).attr('data-photo-idx'), 10);
                if (places[pIdx]) openPlaceDetailModal(places[pIdx]);
            });

            $card.find('.s2-timeline-item').on('click', function (e) {
                e.stopPropagation();
                const pIdx = parseInt($(this).attr('data-place-idx'), 10);
                if (places[pIdx]) openPlaceDetailModal(places[pIdx]);
            });

            $grid.append($card);
        });
    }

    // ============================================================
    // BẢNG SO SÁNH LỘ TRÌNH Ở CUỐI SCREEN 2
    // ============================================================
    function renderComparisonTable(routes) {
        const $table = $('#route-comparison-table').empty();
        if (!Array.isArray(routes) || routes.length === 0) return;

        const routeStats = routes.map((r, idx) => {
            const rawPlaces = Array.isArray(r.places) ? r.places : (Array.isArray(r.optimized_route) ? r.optimized_route : []);
            const places = rawPlaces.filter(
                p => p.loai_hinh !== 'diem_xuat_phat' && p.id !== 'gps_current'
            );
            const totalMins = firstDefined(r.total_duration, r.total_time_minutes, 0);
            const travelMins = r.travel_time ? r.travel_time : places.reduce((acc, p) => acc + (p.travel_to_next ? p.travel_to_next : 0), 0);
            const distKm = computeRouteDistanceKm(r);
            const costStr = formatCurrencyVND(estimateRouteCost(places));

            const cafes = places.filter(p => p.loai_hinh === 'Cafe');
            const cultures = places.filter(p => p.loai_hinh === 'Tham quan' || p.loai_hinh === 'Checkin');
            const avgVisit = places.length > 0
                ? Math.round(places.reduce((acc, p) => acc + (p.visit_time ? p.visit_time : 60), 0) / places.length)
                : 60;

            let relaxLabel = `Vừa phải (TB ${avgVisit}p/điểm)`;
            if (r.theme === 'relax' || avgVisit >= 85 || places.length <= 3) {
                relaxLabel = `Rất thong thả (TB ${avgVisit}p/điểm)`;
            } else if (r.theme === 'time_optimized' || avgVisit <= 50) {
                relaxLabel = `Năng động · Tối ưu (${avgVisit}p/điểm)`;
            }

            return {
                route_id: r.route_id,
                letter: `Route ${String.fromCharCode(65 + idx)}`,
                name: firstDefined(r.name, r.route_name, r.route_id),
                timeText: `${durationLabel(totalMins)} (di chuyển ${durationLabel(travelMins)})`,
                placeCountText: `${places.length} địa điểm`,
                distanceText: `${distKm} km`,
                costText: `~${costStr}`,
                cafeText: cafes.length > 0 ? `${cafes.length} điểm (${cafes.map(c => c.ten).join(', ')})` : 'Không có',
                cultureText: cultures.length > 0 ? `${cultures.length} điểm (${cultures.map(c => c.ten).join(', ')})` : 'Không có',
                relaxText: relaxLabel,
            };
        });

        let theadHtml = '<thead><tr><th>Tiêu chí</th>';
        routeStats.forEach(st => {
            const isSel = st.route_id === tripState.selectedRouteId ? 'comp-col-selected' : '';
            theadHtml += `
                <th class="${isSel}" data-route-id="${st.route_id}">
                    <div class="fw-bold">${st.letter}</div>
                    <div class="small fw-normal text-secondary">${st.name}</div>
                </th>
            `;
        });
        theadHtml += '</tr></thead>';

        const criteriaRows = [
            { label: 'Thời gian', key: 'timeText' },
            { label: 'Số điểm', key: 'placeCountText' },
            { label: 'Quãng đường', key: 'distanceText' },
            { label: 'Chi phí', key: 'costText' },
            { label: 'Cafe', key: 'cafeText' },
            { label: 'Văn hóa', key: 'cultureText' },
            { label: 'Thư giãn', key: 'relaxText' },
        ];

        let tbodyHtml = '<tbody>';
        criteriaRows.forEach(row => {
            tbodyHtml += `<tr><td>${row.label}</td>`;
            routeStats.forEach(st => {
                const isSel = st.route_id === tripState.selectedRouteId ? 'comp-col-selected' : '';
                tbodyHtml += `<td class="${isSel}" data-route-id="${st.route_id}">${st[row.key]}</td>`;
            });
            tbodyHtml += '</tr>';
        });
        tbodyHtml += '</tbody>';

        $table.html(theadHtml + tbodyHtml);

        $table.find('[data-route-id]').on('click', function () {
            const rid = $(this).attr('data-route-id');
            if (rid) selectRouteOnScreen2(rid);
        });
    }

    // ============================================================
    // QUICK ACTIONS (SCREEN 2)
    // ============================================================
    const QUICK_ACTIONS = [
        { label: '+ Thêm quán ăn trưa', action: 'add_lunch', instruction: 'Thêm một quán ăn trưa phù hợp vào lộ trình' },
        { label: '+ Thêm quán cà phê', action: 'add_cafe', instruction: 'Thêm một quán cà phê đẹp vào lộ trình' },
        { label: '− Bớt một điểm', action: 'remove_one', instruction: 'Bớt một điểm để lịch trình thư thả hơn' },
        { label: 'Đi chậm hơn', action: 'slow_down', instruction: 'Tôi muốn đi chậm hơn, bớt điểm dừng' },
    ];

    function renderQuickActions() {
        const $wrap = $('#ai-quick-actions').empty();
        QUICK_ACTIONS.forEach(qa => {
            const $chip = $(`<button type="button" class="quick-action-chip">${qa.label}</button>`);
            $chip.on('click', function () { runQuickAction(qa); });
            $wrap.append($chip);
        });
    }

    function runQuickAction(qa) {
        $('#ai-quick-actions .quick-action-chip').prop('disabled', true);

        const payload = {
            region: 'Hanoi',
            vehicle_type: $('#vehicle_type').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            user_preference: $('#user_preference').val(),
            current_ids: tripState.aiSuggestedIds,
            instruction: firstDefined(qa.instruction, qa.label),
            session_id: tripState.sessionId,
        };

        $.ajax({
            url: `${API_BASE}/api/ai-refine`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(payload),
            success: function (res) {
                tripState.sessionId = firstDefined(res.session_id, tripState.sessionId);
                tripState.aiSuggestedIds = Array.isArray(res.suggested_ids) ? res.suggested_ids : tripState.aiSuggestedIds;
                if (res.advice_text) {
                    $('#ai-advice-content').html(res.advice_text.replace(/\n/g, '<br>'));
                }
                fetchRoutes(() => $('#ai-quick-actions .quick-action-chip').prop('disabled', false));
            },
            error: function (xhr) {
                alert("Không cập nhật được lộ trình: " + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, "Kiểm tra Backend."));
                $('#ai-quick-actions .quick-action-chip').prop('disabled', false);
            }
        });
    }

    // ============================================================
    // SCREEN 1 → SCREEN 2
    // ============================================================
    function proceedWithPlan(startParams, userPref, resetButtons) {
        if (!startParams || (startParams.start_lat == null && startParams.start_lon == null)) {
            startParams = { start_lat: 21.0285, start_lon: 105.8542, start_point: 'Hồ Hoàn Kiếm, Hà Nội' };
        }

        tripState.baseParams = {
            region: 'Hanoi',
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            trip_date: $('#trip_date').val(),
            vehicle_type: $('#vehicle_type').val(),
            user_preference: userPref,
            weight: 50,
            start_lat: startParams.start_lat,
            start_lon: startParams.start_lon,
            start_point: firstDefined(startParams.start_point),
        };

        addHistoryEntry(userPref, tripState.baseParams.start_time, tripState.baseParams.end_time, tripState.baseParams.vehicle_type);

        $.ajax({
            url: `${API_BASE}/api/ai-suggest`,
            method: 'POST',
            contentType: 'application/json',
            timeout: 15000,
            data: JSON.stringify({
                region: 'Hanoi',
                user_preference: userPref,
                vehicle_type: tripState.baseParams.vehicle_type,
                start_time: tripState.baseParams.start_time,
                end_time: tripState.baseParams.end_time,
                trip_date: firstDefined(tripState.baseParams.trip_date),
                start_point: firstDefined(tripState.baseParams.start_point),
                session_id: tripState.sessionId,
            }),
            success: function (res) {
                applyAdvice(res);
                fetchRoutes(resetButtons);
            },
            error: function () {
                fetchRoutes(resetButtons);
            }
        });
    }

    $('#btn-ask-ai, #btn-reject-route').click(function (e) {
        if (e && e.preventDefault) e.preventDefault();
        const userPref = $('#user_preference').val().trim();
        if (!userPref) {
            alert("Hãy nhập mong muốn của bạn!");
            $('#user_preference').focus();
            return;
        }

        const $askBtn = $('#btn-ask-ai');
        const $rejectBtn = $('#btn-reject-route');
        const isReject = $(this).is($rejectBtn);

        $askBtn.add($rejectBtn).prop('disabled', true);
        if (isReject) {
            $rejectBtn.html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang tìm lộ trình mới...');
        } else {
            $askBtn.html('<i class="fa-solid fa-spinner fa-spin me-2"></i> Đang thiết kế hành trình...');
        }

        const resetButtons = () => {
            $askBtn.html('<i class="fa-solid fa-wand-magic-sparkles me-2"></i> Cùng lên kế hoạch nào').prop('disabled', false);
            $rejectBtn.html('<i class="fa-solid fa-rotate-right me-1"></i> Nghĩ lộ trình khác').prop('disabled', false);
        };

        updateLiveUnderstoodBar();

        resolveStartAndRun(function (startParams) {
            proceedWithPlan(startParams, userPref, resetButtons);
        });
    });

    $('#btn-back-hero').click(function () {
        $('#chat-section').hide();
        $('#hero-section').fadeIn(180);
        setActiveNavTab('hero');
    });

    // ============================================================
    // SCREEN 2 → SCREEN 3 (ITINERARY DETAIL & ON-TRIP MAP)
    // ============================================================
    $('#btn-accept-route').click(function () {
        if (!tripState.currentRoutes || !tripState.selectedRouteId) {
            alert('Chưa có lộ trình nào để chốt, vui lòng thử lại.');
            return;
        }
        const $btn = $(this);
        $btn.html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang mở bản đồ...').prop('disabled', true);

        $.ajax({
            url: `${API_BASE}/api/routes/select`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                route_id: tripState.selectedRouteId
            }),
            success: function (res) {
                if (res.status === 'success') {
                    enterMapScreen(res.route_id);
                    setActiveNavTab('map');
                } else {
                    alert(firstDefined(res.detail, 'Không chốt được lộ trình.'));
                }
            },
            error: (xhr) => alert("Lỗi khi chốt lộ trình: " + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, "Kiểm tra Backend")),
            complete: () => $btn.html('<i class="fa-solid fa-check me-1"></i> Chốt lộ trình & Vẽ bản đồ').prop('disabled', false)
        });
    });

    // Cập nhật thanh tiến độ On-Trip (số điểm đã đi / tổng số điểm)
    function updateOnTripProgressUI(routeLocs) {
        const validStops = (routeLocs ? routeLocs : []).filter(p => p.id !== 'gps_current' && p.loai_hinh !== 'diem_xuat_phat');
        const total = validStops.length;
        const doneCount = validStops.filter(p => visitedPlaceIds.has(String(p.id))).length;
        const pct = total > 0 ? Math.round((doneCount / total) * 100) : 0;

        $('#ontrip-progress-count').text(`${doneCount}/${total} đã đi (${pct}%)`);
        $('#ontrip-progress-fill').css('width', `${pct}%`);
        $('#btn-reset-visited').toggleClass('d-none', doneCount === 0);
    }

    $('#btn-reset-visited').on('click', function () {
        visitedPlaceIds.clear();
        saveVisitedPlaces();
        refreshCurrentItinerary();
        showCrudBanner('Đã đặt lại trạng thái On-Trip về chưa đi.');
    });

    function renderItinerarySummary(itinerary) {
        if (!itinerary) return;

        const rawPlaces = Array.isArray(itinerary.optimized_route) ? itinerary.optimized_route : (Array.isArray(itinerary.places) ? itinerary.places : []);
        const places = rawPlaces.filter(
            p => p.loai_hinh !== 'diem_xuat_phat' && p.id !== 'gps_current'
        );
        const startTime = firstDefined(places[0] && places[0].arrive_time, $('#start_time').val(), '08:00');
        const endTime = firstDefined(places[places.length - 1] && places[places.length - 1].depart_time, $('#end_time').val(), '20:00');
        const totalTime = firstDefined(itinerary.total_time_minutes, itinerary.total_duration, 0);
        const travelTime = itinerary.travel_time ? itinerary.travel_time : places.reduce((acc, p) => acc + (p.travel_to_next ? p.travel_to_next : 0), 0);
        const distKm = computeRouteDistanceKm(itinerary);
        const totalCostStr = formatCurrencyVND(estimateRouteCost(places));

        const vehicleType = firstDefined(tripState.baseParams && tripState.baseParams.vehicle_type, $('#vehicle_type').val());
        const vehicleLabel = firstDefined(VEHICLE_NAMES[vehicleType], 'Xe máy');
        const tripDateStr = firstDefined(tripState.baseParams && tripState.baseParams.trip_date, $('#trip_date').val(), 'Hôm nay');

        $('#itinerary-day-date').text(tripDateStr);
        $('#itinerary-day-theme').text(firstDefined(itinerary.theme_label, 'Khám phá & Trải nghiệm Hà Nội'));
        $('#map-day-label').text(`Day 1 — ${firstDefined(itinerary.name, 'Khám phá Hà Nội')}`);

        $('#map-summary-vehicle').text(`Lộ trình ${vehicleLabel}`);
        $('#map-summary-duration').text(`${durationLabel(travelTime)} di chuyển (${distKm} km)`);
        $('.map-transport-toolbar .transport-tool-btn').removeClass('active');
        $(`.map-transport-toolbar .transport-tool-btn[data-vehicle="${vehicleType}"]`).addClass('active');

        $('#metrics-info').html(`
            <h2 class="trip-main-title">${firstDefined(itinerary.name, itinerary.route_id, 'Hành trình khám phá Hà Nội')}</h2>
            <p class="trip-main-desc">${firstDefined(itinerary.description, 'Lịch trình được tối ưu hóa thời gian di chuyển và điểm dừng theo sở thích của bạn.')}</p>
            <div class="trip-meta-badges">
                <span class="trip-meta-chip">
                    <i class="fa-solid fa-location-dot"></i>
                    <span>Hà Nội · ${places.length} điểm</span>
                </span>
                <span class="trip-meta-chip">
                    <i class="fa-regular fa-clock"></i>
                    <span>${startTime} – ${endTime} (${durationLabel(totalTime)})</span>
                </span>
                <span class="trip-meta-chip">
                    <i class="fa-solid fa-motorcycle"></i>
                    <span>${vehicleLabel} · ${distKm} km</span>
                </span>
                <span class="trip-meta-chip">
                    <i class="fa-solid fa-wallet"></i>
                    <span>~${totalCostStr}/người</span>
                </span>
            </div>
        `);
    }

    function enterMapScreen(routeId) {
        const routes = tripState.currentRoutes.routes;
        let idx = routes.findIndex(r => r.route_id === routeId);
        if (idx < 0) idx = 0;

        tripState.activeRouteIndex = idx;
        tripState.selectedRouteId = routes[idx].route_id;

        $('#chat-section, #hero-section').hide();
        $('#map-section').fadeIn(180);
        setActiveNavTab('map');
        setTimeout(() => map.invalidateSize(), 120);

        $('#route-tabs-container').remove();
        let tabsHtml = '<div id="route-tabs-container" class="d-flex gap-2 mb-3 overflow-auto pb-2">';
        routes.forEach((r, i) => {
            const label = firstDefined(r.name, r.strategy, 'Lộ trình ' + (i + 1));
            tabsHtml += `<button type="button" class="btn btn-sm ${i === idx ? 'btn-dark' : 'btn-outline-dark'} fw-semibold text-nowrap route-tab rounded-pill px-3" data-idx="${i}">${label}</button>`;
        });
        tabsHtml += '</div>';
        $('#metrics-info').before(tabsHtml);

        $('.route-tab').click(function () {
            const newIdx = $(this).data('idx');
            $('.route-tab').removeClass('btn-dark').addClass('btn-outline-dark');
            $(this).removeClass('btn-outline-dark').addClass('btn-dark');

            tripState.activeRouteIndex = newIdx;
            tripState.selectedRouteId = routes[newIdx].route_id;

            setCurrentItinerary(routes[newIdx]);
            refreshCurrentItinerary();

            $.ajax({
                url: `${API_BASE}/api/routes/select`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    session_id: tripState.sessionId,
                    route_id: routes[newIdx].route_id
                }),
            });
        });

        setCurrentItinerary(routes[idx]);
        refreshCurrentItinerary();
    }

    $('.map-transport-toolbar .transport-tool-btn').on('click', function () {
        const vType = $(this).data('vehicle');
        if (!vType) return;
        $('.map-transport-toolbar .transport-tool-btn').removeClass('active');
        $(this).addClass('active');
        $('#vehicle_type').val(vType);
        if (tripState.baseParams) tripState.baseParams.vehicle_type = vType;
        refreshCurrentItinerary();
    });

    // ============================================================
    // RENDER TIMELINE TRÊN SCREEN 3:
    // - Checkbox ☑ Đã đi (On-Trip Experience)
    // - Click mở Place Detail
    // - 6 nút CRUD trực tiếp
    // - Mỗi chặng 1 màu riêng biệt + làm mờ chặng đã qua
    // ============================================================
    function renderTimelineAndMap(routeData, vType) {
        const $timeline = $('#timeline-list').empty();
        const routeLocs = Array.isArray(routeData.optimized_route) ? routeData.optimized_route : (Array.isArray(routeData.places) ? routeData.places : []);

        updateOnTripProgressUI(routeLocs);

        routeLocs.forEach((loc, idx) => {
            const pid = String(firstDefined(loc.id));
            const isFav = favoritePlaceIds.has(pid);
            const isVisited = visitedPlaceIds.has(pid);
            const hasNext = idx < routeLocs.length - 1;
            const legColor = LEG_COLORS[idx % LEG_COLORS.length];

            // Chặng đường từ điểm i -> i+1 được đánh dấu đã qua nếu điểm i đã được tích "Đã đi"
            const travelInfo = hasNext && (loc.travel_to_next > 0)
                ? `<div class="timeline-travel-connector ${isVisited ? 'leg-completed' : ''}" data-leg-idx="${idx}">
                       <span class="leg-color-dot" style="background:${legColor};"></span>
                       <span>Chặng ${idx + 1}→${idx + 2}: ${vehicleSentence(vType, loc.distance_to_next, loc.travel_to_next)}</span>
                   </div>`
                : '';

            const imgUrl = firstDefined(loc.url_hinh_anh, DEFAULT_PLACE_IMG);
            const costStr = formatPlacePrice(loc);
            const catLabel = formatCategoryLabel(loc.loai_hinh);
            const openHoursStr = formatOpeningHours(loc);
            const style = categoryStyle(loc.loai_hinh);

            const $li = $(`
                <li class="timeline-step-item ${idx === 0 ? 'active' : ''} ${isVisited ? 'visited' : ''}" draggable="true" data-place-id="${pid}" data-step-idx="${idx}">
                    <div class="timeline-step-number">${isVisited ? '✓' : (idx + 1)}</div>
                    <div class="timeline-place-card">
                        <!-- Hàng trên cùng: Checkbox ☑ Đã đi + Nút mở Place Detail -->
                        <div class="timeline-card-toprow">
                            <label class="visited-checkbox-label ${isVisited ? 'checked' : ''}">
                                <input type="checkbox" class="visited-checkbox-input" ${isVisited ? 'checked' : ''}>
                                <span>${isVisited ? '☑ Đã đi điểm này' : '☐ Đánh dấu đã đi'}</span>
                            </label>
                            <button type="button" class="btn-view-place-detail" title="Xem chi tiết ảnh, review, giờ mở cửa, hoạt động">
                                <i class="fa-regular fa-eye me-1"></i>Chi tiết
                            </button>
                        </div>

                        <!-- Phần thông tin chính (Click vào mở Place Detail + Focus Map) -->
                        <div class="timeline-card-main">
                            <span class="drag-handle" title="⋮⋮ Kéo thả để đổi thứ tự địa điểm">⋮⋮</span>
                            <img src="${imgUrl}" alt="${loc.ten}" class="timeline-place-thumb" loading="lazy" referrerpolicy="no-referrer" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                            <div class="timeline-place-body">
                                <div class="timeline-place-time">${firstDefined(loc.arrive_time, '--:--')} – ${firstDefined(loc.depart_time, '--:--')} (${firstDefined(loc.visit_time, 60)} phút)</div>
                                <h6 class="timeline-place-title">${idx + 1}. ${loc.ten}</h6>
                                <div class="timeline-place-meta">
                                    <span><i class="fa-solid ${style.icon}" style="color:${style.color};"></i> ${catLabel}</span>
                                    <span>· ★ ${getPlaceRating(loc)}${getPlaceRatingCount(loc) ? ' (' + getPlaceRatingCount(loc) + ')' : ''}</span>
                                    <span>· 🕒 ${openHoursStr}</span>
                                </div>
                                <div class="timeline-place-cost">
                                    Chi phí: <strong>${costStr}</strong>
                                </div>
                            </div>
                        </div>

                        <!-- Thanh 6 nút CRUD trực tiếp -->
                        <div class="crud-toolbar">
                            <button type="button" class="crud-btn btn-crud-add" title="＋ Thêm địa điểm mới ngay sau điểm này">
                                ＋ Thêm
                            </button>
                            <button type="button" class="crud-btn btn-crud-replace" title="↔ Thay thế bằng địa điểm tương đương">
                                ↔ Thay thế
                            </button>
                            <button type="button" class="crud-btn btn-crud-time" title="⏱ Đổi thời lượng tham quan tại điểm này">
                                ⏱ Đổi giờ
                            </button>
                            <button type="button" class="crud-btn crud-btn-fav btn-crud-fav ${isFav ? 'active' : ''}" title="❤️ Đánh dấu Yêu thích">
                                ❤️ ${isFav ? 'Đã thích' : 'Yêu thích'}
                            </button>
                            <button type="button" class="crud-btn crud-btn-del btn-crud-remove" title="✕ Xóa địa điểm này khỏi lộ trình">
                                ✕ Xóa
                            </button>
                        </div>
                    </div>
                    ${travelInfo}
                </li>
            `);

            // Sự kiện Checkbox ☑ Đã đi (On-Trip Experience)
            $li.find('.visited-checkbox-input').on('change', function (e) {
                e.stopPropagation();
                const checked = $(this).is(':checked');
                if (checked) {
                    visitedPlaceIds.add(pid);
                } else {
                    visitedPlaceIds.delete(pid);
                }
                saveVisitedPlaces();

                // Cập nhật trạng thái Timeline Item
                $li.toggleClass('visited', checked);
                $li.find('.timeline-step-number').text(checked ? '✓' : (idx + 1));
                $li.find('.visited-checkbox-label')
                    .toggleClass('checked', checked)
                    .find('span')
                    .text(checked ? '☑ Đã đi điểm này' : '☐ Đánh dấu đã đi');
                $li.find('.timeline-travel-connector').toggleClass('leg-completed', checked);

                // Cập nhật ngay Marker và độ mờ của đoạn đường trên Bản đồ mà không cần gọi lại OSRM
                applyOnTripMapStyles(routeLocs);
                updateOnTripProgressUI(routeLocs);
            });

            $li.find('.visited-checkbox-label').on('click', function (e) {
                e.stopPropagation();
            });

            // Click nút "Chi tiết" hoặc click vào thân Card -> Focus Map & Mở Place Detail Modal
            $li.find('.btn-view-place-detail').on('click', function (e) {
                e.stopPropagation();
                openPlaceDetailModal(loc);
            });

            $li.find('.timeline-card-main').on('click', function () {
                $('#timeline-list .timeline-step-item').removeClass('active');
                $li.addClass('active');
                const m = mapMarkers[idx];
                if (m && loc.lat && loc.lon) {
                    map.panTo([loc.lat, loc.lon]);
                    m.openPopup();
                }
                openPlaceDetailModal(loc);
            });

            // 1. Nút ＋ Thêm
            $li.find('.btn-crud-add').on('click', function (e) {
                e.stopPropagation();
                openAddPlaceModal(pid);
            });

            // 2. Nút ↔ Thay thế
            $li.find('.btn-crud-replace').on('click', function (e) {
                e.stopPropagation();
                openReplacePlaceModal(loc);
            });

            // 3. Nút ⏱ Đổi giờ
            $li.find('.btn-crud-time').on('click', function (e) {
                e.stopPropagation();
                openChangeTimeModal(loc);
            });

            // 4. Nút ❤️ Yêu thích
            $li.find('.btn-crud-fav').on('click', function (e) {
                e.stopPropagation();
                if (favoritePlaceIds.has(pid)) {
                    favoritePlaceIds.delete(pid);
                    $(this).removeClass('active').html('❤️ Yêu thích');
                    showCrudBanner(`Đã bỏ "${loc.ten}" khỏi danh sách yêu thích.`);
                } else {
                    favoritePlaceIds.add(pid);
                    $(this).addClass('active').html('❤️ Đã thích');
                    showCrudBanner(`Đã lưu "${loc.ten}" vào danh sách yêu thích ❤️`);
                }
                saveFavorites();
            });

            // 5. Nút ✕ Xóa
            $li.find('.btn-crud-remove').on('click', function (e) {
                e.stopPropagation();
                handleDirectRemovePlace(loc);
            });

            // 6. Kéo thả ⋮⋮ (HTML5 Drag & Drop Reorder)
            $li.on('dragstart', function (e) {
                dragSrcIndex = idx;
                $(this).addClass('dragging');
                if (e.originalEvent && e.originalEvent.dataTransfer) {
                    e.originalEvent.dataTransfer.effectAllowed = 'move';
                    e.originalEvent.dataTransfer.setData('text/plain', String(idx));
                }
            });

            $li.on('dragend', function () {
                $(this).removeClass('dragging');
                $('#timeline-list .timeline-step-item').removeClass('drag-over');
            });

            $li.on('dragover', function (e) {
                e.preventDefault();
                $(this).addClass('drag-over');
            });

            $li.on('dragleave', function () {
                $(this).removeClass('drag-over');
            });

            $li.on('drop', function (e) {
                e.preventDefault();
                $(this).removeClass('drag-over');
                const targetIdx = idx;
                if (dragSrcIndex === null || dragSrcIndex === targetIdx) return;
                handleDirectReorder(dragSrcIndex, targetIdx);
                dragSrcIndex = null;
            });

            $timeline.append($li);
        });

        drawMultiColorMap(routeLocs, vType);
    }

    // ============================================================
    // CẬP NHẬT TRẠNG THÁI MARKER & LÀM MỜ CHẶNG ĐƯỜNG ĐÃ ĐI TRÊN BẢN ĐỒ
    // ============================================================
    function applyOnTripMapStyles(routeLocs) {
        routeLocs.forEach((loc, idx) => {
            const pid = String(firstDefined(loc.id));
            const isVisited = visitedPlaceIds.has(pid);
            const marker = mapMarkers[idx];
            if (marker && typeof marker.setIcon === 'function') {
                const iconHtml = `<div class="clean-map-marker ${isVisited ? 'visited' : ''}">${isVisited ? '✓' : (idx + 1)}</div>`;
                marker.setIcon(L.divIcon({
                    html: iconHtml,
                    className: '',
                    iconSize: [30, 30],
                    iconAnchor: [15, 15]
                }));
            }
        });

        // Làm mờ chặng đường xuất phát từ điểm đã đi
        polylineRoutes.forEach(legObj => {
            if (!legObj || !legObj.layer || typeof legObj.layer.setStyle !== 'function') return;
            const fromLoc = routeLocs[legObj.legIndex];
            const isLegDone = fromLoc ? visitedPlaceIds.has(String(fromLoc.id)) : false;
            legObj.layer.setStyle({
                color: isLegDone ? '#94A3B8' : legObj.color,
                weight: isLegDone ? 4 : 6,
                opacity: isLegDone ? 0.25 : 0.92,
                dashArray: isLegDone ? '6, 8' : null
            });
        });
    }

    function drawMultiColorMap(routeLocs, vType) {
        mapMarkers.forEach(m => map.removeLayer(m));
        polylineRoutes.forEach(p => {
            if (p && p.layer) map.removeLayer(p.layer);
            else if (p) map.removeLayer(p);
        });
        mapMarkers = [];
        polylineRoutes = [];

        routeLocs.forEach((loc, idx) => {
            const pid = String(firstDefined(loc.id));
            const isVisited = visitedPlaceIds.has(pid);
            const iconHtml = `<div class="clean-map-marker ${isVisited ? 'visited' : ''}">${isVisited ? '✓' : (idx + 1)}</div>`;
            const review = String(firstDefined(loc.review)).trim();
            const reviewHtml = review ? `<div class="mt-1 small text-secondary fst-italic">“${review}”</div>` : '';
            const rating = getPlaceRating(loc);

            const popupHtml = `
                <div style="min-width: 205px; padding: 2px;">
                    <div class="fw-bold text-main" style="font-size: 0.92rem;">
                        ${idx + 1}. ${loc.ten} ${isVisited ? '<span class="badge bg-success ms-1">Đã đi</span>' : ''}
                    </div>
                    <div class="small text-secondary mt-1">
                        ${formatCategoryLabel(loc.loai_hinh)} <span class="badge bg-light text-dark border ms-1">★ ${rating}</span>
                    </div>
                    <div class="small fw-semibold mt-1" style="color: #3978D4;">
                        ${firstDefined(loc.arrive_time)} – ${firstDefined(loc.depart_time)} · ${formatCurrencyVND(estimatePlaceCost(loc))}
                    </div>
                    ${reviewHtml}
                </div>
            `;

            const marker = L.marker([loc.lat, loc.lon], {
                icon: L.divIcon({
                    html: iconHtml,
                    className: '',
                    iconSize: [30, 30],
                    iconAnchor: [15, 15]
                })
            }).bindPopup(popupHtml).addTo(map);

            mapMarkers.push(marker);
        });

        if (routeLocs.length > 1) {
            const osrmProfile = vType === 'di_bo' ? 'foot' : (vType === 'xe_dap' ? 'cycling' : 'driving');
            const promises = [];

            for (let i = 0; i < routeLocs.length - 1; i++) {
                const p1 = routeLocs[i], p2 = routeLocs[i + 1];
                const legColor = LEG_COLORS[i % LEG_COLORS.length];
                const isLegDone = visitedPlaceIds.has(String(p1.id));

                const req = $.get(`https://router.project-osrm.org/route/v1/${osrmProfile}/${p1.lon},${p1.lat};${p2.lon},${p2.lat}?overview=full&geometries=geojson`)
                    .then(res => {
                        const poly = L.polyline(res.routes[0].geometry.coordinates.map(c => [c[1], c[0]]), {
                            color: isLegDone ? '#94A3B8' : legColor,
                            weight: isLegDone ? 4 : 6,
                            opacity: isLegDone ? 0.25 : 0.92,
                            dashArray: isLegDone ? '6, 8' : null
                        }).addTo(map);
                        polylineRoutes.push({ legIndex: i, color: legColor, layer: poly });
                    })
                    .catch(() => {
                        const poly = L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], {
                            color: isLegDone ? '#94A3B8' : legColor,
                            weight: 4,
                            opacity: isLegDone ? 0.25 : 0.85,
                            dashArray: '6, 6'
                        }).addTo(map);
                        polylineRoutes.push({ legIndex: i, color: legColor, layer: poly });
                    });
                promises.push(req);
            }
            Promise.all(promises).then(() => {
                if (mapMarkers.length > 0) {
                    map.fitBounds(new L.featureGroup(mapMarkers).getBounds(), { padding: [50, 50] });
                }
            });
        }
    }

    // ============================================================
    // LOGIC XỬ LÝ 6 THAO TÁC CRUD TRỰC TIẾP VỚI BACKEND
    // ============================================================
    function applyDirectItineraryUpdate(res, fallbackMessage) {
        if (res && res.status === 'success' && res.itinerary) {
            setCurrentItinerary(res.itinerary);
            refreshCurrentItinerary();
            if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                renderRouteCards(tripState.currentRoutes.routes);
                renderComparisonTable(tripState.currentRoutes.routes);
            }
            const warnText = (res.warnings && res.warnings.length) ? ` (Lưu ý: ${res.warnings[0]})` : '';
            showCrudBanner(firstDefined(res.message, fallbackMessage, 'Đã cập nhật lịch trình!') + warnText, !!(res.warnings && res.warnings.length));
        } else if (res && res.message) {
            showCrudBanner(res.message, true);
        }
    }

    function handleDirectReorder(fromIdx, toIdx) {
        const itinerary = getActiveItinerary();
        if (!itinerary) return;
        const rawPlaces = Array.isArray(itinerary.optimized_route) ? itinerary.optimized_route : (Array.isArray(itinerary.places) ? itinerary.places : []);
        const places = rawPlaces.slice();
        if (fromIdx < 0 || toIdx < 0 || fromIdx >= places.length || toIdx >= places.length) return;

        const [moved] = places.splice(fromIdx, 1);
        places.splice(toIdx, 0, moved);

        const orderedIds = places.map(p => String(p.id)).filter(id => id !== 'gps_current');

        $.ajax({
            url: `${API_BASE}/api/itinerary/reorder`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                ordered_place_ids: orderedIds
            }),
            success: function (res) {
                applyDirectItineraryUpdate(res, 'Đã sắp xếp lại thứ tự các điểm đến.');
            },
            error: function () {
                itinerary.optimized_route = places;
                itinerary.places = places;
                refreshCurrentItinerary();
                showCrudBanner('Đã đổi thứ tự địa điểm trên lịch trình.');
            }
        });
    }

    function handleDirectRemovePlace(loc) {
        const itinerary = getActiveItinerary();
        if (!itinerary) return;
        const rawPlaces = Array.isArray(itinerary.optimized_route) ? itinerary.optimized_route : (Array.isArray(itinerary.places) ? itinerary.places : []);
        const places = rawPlaces.filter(p => p.id !== 'gps_current');
        if (places.length <= 2) {
            showCrudBanner('Lộ trình cần tối thiểu 2 địa điểm, không thể xóa thêm.', true);
            return;
        }

        $.ajax({
            url: `${API_BASE}/api/itinerary/remove`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                place_id: String(loc.id),
                exclude_from_session: true
            }),
            success: function (res) {
                applyDirectItineraryUpdate(res, `Đã xóa "${loc.ten}" khỏi lộ trình.`);
            },
            error: function (xhr) {
                showCrudBanner(firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'Không thể xóa địa điểm này.'), true);
            }
        });
    }

    function openReplacePlaceModal(loc) {
        $('#replace-old-place-id').val(String(loc.id));
        $('#replace-old-place-name').text(loc.ten);
        const $list =$('#replace-candidates-list').html(
            '<div class="text-center py-4 text-secondary"><i class="fa-solid fa-spinner fa-spin me-2"></i>Đang tìm địa điểm thay thế phù hợp nhất...</div>'
        );

        const modalEl = document.getElementById('replacePlaceModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }

        $.ajax({
            url: `${API_BASE}/api/itinerary/replacement-candidates`,
            method: 'GET',
            data: {
                session_id: tripState.sessionId,
                place_id: String(loc.id),
                limit: 6
            },
            success: function (res) {
                const cands = Array.isArray(res.candidates) ? res.candidates : [];
                if (cands.length === 0) {
                    $list.html('<div class="text-center py-3 text-secondary">Không tìm thấy địa điểm thay thế nào khác phù hợp trong khung giờ này.</div>');
                    return;
                }
                $list.empty();
                cands.forEach(c => {
                    const img = firstDefined(c.url_hinh_anh, DEFAULT_PLACE_IMG);
                    const rating = getPlaceRating(c);
                    const openBadge = c.open_time && c.close_time ? `${c.open_time}–${c.close_time}` : 'Đang mở cửa';
                    const distVal = c.distance_km !== undefined && c.distance_km !== null ? c.distance_km : 1.2;
                    const $row =$(`
                        <div class="crud-candidate-card">
                            <div class="d-flex align-items-center gap-3">
                                <img src="${img}" alt="${c.ten}" class="crud-cand-img" referrerpolicy="no-referrer" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                                <div>
                                    <div class="fw-bold text-main">${c.ten} <span class="badge bg-light text-dark border ms-1">★ ${rating}</span></div>
                                    <div class="small text-secondary">${formatCategoryLabel(c.loai_hinh)} · Cách ${distVal} km · 🕒 ${openBadge}</div>
                                    <div class="small text-secondary fst-italic text-truncate" style="max-width: 380px;">${firstDefined(c.review)}</div>
                                </div>
                            </div>
                            <button type="button" class="btn-primary-clean btn-sm text-nowrap">↔ Chọn thay thế</button>
                        </div>
                    `);
                    $row.find('button').on('click', function () {
                        executeReplacePlace(String(loc.id), String(c.id), modalEl);
                    });
                    $list.append($row);
                });
            },
            error: function () {
                $list.html(`
                    <div class="text-center py-3">
                        <p class="small text-secondary mb-2">Nhấn nút bên dưới để hệ thống tự động chọn điểm thay thế tốt nhất:</p>
                        <button type="button" id="btn-auto-replace-fallback" class="btn-primary-clean">↔ Đổi tự động ngay</button>
                    </div>
                `);
                $('#btn-auto-replace-fallback').on('click', function () {
                    executeReplacePlace(String(loc.id), null, modalEl);
                });
            }
        });
    }

    function executeReplacePlace(oldId, newId, modalEl) {
        $.ajax({
            url: `${API_BASE}/api/itinerary/replace`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                old_place_id: oldId,
                new_place_id: newId
            }),
            success: function (res) {
                if (modalEl && window.bootstrap) {
                    window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                }
                applyDirectItineraryUpdate(res, 'Đã thay thế địa điểm thành công!');
            },
            error: function (xhr) {
                alert(firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'Không thể thay thế địa điểm.'));
            }
        });
    }

    function openAddPlaceModal(insertAfterId = '') {
        $('#add-place-after-id').val(firstDefined(insertAfterId));
        $('#add-place-search').val('');
        $('#add-place-category').val('');

        const modalEl = document.getElementById('addPlaceModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }

        if (cachedAllLocations) {
            renderAddPlaceCandidates(cachedAllLocations);
        } else {
            $('#add-place-candidates-list').html('<div class="text-center py-4 text-secondary"><i class="fa-solid fa-spinner fa-spin me-2"></i>Đang tải kho địa điểm Hà Nội...</div>');
            $.ajax({
                url: `${API_BASE}/api/locations`,
                method: 'GET',
                success: function (res) {
                    cachedAllLocations = Array.isArray(res.data) ? res.data : [];
                    renderAddPlaceCandidates(cachedAllLocations);
                },
                error: function () {
                    $('#add-place-candidates-list').html('<div class="text-center py-3 text-danger">Không tải được danh sách địa điểm.</div>');
                }
            });
        }
    }

    function renderAddPlaceCandidates(allLocs) {
        const $list =$('#add-place-candidates-list').empty();
        const currentIds = new Set(currentItineraryIds().map(String));
        const keyword = String(firstDefined($('#add-place-search').val())).trim().toLowerCase();
        const catFilter = firstDefined($('#add-place-category').val());

        const filtered = (Array.isArray(allLocs) ? allLocs : []).filter(p => {
            if (currentIds.has(String(p.id))) return false;
            if (catFilter && p.loai_hinh !== catFilter) return false;
            if (keyword && !String(firstDefined(p.ten)).toLowerCase().includes(keyword)) return false;
            return true;
        });

        if (filtered.length === 0) {
            $list.html('<div class="text-center py-3 text-secondary">Không tìm thấy địa điểm phù hợp chưa có trong lộ trình.</div>');
            return;
        }

        filtered.forEach(p => {
            const img = firstDefined(p.url_hinh_anh, DEFAULT_PLACE_IMG);
            const rating = getPlaceRating(p);
            const costStr = formatCurrencyVND(estimatePlaceCost(p));
            const $row =$(`
                <div class="crud-candidate-card">
                    <div class="d-flex align-items-center gap-3">
                        <img src="${img}" alt="${p.ten}" class="crud-cand-img" referrerpolicy="no-referrer" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                        <div>
                            <div class="fw-bold text-main">${p.ten} <span class="badge bg-light text-dark border ms-1">★ ${rating}</span></div>
                            <div class="small text-secondary">${formatCategoryLabel(p.loai_hinh)} · Giá dự kiến: ${costStr}</div>
                            <div class="small text-secondary fst-italic text-truncate" style="max-width: 380px;">${firstDefined(p.review)}</div>
                        </div>
                    </div>
                    <button type="button" class="btn-primary-clean btn-sm text-nowrap">＋ Thêm vào</button>
                </div>
            `);
            $row.find('button').on('click', function () {
                const visitMins = parseFloat($('#add-place-duration').val()) ? parseFloat($('#add-place-duration').val()) : 45;
                const afterId = $('#add-place-after-id').val() ? $('#add-place-after-id').val() : null;
                $.ajax({
                    url: `${API_BASE}/api/itinerary/add`,
                    method: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({
                        session_id: tripState.sessionId,
                        place_id: String(p.id),
                        insert_after_id: afterId,
                        custom_visit_time: visitMins
                    }),
                    success: function (res) {
                        const modalEl = document.getElementById('addPlaceModal');
                        if (modalEl && window.bootstrap) {
                            window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                        }
                        applyDirectItineraryUpdate(res, `Đã thêm "${p.ten}" vào lộ trình!`);
                    },
                    error: function (xhr) {
                        alert(firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'Không thể thêm địa điểm.'));
                    }
                });
            });
            $list.append($row);
        });
    }

    $('#add-place-search, #add-place-category').on('input change', function () {
        if (cachedAllLocations) renderAddPlaceCandidates(cachedAllLocations);
    });

    $('#btn-open-add-place-top').on('click', function () {
        openAddPlaceModal('');
    });

    function openChangeTimeModal(loc = null) {
        if (loc) {
            $('#change-time-place-id').val(String(loc.id));
            $('#change-time-place-name').text(loc.ten);
            $('#custom_visit_time_input').val(firstDefined(loc.visit_time, 60));
            $('#change-time-place-box').removeClass('d-none');
        } else {
            $('#change-time-place-id').val('');
            $('#change-time-place-box').addClass('d-none');
        }

        const startDefault = firstDefined(tripState.baseParams && tripState.baseParams.start_time, $('#start_time').val(), '08:00');
        const endDefault = firstDefined(tripState.baseParams && tripState.baseParams.end_time, $('#end_time').val(), '20:00');
        $('#modal_start_time').val(startDefault);
        $('#modal_end_time').val(endDefault);

        const modalEl = document.getElementById('changeTimeModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }
    }

    $('#btn-open-trip-time').on('click', function () {
        openChangeTimeModal(null);
    });

    $('#btn-confirm-change-time').on('click', function () {
        const pid = $('#change-time-place-id').val() ? $('#change-time-place-id').val() : null;
        const parsedCustom = parseFloat($('#custom_visit_time_input').val());
        const customMins = pid ? (parsedCustom ? parsedCustom : 60) : null;
        const newStart = firstDefined($('#modal_start_time').val(), $('#start_time').val());
        const newEnd = firstDefined($('#modal_end_time').val(), $('#end_time').val());

        $('#start_time').val(newStart);
        $('#end_time').val(newEnd);
        if (tripState.baseParams) {
            tripState.baseParams.start_time = newStart;
            tripState.baseParams.end_time = newEnd;
        }

        $.ajax({
            url: `${API_BASE}/api/itinerary/change-time`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                place_id: pid,
                custom_visit_time: customMins,
                start_time: newStart,
                end_time: newEnd
            }),
            success: function (res) {
                const modalEl = document.getElementById('changeTimeModal');
                if (modalEl && window.bootstrap) {
                    window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                }
                applyDirectItineraryUpdate(res, 'Đã cập nhật thời gian và tính lại toàn bộ lịch trình!');
            },
            error: function (xhr) {
                alert(firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'Không thể cập nhật thời gian.'));
            }
        });
    });

    $('#btn-back-chat').click(function () {
        $('#map-section').hide();
        $('#chat-section').fadeIn(180);
        setActiveNavTab('chat');
    });

    // ============================================================
    // GLOBAL AI CHATBOT WIDGET (HOẠT ĐỘNG Ở CẢ SCREEN 2 & SCREEN 3)
    // ============================================================
    $('#btn-ai-fab').click(function () {
        $('#ai-map-chat').toggleClass('open');
        if ($('#ai-map-chat').hasClass('open')) {
            $('#ai_map_instruction').focus();
        }
    });

    $('#btn-close-ai-chat').click(function () {
        $('#ai-map-chat').removeClass('open');
    });

    function currentItineraryIds() {
        const itinerary = getActiveItinerary();
        if (!itinerary) return [];
        const list = Array.isArray(itinerary.optimized_route) ? itinerary.optimized_route : (Array.isArray(itinerary.places) ? itinerary.places : []);
        return list
            .map(place => place.id)
            .filter(id => id && id !== 'gps_current');
    }

    function appendChatBubble(text, who) {
        $('#ai-map-chat-log').append(`<div class="chat-bubble ${who}">${text}</div>`);
        const log = document.getElementById('ai-map-chat-log');
        if (log) log.scrollTop = log.scrollHeight;
    }

    function sendAiMapInstruction() {
        if (isAiUpdating) return;

        const instruction = $('#ai_map_instruction').val().trim();
        if (!instruction) return;
        if (!tripState.sessionId) {
            alert('Chưa có lộ trình nào để chỉnh sửa.');
            return;
        }

        isAiUpdating = true;
        appendChatBubble(instruction, 'user');
        $('#ai_map_instruction').val('').prop('disabled', true);
        $('#btn-send-ai-chat').prop('disabled', true);
        appendChatBubble('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang cập nhật lộ trình...', 'assistant loading');

        const refinePayload = {
            region: 'Hanoi',
            vehicle_type: $('#vehicle_type').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            user_preference: $('#user_preference').val(),
            current_ids: currentItineraryIds(),
            instruction: instruction,
            session_id: tripState.sessionId,
        };

        $.ajax({
            url: `${API_BASE}/api/ai-refine`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(refinePayload),
            success: function (refineRes) {
                $('#ai-map-chat-log .loading').remove();
                tripState.sessionId = firstDefined(refineRes.session_id, tripState.sessionId);

                $.ajax({
                    url: `${API_BASE}/api/itinerary/update`,
                    method: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({ session_id: tripState.sessionId }),
                    success: function (res) {
                        if (res.status !== 'success' || !res.itinerary) {
                            appendChatBubble(
                                'Không thể cập nhật lộ trình: ' + firstDefined(res.message, 'Lỗi không xác định.'),
                                'assistant'
                            );
                            return;
                        }

                        setCurrentItinerary(res.itinerary);
                        refreshCurrentItinerary();
                        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                            renderRouteCards(tripState.currentRoutes.routes);
                            renderComparisonTable(tripState.currentRoutes.routes);
                        }

                        appendChatBubble(
                            firstDefined(refineRes.advice_text, 'Mình đã cập nhật và sắp xếp lại hành trình cho bạn.'),
                            'assistant'
                        );
                    },
                    error: function () {
                        appendChatBubble('Không thể tính lại lộ trình, vui lòng thử lại.', 'assistant');
                    },
                    complete: function () {
                        isAiUpdating = false;
                        $('#ai_map_instruction').prop('disabled', false);
                        $('#btn-send-ai-chat').prop('disabled', false);
                    }
                });
            },
            error: function (xhr) {
                $('#ai-map-chat-log .loading').remove();
                appendChatBubble(
                    'Xin lỗi, mình chưa hiểu ý bạn lắm: ' + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'vui lòng thử lại.'),
                    'assistant'
                );
                isAiUpdating = false;
                $('#ai_map_instruction').prop('disabled', false);
                $('#btn-send-ai-chat').prop('disabled', false);
            }
        });
    }

    $('#btn-send-ai-chat').click(sendAiMapInstruction);
    $('#ai_map_instruction').keypress(function (e) {
        if (e.which === 13) sendAiMapInstruction();
    });

    // ============================================================
    // ADMIN: IMPORT EXCEL
    // ============================================================
    $('#btn-upload-excel').click(function () {
        const file = document.getElementById('excel_file').files[0];
        if (!file) return alert('Chọn file trước!');
        const formData = new FormData();
        formData.append("file", file);
        $('#upload-status').html('<span class="text-primary">Đang nạp dữ liệu...</span>');
        $.ajax({
            url: `${API_BASE}/api/admin/import-excel`,
            method: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: res => $('#upload-status').html(`<span class="text-success">${res.message}</span>`),
            error: () => $('#upload-status').html('<span class="text-danger">Lỗi Upload</span>')
        });
    });
});