$(document).ready(function () {
    const map = L.map('map').setView([21.0285, 105.8542], 13);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { attribution: '&copy; OpenStreetMap' }).addTo(map);

    let mapMarkers = [], polylineRoutes = [];

    // ============================================================
    // STATE MANAGEMENT: một nguồn state duy nhất được giữ xuyên suốt
    // Screen 1 → 2 → 3 → chatbot trên bản đồ.
    //
    // sessionId: khớp với itinerary_store.TripSession phía backend — đây là
    // "sợi dây" nối app.js với state loại trừ (excluded_ids) ở backend. Thiếu
    // trường này thì mọi ràng buộc "đã bỏ điểm X" chỉ sống được trong MỘT lần
    // gọi API rồi mất, nên nó được gắn vào mọi request kể từ khi được cấp.
    // baseParams: tham số Screen 1 (đã geocode xong điểm xuất phát), dùng lại
    // cho mọi lần /api/routes tiếp theo (quick action, "Nghĩ lộ trình khác")
    // mà không phải geocode lại.
    // currentRoutes: nguyên văn response gần nhất của /api/routes — Screen 3
    // dùng lại để vẽ tab, KHÔNG gọi lại thuật toán khi chuyển tab.
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
    function getActiveItinerary() {
    return tripState.currentItinerary;
}
function setCurrentItinerary(itinerary) {
    if (!itinerary) return;

    tripState.currentItinerary = itinerary;

    if (tripState.currentRoutes?.routes) {
        const index = tripState.currentRoutes.routes.findIndex(
            r => r.route_id === itinerary.route_id
        );

        if (index >= 0) {
            tripState.activeRouteIndex = index;
        } else if (tripState.currentRoutes.routes[tripState.activeRouteIndex]) {
            tripState.currentRoutes.routes[tripState.activeRouteIndex] = itinerary;
        }
    }

    tripState.selectedRouteId =
        itinerary.route_id || tripState.selectedRouteId;
} 
function refreshCurrentItinerary() {
    const itinerary = getActiveItinerary();

    if (!itinerary) return;

    const vehicleType =
        tripState.baseParams?.vehicle_type ||
        $('#vehicle_type').val();

    const availableMinutes =
        tripState.currentRoutes?.available_minutes ||
        itinerary.available_minutes ||
        0;

    renderTimelineAndMap(
        itinerary,
        vehicleType,
        availableMinutes
    );

    renderItinerarySummary(itinerary);
}
setCurrentItinerary(newItinerary);
refreshCurrentItinerary();


    const today = new Date(new Date().getTime() - (new Date().getTimezoneOffset() * 60000)).toISOString().split('T')[0];
    $('#trip_date').val(today).attr('min', today);

    // Công cụ Admin (import Excel) chỉ hiện khi truy cập với ?admin=1, để
    // người dùng thường không thấy control quản trị trong trải nghiệm của họ.
    if (new URLSearchParams(window.location.search).get('admin') === '1') {
        $('.admin-trigger').removeClass('d-none');
    }

    const CATEGORY_ICON = {
        "Cafe":       { icon: "fa-mug-saucer",   color: "#8B5E3C" },
        "Ăn uống":    { icon: "fa-utensils",     color: "#E67E22" },
        "Tham quan":  { icon: "fa-landmark",     color: "#2D6A4F" },
        "TTTM":       { icon: "fa-bag-shopping", color: "#5856D6" },
        "Checkin":    { icon: "fa-camera",       color: "#FF3B30" },
        "diem_xuat_phat": { icon: "fa-flag",     color: "#2D3748" },
    };
    function categoryStyle(loaiHinh) {
        return CATEGORY_ICON[loaiHinh] || { icon: "fa-location-dot", color: "#4A5568" };
    }

    function vehicleSentence(vType, distKm, mins) {
        const distStr = (Math.round((distKm || 0) * 10) / 10).toString().replace('.', ',');
        const minsStr = Math.round(mins || 0);
        if (vType === 'di_bo') return '🚶 Bạn sẽ đi bộ khoảng ' + distStr + ' km, mất khoảng ' + minsStr + ' phút.';
        if (vType === 'xe_dap') return '🚲 Bạn đạp xe khoảng ' + distStr + ' km, mất khoảng ' + minsStr + ' phút.';
        if (vType === 'o_to') return '🚗 Đi xe khoảng ' + distStr + ' km, mất khoảng ' + minsStr + ' phút.';
        if (vType && vType.includes('cho')) return '🚌 Di chuyển khoảng ' + distStr + ' km bằng xe, mất khoảng ' + minsStr + ' phút.';
        return '🏍️ Di chuyển khoảng ' + distStr + ' km bằng xe máy, mất khoảng ' + minsStr + ' phút.';
    }

    function durationLabel(mins) {
        mins = Math.round(mins || 0);
        const h = Math.floor(mins / 60), m = mins % 60;
        if (h <= 0) return m + ' phút';
        return m > 0 ? h + 'h' + m : h + 'h';
    }

    // Bắt sự kiện click để mở/đóng ảnh và review trong Timeline
    $('#timeline-list').on('click', '.timeline-header', function() {
        const $item = $(this).closest('.timeline-item');
        const $extra = $item.find('.timeline-extra-content');
        const $icon = $(this).find('.toggle-icon');
        
        if ($extra.length) {
            $extra.slideToggle(200);
            $icon.toggleClass('fa-chevron-down fa-chevron-up');
        }
    });

    // ============================================================
    // SCREEN 1 → INPUT
    // ============================================================
    $('#btn-use-location').click(function () {
        const $status = $('#location-status');
        if (!navigator.geolocation) return $status.html('<span class="text-danger">Không hỗ trợ GPS</span>');
        $status.html('<span class="text-warning"><i class="fa-solid fa-spinner fa-spin"></i> Định vị...</span>');
        navigator.geolocation.getCurrentPosition(
            pos => {
                tripState.currentLocationData = { lat: pos.coords.latitude, lon: pos.coords.longitude };
                $('#start_point').val('📍 Vị trí GPS hiện tại');
                $status.html('<span class="text-success"><i class="fa-solid fa-check"></i> Đã định vị</span>');
            }, () => $status.html('<span class="text-danger">Lỗi định vị</span>')
        );
    });

    $('#start_point').on('input', function() { tripState.currentLocationData = null; $('#location-status').empty(); });

    // ============================================================
    // ĐIỂM XUẤT PHÁT → toạ độ thật (mục 2)
    // ------------------------------------------------------------
    // Backend cần start_lat/start_lon THẬT để Greedy tính khoảng cách, nên
    // việc geocode phải xong TRƯỚC KHI sinh route — tức trước khi Screen 2
    // hiển thị, không phải ở nút "Chốt lộ trình" như luồng cũ. `startFingerprint`
    // nhớ lại lần geocode gần nhất để "Nghĩ lộ trình khác"/quick action không
    // gọi lại Nominatim một cách vô ích khi người dùng chưa đổi điểm xuất phát.
    // ============================================================
    function resolveStartAndRun(onReady) {
        const startPointText = $('#start_point').val().trim();
        const usingGPS = !!tripState.currentLocationData;
        const fingerprint = usingGPS
            ? 'gps:' + tripState.currentLocationData.lat + ',' + tripState.currentLocationData.lon
            : 'text:' + startPointText;

        if (tripState.baseParams && tripState.startFingerprint === fingerprint) {
            onReady({
                start_lat: tripState.baseParams.start_lat,
                start_lon: tripState.baseParams.start_lon,
                start_point: tripState.baseParams.start_point,
            });
            return;
        }

        if (usingGPS) {
            tripState.startFingerprint = fingerprint;
            onReady({ start_lat: tripState.currentLocationData.lat, start_lon: tripState.currentLocationData.lon, start_point: '' });
            return;
        }

        $.get('https://nominatim.openstreetmap.org/search', { q: startPointText + ', Việt Nam', format: 'json', limit: 1 })
            .done(res => {
                tripState.startFingerprint = fingerprint;
                if (res && res.length > 0) onReady({ start_lat: parseFloat(res[0].lat), start_lon: parseFloat(res[0].lon), start_point: '' });
                else onReady({ start_point: startPointText });
            })
            .fail(() => { tripState.startFingerprint = fingerprint; onReady({ start_point: startPointText }); });
    }

    // ============================================================
    // AI ADVICE (advice_text + wishlist ưu tiên) — CHƯA phải route
    // ============================================================
    function applyAdvice(res) {
        tripState.sessionId = res.session_id || tripState.sessionId;
        tripState.aiSuggestedIds = res.suggested_ids || [];
        $('#ai-advice-content').html((res.advice_text || '').replace(/\n/g, '<br>'));
        $('#chat-title').text('🗺️ Chọn hành trình hợp với bạn');
        renderQuickActions();
    }

    // ============================================================
    // SINH / LÀM MỚI 3–5 ROUTE (mục 2, mục 3)
    // ------------------------------------------------------------
    // Gọi /api/routes — trả route ĐÃ HOÀN CHỈNH (places + timeline có sẵn),
    // dùng chung cho: lần đầu vào Screen 2, "Nghĩ lộ trình khác", và mọi quick
    // action (vì các action đó đổi wishlist → 3–5 route phải phản ánh lại).
    // Route đang được chọn được giữ nguyên lựa chọn nếu vẫn còn trong danh
    // sách mới; nếu không còn thì mặc định chọn route đầu tiên.
    // ============================================================
    function fetchRoutes(onComplete) {
        const payload = Object.assign({}, tripState.baseParams, {
            session_id: tripState.sessionId,
            ai_selected_ids: tripState.aiSuggestedIds,
            num_routes: 5,
        });
        $.ajax({
            url: 'http://127.0.0.1:8000/api/routes', method: 'POST',
            contentType: 'application/json', data: JSON.stringify(payload),
            success: function (res) {
                if (res.status !== 'success') {
                    alert(res.message || 'Không tạo được lộ trình phù hợp, hãy thử mô tả khác.');
                    return;
                }
                tripState.sessionId = res.session_id || tripState.sessionId;
                tripState.currentRoutes = res;
                const kept = res.routes.find(r => r.route_id === tripState.selectedRouteId);
                tripState.selectedRouteId = (kept || res.routes[0] || {}).route_id || null;
                renderRouteCards(res.routes);

                if (!$('#chat-section').is(':visible')) {
                    $('#hero-section, #map-section').hide();
                    $('#chat-section').fadeIn();
                }
            },
            error: (xhr) => alert("Không tạo được lộ trình: " + (xhr.responseJSON?.detail || "Kiểm tra Backend.")),
            complete: () => { if (onComplete) onComplete(); }
        });
    }

    function renderRouteCards(routes) {
        const $grid = $('#ai-routes-grid').empty();
        routes.forEach(r => {
            const isSelected = r.route_id === tripState.selectedRouteId;
            const routeName = r.name || r.route_id;
            const themeLabel = r.theme_label || '';
            const description = r.description || '';
            const placeCount = r.place_count || (r.places || []).length;
            const durationTxt = durationLabel(r.total_duration);
            
            const $card = $('<div class="route-card"></div>')
                .toggleClass('selected', isSelected)
                .attr('data-route-id', r.route_id);
                
            let htmlStr = '';
            htmlStr += '<div class="route-card-theme">' + themeLabel + '</div>';
            htmlStr += '<h6 class="fw-bold mb-1">' + routeName + '</h6>';
            htmlStr += '<p class="route-card-desc">' + description + '</p>';
            htmlStr += '<div class="route-card-stats"><i class="fa-regular fa-clock"></i> ' + placeCount + ' điểm · ' + durationTxt + '</div>';
            
            $card.html(htmlStr);
            $card.on('click', function () {
                tripState.selectedRouteId = r.route_id;
                $('#ai-routes-grid .route-card').removeClass('selected');
                $(this).addClass('selected');
            });
            $grid.append($card);
        });
    }

    const QUICK_ACTIONS = [
        { label: '+ Thêm quán ăn trưa', text: 'Thêm một quán ăn trưa phù hợp' },
        { label: '+ Thêm quán cà phê', text: 'Thêm một quán cà phê phù hợp' },
        { label: '− Bớt một điểm', text: 'Bớt bớt một điểm tham quan, lịch trình đang hơi dày' },
        { label: 'Đi chậm hơn', text: 'Tôi muốn đi chậm hơn, có nhiều thời gian nghỉ hơn' },
    ];

    function renderQuickActions() {
        const $wrap = $('#ai-quick-actions').empty();
        QUICK_ACTIONS.forEach(qa => {
            const $chip = $('<button type="button" class="quick-action-chip">' + qa.label + '</button>');
            $chip.on('click', function () { runQuickAction(qa.text, $chip); });
            $wrap.append($chip);
        });
    }

    function runQuickAction(instructionText, $chip) {
        const labelText = $chip.text();
        if (labelText.includes("ăn trưa")) {
            $('#chat-title').text("🍜 Mình tìm vài lựa chọn ăn trưa hợp với hành trình này");
        } else if (labelText.includes("cà phê")) {
            $('#chat-title').text("☕ Một vài nơi để bạn nghỉ chân");
        } else {
            $('#chat-title').text("🗺️ Chọn hành trình hợp với bạn");
        }

        $('#ai-quick-actions .quick-action-chip').prop('disabled', true);
        const payload = {
            region: 'Hanoi',
            vehicle_type: $('#vehicle_type').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            user_preference: $('#user_preference').val(),
            current_ids: tripState.aiSuggestedIds,
            instruction: instructionText,
            session_id: tripState.sessionId,
        };
        $.ajax({
            url: 'http://127.0.0.1:8000/api/ai-refine', method: 'POST',
            contentType: 'application/json', data: JSON.stringify(payload),
            success: function (res) {
                tripState.sessionId = res.session_id || tripState.sessionId;
                tripState.aiSuggestedIds = res.suggested_ids || tripState.aiSuggestedIds;
                $('#ai-advice-content').html((res.advice_text || '').replace(/\n/g, '<br>'));
                // Wishlist vừa đổi → 3–5 route phải được tính lại để phản ánh đúng
                // (mục 2: route luôn phải là bản hoàn chỉnh, không phải rổ điểm cũ).
                fetchRoutes(() => $('#ai-quick-actions .quick-action-chip').prop('disabled', false));
            },
            error: (xhr) => {
                alert("Không cập nhật được gợi ý: " + (xhr.responseJSON?.detail || "Kiểm tra Backend."));
                $('#ai-quick-actions .quick-action-chip').prop('disabled', false);
            }
        });
    }

    // ============================================================
    // SCREEN 1 → SCREEN 2: sinh advice + 3–5 route HOÀN CHỈNH trước khi hiện
    // Screen 2 (mục 2). "Nghĩ lộ trình khác" dùng chung handler này.
    // ============================================================
    $('#btn-ask-ai, #btn-reject-route').click(function () {
        const userPref = $('#user_preference').val().trim();
        if (!userPref) { alert("Hãy nhập mong muốn của bạn!"); $('#user_preference').focus(); return; }
        if (!tripState.currentLocationData && !$('#start_point').val().trim()) {
            alert('Vui lòng nhập điểm xuất phát!'); return;
        }

        const $askBtn = $('#btn-ask-ai');
        const $rejectBtn = $('#btn-reject-route');
        const isReject = $(this).is($rejectBtn);

        // Khoá NGAY cả hai nút + đổi text để có phản hồi tức thì, tránh việc
        // click liên tục "Nghĩ lộ trình khác" tạo nhiều request chồng chéo.
        $askBtn.add($rejectBtn).prop('disabled', true);
        if (isReject) $rejectBtn.html('<i class="fa-solid fa-spinner fa-spin"></i> Đang tìm lộ trình mới...');
        else $askBtn.html('<i class="fa-solid fa-spinner fa-spin"></i> Đang suy nghĩ...');

        const resetButtons = () => {
            $askBtn.html('<i class="fa-solid fa-wand-magic-sparkles me-2"></i> Cùng lên kế hoạch nào').prop('disabled', false);
            $rejectBtn.html('<i class="fa-solid fa-rotate-right"></i> Nghĩ lộ trình khác').prop('disabled', false);
        };

        resolveStartAndRun(function (startParams) {
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
                start_point: startParams.start_point || '',
            };

            $.ajax({
                url: 'http://127.0.0.1:8000/api/ai-suggest', method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    region: 'Hanoi', user_preference: userPref,
                    vehicle_type: tripState.baseParams.vehicle_type,
                    start_time: tripState.baseParams.start_time,
                    end_time: tripState.baseParams.end_time,
                    session_id: tripState.sessionId,
                }),
                success: function (res) {
                    applyAdvice(res);
                    fetchRoutes(resetButtons);
                },
                error: function (xhr) {
                    alert("Chưa nghĩ ra được lộ trình: " + (xhr.responseJSON?.detail || "Không thể kết nối tới hệ thống, vui lòng thử lại."));
                    resetButtons();
                }
            });
        });
    });

    // Quay lại Screen 1 mà KHÔNG xoá dữ liệu đã nhập (ngày/giờ/phương tiện/sở
    // thích/điểm xuất phát vẫn còn nguyên vì hero-section chỉ bị ẩn, không bị
    // gỡ khỏi DOM, nên không cần khôi phục thủ công).
    $('#btn-back-hero').click(function () {
        $('#chat-section').hide();
        $('#hero-section').fadeIn();
    });

    // ============================================================
    // SCREEN 2 CHỌN ROUTE → SCREEN 3 (mục 10)
    // ------------------------------------------------------------
    // Route đã tồn tại sẵn từ fetchRoutes() ở bước trước. "Chốt lộ trình" chỉ
    // xác nhận lựa chọn qua /api/routes/select — KHÔNG generate lại route mới.
    // ============================================================
    $('#btn-accept-route').click(function () {
        if (!tripState.currentRoutes || !tripState.selectedRouteId) {
            alert('Chưa có lộ trình nào để chốt, vui lòng thử lại.');
            return;
        }
        const $btn = $(this);
        $btn.html('<i class="fa-solid fa-spinner fa-spin"></i> Đang tính toán...').prop('disabled', true);

        $.ajax({
            url: 'http://127.0.0.1:8000/api/routes/select', method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ session_id: tripState.sessionId, route_id: tripState.selectedRouteId }),
            success: function (res) {
                if (res.status === 'success') enterMapScreen(res.route_id);
                else alert(res.detail || 'Không chốt được lộ trình.');
            },
            error: (xhr) => alert("Lỗi khi chốt lộ trình: " + (xhr.responseJSON?.detail || "Kiểm tra Backend")),
            complete: () => $btn.html('<i class="fa-solid fa-check me-2"></i> Chốt lộ trình & Vẽ bản đồ').prop('disabled', false)
        });
    });
