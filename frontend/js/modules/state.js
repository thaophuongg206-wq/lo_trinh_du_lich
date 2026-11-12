    // ============================================================
    // STATE MANAGEMENT
    // ============================================================
    var tripState = {
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
    // Du lieu duoc tach doc lap ra js/data/reviews.js de toi uu hieu nang va clean code
    var POI_ACCENTED_REVIEWS = window.POI_ACCENTED_REVIEWS || {};

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

