$(document).ready(function () {
    const API_BASE = (function () {
        if (window.location.protocol === 'file:') return 'http://127.0.0.1:8000';
        if (window.location.port === '8000') return '';
        const host = window.location.hostname ? window.location.hostname : '127.0.0.1';
        return `${window.location.protocol}//${host}:8000`;
    })();

    // Khởi tạo bản đồ nền Esri World Street Map & OSM Hot (Miễn phí, không watermark, không chặn file:/// 403)
    const map = L.map('map', { zoomControl: true }).setView([21.0285, 105.8542], 13);
    
    // Layer 1: Esri World Street Map (Sạch sẽ, chi tiết đường phố Hà Nội, không bao giờ bị chặn 403 khi chạy file:///)
    const esriStreetLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19,
        attribution: 'Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ, USGS, TomTom'
    });

    // Layer 2: OSM Humanitarian Hot (Bản đồ phong cách du lịch, màu sắc tươi sáng)
    const osmHotLayer = L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors, Tiles style by Humanitarian OSM'
    });

    // Mặc định kích hoạt Esri World Street Map
    esriStreetLayer.addTo(map);

    // Bổ sung bộ chuyển đổi Layer bản đồ trực quan
    L.control.layers({
        "Bản đồ Đường phố (Esri)": esriStreetLayer,
        "Bản đồ Du lịch (OSM Hot)": osmHotLayer
    }, null, { position: 'topright' }).addTo(map);

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

    // Quản lý người dùng & Bộ sưu tập yêu thích (Account & Favorite)
    let currentUserId = null;
    try {
        const savedUser = localStorage.getItem('teravue_current_user_id');
        if (savedUser && savedUser.trim() && savedUser !== 'guest' && savedUser !== 'null') {
            currentUserId = savedUser.trim();
        }
    } catch (e) {
        currentUserId = null;
    }

    let favoritePlaceIds = new Set();
    try {
        const savedFavs = JSON.parse(localStorage.getItem('teravue_fav_places') ? localStorage.getItem('teravue_fav_places') : '[]');
        if (Array.isArray(savedFavs)) favoritePlaceIds = new Set(savedFavs.map(String));
    } catch (e) {
        favoritePlaceIds = new Set();
    }

    function updateFavoriteBadges(count) {
        const c = count !== undefined ? count : favoritePlaceIds.size;
        $('#nav-favorites-badge').text(c);
        const $qBadge = $('#quick-fav-badge');
        if (c > 0) {
            $qBadge.text(c).show();
        } else {
            $qBadge.hide();
        }
    }

    function saveFavorites() {
        try {
            localStorage.setItem('teravue_fav_places', JSON.stringify(Array.from(favoritePlaceIds)));
        } catch (e) {}
        updateFavoriteBadges();
    }

    function syncFavoritesWithServer() {
        if (!currentUserId) {
            favoritePlaceIds.clear();
            updateFavoriteBadges(0);
            return;
        }
        $.ajax({
            url: `${API_BASE}/api/user/favorites?user_id=${encodeURIComponent(currentUserId)}`,
            method: 'GET',
            success: function (res) {
                if (res.status === 'success' && Array.isArray(res.data)) {
                    favoritePlaceIds = new Set(res.data.map(p => String(p.id)));
                    saveFavorites();
                    updateFavoriteBadges(res.favorites_count || favoritePlaceIds.size);
                } else if (res.status === 'unauthenticated') {
                    favoritePlaceIds.clear();
                    updateFavoriteBadges(0);
                }
            }
        });
    }

    function toggleUserFavorite(placeId, callback) {
        if (!currentUserId) {
            showCrudBanner('Vui lòng đăng nhập để lưu địa điểm yêu thích vào tài khoản ❤️', 'warning');
            const loginModalEl = document.getElementById('loginModal');
            if (loginModalEl && window.bootstrap) {
                window.bootstrap.Modal.getOrCreateInstance(loginModalEl).show();
            }
            if (typeof callback === 'function') callback(false);
            return;
        }
        const pid = String(placeId);
        $.ajax({
            url: `${API_BASE}/api/user/favorites/toggle`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ user_id: currentUserId, place_id: pid }),
            success: function (res) {
                if (res.status === 'success') {
                    if (res.is_favorite) {
                        favoritePlaceIds.add(pid);
                    } else {
                        favoritePlaceIds.delete(pid);
                    }
                    saveFavorites();
                    updateFavoriteBadges(res.favorites_count);
                    if (typeof callback === 'function') callback(res.is_favorite);
                }
            },
            error: function (xhr) {
                if (xhr && xhr.status === 401) {
                    showCrudBanner('Vui lòng đăng nhập để lưu địa điểm yêu thích ❤️', 'warning');
                    const loginModalEl = document.getElementById('loginModal');
                    if (loginModalEl && window.bootstrap) {
                        window.bootstrap.Modal.getOrCreateInstance(loginModalEl).show();
                    }
                    if (typeof callback === 'function') callback(false);
                    return;
                }
                showCrudBanner('Không thể cập nhật danh sách yêu thích lúc này.', 'danger');
                if (typeof callback === 'function') callback(favoritePlaceIds.has(pid));
            }
        });
    }

    // Đồng bộ danh sách yêu thích và trạng thái đăng nhập ngay khi load trang
    syncFavoritesWithServer();

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

    function formatErrorDetail(xhr, defaultMsg) {
        if (!xhr || !xhr.responseJSON) return defaultMsg;
        const d = xhr.responseJSON.detail;
        if (!d) return xhr.responseJSON.message || defaultMsg;
        if (typeof d === 'string') return d;
        if (Array.isArray(d)) {
            return d.map(item => item.msg || JSON.stringify(item)).join('; ');
        }
        if (typeof d === 'object') {
            return JSON.stringify(d);
        }
        return String(d);
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
    // ============================================================
    // BẢNG TRA TỪ ĐIỂN REVIEW CÓ DẤU CHUẨN XÁC 100% (BUG-REVIEW-ACCENTS)
    // ============================================================
    const POI_ACCENTED_REVIEWS = {
        "1": "Không gian sang trọng, dịch vụ chuyên nghiệp, đài quan sát view toàn cảnh thủ đô tuyệt đẹp. Giá cả thuộc phân khúc cao cấp.",
        "2": "Vị trí trung tâm tiện di chuyển, thương hiệu mua sắm và ẩm thực đa dạng. Hầm gửi xe đông vào giờ tan tầm.",
        "3": "Kiến trúc Pháp cổ kính lộng lẫy, không gian yên tĩnh và sang trọng. Nơi mua sắm cao cấp và check-in ấn tượng.",
        "4": "Khu đô thị ngầm hiện đại với vô vàn tiện ích vui chơi, trượt băng và ăn uống. Không gian rất rộng rãi và náo nhiệt.",
        "5": "Dịch vụ chuẩn phong cách Nhật chu đáo, tiện nghi sạch sẽ, bãi đỗ xe rộng rãi miễn phí. Cuối tuần thường rất đông vui.",
        "6": "Nước dùng thanh ngọt chuẩn vị truyền thống Hà thành, thịt bò tái lăn tươi ngon. Không gian ấm cúng, đậm nét phố cổ.",
        "7": "Bún chả đậm đà nướng than hoa thơm lừng, suất ăn đầy đặn kèm nem cua bể giòn rụm. Điểm hẹn ẩm thực nổi tiếng với du khách quốc tế.",
        "8": "Vị trí tuyệt đẹp đón gió mát bên Hồ Tây, bánh tôm chiên vàng giòn rụm chấm nước mắm chua ngọt thanh tao.",
        "9": "Chả cá lăng tươi ngon béo ngậy ăn kèm thì là và mắm tôm dậy vị, không gian thanh lịch không bị ám mùi dầu mỡ.",
        "10": "Đậu Mơ rán giòn vỏ mềm ruột, mắm tôm Thanh Hóa thơm dậy mùi chanh ớt, giữ trọn nét mộc mạc của ẩm thực ngõ phố.",
        "11": "Phở cuốn thanh mát, rau tươi thịt bò xào lăn đậm đà kèm nước chấm vừa miệng. Phục vụ nhanh nhẹn, quán sạch sẽ.",
        "12": "Nộm bò khô phố cổ giòn sần sật, thịt bò khô thấm đượm gia vị, nước trộn chua cay mặn ngọt hài hòa khó quên.",
        "13": "Bánh mì sốt vang thơm nức hương hoa hồi ngũ vị, thịt bò gân mềm dẻo chấm cùng bánh mì giòn tan, chuẩn vị Hà Nội xưa.",
        "14": "Miến lươn giòn tan vàng rộm, nước dùng hầm từ xương lươn ngọt thanh tự nhiên, rau răm và hành phi thơm lừng.",
        "15": "Xôi nếp dẻo thơm hạt bóng bẩy, phong phú topping pate béo ngậy, thịt kho, gà xé đậm vị, phục vụ cực kỳ nhanh chóng.",
        "16": "Không gian biệt thự Pháp cổ vintage, yên tĩnh, đượm nét thơ. Cà phê quế và cà phê trứng ngọt ngào, ấm cúng để hàn huyên.",
        "17": "Không gian ấm áp với tone gỗ nâu trầm, lý tưởng để làm việc hay hẹn hò. Nhân viên chu đáo, Cold Brew sấu rất độc đáo.",
        "18": "Thương hiệu cà phê trứng lâu đời nhất Hà Nội, lớp kem trứng béo ngậy hòa quyện cà phê đậm đà không chút mùi tanh.",
        "19": "Quán nhỏ ấm cúng nép mình trên phố Chân Cầm, ban công và vỉa hè ngắm phố rất chill, bạc xỉu và cold brew đậm vị.",
        "20": "Cà phê pha máy chuyên nghiệp, hương vị tinh tế. Không gian mở ngập tràn cây xanh và ánh sáng tự nhiên ven Hồ Tây.",
        "21": "Trà Ô Long đậm đà thơm ngát, ban công nhìn ra phố Tông Đản thoáng đãng, thích hợp để thư giãn sau giờ làm việc.",
        "22": "Vibe biệt thự cổ duyên dáng, ban công tầng 2 bao quát ngã tư Đinh Liệt tấp nập, điểm dừng chân lý tưởng khi dạo bộ phố cổ.",
        "23": "Nằm sâu trong ngõ nhỏ yên bình, thiết kế mở chan hòa nắng và cây xanh. Cà phê đậm chất mộc mạc, nhân viên niềm nở.",
        "24": "Không gian tối giản thanh lịch, giai điệu nhạc thư thái thích hợp tập trung làm việc hay đọc sách. Thức uống chỉn chu.",
        "25": "Không gian rộng rãi, thoáng mát, phong cách mộc mạc gần gũi. Thức uống phong phú, nhân viên thân thiện và nhiệt tình.",
        "26": "Di tích văn hóa biểu tượng giữa lòng Hồ Gươm, cầu Thê Húc son đỏ rực rỡ dẫn vào đền cổ kính rợp bóng cây xanh.",
        "27": "Công trình thiêng liêng và trang nghiêm tại Quảng trường Ba Đình lịch sử. Khuôn viên rợp bóng tre ngà và cây cảnh quý.",
        "28": "Trường đại học đầu tiên của Việt Nam với kiến trúc Khuê Văn Các cổ kính, hồ Thiên Quang tĩnh lặng và vườn bia Tiến sĩ vô giá.",
        "29": "Ngôi chùa cổ nhất Thăng Long hơn 1500 năm tuổi soi bóng bên Hồ Tây, cảnh sắc thanh tịnh, bảo tháp lục độ sen độc đáo.",
        "30": "Biểu tượng kiến trúc Gothic uy nghiêm giữa lòng thủ đô, không gian sinh hoạt văn hóa và trà chanh vỉa hè nhộn nhịp về đêm.",
        "31": "Rạp chiếu phim quen thuộc của người Hà Nội, giá vé hợp lý, nhiều suất chiếu đa dạng và không gian sảnh chờ rộng rãi."
    };

    function getAccentedReview(loc) {
        if (!loc) return '';
        const pid = String(firstDefined(loc.id, ''));
        if (POI_ACCENTED_REVIEWS[pid]) {
            return POI_ACCENTED_REVIEWS[pid];
        }
        if (Array.isArray(cachedAllLocations) && cachedAllLocations.length) {
            const found = cachedAllLocations.find(p => String(p.id) === pid);
            if (found && found.review && /[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]/i.test(found.review)) {
                return found.review;
            }
        }
        let rev = String(firstDefined(loc.review, loc.thong_tin_chi_tiet, '')).trim();
        if (rev.includes('Đánh giá thực tế từ khách hàng:')) {
            rev = rev.split('Đánh giá thực tế từ khách hàng:')[0].trim();
        }
        return rev;
    }

    const POI_ACCENTED_PHUHOP = {
        "1": "Mua sắm hàng hiệu, hẹn hò, vui chơi gia đình cuối tuần.",
        "2": "Mua sắm thời trang, xem phim, ăn uống, gặp gỡ bạn bè.",
        "3": "Mua sắm đồ hiệu cao cấp, dạo phố cổ, check-in kiến trúc.",
        "4": "Vui chơi giải trí nhóm đông, gia đình cả ngày, trượt băng.",
        "5": "Mua sắm gia đình cuối tuần, trải nghiệm ẩm thực, dã ngoại mua sắm.",
        "6": "Ăn sáng, ăn trưa nhanh, trải nghiệm ẩm thực Hà Nội xưa.",
        "7": "Du lịch trải nghiệm địa điểm nổi tiếng, ăn trưa văn phòng.",
        "8": "Tụ tập gia đình, đón tiếp khách du lịch, ăn uống ngắm cảnh hồ.",
        "9": "Tiếp đối tác, họp mặt gia đình, ăn uống nhóm đông lịch sự.",
        "10": "Ăn trưa nhanh, trải nghiệm ẩm thực ngõ ngách Hà Nội.",
        "11": "Tụ tập bạn bè, ăn trưa, ăn tối, đổi vị món ăn nhẹ nhàng.",
        "12": "Ăn vặt chiều, dạo phố Bờ Hồ, trải nghiệm văn hóa vỉa hè.",
        "13": "Ăn sáng, ăn trưa nhanh, thưởng thức bánh mì ấm nóng ngày se lạnh.",
        "14": "Ăn sáng, ăn trưa thanh nhẹ, trải nghiệm ẩm thực chuẩn Michelin.",
        "15": "Ăn nhanh no lâu, ăn sáng, ăn đêm muộn phố cổ.",
        "16": "Làm việc hoặc trò chuyện tâm tình trong không gian vintage.",
        "17": "Tập trung làm việc hoặc có những buổi hẹn hò nhẹ nhàng.",
        "18": "Thưởng thức cà phê trứng và trải nghiệm văn hóa phố cổ lâu đời.",
        "19": "Ngồi vỉa hè ngắm view phố cổ nhộn nhịp, chill và thư thái.",
        "20": "Tập trung làm việc, học tập với cà phê pha máy chuyên sâu.",
        "21": "Check-in ngắm phố, gặp gỡ bạn bè và dạo phố Tông Đản.",
        "22": "Tâm sự nhẹ nhàng, ngắm toàn cảnh ngã tư Đinh Liệt từ ban công.",
        "23": "Thư giãn, tìm không gian yên bình chan hòa cây xanh.",
        "24": "Tập trung làm việc hoặc có những cuộc trò chuyện riêng tư.",
        "25": "Không gian làm việc ban ngày, bàn công việc và đọc sách.",
        "26": "Tham quan di tích, ngắm cảnh Hồ Gươm và check-in Cầu Thê Húc.",
        "27": "Tham quan lịch sử, viếng lăng Bác, chiêm ngưỡng kiến trúc trang nghiêm.",
        "28": "Tìm hiểu văn hóa, giáo dục truyền thống và thư giãn trong khuôn viên cổ kính.",
        "29": "Đi lễ, vãn cảnh Hồ Tây và tìm sự thanh tịnh nơi cửa Phật.",
        "30": "Check-in chụp ảnh, dạo phố đi bộ, tụ tập bạn bè ngắm cảnh đêm.",
        "31": "Xem phim, giải trí, hẹn hò, tụ họp bạn bè."
    };

    function getAccentedPhuHop(place) {
        if (!place) return '';
        const pid = String(firstDefined(place.id, ''));
        if (POI_ACCENTED_PHUHOP[pid]) return POI_ACCENTED_PHUHOP[pid];
        return String(firstDefined(place.phu_hop, '')).trim();
    }

    function enrichPlacesWithAccentedReviews(routes) {
        if (!Array.isArray(routes)) return;
        routes.forEach(r => {
            const places = r.optimized_route || r.places || [];
            places.forEach(p => {
                p.review = getAccentedReview(p);
            });
        });
    }

    // Review nổi bật: ưu tiên trích dẫn đã chọn từ review thật (có số sao); không có thì cắt ngắn review cũ
    function reviewSnippet(place) {
        const ins = (place && place.review_insight) || {};
        const q = Array.isArray(ins.trich_dan) && ins.trich_dan.length ? ins.trich_dan[0] : null;
        if (q && q.noi_dung) return (q.sao ? `★${q.sao} · ` : '') + q.noi_dung;
        let r = getAccentedReview(place);
        return r.length > 220 ? r.slice(0, 220).replace(/\s+\S*$/, '') + '…' : r;
    }
    // Chip "khách hay khen" / "lưu ý" / hoạt động, dùng ở Screen 2
    function buildInsightChipsHtml(place) {
        const ins = (place && place.review_insight) || {};
        const tg = placeTagGroups(place);
        const chip = (cls, t) => `<span class="pd-chip ${cls}">${escHtml(t)}</span>`;
        const parts = [];
        if (tg.space) parts.push(chip('pd-chip-space', tg.space));
        const arrHour = parseInt(String(firstDefined(place.arrive_time, '09:00')).split(':')[0], 10) || 9;
        const validActs = tg.hoatDong.filter(t => {
            if (arrHour < 11 && t === 'Ăn tối') return false;
            return true;
        });
        validActs.slice(0, 4).forEach(t => parts.push(chip('pd-chip-act', t)));
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
        const phuHop = getAccentedPhuHop(place).replace(/\.$/, '');
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
            ? `Điểm đến đặc biệt phù hợp cho: ${phuHop}.`
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
            const $btn = $(this);
            $btn.prop('disabled', true);
            toggleUserFavorite(pid, function (isFav) {
                $btn.prop('disabled', false);
                updateModalFavBtn();
                refreshCurrentItinerary();
            });
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
                backgroundColor: isWarning ? 'rgba(254, 245, 231, 0.98)' : 'rgba(230, 244, 238, 0.98)',
                color: isWarning ? 'var(--status-warning)' : 'var(--status-success)',
                borderColor: isWarning ? '#F6E0B5' : '#B7E4CE'
            })
            .html(`
                <i class="fa-solid ${isWarning ? 'fa-triangle-exclamation text-warning' : 'fa-circle-check text-success'} fs-5 flex-shrink-0"></i>
                <div class="flex-grow-1" style="line-height: 1.45;">${msg}</div>
                <button type="button" class="btn-close btn-close-sm ms-2 flex-shrink-0" style="font-size: 0.7rem;" aria-label="Đóng" onclick="$(this).closest('.crud-feedback-banner').addClass('d-none');"></button>
            `);
        clearTimeout($banner.data('timer'));
        const timer = setTimeout(() => {
            $banner.fadeOut(250, function () {
                $(this).addClass('d-none').show();
            });
        }, 4500);
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
                showCrudBanner('Vui lòng tạo lịch trình ở bước 1 trước nhé!', true);
                return;
            }
            $('#hero-section, #map-section').hide();
            $('#chat-section').fadeIn(180);
            setActiveNavTab('chat');
        } else if (target === 'map') {
            if (!tripState.currentRoutes) {
                showCrudBanner('Vui lòng tạo và chọn một lộ trình trước khi mở bản đồ nhé!', true);
                return;
            }
            enterMapScreen(tripState.selectedRouteId);
            setActiveNavTab('map');
        }
    });

    function getStoredHistory() {
        try {
            return JSON.parse(localStorage.getItem('teravue_trip_history')) || [];
        } catch (e) {
            return [];
        }
    }

    function saveTripToHistory(sessionId, prefText, startTime, endTime, vType) {
        if (!sessionId) return;
        try {
            let history = getStoredHistory();
            history = history.filter(h => h.sessionId !== sessionId);
            history.unshift({
                sessionId: sessionId,
                title: prefText || 'Hành trình khám phá Hà Nội',
                startTime: startTime || '08:00',
                endTime: endTime || '20:00',
                vehicleType: vType || 'xe_may',
                timestamp: Date.now()
            });
            if (history.length > 20) history = history.slice(0, 20);
            localStorage.setItem('teravue_trip_history', JSON.stringify(history));
            localStorage.setItem('teravue_active_session', sessionId);
            renderTripHistoryUI();
        } catch (e) {
            console.warn('Lỗi lưu lịch sử:', e);
        }
    }

    function renderTripHistoryUI() {
        const $list = $('#chat-history-list');
        if (!$list.length) return;
        $list.empty();
        const history = getStoredHistory();
        history.forEach(item => {
            const isActive = item.sessionId === tripState.sessionId;
            const shortTitle = item.title.length > 28 ? item.title.slice(0, 28) + '...' : item.title;
            const vName = VEHICLE_NAMES[item.vehicleType] ? VEHICLE_NAMES[item.vehicleType] : 'Xe máy';
            const $item = $(`
                <li class="history-item ${isActive ? 'active' : ''}" data-session-id="${escapeHtml(item.sessionId)}">
                    <i class="fa-solid fa-route mt-1"></i>
                    <div class="history-item-text">
                        <div class="history-title">${escapeHtml(shortTitle)}</div>
                        <div class="history-meta">${escapeHtml(item.startTime)} - ${escapeHtml(item.endTime)} · ${escapeHtml(vName)}</div>
                    </div>
                </li>
            `);
            $item.on('click', function () {
                const sId = item.sessionId;
                if (tripState.sessionId === sId && tripState.currentRoutes) {
                    $('#hero-section, #map-section').hide();
                    $('#chat-section').fadeIn(180);
                    setActiveNavTab('chat');
                    return;
                }
                $.ajax({
                    url: `${API_BASE}/api/routes?session_id=${encodeURIComponent(sId)}`,
                    method: 'GET',
                    success: function (res) {
                        if (res && res.status === 'success' && res.routes && res.routes.length) {
                            tripState.sessionId = sId;
                            tripState.currentRoutes = res;
                    enrichPlacesWithAccentedReviews(res.routes);
                            tripState.selectedRouteId = res.selected_route_id || res.routes[0].route_id;
                            localStorage.setItem('teravue_active_session', sId);
                            renderRouteCards(res.routes);
                            renderComparisonTable(res.routes);
                            selectRouteOnScreen2(tripState.selectedRouteId);
                            $('#chat-history-list .history-item').removeClass('active');
                            $item.addClass('active');
                            $('#hero-section, #map-section').hide();
                            $('#chat-section').fadeIn(180);
                            setActiveNavTab('chat');
                            showCrudBanner(`Đã tải lại lịch trình: ${shortTitle}`);
                        }
                    },
                    error: function () {
                        showCrudBanner('Phiên lịch trình này đã hết hạn trên máy chủ.', true);
                    }
                });
            });
            $list.append($item);
        });
    }

    function addHistoryEntry(prefText, startTime, endTime, vType) {
        if (tripState.sessionId) {
            saveTripToHistory(tripState.sessionId, prefText, startTime, endTime, vType);
        }
    }

    // ============================================================
    // ============================================================
    // LIVE "AI ĐÃ HIỂU" PREVIEW TRÊN SCREEN 1 (SEMANTIC PARSER INTEGRATION)
    // ============================================================
    const CLIENT_TAG_RULES = [
        { id: 'lively', label: '🔥 Nhộn nhịp, sôi động', test: /(nhộn nhịp|sôi động|náo nhiệt|tấp nập|sầm uất|vui nhộn|đông vui|náo nức|quẩy)/i },
        { id: 'quiet', label: '🍃 Yên tĩnh, thư giãn', test: /(yên tĩnh|vắng vẻ|ít người|thư giãn|nhẹ nhàng|chill|bình yên|tránh đông|không đông|thư thái|tĩnh lặng|an yên|chữa lành)/i },
        { id: 'cafe', label: '☕ Cà phê', test: /(cafe|cà phê|cafè|coffee|café|trà sữa|tiệm trà|quán nước|cf|trà chanh|cà phê trứng|uống trà)/i },
        { id: 'photo', label: '📸 View đẹp / Sống ảo', test: /(chụp ảnh|chụp hình|check in|check-in|checkin|sống ảo|view đẹp|view|instagram|góc đẹp|selfie|photo|hoàng hôn|bình minh|rooftop|ngắm cảnh)/i },
        { id: 'food', label: '🍜 Ẩm thực', test: /(ăn uống|ẩm thực|đồ ăn|quán ăn|ăn trưa|ăn sáng|ăn tối|ăn vặt|phở|bún chả|bún|bánh|đặc sản|nhà hàng|món ngon|ăn ngon|bún đậu|chả cá|bánh mì|ăn đêm)/i },
        { id: 'culture', label: '🏛️ Văn hóa - Lịch sử', test: /(văn hóa|văn hoá|lịch sử|di tích|bảo tàng|đền|chùa|phố cổ|kiến trúc|truyền thống|cổ kính|văn miếu|lăng bác|hỏa lò|tâm linh)/i },
        { id: 'nature', label: '🌳 Thiên nhiên ven hồ', test: /(thiên nhiên|công viên|hồ tây|hồ gươm|ven hồ|cây xanh|vườn|ngoài trời|dạo bộ|hóng gió|tản bộ|đi dạo)/i },
        { id: 'entertainment', label: '🎬 Xem phim / Rạp phim', test: /(xem phim|chiếu phim|rạp phim|rạp chiếu phim|rap phim|chieu phim|coi phim|xem film|cinema|cgv|lotte cinema|bhd|giải trí|vui chơi|trượt băng|bowling|khu vui chơi)/i },
        { id: 'shopping', label: '🛍️ Mua sắm', test: /(mua sắm|shopping|trung tâm thương mại|tttm|mall|siêu thị|quần áo|mua đồ|chợ|chợ đêm)/i },
        { id: 'nightlife', label: '🌙 Về đêm', test: /(về đêm|buổi tối|nightlife|bar|pub|phố đi bộ|chợ đêm|đi đêm|tạ hiện|uống bia|bia hơi)/i },
        { id: 'romantic', label: '❤️ Lãng mạn', test: /(hẹn hò|lãng mạn|người yêu|bạn gái|bạn trai|cặp đôi)/i },
        { id: 'family', label: '👨‍👩‍👧 Gia đình', test: /(gia đình|trẻ em|trẻ con|con nhỏ|bố mẹ|ông bà)/i },
        { id: 'pace_slow', label: '⏳ Thong thả', test: /(thong thả|chậm rãi|từ từ|không vội|thư thái)/i },
        { id: 'pace_fast', label: '⚡ Đi nhiều điểm', test: /(nhiều điểm|nhanh|tranh thủ|càng nhiều càng tốt)/i },
        { id: 'budget_low', label: '💰 Tiết kiệm', test: /(tiết kiệm|bình dân|giá rẻ|hạt dẻ|sinh viên|ít tiền)/i },
        { id: 'budget_high', label: '✨ Sang trọng', test: /(sang trọng|sang chảnh|cao cấp|luxury|đắt tiền)/i },
        { id: 'indoor', label: '🏠 Trong nhà', test: /(trong nhà|điều hòa|điều hoà|máy lạnh|tránh nắng|trời mưa)/i },
        { id: 'outdoor', label: '⛅ Ngoài trời', test: /(ngoài trời|thoáng mát|dã ngoại|hóng gió)/i },
    ];

    const CLIENT_KNOWN_PLACES = [
        { name: 'Cafe Giảng', test: /(cafe gi[aả]ng|c[aà] ph[eê] gi[aả]ng|gi[aả]ng cafe|c[aà] ph[eê] tr[uứ]ng|cafe tr[uứ]ng)/i },
        { name: 'Phở Thìn Bờ Hồ', test: /(ph[oở] th[iì]n)/i },
        { name: 'Bún chả Hương Liên', test: /(b[uú]n ch[aả] h[uư][oơ]ng li[eê]n|b[uú]n ch[aả] obama|huong lien)/i },
        { name: 'Hồ Gươm', test: /(h[oồ] g[uươ]m|h[oồ] ho[aà]n ki[eế]m|b[oờ] h[oồ])/i },
        { name: 'Hồ Tây', test: /(h[oồ] t[aâ]y|ven h[oồ] t[aâ]y|quanh h[oồ] t[aâ]y)/i },
        { name: 'Lăng Bác', test: /(l[aă]ng b[aá]c|l[aă]ng ch[uủ] t[iị]ch)/i },
        { name: 'Văn Miếu', test: /(v[aă]n mi[eế]u|qu[oố]c t[uử] gi[aá]m)/i },
        { name: 'Chùa Trấn Quốc', test: /(ch[uù]a tr[aấ]n qu[oố]c|tr[aấ]n qu[oố]c)/i },
        { name: 'Nhà thờ Lớn', test: /(nh[aà] th[oờ] l[oớ]n)/i },
        { name: 'Hỏa Lò', test: /(ho[aả] l[oò]|h[oỏ]a l[oò]|nh[aà] t[uù] ho[aả] l[oò])/i },
        { name: 'Phố Tạ Hiện', test: /(t[aạ] hi[eệ]n|ph[oố] t[aạ] hi[eệ]n|bia t[aạ] hi[eệ]n)/i },
        { name: 'Tràng Tiền', test: /(tr[aà]ng ti[eề]n|kem tr[aà]ng ti[eề]n)/i },
        { name: 'Chợ Đồng Xuân', test: /(ch[oợ] [dđ][oồ]ng xu[aâ]n|[dđ][oồ]ng xu[aâ]n)/i },
        { name: 'Ban Công Cafe', test: /(ban c[oô]ng cafe|cafe ban c[oô]ng)/i },
        { name: 'Loading T Cafe', test: /(loading t)/i },
        { name: 'Vincom Bà Triệu', test: /(vincom b[aà] tri[eệ]u|vincom)/i },
        { name: 'Lotte Center', test: /(lotte)/i },
        { name: 'Royal City', test: /(royal city)/i },
    ];

    let parsePromptTimer = null;
    function fetchSemanticParse(pref) {
        clearTimeout(parsePromptTimer);
        if (!pref) {
            $('#ai-clarification-box').addClass('d-none');
            $('#ai-understood-detail').addClass('d-none');
            return;
        }
        parsePromptTimer = setTimeout(() => {
            $.ajax({
                url: `${API_BASE}/api/parse-prompt`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    prompt: pref,
                    context: {
                        start_time: $('#start_time').val(),
                        end_time: $('#end_time').val(),
                        vehicle_type: $('#vehicle_type').val(),
                        trip_date: $('#trip_date').val(),
                        start_point: $('#start_point').val().trim()
                    },
                    use_llm: false
                }),
                success: function (res) {
                    if (res && res.badge_text) {
                        $('#ai-understood-text').text(res.badge_text);
                    }
                    if (res && res.understood_summary) {
                        $('#ai-understood-detail-text').text(res.understood_summary);
                        $('#ai-understood-detail').removeClass('d-none');
                        $('#ai-understood-badge').attr('title', res.understood_summary);
                    }
                    if (res && res.status === 'need_clarification' && res.clarification_questions && res.clarification_questions.length > 0) {
                        const q = res.clarification_questions[0];
                        $('#ai-clarification-question').text(q.question);
                        const $opts = $('#ai-clarification-options').empty();
                        (q.options || []).forEach(opt => {
                            const $chip = $('<button type="button" class="clarification-chip"></button>').text(opt.label);
                            $chip.click(function () {
                                const curVal = $('#user_preference').val().trim();
                                $('#user_preference').val(curVal + ', ' + opt.value).trigger('input');
                            });
                            $opts.append($chip);
                        });
                        if (q.options && q.options.length > 0) {
                            $('#ai-clarification-box').removeClass('d-none');
                        }
                    } else {
                        $('#ai-clarification-box').addClass('d-none');
                    }
                },
                error: function () {
                    // Giữ kết quả client-side mượt mà nếu offline
                }
            });
        }, 300);
    }

    function updateLiveUnderstoodBar(customSummary) {
        const pref = $('#user_preference').val().trim();
        const sTime = firstDefined($('#start_time').val(), '08:00');
        const eTime = firstDefined($('#end_time').val(), '20:00');
        const vType = firstDefined($('#vehicle_type').val(), 'xe_may');
        const sPoint = $('#start_point').val().trim();
        const ePoint = $('#end_point').val().trim();

        if (!pref && !customSummary) {
            $('#ai-understood-badge').addClass('d-none');
            $('#ai-clarification-box').addClass('d-none');
            $('#ai-understood-detail').addClass('d-none');
            return;
        }

        if (customSummary) {
            $('#ai-understood-text').text(customSummary);
            $('#ai-understood-badge').removeClass('d-none');
            return;
        }

        // 1. Nhận diện sở thích, tag & địa điểm trực tiếp trên client (phản hồi tức thì)
        const matchedItems = [];
        CLIENT_TAG_RULES.forEach(r => {
            if (r.test.test(pref)) {
                matchedItems.push(r.label);
            }
        });
        CLIENT_KNOWN_PLACES.forEach(p => {
            if (p.test.test(pref)) {
                matchedItems.push('📍 ' + p.name);
            }
        });

        // 2. Trích xuất yêu cầu / mong muốn tự do của con người nếu có câu/mệnh đề riêng biệt
        const clauses = pref.split(/[,;.!?\n]|\b(?:và|với|hoặc|cùng|nhưng|mà|để|kèm|thêm|rồi)\b/i);
        clauses.forEach(c => {
            let clean = c.trim().replace(/^["'“”()\[\]]+|["'“”()\[\]]+$/g, '');
            clean = clean.replace(/^(?:thích|muốn|yêu cầu|cần|có|đi|trải nghiệm|khám phá|ghé|xem|uống|ăn|tìm|ngắm|thử|thong thả|nhẹ nhàng|chậm rãi|nhanh)\s+/i, '').trim();
            if (clean.length >= 3) {
                const isCovered = matchedItems.some(item => item.toLowerCase().includes(clean.toLowerCase()));
                if (!isCovered && !matchedItems.includes('✨ ' + clean)) {
                    if (!/^(hà nội|xe máy|ô tô|đi bộ|xe đạp|ngày|giờ|tiếng|hôm nay|ngày mai)$/i.test(clean)) {
                        matchedItems.push('✨ ' + (clean.length > 35 ? clean.slice(0, 35) + '...' : clean));
                    }
                }
            }
        });

        const parts = [`${sTime}–${eTime}`, firstDefined(VEHICLE_NAMES[vType], vType)];
        if (sPoint) parts.push(`Từ: ${sPoint}`);
        if (ePoint) parts.push(`Đến: ${ePoint}`);

        if (matchedItems.length > 0) {
            parts.push(matchedItems.join(' · '));
        } else if (pref) {
            const cleanPref = pref.length > 60 ? pref.slice(0, 60) + '...' : pref;
            parts.push('✨ ' + cleanPref);
        }

        $('#ai-understood-text').text(parts.join(' | '));
        $('#ai-understood-badge').removeClass('d-none');

        // 3. Gọi Semantic Parser Backend đồng bộ ngữ nghĩa & gợi ý câu hỏi làm rõ
        fetchSemanticParse(pref);
    }

    $('#btn-edit-understood').click(function () {
        $('#ai-understood-detail').toggleClass('d-none');
        $('#user_preference').focus();
    });

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
                // Mặc định ở Screen 2: ĐÓNG LẠI TOÀN BỘ các lộ trình, không mở sẵn lộ trình nào
                tripState.selectedRouteId = null;
                if (res.routes && res.routes.length > 0) {
                    tripState.currentItinerary = res.routes[0];
                }

                if (tripState.sessionId) {
                    localStorage.setItem('teravue_active_session', tripState.sessionId);
                    const p = tripState.baseParams || {};
                    saveTripToHistory(
                        tripState.sessionId,
                        p.user_preference || $('#user_preference').val(),
                        p.start_time || $('#start_time').val() || '08:00',
                        p.end_time || $('#end_time').val() || '20:00',
                        p.vehicle_type || $('#vehicle_type').val() || 'xe_may'
                    );
                }

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
                <div class="route-detail-footer text-end mt-3 pt-3 border-top">
                    <button type="button" class="btn btn-primary-clean btn-accept-card-route" style="padding: 10px 20px; font-size: 0.92rem;">
                        <i class="fa-solid fa-map-location-dot me-1"></i> Chốt phương án này & Xem bản đồ
                    </button>
                </div>
            </div>
        `;
    }

    function selectRouteOnScreen2(routeId) {
        // Toggle mở / đóng: nếu nhấn vào route đang mở thì thu gọn lại, nếu nhấn route khác thì mở route đó
        if (tripState.selectedRouteId === routeId) {
            tripState.selectedRouteId = null;
        } else {
            tripState.selectedRouteId = routeId;
            if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                const chosen = tripState.currentRoutes.routes.find(r => r.route_id === routeId);
                if (chosen) tripState.currentItinerary = chosen;
            }
        }

        $('#ai-routes-grid .route-card').each(function () {
            const isMatch = $(this).attr('data-route-id') === tripState.selectedRouteId;
            $(this)
                .toggleClass('selected', isMatch)
                .attr('aria-selected', isMatch ? 'true' : 'false')
                .find('.route-card-select-indicator')
                .html(isMatch
                    ? '<i class="fa-solid fa-circle-check me-1"></i> Đang xem chi tiết <i class="fa-solid fa-chevron-up ms-1 chevron-icon"></i>'
                    : 'Nhấn để xem Timeline chi tiết <i class="fa-solid fa-chevron-down ms-1 chevron-icon"></i>');
        });

        $('#route-comparison-table [data-route-id]').each(function () {
            const isMatch = $(this).attr('data-route-id') === tripState.selectedRouteId;
            $(this).toggleClass('comp-col-selected', isMatch);
        });

        $('.comparison-smart-card').each(function () {
            const isMatch = $(this).attr('data-route-id') === tripState.selectedRouteId;
            $(this).toggleClass('selected', isMatch);
            $(this).find('.comp-select-btn')
                .toggleClass('btn-primary', isMatch)
                .toggleClass('btn-outline-primary', !isMatch)
                .html(isMatch
                    ? '<i class="fa-solid fa-check me-1"></i> Đang xem lộ trình này'
                    : '<i class="fa-solid fa-arrow-right me-1"></i> Chọn phương án này');
        });

        // Đồng bộ lựa chọn với Backend
        if (tripState.sessionId && tripState.selectedRouteId) {
            $.ajax({
                url: `${API_BASE}/api/routes/select`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    session_id: tripState.sessionId,
                    route_id: tripState.selectedRouteId
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
                const maxVisible = 5;
                const photoItems = places.slice(0, maxVisible).map((p, pIdx) => {
                    const imgUrl = firstDefined(p.url_hinh_anh, DEFAULT_PLACE_IMG);
                    const timeBadge = p.arrive_time ? `${p.arrive_time} · ` : '';
                    return `
                        <div class="route-photo-item" data-photo-idx="${pIdx}" title="Nhấn để xem chi tiết ${p.ten}">
                            <img src="${imgUrl}" alt="${firstDefined(p.ten, 'Điểm đến')}" loading="lazy" referrerpolicy="no-referrer" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                            <div class="route-photo-caption">${pIdx + 1}. ${timeBadge}${p.ten}</div>
                        </div>
                    `;
                }).join('');
                let moreBadge = '';
                if (places.length > maxVisible) {
                    const rem = places.length - maxVisible;
                    moreBadge = `
                        <div class="route-photo-more-badge" title="Lộ trình còn ${rem} địa điểm tiếp theo">
                            <div class="route-photo-more-circle">+${rem}</div>
                            <span class="route-photo-more-label">Còn ${rem} điểm</span>
                        </div>
                    `;
                }
                photosHtml = `<div class="route-card-photos">${photoItems}${moreBadge}</div>`;
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
                            ${isSelected ? '<i class="fa-solid fa-circle-check me-1"></i> Đang xem chi tiết <i class="fa-solid fa-chevron-up ms-1 chevron-icon"></i>' : 'Nhấn để xem Timeline chi tiết <i class="fa-solid fa-chevron-down ms-1 chevron-icon"></i>'}
                        </span>
                    </div>
                    <h4 class="route-card-title">${(() => {
                        let t = firstDefined(r.name, r.route_name, r.route_id);
                        if (t && totalDurationStr && /Thong thả/i.test(t)) {
                            t = t.replace(/\s*·\s*Thong thả\s+[\w\d]+/gi, '') + ` · Thong thả ${totalDurationStr}`;
                        }
                        return t;
                    })()}</h4>
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
                    <div class="route-detail-wrapper">
                        ${detailTimelineHtml}
                    </div>
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

            $card.find('.btn-accept-card-route').on('click', function (e) {
                e.stopPropagation();
                tripState.selectedRouteId = r.route_id;
                $('#btn-accept-route').trigger('click');
            });

            $grid.append($card);
        });
    }

    // ============================================================
    // BẢNG SO SÁNH LỘ TRÌNH Ở CUỐI SCREEN 2
    // ============================================================
    function renderComparisonTable(routes) {
        const $cardsGrid = $('#route-comparison-cards-grid').empty();
        const $aiBox = $('#route-ai-recommendation-box').empty();
        const $table = $('#route-comparison-table').empty();

        if (!Array.isArray(routes) || routes.length === 0) return;

        // 1. Phân tích dữ liệu & tính toán các chỉ số
        const parsedRoutes = routes.map((r, idx) => {
            const rawPlaces = Array.isArray(r.places) ? r.places : (Array.isArray(r.optimized_route) ? r.optimized_route : []);
            const places = rawPlaces.filter(
                p => p.loai_hinh !== 'diem_xuat_phat' && p.id !== 'gps_current'
            );
            const totalMins = firstDefined(r.total_duration, r.total_time_minutes, 0);
            const travelMins = r.travel_time ? r.travel_time : places.reduce((acc, p) => acc + (p.travel_to_next ? p.travel_to_next : 0), 0);
            const distKm = computeRouteDistanceKm(r);
            const cost = estimateRouteCost(places);
            const costStr = formatCurrencyVND(cost);

            const cafes = places.filter(p => p.loai_hinh === 'Cafe');
            const cultures = places.filter(p => p.loai_hinh === 'Tham quan' || p.loai_hinh === 'Checkin');
            const foods = places.filter(p => p.loai_hinh === 'Ăn uống');
            const others = places.filter(p => !['Cafe', 'Tham quan', 'Checkin', 'Ăn uống'].includes(p.loai_hinh));

            const totalCount = places.length || 1;
            const culturePct = Math.round((cultures.length / totalCount) * 100);
            const cafePct = Math.round((cafes.length / totalCount) * 100);
            const foodPct = Math.round((foods.length / totalCount) * 100);
            const otherPct = Math.max(0, 100 - culturePct - cafePct - foodPct);

            // 2-3 điểm nổi bật
            const highlights = places.slice(0, 3).map(p => p.ten);

            return {
                route_id: r.route_id,
                letter: `Route ${String.fromCharCode(65 + idx)}`,
                name: firstDefined(r.name, r.route_name, r.route_id),
                places,
                totalMins,
                travelMins,
                distKm,
                cost,
                costStr,
                cafes,
                cultures,
                foods,
                others,
                culturePct,
                cafePct,
                foodPct,
                otherPct,
                highlights,
            };
        });

        // 2. Tìm giá trị cực trị để gắn nhãn danh hiệu
        const distances = parsedRoutes.map(r => r.distKm);
        const costs = parsedRoutes.map(r => r.cost);
        const minDist = Math.min(...distances);
        const minCost = Math.min(...costs);

        // Gán danh hiệu Persona cho từng Route
        parsedRoutes.forEach((st, idx) => {
            if (st.distKm === minDist && minDist <= 7.0) {
                st.badge = { text: 'Đi gần · Tiết kiệm sức', cls: 'badge-comp-speed', icon: 'fa-bolt' };
            } else if (st.cost === minCost && minCost < 300000) {
                st.badge = { text: 'Tối ưu ngân sách', cls: 'badge-comp-budget', icon: 'fa-coins' };
            } else if (st.cultures.length >= 3) {
                st.badge = { text: 'Di sản & Văn hóa', cls: 'badge-comp-culture', icon: 'fa-landmark' };
            } else if (st.cafes.length >= 2 || st.cafePct >= 30) {
                st.badge = { text: 'Cà phê chill & Sống ảo', cls: 'badge-comp-relax', icon: 'fa-mug-hot' };
            } else if (idx === 0) {
                st.badge = { text: 'Cân bằng & Toàn diện', cls: 'badge-comp-star', icon: 'fa-star' };
            } else {
                st.badge = { text: 'Trải nghiệm phong phú', cls: 'badge-comp-star', icon: 'fa-compass' };
            }
        });

        // 3. Render Hộp Lời khuyên Trợ lý AI (Hướng 3)
        const bestRoute = parsedRoutes[0];
        const shortestRoute = parsedRoutes.find(r => r.distKm === minDist) || bestRoute;
        const cultureRoute = parsedRoutes.slice().sort((a, b) => b.cultures.length - a.cultures.length)[0];

        let aiRecItems = [];
        if (bestRoute) {
            aiRecItems.push(`
                <div class="ai-rec-item">
                    <strong>🏆 Phù hợp nhất cho chuyến đi:</strong> 
                    <span class="text-primary fw-bold">${bestRoute.letter}</span> — Cân bằng lý tưởng giữa tham quan danh thắng lịch sử và ẩm thực phố cổ (${bestRoute.distKm} km, ${durationLabel(bestRoute.totalMins)}).
                </div>
            `);
        }
        if (shortestRoute && shortestRoute.route_id !== bestRoute.route_id) {
            aiRecItems.push(`
                <div class="ai-rec-item">
                    <strong>⚡ Nếu bạn muốn đi gần & đỡ mỏi:</strong> 
                    <span class="text-success fw-bold">${shortestRoute.letter}</span> — Quãng đường chỉ <strong>${shortestRoute.distKm} km</strong> (~${shortestRoute.travelMins} phút di chuyển), rất nhẹ nhàng để tránh nắng nóng hoặc tắc đường.
                </div>
            `);
        }
        if (cultureRoute && cultureRoute.cultures.length >= 2 && cultureRoute.route_id !== bestRoute.route_id && cultureRoute.route_id !== shortestRoute.route_id) {
            aiRecItems.push(`
                <div class="ai-rec-item">
                    <strong>🏛️ Nếu bạn ưu tiên tìm hiểu lịch sử:</strong> 
                    <span class="text-purple fw-bold" style="color: #6D28D9;">${cultureRoute.letter}</span> — Tập trung ${cultureRoute.cultures.length} di tích lịch sử hàng đầu Hà Nội với thời lượng dừng sâu sắc.
                </div>
            `);
        }

        $aiBox.html(`
            <div class="d-flex align-items-center gap-2 mb-2">
                <div class="ai-rec-avatar">
                    <i class="fa-solid fa-wand-magic-sparkles text-primary" style="font-size: 0.85rem;"></i>
                </div>
                <div class="fw-bold text-main" style="font-size: 0.95rem;">Trợ lý AI gợi ý lựa chọn nhanh:</div>
            </div>
            <div class="d-flex flex-column gap-2">
                ${aiRecItems.join('')}
            </div>
        `);

        // 4. Render Bộ thẻ so sánh thông minh (Hướng 1)
        parsedRoutes.forEach(st => {
            const isSelected = st.route_id === tripState.selectedRouteId;
            const distBadge = st.distKm === minDist
                ? '<span class="badge bg-success-subtle text-success border border-success-subtle px-1">Ngắn nhất</span>'
                : '<span class="comp-metric-sub">Toàn cung</span>';

            const costBadge = st.cost === minCost
                ? '<span class="badge bg-success-subtle text-success border border-success-subtle px-1">Rẻ nhất</span>'
                : '<span class="comp-metric-sub">Dự kiến</span>';

            const $card = $(`
                <div class="comparison-smart-card ${isSelected ? 'selected' : ''}" data-route-id="${st.route_id}">
                    <div>
                        <div class="d-flex align-items-center justify-content-between mb-2">
                            <span class="comp-route-letter">${st.letter}</span>
                            <span class="badge ${st.badge.cls}"><i class="fa-solid ${st.badge.icon} me-1"></i>${st.badge.text}</span>
                        </div>

                        <div class="comp-route-title mb-2 text-truncate" title="${escapeHtml(st.name)}">${escapeHtml(st.name)}</div>

                        <!-- 4 Chỉ số cốt lõi -->
                        <div class="comp-metrics-grid mb-3">
                            <div class="comp-metric-item">
                                <span class="comp-metric-label"><i class="fa-regular fa-clock me-1"></i>Thời gian</span>
                                <span class="comp-metric-val">${durationLabel(st.totalMins)}</span>
                                <span class="comp-metric-sub">Di chuyển ${st.travelMins}p</span>
                            </div>
                            <div class="comp-metric-item">
                                <span class="comp-metric-label"><i class="fa-solid fa-route me-1"></i>Quãng đường</span>
                                <span class="comp-metric-val ${st.distKm === minDist ? 'text-success fw-bold' : ''}">${st.distKm} km</span>
                                ${distBadge}
                            </div>
                            <div class="comp-metric-item">
                                <span class="comp-metric-label"><i class="fa-solid fa-wallet me-1"></i>Chi phí</span>
                                <span class="comp-metric-val ${st.cost === minCost ? 'text-success fw-bold' : ''}">~${st.costStr}</span>
                                ${costBadge}
                            </div>
                            <div class="comp-metric-item">
                                <span class="comp-metric-label"><i class="fa-solid fa-location-dot me-1"></i>Điểm dừng</span>
                                <span class="comp-metric-val">${st.places.length} điểm</span>
                                <span class="comp-metric-sub">Khám phá</span>
                            </div>
                        </div>

                        <!-- Mini progress bar cơ cấu trải nghiệm -->
                        <div class="comp-activity-wrap mb-3">
                            <div class="d-flex justify-content-between small text-secondary mb-1" style="font-size: 0.75rem;">
                                <span>Cơ cấu hoạt động</span>
                                <span>🏛️ ${st.cultures.length} · ☕ ${st.cafes.length} ${st.foods.length ? '· 🍜 ' + st.foods.length : ''}</span>
                            </div>
                            <div class="comp-activity-bar d-flex rounded-pill overflow-hidden">
                                <div style="width: ${st.culturePct}%; background-color: #3B82F6;" title="${st.cultures.length} điểm Văn hóa (${st.culturePct}%)"></div>
                                <div style="width: ${st.cafePct}%; background-color: #F59E0B;" title="${st.cafes.length} quán Cafe (${st.cafePct}%)"></div>
                                <div style="width: ${st.foodPct}%; background-color: #10B981;" title="${st.foods.length} điểm Ăn uống (${st.foodPct}%)"></div>
                                <div style="width: ${st.otherPct}%; background-color: #94A3B8;" title="${st.others.length} điểm khác"></div>
                            </div>
                        </div>

                        <!-- Điểm nhấn chính -->
                        <div class="comp-highlights mb-3">
                            <div class="small text-secondary mb-1" style="font-size: 0.75rem;">Điểm nhấn chính:</div>
                            <div class="d-flex flex-wrap gap-1">
                                ${st.highlights.map(h => `<span class="badge bg-light text-dark border text-truncate" style="max-width: 140px; font-weight: normal; font-size: 0.75rem;">${escapeHtml(h)}</span>`).join('')}
                            </div>
                        </div>
                    </div>

                    <!-- Nút chọn hành động -->
                    <div class="comp-card-action mt-2">
                        <button type="button" class="btn w-100 ${isSelected ? 'btn-primary' : 'btn-outline-primary'} btn-sm comp-select-btn">
                            ${isSelected ? '<i class="fa-solid fa-check me-1"></i> Đang xem lộ trình này' : '<i class="fa-solid fa-arrow-right me-1"></i> Chọn phương án này'}
                        </button>
                    </div>
                </div>
            `);

            $card.on('click', function () {
                selectRouteOnScreen2(st.route_id);
            });

            $cardsGrid.append($card);
        });

        // 5. Render bảng ẩn cho tương thích ngược
        let theadHtml = '<thead><tr><th>Tiêu chí</th>';
        parsedRoutes.forEach(st => {
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
            { label: 'Thời gian', getVal: st => `${durationLabel(st.totalMins)} (di chuyển ${durationLabel(st.travelMins)})` },
            { label: 'Số điểm', getVal: st => `${st.places.length} địa điểm` },
            { label: 'Quãng đường', getVal: st => `${st.distKm} km` },
            { label: 'Chi phí', getVal: st => `~${st.costStr}` },
            { label: 'Cafe', getVal: st => st.cafes.length > 0 ? `${st.cafes.length} điểm (${st.cafes.map(c => c.ten).join(', ')})` : 'Không có' },
            { label: 'Văn hóa', getVal: st => st.cultures.length > 0 ? `${st.cultures.length} điểm (${st.cultures.map(c => c.ten).join(', ')})` : 'Không có' },
            { label: 'Thư giãn', getVal: st => st.badge.text },
        ];

        let tbodyHtml = '<tbody>';
        criteriaRows.forEach(row => {
            tbodyHtml += `<tr><td>${row.label}</td>`;
            parsedRoutes.forEach(st => {
                const isSel = st.route_id === tripState.selectedRouteId ? 'comp-col-selected' : '';
                tbodyHtml += `<td class="${isSel}" data-route-id="${st.route_id}">${row.getVal(st)}</td>`;
            });
            tbodyHtml += '</tr>';
        });
        tbodyHtml += '</tbody>';
        $table.html(theadHtml + tbodyHtml);
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

        // 1. Tự động hiển thị và mở khung chat AI ở góc dưới bên phải
        $('#global-ai-assistant').removeClass('d-none');
        $('#ai-map-chat').addClass('open');

        const userMsg = firstDefined(qa.label, qa.instruction, 'Thêm địa điểm');
        appendChatBubble(escapeHtml(userMsg), 'user');
        appendChatBubble('<i class="fa-solid fa-spinner fa-spin me-2"></i>Đang tìm phương án ' + escapeHtml(userMsg.toLowerCase()) + ' phù hợp với bạn...', 'assistant loading');

        // Xác định current_ids của lộ trình đang chọn (hoặc lộ trình đầu tiên)
        let currIds = currentItineraryIds();
        if (!currIds.length && tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes) && tripState.currentRoutes.routes.length) {
            const firstR = tripState.currentRoutes.routes.find(r => r.route_id === tripState.selectedRouteId) || tripState.currentRoutes.routes[0];
            const rPlaces = Array.isArray(firstR.optimized_route) ? firstR.optimized_route : (Array.isArray(firstR.places) ? firstR.places : []);
            currIds = rPlaces.filter(p => p.id !== 'gps_current' && p.loai_hinh !== 'diem_xuat_phat').map(p => String(p.id));
        }

        const payload = {
            region: 'Hanoi',
            vehicle_type: $('#vehicle_type').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            user_preference: $('#user_preference').val(),
            current_ids: currIds,
            instruction: firstDefined(qa.instruction, qa.label),
            session_id: tripState.sessionId,
        };

        $.ajax({
            url: `${API_BASE}/api/ai-refine`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(payload),
            success: function (res) {
                $('#ai-map-chat-log .loading').remove();
                $('#ai-quick-actions .quick-action-chip').prop('disabled', false);

                tripState.sessionId = firstDefined(res.session_id, tripState.sessionId);
                tripState.aiSuggestedIds = Array.isArray(res.suggested_ids) ? res.suggested_ids : tripState.aiSuggestedIds;
                if (res.advice_text) {
                    $('#ai-advice-content').html(res.advice_text.replace(/\n/g, '<br>'));
                }

                // Nếu AI trả về danh sách Options (ví dụ 2-3 quán ăn/cafe) -> render ngay trong khung chat AI
                if (res.options && res.options.length > 0) {
                    renderAiRefineOptions(res);
                } else {
                    // Nếu AI trả về trực tiếp lộ trình mới -> cập nhật và reload Screen 2 ngay lập tức
                    $.ajax({
                        url: `${API_BASE}/api/itinerary/update`,
                        method: 'POST',
                        contentType: 'application/json',
                        data: JSON.stringify({ session_id: tripState.sessionId }),
                        success: function (updRes) {
                            if (updRes.status === 'success' && updRes.itinerary) {
                                setCurrentItinerary(updRes.itinerary);
                                refreshCurrentItinerary();
                                if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                                    renderRouteCards(tripState.currentRoutes.routes);
                                    renderComparisonTable(tripState.currentRoutes.routes);
                                }
                            }
                        }
                    });
                    if (res.advice_text) {
                        appendChatBubble(escapeHtml(res.advice_text), 'assistant');
                    }
                }

                const log = document.getElementById('ai-map-chat-log');
                if (log) log.scrollTop = log.scrollHeight;
            },
            error: function (xhr) {
                $('#ai-map-chat-log .loading').remove();
                $('#ai-quick-actions .quick-action-chip').prop('disabled', false);
                appendChatBubble("Không thể tìm phương án lúc này: " + formatErrorDetail(xhr, "Kiểm tra kết nối Backend."), 'assistant');
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
        let userPref = $('#user_preference').val().trim();
        if (!userPref) {
            userPref = 'Khám phá các điểm đến tiêu biểu tại Hà Nội';
            $('#user_preference').val(userPref);
            showCrudBanner('Đã tự động chọn chủ đề: ' + userPref);
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
        if (!tripState.currentRoutes || !tripState.currentRoutes.routes || !tripState.currentRoutes.routes.length) {
            alert('Chưa có lộ trình nào để chốt, vui lòng thử lại.');
            return;
        }
        if (!tripState.selectedRouteId) {
            tripState.selectedRouteId = tripState.currentRoutes.routes[0].route_id;
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

    // ============================================================

    // MOBILE VIEW SWITCHER (CHẾ ĐỘ XEM TRÊN DI ĐỘNG: CHIA ĐÔI | BẢN ĐỒ | LỊCH TRÌNH)

    // ============================================================

    $('.btn-view-mode').on('click', function () {

        const mode = $(this).data('mode');

        $('.btn-view-mode').removeClass('active');

        $(this).addClass('active');



        const $sec = $('#map-section');

        $sec.removeClass('mobile-mode-map mobile-mode-list');



        if (mode === 'map') {

            $sec.addClass('mobile-mode-map');

        } else if (mode === 'list') {

            $sec.addClass('mobile-mode-list');

        }

        // mode === 'split' (mặc định chia đôi: Map cố định 38vh ở trên, timeline cuộn ở dưới)



        if (typeof map !== 'undefined' && map) {

            setTimeout(() => map.invalidateSize(), 260);

        }

    });



    // Khi người dùng bấm/chạm vào một điểm trên Timeline: tự động căn giữa bản đồ đến điểm đó

    $('#timeline-list').on('click', '.timeline-item', function (e) {

        if ($(e.target).closest('button, input, a, .btn-crud-remove, .btn-crud-fav, .btn-crud-time').length) {

            return;

        }

        const pid = $(this).data('id');

        if (!pid || typeof map === 'undefined' || !map) return;



        const currentItin = getActiveItinerary();

        const routeLocs = (currentItin && (currentItin.optimized_route || currentItin.places)) || [];

        const idx = routeLocs.findIndex(p => String(p.id) === String(pid));



        if (idx >= 0) {

            const loc = routeLocs[idx];

            const lat = Number(loc.lat || loc.vi_do);

            const lon = Number(loc.lon || loc.kinh_do);

            if (lat && lon) {

                map.panTo([lat, lon], { animate: true, duration: 0.5 });

                if (mapMarkers && mapMarkers[idx]) {

                    mapMarkers[idx].openPopup();

                }

            }

        }

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
                success: function (res) {
                    if (res && res.status === 'success' && res.itinerary) {
                        setCurrentItinerary(res.itinerary);
                        refreshCurrentItinerary();
                    }
                }
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

        if (tripState.sessionId) {
            const vNames = { xe_may: 'Xe máy', o_to: 'Ô tô', di_bo: 'Đi bộ', xe_buyt: 'Xe buýt' };
            $.ajax({
                url: `${API_BASE}/api/itinerary/change-vehicle`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    session_id: tripState.sessionId,
                    vehicle_type: vType,
                }),
                success: function (res) {
                    if (res && res.status === 'success' && res.itinerary) {
                        applyDirectItineraryUpdate(res, `Đã đổi phương tiện sang ${vNames[vType] || vType} và tính lại thời gian di chuyển.`);
                    } else {
                        refreshCurrentItinerary();
                    }
                },
                error: function (xhr) {
                    refreshCurrentItinerary();
                    showCrudBanner(formatErrorDetail(xhr, 'Không thể cập nhật phương tiện trên máy chủ.'), true);
                }
            });
        } else {
            refreshCurrentItinerary();
        }
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
                    map.panTo([loc.lat, loc.lon], { animate: true, duration: 0.4 });
                    m.openPopup();
                }
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
                const $btn = $(this);
                $btn.prop('disabled', true);
                toggleUserFavorite(pid, function (isFav) {
                    $btn.prop('disabled', false);
                    if (isFav) {
                        $btn.addClass('active').html('❤️ Đã thích');
                        showCrudBanner(`Đã lưu "${loc.ten}" vào danh sách yêu thích ❤️`);
                    } else {
                        $btn.removeClass('active').html('❤️ Yêu thích');
                        showCrudBanner(`Đã bỏ "${loc.ten}" khỏi danh sách yêu thích.`);
                    }
                });
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
            const review = getAccentedReview(loc);
            let reviewSnippet = '';
            if (review && review !== 'None' && review !== 'undefined' && review !== 'null') {
                let clean = review.replace(/#\S+/g, '').replace(/\*+/g, '').replace(/\s+/g, ' ').trim();
                if (clean.length > 95) clean = clean.substring(0, 95).trim() + '...';
                if (clean) {
                    reviewSnippet = `<div class="mt-1 small text-secondary fst-italic" style="font-size: 0.76rem; line-height: 1.35;">“${escapeHtml(clean)}”</div>`;
                }
            }
            const rating = getPlaceRating(loc);

            const popupHtml = `
                <div class="map-popup-card" style="min-width: 210px; max-width: 260px; padding: 2px;">
                    <div class="fw-bold text-main" style="font-size: 0.9rem; line-height: 1.3;">
                        ${idx + 1}. ${escapeHtml(loc.ten)} ${isVisited ? '<span class="badge bg-success ms-1" style="font-size: 0.65rem;">Đã đi</span>' : ''}
                    </div>
                    <div class="small text-secondary mt-1 d-flex align-items-center gap-1" style="font-size: 0.78rem;">
                        <span>${formatCategoryLabel(loc.loai_hinh)}</span>
                        <span class="badge bg-light text-dark border ms-1">★ ${rating}</span>
                    </div>
                    <div class="small fw-semibold mt-1" style="color: #2563EB; font-size: 0.78rem;">
                        ${firstDefined(loc.arrive_time, '08:00')} – ${firstDefined(loc.depart_time, '09:00')} · ${formatCurrencyVND(estimatePlaceCost(loc))}
                    </div>
                    ${reviewSnippet}
                    <div class="mt-2 pt-1 border-top d-flex justify-content-end">
                        <button type="button" class="btn btn-xs btn-outline-primary py-0 px-2 btn-popup-more" data-idx="${idx}" data-pid="${escapeHtml(pid)}" style="font-size: 0.72rem; border-radius: 12px; cursor: pointer;">
                            Xem chi tiết ➜
                        </button>
                    </div>
                </div>
            `;

            const marker = L.marker([loc.lat, loc.lon], {
                icon: L.divIcon({
                    html: iconHtml,
                    className: '',
                    iconSize: [30, 30],
                    iconAnchor: [15, 15]
                })
            }).bindPopup(popupHtml, { maxWidth: 280, minWidth: 210 }).addTo(map);

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

    // Sự kiện mở chi tiết địa điểm khi bấm nút "Xem chi tiết ➜" trong popup Bản đồ
    $(document).on('click', '.btn-popup-more', function (e) {
        e.preventDefault();
        e.stopPropagation();
        const pIdx = parseInt($(this).attr('data-idx'), 10);
        const pid = String($(this).attr('data-pid') || '');
        const itin = getActiveItinerary();
        const routeLocs = (itin && (itin.optimized_route || itin.places)) || [];
        let targetPlace = (!isNaN(pIdx) && routeLocs[pIdx]) ? routeLocs[pIdx] : null;
        if (!targetPlace && pid && Array.isArray(tripState.currentLocationData)) {
            targetPlace = tripState.currentLocationData.find(p => String(p.id) === pid);
        }
        if (targetPlace) {
            openPlaceDetailModal(targetPlace);
        } else {
            console.warn('Place not found for detail popup:', pIdx, pid);
        }
    });

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
            error: function (xhr) {
                itinerary.optimized_route = rawPlaces;
                itinerary.places = rawPlaces;
                refreshCurrentItinerary();
                const errText = formatErrorDetail(xhr, 'Không thể cập nhật thứ tự điểm do lỗi kết nối. Đang hoàn tác...');
                showCrudBanner(errText, true);
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

    let replaceModalState = {
        loc: null,
        candidates: [],
        currentCategory: 'same',
        searchQuery: ''
    };

    function removeVietnameseTones(str) {
        if (!str) return '';
        return String(str)
            .normalize('NFD')
            .replace(/[̀-ͯ]/g, '')
            .replace(/[đĐ]/g, 'd')
            .toLowerCase()
            .trim();
    }

    function renderReplaceCandidatesList(modalEl) {
        const $list = $('#replace-candidates-list');
        const loc = replaceModalState.loc;
        if (!loc) return;

        let filtered = (replaceModalState.candidates || []).slice();

        // 1. Lọc theo category
        const cat = replaceModalState.currentCategory;
        if (cat === 'same') {
            const sameList = filtered.filter(c => c.same_category || c.compatible_category);
            if (sameList.length > 0) {
                filtered = sameList;
            }
        } else if (cat) {
            filtered = filtered.filter(c => {
                const cCat = removeVietnameseTones(c.loai_hinh || '');
                return cCat.includes(removeVietnameseTones(cat));
            });
        }

        // 2. Lọc theo search query
        const q = removeVietnameseTones(replaceModalState.searchQuery || '');
        if (q) {
            filtered = filtered.filter(c => {
                const name = removeVietnameseTones(c.ten || '');
                const rev = removeVietnameseTones(firstDefined(c.review, c.thong_tin_chi_tiet, ''));
                return name.includes(q) || rev.includes(q);
            });
        }

        // Cập nhật tip ngữ cảnh
        const $tip = $('#replace-context-text');
        const locCatName = formatCategoryLabel(loc.loai_hinh);
        if (cat === 'same') {
            const hasExact = filtered.some(c => c.same_category);
            if (hasExact) {
                $tip.html(`Gợi ý các địa điểm <strong>cùng loại hình (${escapeHtml(locCatName)})</strong>, gần lộ trình và đang mở cửa:`);
            } else {
                $tip.html(`Các điểm <strong>${escapeHtml(locCatName)}</strong> khác đã có trong lộ trình. Dưới đây là các gợi ý lân cận tương đương:`);
            }
        } else if (cat === 'Ăn uống') {
            $tip.html(`Gợi ý nhà hàng, quán ăn đặc sản Hà Nội lân cận để đổi bữa:`);
        } else if (cat === 'Cafe') {
            $tip.html(`Gợi ý quán cafe đẹp, không gian thư giãn gần cung đường:`);
        } else if (cat === 'TTTM') {
            $tip.html(`Gợi ý trung tâm thương mại, khu vui chơi & rạp chiếu phim gần đó:`);
        } else if (cat === 'Tham quan') {
            $tip.html(`Gợi ý di tích văn hóa, điểm tham quan danh thắng thủ đô:`);
        } else {
            $tip.html(`Tất cả địa điểm gợi ý gần lộ trình di chuyển:`);
        }

        if (filtered.length === 0) {
            $list.html(`
                <div class="text-center py-4 px-3" style="background: var(--bg-secondary); border-radius: var(--radius-md);">
                    <i class="fa-solid fa-filter-circle-xmark fa-2x text-muted mb-2"></i>
                    <div class="fw-bold text-main mb-1">Không tìm thấy địa điểm phù hợp</div>
                    <p class="small text-secondary mb-3">Không có điểm nào khớp với tiêu chí bộ lọc trong khung giờ này.</p>
                    <button type="button" class="btn btn-sm btn-outline-primary" id="btn-reset-replace-filter">
                        <i class="fa-solid fa-rotate-left me-1"></i> Xem tất cả địa điểm
                    </button>
                </div>
            `);
            $('#btn-reset-replace-filter').on('click', function () {
                replaceModalState.currentCategory = '';
                replaceModalState.searchQuery = '';
                $('#replace-search-input').val('');
                $('#replace-category-chips .btn').removeClass('active');
                $('#replace-category-chips .btn[data-cat=""]').addClass('active');
                renderReplaceCandidatesList(modalEl);
            });
            return;
        }

        $list.empty();
        filtered.forEach(c => {
            const img = firstDefined(c.url_hinh_anh, DEFAULT_PLACE_IMG);
            const rating = getPlaceRating(c);
            const openBadge = c.open_time && c.close_time ? `${c.open_time}–${c.close_time}` : 'Đang mở cửa';
            const distFromTarget = c.dist_from_target_km !== undefined ? c.dist_from_target_km : (c.distance_km !== undefined ? c.distance_km : 1.2);
            
            // Badge phù hợp loại hình
            let badgeMatch = '';
            if (c.same_category) {
                badgeMatch = '<span class="badge bg-success-subtle text-success border border-success-subtle"><i class="fa-solid fa-check me-1"></i>Cùng loại hình</span>';
            } else if (c.compatible_category) {
                badgeMatch = '<span class="badge bg-info-subtle text-info border border-info-subtle"><i class="fa-solid fa-sparkles me-1"></i>Tương đương</span>';
            } else {
                badgeMatch = `<span class="badge bg-light text-secondary border">${formatCategoryLabel(c.loai_hinh)}</span>`;
            }

            // Route impact badge
            let impactBadge = '';
            if (c.delta_dist_km !== undefined && c.delta_dist_km !== null) {
                if (c.delta_dist_km < 0) {
                    impactBadge = `<span class="badge bg-success-subtle text-success border border-success-subtle" title="Tiết kiệm quãng đường"><i class="fa-solid fa-route me-1"></i>Ngắn hơn ${Math.abs(c.delta_dist_km)} km</span>`;
                } else if (c.delta_dist_km > 0) {
                    impactBadge = `<span class="badge bg-light text-secondary border" title="Độ lệch so với cung đường hiện tại">+${c.delta_dist_km} km</span>`;
                } else {
                    impactBadge = `<span class="badge bg-light text-secondary border">Giữ nguyên cự ly</span>`;
                }
            }

            // Duration badge
            const visitTime = c.time || c.thoi_gian_tham_quan_phut || 45;

            // Accented review snippet
            const revText = getAccentedReview(c) || firstDefined(c.review, c.thong_tin_chi_tiet, '');

            const $row = $(`
                <div class="crud-candidate-card">
                    <div class="d-flex align-items-center gap-3" style="min-width: 0; flex: 1;">
                        <img src="${img}" alt="${escapeHtml(c.ten)}" class="crud-cand-img" referrerpolicy="no-referrer" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                        <div style="min-width: 0;">
                            <div class="d-flex flex-wrap align-items-center gap-2 mb-1">
                                <span class="fw-bold text-main text-truncate" style="max-width: 280px;">${escapeHtml(c.ten)}</span>
                                <span class="badge bg-light text-dark border">★ ${rating}</span>
                                ${badgeMatch}
                            </div>
                            <div class="small text-secondary d-flex flex-wrap align-items-center gap-1 gap-md-2 mb-1">
                                <span><i class="fa-solid fa-location-arrow me-1"></i>Cách ${distFromTarget} km</span>
                                <span>·</span>
                                <span><i class="fa-regular fa-clock me-1"></i>⏱ ${visitTime}p</span>
                                ${impactBadge ? '<span>·</span>' + impactBadge : ''}
                                <span>·</span>
                                <span class="${c.is_open ? 'text-success' : 'text-danger'}">🕒 ${openBadge}</span>
                            </div>
                            <div class="small text-secondary fst-italic text-truncate" style="max-width: 440px;">${escapeHtml(revText)}</div>
                        </div>
                    </div>
                    <div class="crud-card-action ms-md-3">
                        <button type="button" class="btn-primary-clean btn-sm text-nowrap px-3 py-1">
                            <i class="fa-solid fa-arrows-rotate me-1"></i> Chọn thay thế
                        </button>
                    </div>
                </div>
            `);

            $row.find('button').on('click', function () {
                executeReplacePlace(String(loc.id), String(c.id), modalEl);
            });
            $list.append($row);
        });
    }

    function openReplacePlaceModal(loc) {
        replaceModalState.loc = loc;
        replaceModalState.candidates = [];
        replaceModalState.currentCategory = 'same';
        replaceModalState.searchQuery = '';

        $('#replace-old-place-id').val(String(loc.id));
        $('#replace-old-place-name').text(loc.ten);
        $('#replace-old-place-cat').text(formatCategoryLabel(loc.loai_hinh));
        
        const locTime = firstDefined(loc.thoi_gian_tham_quan_phut, loc.time, 45);
        $('#replace-old-place-time').html(`<i class="fa-regular fa-clock me-1"></i>${locTime} phút`);

        if (loc.arrive_time && loc.leave_time) {
            $('#replace-old-place-slot').text(`${loc.arrive_time}–${loc.leave_time}`).show();
        } else {
            $('#replace-old-place-slot').hide();
        }

        $('#replace-search-input').val('');
        $('#replace-category-chips .btn').removeClass('active');
        $('#replace-category-chips .btn[data-cat="same"]').addClass('active');

        const $list = $('#replace-candidates-list').html(
            '<div class="text-center py-4 text-secondary"><i class="fa-solid fa-spinner fa-spin me-2"></i>Đang tìm địa điểm thay thế tối ưu...</div>'
        );

        const modalEl = document.getElementById('replacePlaceModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }

        // Đăng ký event cho search và category chips
        $('#replace-search-input').off('input').on('input', function () {
            replaceModalState.searchQuery = $(this).val();
            renderReplaceCandidatesList(modalEl);
        });

        $('#replace-category-chips .btn').off('click').on('click', function () {
            $('#replace-category-chips .btn').removeClass('active');
            $(this).addClass('active');
            replaceModalState.currentCategory = $(this).data('cat');
            renderReplaceCandidatesList(modalEl);
        });

        $.ajax({
            url: `${API_BASE}/api/itinerary/replacement-candidates`,
            method: 'GET',
            data: {
                session_id: tripState.sessionId,
                place_id: String(loc.id),
                limit: 25
            },
            success: function (res) {
                replaceModalState.candidates = Array.isArray(res.candidates) ? res.candidates : [];
                if (replaceModalState.candidates.length === 0) {
                    $list.html('<div class="text-center py-3 text-secondary">Không tìm thấy địa điểm thay thế nào khác phù hợp trong khung giờ này.</div>');
                    return;
                }
                renderReplaceCandidatesList(modalEl);
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
            $list.html('<div class="text-center py-4 text-muted"><i class="fa-solid fa-magnifying-glass fs-3 mb-2 d-block text-secondary opacity-50"></i>Không tìm thấy địa điểm nào phù hợp với bộ lọc hiện tại.</div>');
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
                const $btn = $(this);
                if ($btn.prop('disabled')) return;
                $btn.prop('disabled', true).html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang thêm...');
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
                    },
                    complete: function () {
                        $btn.prop('disabled', false).html('＋ Thêm vào');
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

    // ============================================================
    // VẼ HÌNH CHỮ NHẬT BO GÓC TRÊN CANVAS 2D
    // ============================================================
    function roundRect(ctx, x, y, width, height, radius) {
        if (ctx.roundRect) {
            ctx.beginPath();
            ctx.roundRect(x, y, width, height, radius);
            return;
        }
        ctx.beginPath();
        ctx.moveTo(x + radius, y);
        ctx.lineTo(x + width - radius, y);
        ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
        ctx.lineTo(x + width, y + height - radius);
        ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
        ctx.lineTo(x + radius, y + height);
        ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
        ctx.lineTo(x, y + radius);
        ctx.quadraticCurveTo(x, y, x + radius, y);
        ctx.closePath();
    }

    function showImageDownloadSuccessModal(dataUrl, filename) {
        $('#itinerary-preview-img').attr('src', dataUrl);
        $('#modal-btn-download-again').attr('href', dataUrl).attr('download', filename);
        const modalEl = document.getElementById('imagePreviewModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }
        showCrudBanner(`Đã tải xuống ảnh lịch trình: "${filename}"`);
    }

    // ============================================================
    // TẢI XUỐNG ẢNH LỘ TRÌNH DU LỊCH CHẤT LƯỢNG CAO (CANVAS PNG)
    // ============================================================
    function downloadItineraryImage() {
        const itinerary = getActiveItinerary();
        if (!itinerary) {
            showCrudBanner('Vui lòng chọn hoặc tạo một lộ trình trước khi tải ảnh.', true);
            return;
        }
        const routeLocs = Array.isArray(itinerary.optimized_route)
            ? itinerary.optimized_route
            : (Array.isArray(itinerary.places) ? itinerary.places : []);
        if (routeLocs.length === 0) {
            showCrudBanner('Lộ trình chưa có địa điểm nào để xuất ảnh.', true);
            return;
        }

        const vType = tripState.baseParams ? tripState.baseParams.vehicle_type : 'xe_may';
        const vehicleNames = {
            'xe_may': 'Xe máy',
            'xe_dap': 'Xe đạp',
            'di_bo': 'Đi bộ',
            'o_to': 'Ô tô / Taxi'
        };
        const vehicleLabel = vehicleNames[vType] || 'Xe máy';
        const routeName = itinerary.route_name || itinerary.name || 'Lộ trình gợi ý Du Lịch Hà Nội';
        const dateStr = (tripState.baseParams && tripState.baseParams.trip_date)
            ? tripState.baseParams.trip_date
            : new Date().toLocaleDateString('vi-VN');
        const totalTimeHours = itinerary.total_time
            ? (itinerary.total_time >= 60 ? `${Math.floor(itinerary.total_time / 60)}h${Math.round(itinerary.total_time % 60).toString().padStart(2, '0')}` : `${Math.round(itinerary.total_time)} phút`)
            : '--';
        const totalDistKm = itinerary.total_distance
            ? `${(Number(itinerary.total_distance)).toFixed(1)} km`
            : '--';
        const totalCostStr = (itinerary.total_cost || itinerary.estimated_cost)
            ? formatCurrencyVND(itinerary.total_cost || itinerary.estimated_cost)
            : 'Theo chi tiêu thực tế';

        // Độ phân giải cao (Retina 2x)
        const scale = 2;
        const width = 960;
        const headerH = 250;
        const footerH = 140;
        const cardH = 175;
        const connectorH = 50;
        const totalHeight = headerH + (routeLocs.length * cardH) + Math.max(0, (routeLocs.length - 1) * connectorH) + footerH;

        const canvas = document.createElement('canvas');
        canvas.width = width * scale;
        canvas.height = totalHeight * scale;
        const ctx = canvas.getContext('2d');
        ctx.scale(scale, scale);

        // 1. Nền tổng thể
        ctx.fillStyle = '#F8FAFC';
        ctx.fillRect(0, 0, width, totalHeight);

        // 2. Header Card (Gradient xanh navy sang trọng)
        const grad = ctx.createLinearGradient(0, 0, width, headerH);
        grad.addColorStop(0, '#1E3A8A');
        grad.addColorStop(1, '#2563EB');
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, width, headerH);

        // Họa tiết trang trí header
        ctx.fillStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.beginPath();
        ctx.arc(width - 80, 50, 140, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(80, 200, 80, 0, Math.PI * 2);
        ctx.fill();

        // Brand Title
        ctx.fillStyle = '#93C5FD';
        ctx.font = 'bold 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText('✦ TERAVUE · HỆ THỐNG LẬP LỘ TRÌNH DU LỊCH THÔNG MINH HÀ NỘI', 40, 45);

        // Tên lộ trình
        ctx.fillStyle = '#FFFFFF';
        ctx.font = 'bold 28px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        let displayTitle = routeName;
        if (ctx.measureText(displayTitle).width > width - 80) {
            while (ctx.measureText(displayTitle + '...').width > width - 80 && displayTitle.length > 10) {
                displayTitle = displayTitle.substring(0, displayTitle.length - 1);
            }
            displayTitle += '...';
        }
        ctx.fillText(displayTitle, 40, 92);

        // Stats Pills trong Header
        const pills = [
            `📅 Ngày: ${dateStr}`,
            `⏱ ${totalTimeHours}`,
            `🚗 ${totalDistKm}`,
            `📍 ${routeLocs.length} điểm`,
            `💰 ${totalCostStr}`
        ];
        
        let pillX = 40;
        const pillY = 125;
        ctx.font = 'bold 14.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        pills.forEach(pill => {
            const pWidth = ctx.measureText(pill).width + 24;
            ctx.fillStyle = 'rgba(255, 255, 255, 0.2)';
            roundRect(ctx, pillX, pillY, pWidth, 36, 18);
            ctx.fill();
            ctx.fillStyle = '#FFFFFF';
            ctx.fillText(pill, pillX + 12, pillY + 23);
            pillX += pWidth + 12;
        });

        // Ghi chú phương tiện
        ctx.fillStyle = 'rgba(255, 255, 255, 0.88)';
        ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText(`Phương tiện: ${vehicleLabel} · Lịch trình đã được tối ưu hóa theo cửa sổ thời gian và khoảng cách di chuyển`, 40, 205);

        // 3. Render danh sách từng địa điểm
        let currentY = headerH + 30;

        routeLocs.forEach((loc, idx) => {
            const arriveT = firstDefined(loc.arrive_time, '--:--');
            const departT = firstDefined(loc.depart_time, '--:--');
            const duration = firstDefined(loc.visit_time, loc.time, 60);
            const catLabel = formatCategoryLabel(loc.loai_hinh);
            const rating = getPlaceRating(loc);
            const address = firstDefined(loc.dia_chi, loc.mo_ta, 'Hà Nội, Việt Nam');
            const price = formatPlacePrice(loc);
            const reviewText = getAccentedReview(loc);
            const isVisited = visitedPlaceIds.has(String(firstDefined(loc.id)));

            // Thẻ card trắng bo tròn
            ctx.fillStyle = '#FFFFFF';
            roundRect(ctx, 40, currentY, width - 80, cardH - 15, 16);
            ctx.fill();

            // Viền card
            ctx.strokeStyle = isVisited ? '#10B981' : '#E2E8F0';
            ctx.lineWidth = isVisited ? 2.5 : 1.2;
            ctx.stroke();

            // Badge tròn số thứ tự
            const badgeColor = isVisited ? '#10B981' : (idx === 0 ? '#2563EB' : (idx === routeLocs.length - 1 ? '#DC2626' : '#3B82F6'));
            ctx.fillStyle = badgeColor;
            ctx.beginPath();
            ctx.arc(85, currentY + 45, 22, 0, Math.PI * 2);
            ctx.fill();

            ctx.fillStyle = '#FFFFFF';
            ctx.font = 'bold 17px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(isVisited ? '✓' : String(idx + 1), 85, currentY + 51);
            ctx.textAlign = 'left';

            // Tên địa điểm
            ctx.fillStyle = '#0F172A';
            ctx.font = 'bold 19px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            let placeName = loc.ten;
            if (ctx.measureText(placeName).width > width - 360) {
                while (ctx.measureText(placeName + '...').width > width - 360 && placeName.length > 5) {
                    placeName = placeName.substring(0, placeName.length - 1);
                }
                placeName += '...';
            }
            ctx.fillText(placeName, 125, currentY + 38);

            // Time slot badge
            const timeBadge = `${arriveT} – ${departT} (${duration}p)`;
            ctx.font = 'bold 13.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            const timeW = ctx.measureText(timeBadge).width + 20;
            ctx.fillStyle = isVisited ? '#ECFDF5' : '#EFF6FF';
            roundRect(ctx, width - 60 - timeW, currentY + 18, timeW, 30, 15);
            ctx.fill();
            ctx.fillStyle = isVisited ? '#059669' : '#2563EB';
            ctx.fillText(timeBadge, width - 60 - timeW + 10, currentY + 38);

            // Thể loại | Đánh giá | Chi phí
            ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.fillStyle = '#64748B';
            ctx.fillText(`🏷️ ${catLabel}   |   ★ ${rating}   |   💵 ${price}`, 125, currentY + 66);

            // Địa chỉ
            ctx.fillStyle = '#475569';
            ctx.font = '13.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            let addrStr = '📍 ' + address;
            if (ctx.measureText(addrStr).width > width - 170) {
                addrStr = addrStr.substring(0, 75) + '...';
            }
            ctx.fillText(addrStr, 125, currentY + 92);

            // Trích dẫn review tiếng Việt có dấu
            ctx.fillStyle = '#334155';
            ctx.font = 'italic 13.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            let revStr = '“' + reviewText + '”';
            if (ctx.measureText(revStr).width > width - 170) {
                revStr = revStr.substring(0, 85) + '...”';
            }
            ctx.fillText(revStr, 125, currentY + 120);

            currentY += cardH - 15;

            // Chặng di chuyển nối giữa 2 điểm
            if (idx < routeLocs.length - 1) {
                const hasNext = (loc.travel_to_next > 0) || (loc.distance_to_next > 0);
                const distStr = loc.distance_to_next ? `${loc.distance_to_next.toFixed(1)} km` : '';
                const timeStr = loc.travel_to_next ? `${Math.round(loc.travel_to_next)} phút` : '';
                const travelLabel = hasNext ? `>>> Di chuyển: ~${timeStr} (${distStr}) bằng ${vehicleLabel.toLowerCase()}` : `>>> Di chuyển sang Điểm ${idx + 2}`;

                ctx.strokeStyle = '#CBD5E1';
                ctx.lineWidth = 2;
                ctx.setLineDash([4, 4]);
                ctx.beginPath();
                ctx.moveTo(85, currentY);
                ctx.lineTo(85, currentY + connectorH);
                ctx.stroke();
                ctx.setLineDash([]);

                // Pill chặng
                ctx.fillStyle = '#E2E8F0';
                ctx.font = '13px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                const connW = ctx.measureText(travelLabel).width + 24;
                roundRect(ctx, 125, currentY + 10, connW, 28, 14);
                ctx.fill();
                ctx.fillStyle = '#475569';
                ctx.fillText(travelLabel, 137, currentY + 28);

                currentY += connectorH;
            }
        });

        // 4. Footer
        currentY += 25;
        ctx.fillStyle = '#FFFFFF';
        roundRect(ctx, 40, currentY, width - 80, footerH - 40, 16);
        ctx.fill();
        ctx.strokeStyle = '#E2E8F0';
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = '#0F172A';
        ctx.font = 'bold 15px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText('💡 Lưu ý hành trình:', 65, currentY + 32);

        ctx.fillStyle = '#64748B';
        ctx.font = '13.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText('• Quý khách vui lòng mặc trang phục lịch sự khi ghé thăm các di tích văn hóa - tâm linh (Lăng Bác, Đền, Chùa).', 65, currentY + 56);
        ctx.fillText('• Chú ý bảo quản tư trang cá nhân tại nơi đông người & chúc bạn có một chuyến đi Hà Nội an toàn, trọn vẹn!', 65, currentY + 78);

        // Xuất file ảnh PNG
        const safeName = routeName.replace(/[\/:*?"<>|]/g, '_').replace(/\s+/g, '_');
        const safeDate = String(dateStr).replace(/[\/:*?"<>|]/g, '-');
        const filename = `Lich_Trinh_Du_Lich_Ha_Noi_${safeName}_${safeDate}.png`;

        try {
            canvas.toBlob(function (blob) {
                if (!blob) {
                    const dataUrl = canvas.toDataURL('image/png');
                    triggerDownload(dataUrl, filename);
                    showImageDownloadSuccessModal(dataUrl, filename);
                    return;
                }
                const url = URL.createObjectURL(blob);
                triggerDownload(url, filename);
                showImageDownloadSuccessModal(url, filename);
            }, 'image/png');
        } catch (err) {
            console.error('Canvas export error:', err);
            const dataUrl = canvas.toDataURL('image/png');
            triggerDownload(dataUrl, filename);
            showImageDownloadSuccessModal(dataUrl, filename);
        }

        function triggerDownload(u, fn) {
            const a = document.createElement('a');
            a.href = u;
            a.download = fn;
            document.body.appendChild(a);
            a.click();
            setTimeout(() => {
                document.body.removeChild(a);
            }, 300);
        }
    }


    $(document).on('click', '#btn-download-itinerary', function () {
        downloadItineraryImage();
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
            if (tripState.sessionId) {
                loadChatHistory(tripState.sessionId);
            }
        }
    });

    $('#btn-close-ai-chat').click(function () {
        $('#ai-map-chat').removeClass('open');
    });

    function currentItineraryIds() {
        const itinerary = getActiveItinerary();
        if (itinerary) {
            const list = Array.isArray(itinerary.optimized_route) ? itinerary.optimized_route : (Array.isArray(itinerary.places) ? itinerary.places : []);
            const ids = list
                .map(place => place.id)
                .filter(id => id && id !== 'gps_current' && id !== 'custom_start');
            if (ids.length) return ids.map(String);
        }
        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes) && tripState.currentRoutes.routes.length) {
            const r = tripState.currentRoutes.routes.find(x => x.route_id === tripState.selectedRouteId) || tripState.currentRoutes.routes[0];
            const rPlaces = Array.isArray(r.optimized_route) ? r.optimized_route : (Array.isArray(r.places) ? r.places : []);
            return rPlaces
                .filter(p => p && p.id && p.id !== 'gps_current' && p.id !== 'custom_start' && p.loai_hinh !== 'diem_xuat_phat')
                .map(p => String(p.id));
        }
        return [];
    }

    function escapeHtml(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function appendChatBubble(text, who) {
        $('#ai-map-chat-log').append(`<div class="chat-bubble ${who}">${text}</div>`);
        const log = document.getElementById('ai-map-chat-log');
        if (log) log.scrollTop = log.scrollHeight;
    }

    function renderAiRefineOptions(refineRes) {
        if (refineRes.advice_text) {
            appendChatBubble(escapeHtml(refineRes.advice_text), 'assistant');
        }

        const options = refineRes.options || [];
        if (!options.length) return;

        let html = `<div class="ai-refine-options-container">`;
        html += `<div class="ai-options-header"><i class="fa-solid fa-list-check me-1 text-primary"></i> Lựa chọn đề xuất cho bạn:</div>`;

        options.forEach((opt, idx) => {
            const starStr = opt.rating ? `<span class="opt-rating text-warning"><i class="fa-solid fa-star"></i> ${Number(opt.rating).toFixed(1)}</span>` : '';
            const distStr = opt.distance_km != null ? `<span class="opt-dist text-primary ms-2"><i class="fa-solid fa-location-arrow"></i> Cách ${opt.distance_km} km</span>` : '';
            const tagStr = (opt.tags || []).map(t => `<span class="opt-tag-pill">${escapeHtml(t)}</span>`).join(' ');

            html += `
                <div class="ai-option-item" data-place-id="${escapeHtml(opt.place_id)}">
                    <div class="ai-option-item-header">
                        <span class="ai-option-num">${idx + 1}</span>
                        <div class="ai-option-title-block flex-grow-1">
                            <span class="ai-option-name">${escapeHtml(opt.ten || opt.name || opt.title || (tripState.currentLocationData && tripState.currentLocationData.find(p => String(p.id) === String(opt.place_id))?.ten) || 'Địa điểm đề xuất')}</span>
                            <span class="ai-option-cat">${escapeHtml(opt.loai_hinh || (tripState.currentLocationData && tripState.currentLocationData.find(p => String(p.id) === String(opt.place_id))?.loai_hinh) || '')}</span>
                        </div>
                        <div class="ai-option-badges ms-auto">
                            ${starStr}
                            ${distStr}
                        </div>
                    </div>
                    ${tagStr ? `<div class="ai-option-tags-row mt-1">${tagStr}</div>` : ''}
                    <div class="ai-option-reason mt-1">${escapeHtml(opt.reason || '')}</div>
                    <div class="ai-option-action-row mt-2 text-end">
                        <button type="button" class="btn btn-sm btn-outline-primary btn-apply-ai-opt"
                                data-place-id="${escapeHtml(opt.place_id)}"
                                data-place-name="${escapeHtml(opt.ten)}"
                                data-target-id="${escapeHtml(refineRes.target_place_id || '')}"
                                data-target-name="${escapeHtml(refineRes.target_place_name || '')}"
                                data-action="${escapeHtml(opt.action || 'replace')}">
                            <i class="fa-solid fa-check me-1"></i> Chọn địa điểm này
                        </button>
                    </div>
                </div>
            `;
        });
        html += `</div>`;
        $('#ai-map-chat-log').append(html);

        const log = document.getElementById('ai-map-chat-log');
        if (log) log.scrollTop = log.scrollHeight;
    }

    $(document).on('click', '.btn-apply-ai-opt', function () {
        const $btn = $(this);
        const $container = $btn.closest('.ai-refine-options-container');
        const placeId = String($btn.data('place-id'));
        const placeName = String($btn.data('place-name'));
        const targetId = String($btn.data('target-id') || '');
        const targetName = String($btn.data('target-name') || '');
        const action = String($btn.data('action') || 'replace');

        if ($btn.prop('disabled')) return;
        $container.find('.btn-apply-ai-opt').prop('disabled', true).addClass('opacity-50');
        $btn.html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang áp dụng...').removeClass('opacity-50');

        appendChatBubble(`Đã chọn: <b>${escapeHtml(placeName)}</b>`, 'user');

        if (action === 'replace' && targetId) {
            $.ajax({
                url: `${API_BASE}/api/itinerary/replace`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    session_id: tripState.sessionId,
                    old_place_id: targetId,
                    new_place_id: placeId,
                    ai_advice: `Đã thay '${targetName || 'địa điểm cũ'}' bằng '${placeName}'.`
                }),
                success: function (res) {
                    if (res.status === 'success' && res.itinerary) {
                        setCurrentItinerary(res.itinerary);
                        refreshCurrentItinerary();
                        // RELOAD LỘ TRÌNH ĐANG ĐƯỢC CHỌN Ở SCREEN 2
                        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                            renderRouteCards(tripState.currentRoutes.routes);
                            renderComparisonTable(tripState.currentRoutes.routes);
                        }
                        $btn.html('<i class="fa-solid fa-circle-check me-1"></i> Đã chọn');
                        appendChatBubble(`Tuyệt vời! Đã thay <b>${escapeHtml(targetName || 'địa điểm cũ')}</b> bằng <b>${escapeHtml(placeName)}</b>. Bản đồ và timeline đã được cập nhật!`, 'assistant');
                    } else {
                        $btn.html('<i class="fa-solid fa-rotate me-1"></i> Thử lại');
                        $container.find('.btn-apply-ai-opt').prop('disabled', false).removeClass('opacity-50');
                        appendChatBubble('Không thể thay thế địa điểm: ' + firstDefined(res.message, 'Lỗi không xác định.'), 'assistant');
                    }
                },
                error: function (xhr) {
                    $btn.html('<i class="fa-solid fa-rotate me-1"></i> Thử lại');
                    $container.find('.btn-apply-ai-opt').prop('disabled', false).removeClass('opacity-50');
                    appendChatBubble('Lỗi kết nối khi thay thế: ' + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'vui lòng thử lại.'), 'assistant');
                }
            });
        } else {
            // Action is add
            $.ajax({
                url: `${API_BASE}/api/itinerary/add`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    session_id: tripState.sessionId,
                    place_id: placeId,
                    ai_advice: `Đã thêm '${placeName}' vào lịch trình.`
                }),
                success: function (res) {
                    if (res.status === 'success' && res.itinerary) {
                        setCurrentItinerary(res.itinerary);
                        refreshCurrentItinerary();
                        // RELOAD LỘ TRÌNH ĐANG ĐƯỢC CHỌN Ở SCREEN 2
                        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                            renderRouteCards(tripState.currentRoutes.routes);
                            renderComparisonTable(tripState.currentRoutes.routes);
                        }
                        $btn.html('<i class="fa-solid fa-circle-check me-1"></i> Đã chọn');
                        appendChatBubble(`Tuyệt vời! Đã thêm <b>${escapeHtml(placeName)}</b> vào lịch trình của bạn. Lộ trình trên màn hình đã được cập nhật!`, 'assistant');
                    } else {
                        $btn.html('<i class="fa-solid fa-rotate me-1"></i> Thử lại');
                        $container.find('.btn-apply-ai-opt').prop('disabled', false).removeClass('opacity-50');
                        appendChatBubble('Không thể thêm địa điểm: ' + firstDefined(res.message, 'Lỗi không xác định.'), 'assistant');
                    }
                },
                error: function (xhr) {
                    $btn.html('<i class="fa-solid fa-rotate me-1"></i> Thử lại');
                    $container.find('.btn-apply-ai-opt').prop('disabled', false).removeClass('opacity-50');
                    appendChatBubble('Lỗi kết nối khi thêm địa điểm: ' + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'vui lòng thử lại.'), 'assistant');
                }
            });
        }
    });

    function loadChatHistory(sessionId) {
        if (!sessionId) return;
        $.ajax({
            url: `${API_BASE}/api/chat-history?session_id=${encodeURIComponent(sessionId)}`,
            method: 'GET',
            success: function (res) {
                if (res.status === 'success' && Array.isArray(res.chat_history) && res.chat_history.length > 0) {
                    const $log = $('#ai-map-chat-log');
                    const existingCount = $log.children().length;
                    if (existingCount <= 1) {
                        $log.empty();
                        res.chat_history.forEach(msg => {
                            if (msg.role === 'user') {
                                appendChatBubble(escapeHtml(msg.content), 'user');
                            } else if (msg.role === 'assistant') {
                                if (msg.options && msg.options.length) {
                                    renderAiRefineOptions({
                                        advice_text: msg.content,
                                        options: msg.options,
                                        target_place_id: msg.target_place_id,
                                        target_place_name: msg.target_place_name,
                                    });
                                } else {
                                    appendChatBubble(escapeHtml(msg.content), 'assistant');
                                }
                            }
                        });
                    }
                }
            }
        });
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
        appendChatBubble(escapeHtml(instruction), 'user');
        $('#ai_map_instruction').val('').prop('disabled', true);
        $('#btn-send-ai-chat').prop('disabled', true);
        appendChatBubble('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang tìm kiếm phương án phù hợp...', 'assistant loading');

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

                // 1. Nếu AI trả về danh sách OPTIONS để user lựa chọn (thay vì tự quyết định một điểm duy nhất)
                if (refineRes.options && refineRes.options.length > 0 && refineRes.intent === 'options') {
                    renderAiRefineOptions(refineRes);
                    isAiUpdating = false;
                    $('#ai_map_instruction').prop('disabled', false);
                    $('#btn-send-ai-chat').prop('disabled', false);
                    return;
                }

                // 2. Nếu là lựa chọn từ turn trước (multi-turn) hoặc direct update
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
                    'Xin lỗi, mình chưa hiểu ý bạn lắm: ' + formatErrorDetail(xhr, 'vui lòng thử lại.'),
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
    // 1. XÓA LỊCH SỬ CHAT TRONG PHIÊN (#btn-clear-ai-chat)
    // ============================================================
    $('#btn-clear-ai-chat').on('click', function () {
        if (!tripState.sessionId) {
            $('#ai-map-chat-log').html(
                '<div class="chat-bubble assistant">Chào bạn! Bạn có thể nhắn mình thêm quán ăn, đổi điểm tham quan hoặc điều chỉnh nhịp độ bất cứ lúc nào nhé.</div>'
            );
            return;
        }

        if (!confirm('Bạn có chắc muốn làm mới lịch sử hội thoại AI trong phiên này?')) return;

        $.ajax({
            url: `${API_BASE}/api/chat-history?session_id=${encodeURIComponent(tripState.sessionId)}`,
            method: 'DELETE',
            success: function () {
                $('#ai-map-chat-log').html(
                    '<div class="chat-bubble assistant">Đã làm mới lịch sử trò chuyện. Mình sẵn sàng hỗ trợ bạn tinh chỉnh lộ trình tiếp theo!</div>'
                );
            },
            error: function () {
                $('#ai-map-chat-log').html(
                    '<div class="chat-bubble assistant">Đã làm mới lịch sử trò chuyện.</div>'
                );
            }
        });
    });

    // ============================================================
    // 2. MODAL YÊU THÍCH (#favoritesModal)
    // ============================================================
    function renderFavoritesModal() {
        const $container = $('#favorites-list-container');
        if (!currentUserId) {
            $container.html(`
                <div class="text-center py-5">
                    <div class="mb-3 text-secondary" style="font-size: 3rem;">
                        <i class="fa-regular fa-heart text-danger opacity-75"></i>
                    </div>
                    <h6 class="fw-bold mb-2">Đăng nhập để xem địa điểm yêu thích</h6>
                    <p class="text-secondary small mb-3">Lưu lại những quán cà phê, góc check-in và quán ăn bạn yêu thích để dễ dàng lên lịch trình bất cứ lúc nào.</p>
                    <button type="button" class="btn btn-primary-cta px-4 py-2" data-bs-toggle="modal" data-bs-target="#loginModal">
                        <i class="fa-solid fa-right-to-bracket me-2"></i>Đăng nhập ngay
                    </button>
                </div>
            `);
            return;
        }
        $container.html(`
            <div class="text-center py-5 text-secondary">
                <i class="fa-solid fa-spinner fa-spin fa-2x mb-2 text-primary"></i>
                <div>Đang tải danh sách yêu thích...</div>
            </div>
        `);

        $.ajax({
            url: `${API_BASE}/api/user/favorites?user_id=${encodeURIComponent(currentUserId)}`,
            method: 'GET',
            success: function (res) {
                if (!res || !Array.isArray(res.data) || res.data.length === 0) {
                    $container.html(`
                        <div class="text-center py-5 text-secondary">
                            <i class="fa-regular fa-heart fa-3x mb-3 text-muted"></i>
                            <h6 class="fw-bold text-main">Chưa có địa điểm yêu thích nào</h6>
                            <p class="small text-secondary mb-0">
                                Hãy bấm vào biểu tượng trái tim <span class="text-danger">❤️</span> trên chi tiết địa điểm hoặc thanh Timeline để lưu lại vào đây nhé!
                            </p>
                        </div>
                    `);
                    return;
                }

                $container.empty();
                res.data.forEach(p => {
                    const pid = String(p.id);
                    const img = p.url_hinh_anh || DEFAULT_PLACE_IMG;
                    const cat = p.loai_hinh || 'Điểm đến';
                    const rating = (p.rating !== null && p.rating !== undefined) ? Number(p.rating).toFixed(1) : '4.5';
                    const addr = p.dia_chi || 'Hà Nội';
                    const openH = p.gio_mo_cua && p.gio_dong_cua ? `${p.gio_mo_cua} – ${p.gio_dong_cua}` : '08:00 – 21:00';

                    const $card = $(`
                        <div class="fav-item-card" data-id="${escapeHtml(pid)}">
                            <img src="${escapeHtml(img)}" alt="${escapeHtml(p.ten)}" class="fav-item-img" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                            <div class="fav-item-info">
                                <div class="fav-item-title">${escapeHtml(p.ten)}</div>
                                <div class="fav-item-meta">
                                    <span class="badge bg-light text-dark border">${escapeHtml(cat)}</span>
                                    <span><i class="fa-solid fa-star text-warning me-1"></i>${rating}</span>
                                    <span><i class="fa-regular fa-clock text-secondary me-1"></i>${openH}</span>
                                </div>
                                <div class="small text-secondary text-truncate mt-1" style="max-width: 380px;">
                                    <i class="fa-solid fa-location-dot text-danger me-1"></i>${escapeHtml(addr)}
                                </div>
                            </div>
                            <div class="fav-item-actions">
                                <button type="button" class="btn btn-sm btn-outline-primary btn-add-fav-to-tour" data-id="${escapeHtml(pid)}" data-name="${escapeHtml(p.ten)}" title="Thêm điểm này vào lịch trình đang xem">
                                    <i class="fa-solid fa-plus me-1"></i> Thêm vào tour
                                </button>
                                <button type="button" class="btn btn-sm btn-outline-danger btn-remove-fav-item" data-id="${escapeHtml(pid)}" title="Bỏ khỏi yêu thích">
                                    <i class="fa-regular fa-trash-can me-1"></i> Bỏ thích
                                </button>
                            </div>
                        </div>
                    `);

                    // Thêm vào tour
                    $card.find('.btn-add-fav-to-tour').on('click', function () {
                        const $btn = $(this);
                        if (!tripState.sessionId) {
                            alert('Vui lòng tạo hoặc chọn một lịch trình ở màn hình chính trước khi thêm điểm.');
                            return;
                        }
                        $btn.prop('disabled', true).html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang thêm...');
                        $.ajax({
                            url: `${API_BASE}/api/itinerary/add`,
                            method: 'POST',
                            contentType: 'application/json',
                            data: JSON.stringify({
                                session_id: tripState.sessionId,
                                place_id: pid,
                                ai_advice: `Đã thêm điểm yêu thích '${p.ten}' vào lịch trình.`
                            }),
                            success: function (addRes) {
                                if (addRes.status === 'success' && addRes.itinerary) {
                                    setCurrentItinerary(addRes.itinerary);
                                    refreshCurrentItinerary();
                                    $btn.removeClass('btn-outline-primary').addClass('btn-success').html('<i class="fa-solid fa-check me-1"></i> Đã thêm');
                                    showCrudBanner(`Đã thêm "${p.ten}" từ mục Yêu thích vào lịch trình!`);
                                } else {
                                    $btn.prop('disabled', false).html('<i class="fa-solid fa-plus me-1"></i> Thêm vào tour');
                                    alert('Không thể thêm địa điểm: ' + firstDefined(addRes.message, 'Lỗi không xác định.'));
                                }
                            },
                            error: function () {
                                $btn.prop('disabled', false).html('<i class="fa-solid fa-plus me-1"></i> Thêm vào tour');
                                alert('Lỗi kết nối khi thêm địa điểm vào lịch trình.');
                            }
                        });
                    });

                    // Xóa khỏi favorites
                    $card.find('.btn-remove-fav-item').on('click', function () {
                        toggleUserFavorite(pid, function () {
                            $card.fadeOut(250, function () {
                                $(this).remove();
                                if ($container.children().length === 0) {
                                    renderFavoritesModal();
                                }
                            });
                            refreshCurrentItinerary();
                        });
                    });

                    $container.append($card);
                });
            },
            error: function () {
                $container.html(`
                    <div class="text-center py-5 text-danger">
                        <i class="fa-solid fa-triangle-exclamation fa-2x mb-2"></i>
                        <div>Không thể tải danh sách yêu thích lúc này. Vui lòng thử lại.</div>
                    </div>
                `);
            }
        });
    }

    $('#favoritesModal').on('show.bs.modal', function () {
        renderFavoritesModal();
    });

    // ============================================================
    // 3. PROFILE & LOGIN (ACCOUNT MANAGEMENT)
    // ============================================================
    function loadUserProfile() {
        if (!currentUserId) {
            $('#nav-user-name').text('Đăng nhập');
            $('#nav-user-handle').text('Tài khoản Teravue');
            $('#nav-user-avatar').attr('src', 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=120&q=80');
            $('#nav-item-profile').hide();
            $('#nav-item-logout').hide();
            $('#nav-login-label').text('Đăng nhập');
            updateFavoriteBadges(0);
            return;
        }

        $('#nav-item-profile').show();
        $('#nav-item-logout').show();
        $('#nav-login-label').text('Đổi tài khoản');

        $.ajax({
            url: `${API_BASE}/api/user/profile?user_id=${encodeURIComponent(currentUserId)}`,
            method: 'GET',
            success: function (res) {
                if (res.status === 'success' && res.data) {
                    const u = res.data;
                    $('#nav-user-name').text(u.name || 'Du khách Hà Nội');
                    $('#nav-user-handle').text('@' + (u.id || 'traveler_hn'));
                    if (u.avatar_url) {
                        $('#nav-user-avatar').attr('src', u.avatar_url);
                        $('#profile-avatar-img').attr('src', u.avatar_url);
                    }
                    $('#profile-display-name').text(u.name || 'Du khách Hà Nội');
                    $('#profile-display-email').text(u.email || 'traveler@teravue.vn');
                    $('#profile_name_input').val(u.name || 'Du khách Hà Nội');
                    $('#profile_pref').val(u.default_preference || '');
                    updateFavoriteBadges(u.favorites_count || favoritePlaceIds.size);
                }
            }
        });
    }

    // Tự động cập nhật UI auth ngay khi khởi tạo
    loadUserProfile();

    $('#accountModal').on('show.bs.modal', function (e) {
        if (!currentUserId) {
            e.preventDefault();
            const loginModalEl = document.getElementById('loginModal');
            if (loginModalEl && window.bootstrap) {
                window.bootstrap.Modal.getOrCreateInstance(loginModalEl).show();
            }
            showCrudBanner('Vui lòng đăng nhập để xem hồ sơ cá nhân.', 'warning');
            return;
        }
        loadUserProfile();
        $('#profile-save-feedback').empty();
    });

    $('#btn-save-profile').on('click', function () {
        if (!currentUserId) {
            showCrudBanner('Vui lòng đăng nhập để lưu thông tin hồ sơ.', 'warning');
            return;
        }
        const $btn = $(this);
        const newName = $('#profile_name_input').val().trim();
        const newPref = $('#profile_pref').val().trim();
        $btn.prop('disabled', true).html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang lưu...');

        $.ajax({
            url: `${API_BASE}/api/user/profile`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                user_id: currentUserId,
                name: newName,
                default_preference: newPref
            }),
            success: function (res) {
                $btn.prop('disabled', false).html('<i class="fa-solid fa-floppy-disk me-1"></i> Lưu thông tin');
                $('#profile-save-feedback').html('<span class="text-success"><i class="fa-solid fa-check me-1"></i>Đã cập nhật hồ sơ thành công!</span>');
                loadUserProfile();
                setTimeout(() => {
                    const m = bootstrap.Modal.getInstance(document.getElementById('accountModal'));
                    if (m) m.hide();
                }, 900);
            },
            error: function () {
                $btn.prop('disabled', false).html('<i class="fa-solid fa-floppy-disk me-1"></i> Lưu thông tin');
                $('#profile-save-feedback').html('<span class="text-danger">Không thể lưu thông tin lúc này.</span>');
            }
        });
    });

    $('#login-form').on('submit', function (e) {
        e.preventDefault();
        const email = $('#login_email').val().trim();
        const $btn = $('#btn-submit-login');
        $btn.prop('disabled', true).html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang đăng nhập...');

        $.ajax({
            url: `${API_BASE}/api/user/login`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ email: email }),
            success: function (res) {
                $btn.prop('disabled', false).html('<i class="fa-solid fa-right-to-bracket me-1"></i> Đăng nhập');
                if (res.status === 'success' && res.data) {
                    currentUserId = res.data.id;
                    try {
                        localStorage.setItem('teravue_current_user_id', currentUserId);
                    } catch (e) {}
                    $('#login-feedback').html('<span class="text-success"><i class="fa-solid fa-check me-1"></i>Đăng nhập thành công!</span>');
                    loadUserProfile();
                    syncFavoritesWithServer();
                    refreshCurrentItinerary();
                    showCrudBanner(`Chào mừng ${res.data.name || res.data.id} đã đăng nhập! ❤️`);
                    setTimeout(() => {
                        const m = bootstrap.Modal.getInstance(document.getElementById('loginModal'));
                        if (m) m.hide();
                        $('#login-feedback').empty();
                    }, 800);
                }
            },
            error: function () {
                $btn.prop('disabled', false).html('<i class="fa-solid fa-right-to-bracket me-1"></i> Đăng nhập');
                $('#login-feedback').html('<span class="text-danger">Lỗi kết nối khi đăng nhập.</span>');
            }
        });
    });

    // Xử lý Đăng xuất tài khoản
    $(document).on('click', '#nav-btn-logout', function (e) {
        e.preventDefault();
        currentUserId = null;
        try {
            localStorage.removeItem('teravue_current_user_id');
            localStorage.removeItem('teravue_fav_places');
        } catch (e) {}
        favoritePlaceIds.clear();
        loadUserProfile();
        updateFavoriteBadges(0);
        refreshCurrentItinerary();
        showCrudBanner('Bạn đã đăng xuất tài khoản thành công.');
    });

    // ============================================================
    // 4. CƠ CHẾ THÍCH ỨNG LỘ TRÌNH (ADAPTIVE REPLANNING) & GIẢI THÍCH AI
    // ============================================================
    function simulateAdaptiveReplan(disruptionType) {
        if (!tripState.sessionId) {
            alert('Vui lòng tạo hoặc chọn một lịch trình trước khi thử nghiệm cơ chế thích ứng AI.');
            return;
        }

        const iconMap = { rain: '🌧️', traffic: '🚗', delay: '⏰' };
        const titleMap = {
            rain: 'AI Đề xuất Thích ứng: Tránh Mưa Gió 🌧️',
            traffic: 'AI Đề xuất Thích ứng: Né Ùn Tắc Giao Thông 🚗',
            delay: 'AI Đề xuất Thích ứng: Điều Chỉnh Giờ Mở Cửa ⏰'
        };

        const $banner = $('#adaptive-replan-banner');
        $('#adaptive-banner-icon').text(iconMap[disruptionType] || '🔄');
        $('#adaptive-banner-title').text(titleMap[disruptionType] || 'AI Đề xuất Thích ứng Lộ trình');
        $('#adaptive-banner-desc').html('<i class="fa-solid fa-spinner fa-spin me-1"></i> AI đang phân tích dữ liệu thực tế và tính toán lại lộ trình tối ưu...');
        $banner.removeClass('d-none');

        $.ajax({
            url: `${API_BASE}/api/replan/simulate`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                disruption_type: disruptionType
            }),
            success: function (res) {
                if (res.status === 'success' && res.itinerary) {
                    setCurrentItinerary(res.itinerary);
                    refreshCurrentItinerary();

                    const explanation = res.ai_explanation || 'AI đã tái tối ưu lại lịch trình cho phù hợp với điều kiện thực tế.';
                    const firstSentence = explanation.split('\n')[0].replace(/\*\*/g, '');
                    $('#adaptive-banner-desc').text(firstSentence);

                    // Mở khung chat AI và ghi lời giải thích chi tiết
                    $('#global-ai-assistant').removeClass('d-none');
                    $('#ai-map-chat').addClass('open');
                    appendChatBubble(
                        `<div class="fw-bold mb-1 text-primary"><i class="fa-solid fa-wand-magic-sparkles me-1"></i> Thông báo Thích ứng AI (${iconMap[disruptionType] || '🔄'})</div>${explanation.replace(/\n\n/g, '<br><br>').replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')}`,
                        'assistant'
                    );

                    showCrudBanner(`AI đã cập nhật lộ trình thích ứng theo điều kiện thực tế!`);
                } else {
                    $('#adaptive-banner-desc').text('Không có thay đổi cần áp dụng hoặc lịch trình hiện tại đã tối ưu.');
                }
            },
            error: function (xhr) {
                $('#adaptive-banner-desc').html(
                    '<span class="text-danger">Không thể thực hiện thích ứng: ' + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'Lỗi kết nối.') + '</span>'
                );
            }
        });
    }

    $('.btn-sim-replan').on('click', function () {
        const dtype = $(this).data('type') || 'rain';
        simulateAdaptiveReplan(dtype);
    });

    $('#btn-close-adaptive-banner').on('click', function () {
        $('#adaptive-replan-banner').addClass('d-none');
    });

    $('#btn-open-adaptive-chat').on('click', function () {
        $('#global-ai-assistant').removeClass('d-none');
        $('#ai-map-chat').addClass('open');
        const $log = $('#ai-map-chat-log');
        $log.scrollTop($log[0].scrollHeight);
    });

    // Khởi tạo tải profile ban đầu
    loadUserProfile();

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

    // ============================================================
    // TỰ ĐỘNG KHÔI PHỤC PHIÊN VÀ RENDER LỊCH SỬ KHI TẢI LẠI TRANG (BUG-005)
    // ============================================================
    function restoreSessionIfAny() {
        renderTripHistoryUI();
        const activeSessionId = localStorage.getItem('teravue_active_session');
        if (!activeSessionId) return;

        $.ajax({
            url: `${API_BASE}/api/routes?session_id=${encodeURIComponent(activeSessionId)}`,
            method: 'GET',
            success: function (res) {
                if (res && res.status === 'success' && res.routes && res.routes.length) {
                    tripState.sessionId = activeSessionId;
                    tripState.currentRoutes = res;
                    tripState.selectedRouteId = res.selected_route_id || res.routes[0].route_id;

                    $.ajax({
                        url: `${API_BASE}/api/itinerary?session_id=${encodeURIComponent(activeSessionId)}`,
                        method: 'GET',
                        success: function (itinRes) {
                            if (itinRes && itinRes.status === 'success' && itinRes.itinerary) {
                                setCurrentItinerary(itinRes.itinerary);
                            } else {
                                setCurrentItinerary(res.routes[0]);
                            }
                            renderRouteCards(res.routes);
                            renderComparisonTable(res.routes);
                            selectRouteOnScreen2(tripState.selectedRouteId);
                            $('#hero-section, #map-section').hide();
                            $('#chat-section').fadeIn(180);
                            setActiveNavTab('chat');
                            showCrudBanner('Đã tự động khôi phục hành trình gần nhất của bạn.');
                        },
                        error: function () {
                            setCurrentItinerary(res.routes[0]);
                            renderRouteCards(res.routes);
                            renderComparisonTable(res.routes);
                            selectRouteOnScreen2(tripState.selectedRouteId);
                            $('#hero-section, #map-section').hide();
                            $('#chat-section').fadeIn(180);
                            setActiveNavTab('chat');
                        }
                    });
                }
            },
            error: function () {
                localStorage.removeItem('teravue_active_session');
            }
        });
    }

    restoreSessionIfAny();
});