function renderItinerarySummary(itinerary) {
    if (!itinerary) return;

    const places = itinerary.optimized_route || [];

    const startTime =
        places[0]?.arrive_time || '--:--';

    const endTime =
        places[places.length - 1]?.depart_time || '--:--';

    const totalTime =
        itinerary.total_time_minutes || 0;

    const vehicleType =
        tripState.baseParams?.vehicle_type ||
        $('#vehicle_type').val();

    const vehicleLabel = {
        xe_may: 'Xe máy',
        o_to: 'Ô tô',
        xe_16_cho: 'Xe 16 chỗ',
        xe_29_cho: 'Xe 29 chỗ',
        di_bo: 'Đi bộ',
        xe_dap: 'Xe đạp'
    }[vehicleType] || vehicleType || 'Phương tiện';

    $('#metrics-info').html(`
        <div class="fw-bold mb-2">
            <i class="fa-solid fa-route"></i>
            ${itinerary.name || itinerary.route_id || 'Lộ trình hiện tại'}
        </div>

        <div>
            <i class="fa-regular fa-clock"></i>
            ${startTime} → ${endTime}
        </div>

        <div>
            <i class="fa-solid fa-location-dot"></i>
            ${places.length} địa điểm
        </div>

        <div>
            <i class="fa-solid fa-car"></i>
            ${vehicleLabel}
        </div>

        <div>
            <i class="fa-solid fa-hourglass-half"></i>
            Tổng thời gian: ${durationLabel(totalTime)}
        </div>
    `);
} 
    function enterMapScreen(routeId) {
        const routes = tripState.currentRoutes.routes;
        let idx = routes.findIndex(r => r.route_id === routeId);
        if (idx < 0) idx = 0;
        tripState.activeRouteIndex = idx;
        tripState.selectedRouteId = routes[idx].route_id;

        $('#chat-section').hide(); $('#map-section').fadeIn();
        setTimeout(() => map.invalidateSize(), 100);

        $('#route-tabs-container').remove();
        let tabsHtml = '<div id="route-tabs-container" class="d-flex gap-2 mb-3 overflow-auto pb-2">';
        routes.forEach((r, i) => {
            const label = r.name || r.strategy || ('Lộ trình ' + (i + 1));
            const tabClass = (i === idx) ? 'btn-dark' : 'btn-outline-dark';
            tabsHtml += '<button class="btn btn-sm ' + tabClass + ' fw-bold text-nowrap route-tab" data-idx="' + i + '">' + label + '</button>';
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

            // Đồng bộ lựa chọn với backend (mục 11: source of truth) để lần chat
            // AI kế tiếp trên bản đồ thao tác đúng route đang xem. Không chặn UI
            // khi chờ phản hồi — bảng đã được vẽ lại ở dòng trên rồi.
            $.ajax({
                url: 'http://127.0.0.1:8000/api/routes/select', method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({ session_id: tripState.sessionId, route_id: routes[newIdx].route_id }),
            });
        });

        setCurrentItinerary(routes[idx]);
refreshCurrentItinerary(); 

    function renderTimelineAndMap(routeData, vType, totalMins) {
        const $timeline = $('#timeline-list').empty();
        const startTime = routeData.optimized_route[0]?.arrive_time || '--:--';
        const endTime = routeData.optimized_route[routeData.optimized_route.length - 1]?.depart_time || '--:--';
        
        let headerHtml = '<strong><i class="fa-regular fa-clock"></i> Bắt đầu:</strong> ' + startTime + ' <br> <strong><i class="fa-solid fa-flag-checkered"></i> Kết thúc:</strong> ' + endTime + ' <br> Tổng: ' + routeData.total_time_minutes + ' / ' + totalMins + ' phút.';
        $('#metrics-info').html(headerHtml);

        let currentPeriod = '';

        routeData.optimized_route.forEach((loc, idx) => {
            const arriveHour = parseInt((loc.arrive_time || "00:00").split(':')[0], 10);
            const arriveMin = parseInt((loc.arrive_time || "00:00").split(':')[1], 10);
            let period = '';
            let periodLabel = '';

            if (arriveHour < 11 || (arriveHour === 11 && arriveMin < 30)) {
                period = 'morning';
                periodLabel = '🌅 Buổi sáng';
            } else if (arriveHour < 14 || (arriveHour === 14 && arriveMin < 0)) {
                period = 'noon';
                periodLabel = '☀️ Buổi trưa';
            } else {
                period = 'afternoon';
                periodLabel = '🌇 Buổi chiều / Tối';
            }

            if (period !== currentPeriod) {
                $timeline.append('<li class="timeline-period-header">' + periodLabel + '</li>');
                currentPeriod = period;
            }

            const hasNext = idx < routeData.optimized_route.length - 1;
            const travelInfo = hasNext
                ? '<div class="mt-2 small fw-bold text-muted">' + vehicleSentence(vType, loc.distance_to_next, loc.travel_to_next) + '</div>'
                : '';
            
            // Xử lý khối ẩn/hiện: Ảnh + Review
            const imgUrl = loc.url_hinh_anh || loc.image_url || loc.image || '';
            const imgHtml = imgUrl ? '<img src="' + imgUrl + '" class="timeline-image" alt="Hình ảnh">' : '';
            const review = (loc.review || '').trim();
            const reviewHtml = review ? '<div class="timeline-review">★ “' + review + '”</div>' : '';
            
            const extraContentHtml = (imgHtml !== '' || reviewHtml !== '') 
                ? '<div class="timeline-extra-content">' + imgHtml + reviewHtml + '</div>' 
                : '';
            
            let listItemHtml = '';
            listItemHtml += '<li class="timeline-item">';
            
            // Phần Header (Luôn hiện, có thể click)
            listItemHtml += '<div class="timeline-header">';
            listItemHtml += '  <span class="badge bg-dark mb-1">' + loc.arrive_time + ' - ' + loc.depart_time + '</span>';
            listItemHtml += '  <h6 class="fw-bold mb-1 d-flex justify-content-between align-items-center">';
            listItemHtml += '    <span>' + (idx + 1) + '. ' + loc.ten + '</span>';
            if (extraContentHtml !== '') {
                listItemHtml += '    <i class="fa-solid fa-chevron-down text-muted small toggle-icon"></i>';
            }
            listItemHtml += '  </h6>';
            listItemHtml += '  <div class="text-muted small"><i class="fa-solid fa-camera"></i> Tham quan: ' + loc.visit_time + ' phút</div>';
            listItemHtml += '</div>';

            // Phần nội dung mở rộng (ẩn mặc định)
            listItemHtml += extraContentHtml;
            
            // Di chuyển (Luôn hiện bên dưới cùng)
            listItemHtml += travelInfo;
            
            listItemHtml += '</li>';
            
            $timeline.append(listItemHtml);
        });

        drawMultiColorMap(routeData.optimized_route, vType);
    }

    function drawMultiColorMap(routeLocs, vType) {
        mapMarkers.forEach(m => map.removeLayer(m)); polylineRoutes.forEach(p => map.removeLayer(p));
        mapMarkers = []; polylineRoutes = [];

        const colors = ['#FF3B30', '#4CD964', '#007AFF', '#FFCC00', '#5856D6'];

        routeLocs.forEach((loc, idx) => {
            const style = categoryStyle(loc.loai_hinh);
            let iconHtml = '<div class="map-marker-icon" style="background:' + style.color + ';"><i class="fa-solid ' + style.icon + '"></i></div>';
            
            const review = (loc.review || '').trim();
            const reviewHtml = review ? '<div class="mt-1 small fst-italic">★ “' + review + '”</div>' : '';
            
            let popupHtml = '';
            popupHtml += '<b style="font-size:14px;">' + (idx + 1) + '. ' + loc.ten + '</b>';
            popupHtml += '<div class="small text-muted">' + (loc.loai_hinh || '') + '</div>';
            popupHtml += reviewHtml;
            
            const marker = L.marker([loc.lat, loc.lon], {icon: L.divIcon({html: iconHtml, className: '', iconSize: [30,30], iconAnchor: [15,15]})}).bindPopup(popupHtml).addTo(map);
            mapMarkers.push(marker);
        });

        if (routeLocs.length > 1) {
            let osrmProfile = vType === 'di_bo' ? 'foot' : (vType === 'xe_dap' ? 'cycling' : 'driving');
            let promises = [];
            for (let i = 0; i < routeLocs.length - 1; i++) {
                const p1 = routeLocs[i], p2 = routeLocs[i+1];
                const color = colors[i % colors.length];
                let req = $.get(`https://router.project-osrm.org/route/v1/${osrmProfile}/${p1.lon},${p1.lat};${p2.lon},${p2.lat}?overview=full&geometries=geojson`)
                    .then(res => polylineRoutes.push(L.polyline(res.routes[0].geometry.coordinates.map(c => [c[1], c[0]]), { color: color, weight: 6, opacity: 0.8 }).addTo(map)))
                    .catch(() => polylineRoutes.push(L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], { color: color, weight: 4, dashArray: '5,5' }).addTo(map)));
                promises.push(req);
            }
            Promise.all(promises).then(() => map.fitBounds(new L.featureGroup(mapMarkers).getBounds(), { padding: [50, 50] }));
        }
    }

    $('#btn-back-chat').click(function() { $('#map-section').hide(); $('#chat-section').fadeIn(); });

    // ============================================================
    // AI FAB TRÊN MAP: bản đồ không còn là dead-end — người dùng có thể tiếp
    // tục trò chuyện để chỉnh sửa itinerary tại chỗ.
    // ============================================================
    $('#btn-ai-fab').click(function () { $('#ai-map-chat').addClass('open'); $('#ai_map_instruction').focus(); });
    $('#btn-close-ai-chat').click(function () { $('#ai-map-chat').removeClass('open'); });

    function currentItineraryIds() {
    const itinerary = getActiveItinerary();

    if (!itinerary) return [];

    return (itinerary.optimized_route || [])
        .map(place => place.id)
        .filter(id => id && id !== 'gps_current');
} 

    function appendChatBubble(text, who) {
        $('#ai-map-chat-log').append('<div class="chat-bubble ' + who + '">' + text + '</div>');
        const log = document.getElementById('ai-map-chat-log');
        log.scrollTop = log.scrollHeight;
    }

    function sendAiMapInstruction() {
        const instruction = $('#ai_map_instruction').val().trim();
        if (!instruction) return;
        if (!tripState.sessionId) { alert('Chưa có lộ trình nào để chỉnh sửa.'); return; }

        appendChatBubble(instruction, 'user');
        $('#ai_map_instruction').val('').prop('disabled', true);
        $('#btn-send-ai-chat').prop('disabled', true);
        appendChatBubble('<i class="fa-solid fa-spinner fa-spin"></i> Đang cập nhật lộ trình...', 'assistant loading');

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
            url: 'http://127.0.0.1:8000/api/ai-refine', method: 'POST',
            contentType: 'application/json', data: JSON.stringify(refinePayload),
            success: function (refineRes) {
                $('#ai-map-chat-log .loading').remove();
                appendChatBubble(refineRes.advice_text || 'Mình đã cập nhật lộ trình cho bạn.', 'assistant');
                tripState.sessionId = refineRes.session_id || tripState.sessionId;

                // CHỈ TÍNH LẠI đúng itinerary đang xem (mục 9), không sinh lại 5
                // route mới: ai-refine đã tự ghi excluded_ids/pinned_ids vào phiên
                // ở trên rồi, nên ở đây không cần truyền lại remove_ids/add_ids —
                // /api/itinerary/update tự đọc state đó ra để tính lại.
                $.ajax({
                    url: 'http://127.0.0.1:8000/api/itinerary/update', method: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({ session_id: tripState.sessionId }),
                    success: function (res) {
                        if (res.status === 'success') {
    setCurrentItinerary(res.itinerary);

    refreshCurrentItinerary();

    appendChatBubble(
        refineRes.advice_text ||
        'Mình đã cập nhật và sắp xếp lại lịch trình cho bạn.',
        'assistant'
    );
} else {
    appendChatBubble(
        'Không thể cập nhật lộ trình: ' +
        (res.message || 'Lỗi không xác định.'),
        'assistant'
    );
}
                    },
                    error: () => appendChatBubble('Không thể tính lại lộ trình, vui lòng thử lại.', 'assistant'),
                });
            },
            error: function (xhr) {
                $('#ai-map-chat-log .loading').remove();
                appendChatBubble('Xin lỗi, mình chưa hiểu ý bạn lắm: ' + (xhr.responseJSON?.detail || 'vui lòng thử lại.'), 'assistant');
            },
            complete: () => { $('#ai_map_instruction').prop('disabled', false); $('#btn-send-ai-chat').prop('disabled', false); }
        });
    }
    $('#btn-send-ai-chat').click(sendAiMapInstruction);
    $('#ai_map_instruction').keypress(function (e) { if (e.which === 13) sendAiMapInstruction(); });

    // ============================================================
    // ADMIN: IMPORT EXCEL (không đổi logic, chỉ ẩn khỏi user thường)
    // ============================================================
    $('#btn-upload-excel').click(function() {
        const file = document.getElementById('excel_file').files[0];
        if (!file) return alert('Chọn file trước!');
        const formData = new FormData(); formData.append("file", file);
        $('#upload-status').html('<span class="text-primary">Đang nạp dữ liệu...</span>');
        $.ajax({
            url: 'http://127.0.0.1:8000/api/admin/import-excel', method: 'POST', data: formData, processData: false, contentType: false,
            success: res => $('#upload-status').html('<span class="text-success">' + res.message + '</span>'),
            error: () => $('#upload-status').html('<span class="text-danger">Lỗi Upload</span>')
        });
    });
});