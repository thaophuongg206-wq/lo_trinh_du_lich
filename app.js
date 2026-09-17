$(document).ready(function () {
    const map = L.map('map').setView([21.0285, 105.8542], 13);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { attribution: '&copy; OpenStreetMap' }).addTo(map);

    let mapMarkers = [], polylineRoutes = [];
    let dbLocations = [];

    // ============================================================
    // STATE MANAGEMENT (mục XVII yêu cầu): một nguồn state duy nhất được
    // giữ xuyên suốt Screen 1 → 2 → 3, để quay lại/màn AI trên map luôn có
    // đủ ngữ cảnh (ngày, giờ, phương tiện, sở thích, itinerary hiện tại...).
    // ============================================================
    const tripState = {
        currentLocationData: null,
        aiSuggestedIds: [],
        currentRoutes: null,
        activeRouteIndex: 0,
        lastOptimizePayload: null,
    };

    const today = new Date(new Date().getTime() - (new Date().getTimezoneOffset() * 60000)).toISOString().split('T')[0];
    $('#trip_date').val(today).attr('min', today);

    // Công cụ Admin (import Excel) chỉ hiện khi truy cập với ?admin=1, để
    // người dùng thường không thấy control quản trị trong trải nghiệm của họ.
    if (new URLSearchParams(window.location.search).get('admin') === '1') {
        $('.admin-trigger').removeClass('d-none');
    }

    $.get('http://127.0.0.1:8000/api/locations', res => { if (res.status === "success") dbLocations = res.data; });

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
        if (vType === 'di_bo') return `🚶 Bạn sẽ đi bộ khoảng ${distStr} km, mất khoảng ${minsStr} phút.`;
        if (vType === 'xe_dap') return `🚲 Bạn đạp xe khoảng ${distStr} km, mất khoảng ${minsStr} phút.`;
        if (vType === 'o_to') return `🚗 Đi xe khoảng ${distStr} km, mất khoảng ${minsStr} phút.`;
        if (vType && vType.includes('cho')) return `🚌 Di chuyển khoảng ${distStr} km bằng xe, mất khoảng ${minsStr} phút.`;
        return `🏍️ Di chuyển khoảng ${distStr} km bằng xe máy, mất khoảng ${minsStr} phút.`;
    }

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
                $('#start_point').val(`📍 Vị trí GPS hiện tại`);
                $status.html('<span class="text-success"><i class="fa-solid fa-check"></i> Đã định vị</span>');
            }, () => $status.html('<span class="text-danger">Lỗi định vị</span>')
        );
    });

    $('#start_point').on('input', function() { tripState.currentLocationData = null; $('#location-status').empty(); });

    // ============================================================
    // AI SUGGEST (Screen 1 → Screen 2)
    // "Nghĩ lộ trình khác" dùng chung handler nhưng phải tự tạo trạng thái
    // loading NGAY LẬP TỨC và khoá cả hai nút để tránh double-click trong
    // lúc chờ phản hồi (mục IX).
    // ============================================================
    function renderAdviceAndPlaces(res) {
        tripState.aiSuggestedIds = res.suggested_ids || [];
        $('#hero-section, #map-section').hide();
        $('#chat-section').fadeIn();
        $('#ai-advice-content').html((res.advice_text || '').replace(/\n/g, '<br>'));
        renderQuickActions();
        renderPlacesCarousel();
    }

    function renderPlacesCarousel() {
        const $grid = $('#ai-places-grid').empty();
        tripState.aiSuggestedIds.forEach(id => {
            const place = dbLocations.find(p => p.id === id);
            if (!place) return;
            const img = place.url_hinh_anh || "https://images.unsplash.com/photo-1511884642898-4c92249e20b6?auto=format&fit=crop&w=500&q=80";
            const rating = place.rating ? `${place.rating} ⭐` : 'Mới';
            $grid.append(`<div class="place-card"><div class="rating-badge">${rating}</div><img src="${img}"><div class="p-3"><h6 class="fw-bold mb-1">${place.ten}</h6><small class="text-muted"><i class="fa-solid fa-tag"></i> ${place.loai_hinh}</small></div></div>`);
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
            const $chip = $(`<button type="button" class="quick-action-chip">${qa.label}</button>`);
            $chip.on('click', function () { runQuickAction(qa.text, $chip); });
            $wrap.append($chip);
        });
    }

    function runQuickAction(instructionText, $chip) {
        $('#ai-quick-actions .quick-action-chip').prop('disabled', true);
        const payload = {
            region: 'Hanoi',
            vehicle_type: $('#vehicle_type').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            user_preference: $('#user_preference').val(),
            current_ids: tripState.aiSuggestedIds,
            instruction: instructionText,
        };
        $.ajax({
            url: 'http://127.0.0.1:8000/api/ai-refine', method: 'POST',
            contentType: 'application/json', data: JSON.stringify(payload),
            success: function (res) {
                tripState.aiSuggestedIds = res.suggested_ids || tripState.aiSuggestedIds;
                $('#ai-advice-content').html((res.advice_text || '').replace(/\n/g, '<br>'));
                renderPlacesCarousel();
            },
            error: (xhr) => alert("Không cập nhật được gợi ý: " + (xhr.responseJSON?.detail || "Kiểm tra Backend.")),
            complete: () => $('#ai-quick-actions .quick-action-chip').prop('disabled', false)
        });
    }

    $('#btn-ask-ai, #btn-reject-route').click(function () {
        const userPref = $('#user_preference').val().trim();
        if (!userPref) { alert("Hãy nhập mong muốn của bạn!"); $('#user_preference').focus(); return; }

        const $askBtn = $('#btn-ask-ai');
        const $rejectBtn = $('#btn-reject-route');
        const isReject = $(this).is($rejectBtn);

        // Khoá NGAY cả hai nút + đổi text để có phản hồi tức thì, tránh việc
        // click liên tục "Nghĩ lộ trình khác" tạo nhiều request chồng chéo.
        $askBtn.add($rejectBtn).prop('disabled', true);
        if (isReject) {
            $rejectBtn.html('<i class="fa-solid fa-spinner fa-spin"></i> Đang tìm lộ trình mới...');
        } else {
            $askBtn.html('<i class="fa-solid fa-spinner fa-spin"></i> Đang suy nghĩ...');
        }

        const payload = { region: 'Hanoi', user_preference: userPref, vehicle_type: $('#vehicle_type').val(), start_time: $('#start_time').val(), end_time: $('#end_time').val() };

        $.ajax({
            url: 'http://127.0.0.1:8000/api/ai-suggest', method: 'POST',
            contentType: 'application/json', data: JSON.stringify(payload),
            success: function (res) { renderAdviceAndPlaces(res); },
            error: (xhr) => {
                const msg = xhr.responseJSON?.detail || "Không thể kết nối tới hệ thống, vui lòng thử lại.";
                alert("Chưa nghĩ ra được lộ trình: " + msg);
            },
            complete: () => {
                $askBtn.html('<i class="fa-solid fa-wand-magic-sparkles me-2"></i> Cùng lên kế hoạch nào').prop('disabled', false);
                $rejectBtn.html('<i class="fa-solid fa-rotate-right"></i> Nghĩ lộ trình khác').prop('disabled', false);
            }
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
    // CHỐT LỘ TRÌNH (Screen 2 → Screen 3)
    // ============================================================
    $('#btn-accept-route').click(function() {
        const startPointText = $('#start_point').val().trim();
        if (!tripState.currentLocationData && !startPointText) return alert('Vui lòng nhập điểm xuất phát!');

        $(this).html('<i class="fa-solid fa-spinner fa-spin"></i> Đang tính toán...').prop('disabled', true);
        const payload = {
            region: 'Hanoi', start_time: $('#start_time').val(), end_time: $('#end_time').val(),
            trip_date: $('#trip_date').val(), vehicle_type: $('#vehicle_type').val(),
            user_preference: $('#user_preference').val(), weight: 50, ai_selected_ids: tripState.aiSuggestedIds
        };

        const executeOptimize = (data) => {
            tripState.lastOptimizePayload = data;
            $.ajax({
                url: 'http://127.0.0.1:8000/api/optimize-route', method: 'POST',
                contentType: 'application/json', data: JSON.stringify(data),
                success: function (res) {
                    if (res.status === "success") {
                        applyOptimizeResult(res, data);
                    } else alert(res.message);
                },
                error: (xhr) => alert("Lỗi Thuật toán: " + (xhr.responseJSON?.detail || "Kiểm tra Backend")),
                complete: () => $('#btn-accept-route').html('<i class="fa-solid fa-check me-2"></i> Chốt lộ trình & Vẽ bản đồ').prop('disabled', false)
            });
        };

        if (tripState.currentLocationData) {
            payload.start_lat = tripState.currentLocationData.lat; payload.start_lon = tripState.currentLocationData.lon; payload.start_point = '';
            executeOptimize(payload);
        } else {
            $.get('https://nominatim.openstreetmap.org/search', { q: startPointText + ', Việt Nam', format: 'json', limit: 1 }, res => {
                if (res && res.length > 0) { payload.start_lat = parseFloat(res[0].lat); payload.start_lon = parseFloat(res[0].lon); payload.start_point = ''; }
                else payload.start_point = startPointText;
                executeOptimize(payload);
            }).fail(() => { payload.start_point = startPointText; executeOptimize(payload); });
        }
    });

    function applyOptimizeResult(res, data) {
        tripState.currentRoutes = res;
        tripState.activeRouteIndex = 0;

        $('#chat-section').hide(); $('#map-section').fadeIn();
        setTimeout(() => map.invalidateSize(), 100);

        $('#route-tabs-container').remove();
        let tabsHtml = '<div id="route-tabs-container" class="d-flex gap-2 mb-3 overflow-auto pb-2">';
        res.routes.forEach((r, i) => { tabsHtml += `<button class="btn btn-sm ${i===0 ? 'btn-dark' : 'btn-outline-dark'} fw-bold text-nowrap route-tab" data-idx="${i}">${r.strategy || 'Lộ trình ' + (i+1)}</button>`; });
        tabsHtml += '</div>';
        $('#metrics-info').before(tabsHtml);

        $('.route-tab').click(function() {
            $('.route-tab').removeClass('btn-dark').addClass('btn-outline-dark');
            $(this).removeClass('btn-outline-dark').addClass('btn-dark');
            tripState.activeRouteIndex = $(this).data('idx');
            renderTimelineAndMap(res.routes[tripState.activeRouteIndex], data.vehicle_type, res.available_minutes);
        });

        renderTimelineAndMap(res.routes[0], data.vehicle_type, res.available_minutes);
    }

    function renderTimelineAndMap(routeData, vType, totalMins) {
        let vIcon = '<i class="fa-solid fa-car-side text-primary"></i>';
        if (vType === 'xe_may') vIcon = '<i class="fa-solid fa-motorcycle text-primary"></i>';
        if (vType === 'di_bo') vIcon = '<i class="fa-solid fa-person-walking text-primary"></i>';
        if (vType === 'xe_dap') vIcon = '<i class="fa-solid fa-bicycle text-primary"></i>';
        if (vType.includes('cho')) vIcon = '<i class="fa-solid fa-bus text-primary"></i>';

        const $timeline = $('#timeline-list').empty();
        $('#metrics-info').html(`<strong><i class="fa-regular fa-clock"></i> Bắt đầu:</strong> ${routeData.optimized_route[0]?.arrive_time || '--:--'} <br> <strong><i class="fa-solid fa-flag-checkered"></i> Kết thúc:</strong> ${routeData.optimized_route[routeData.optimized_route.length - 1]?.depart_time || '--:--'} <br> Tổng: ${routeData.total_time_minutes} / ${totalMins} phút.`);

        routeData.optimized_route.forEach((loc, idx) => {
            const hasNext = idx < routeData.optimized_route.length - 1;
            // Câu chữ di chuyển thân thiện, lấy đúng phương tiện thực tế từ itinerary
            // thay vì hard-code "đi bộ" cho mọi trường hợp (mục XIV).
            const travelInfo = hasNext
                ? `<div class="mt-2 small fw-bold text-muted">${vehicleSentence(vType, loc.distance_to_next, loc.travel_to_next)}</div>`
                : '';
            // Hiển thị review nếu có, không render "undefined"/"null"/khoảng trống vô nghĩa.
            const review = (loc.review || '').trim();
            const reviewHtml = review ? `<div class="timeline-review">★ “${review}”</div>` : '';
            $timeline.append(`<li><span class="badge bg-dark mb-1">${loc.arrive_time} - ${loc.depart_time}</span><h6 class="fw-bold mb-1">${idx + 1}. ${loc.ten}</h6><div class="text-muted small"><i class="fa-solid fa-camera"></i> Tham quan: ${loc.visit_time} phút</div>${reviewHtml}${travelInfo}</li>`);
        });

        drawMultiColorMap(routeData.optimized_route, vType);
    }

    function drawMultiColorMap(routeLocs, vType) {
        mapMarkers.forEach(m => map.removeLayer(m)); polylineRoutes.forEach(p => map.removeLayer(p));
        mapMarkers = []; polylineRoutes = [];

        const colors = ['#FF3B30', '#4CD964', '#007AFF', '#FFCC00', '#5856D6'];

        routeLocs.forEach((loc, idx) => {
            // Marker phân biệt theo category thực tế (loai_hinh) thay vì chỉ là số
            // đen trắng (mục XII); số thứ tự vẫn có trong popup + timeline sidebar.
            const style = categoryStyle(loc.loai_hinh);
            const iconHtml = `<div class="map-marker-icon" style="background:${style.color};"><i class="fa-solid ${style.icon}"></i></div>`;
            const review = (loc.review || '').trim();
            const reviewHtml = review ? `<div class="mt-1 small fst-italic">★ “${review}”</div>` : '';
            const popupHtml = `<b style="font-size:14px;">${idx + 1}. ${loc.ten}</b><div class="small text-muted">${loc.loai_hinh || ''}</div>${reviewHtml}`;
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
    // AI FAB TRÊN MAP (mục XV/XVI): bản đồ không còn là dead-end — người
    // dùng có thể tiếp tục trò chuyện để chỉnh sửa itinerary tại chỗ.
    // ============================================================
    $('#btn-ai-fab').click(function () { $('#ai-map-chat').addClass('open'); $('#ai_map_instruction').focus(); });
    $('#btn-close-ai-chat').click(function () { $('#ai-map-chat').removeClass('open'); });

    function currentItineraryIds() {
        if (!tripState.currentRoutes) return [];
        const route = tripState.currentRoutes.routes[tripState.activeRouteIndex];
        return (route?.optimized_route || []).map(p => p.id).filter(id => id !== 'gps_current');
    }

    function appendChatBubble(text, who) {
        $('#ai-map-chat-log').append(`<div class="chat-bubble ${who}">${text}</div>`);
        const log = document.getElementById('ai-map-chat-log');
        log.scrollTop = log.scrollHeight;
    }

    function sendAiMapInstruction() {
        const instruction = $('#ai_map_instruction').val().trim();
        if (!instruction) return;
        if (!tripState.lastOptimizePayload) { alert('Chưa có lộ trình nào để chỉnh sửa.'); return; }

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
        };

        $.ajax({
            url: 'http://127.0.0.1:8000/api/ai-refine', method: 'POST',
            contentType: 'application/json', data: JSON.stringify(refinePayload),
            success: function (refineRes) {
                $('#ai-map-chat-log .loading').remove();
                appendChatBubble(refineRes.advice_text || 'Mình đã cập nhật lộ trình cho bạn.', 'assistant');

                // AI -> Backend -> Route recalculation -> Timeline update -> Map update:
                // gọi lại /api/optimize-route với ai_selected_ids mới để toàn bộ pipeline
                // (Greedy fill-up, timeline, map) được cập nhật đồng bộ, không chỉ vá 1 phần.
                const optimizePayload = Object.assign({}, tripState.lastOptimizePayload, { ai_selected_ids: refineRes.suggested_ids });
                tripState.aiSuggestedIds = refineRes.suggested_ids;
                $.ajax({
                    url: 'http://127.0.0.1:8000/api/optimize-route', method: 'POST',
                    contentType: 'application/json', data: JSON.stringify(optimizePayload),
                    success: function (res) {
                        if (res.status === 'success') applyOptimizeResult(res, optimizePayload);
                        else appendChatBubble('Không thể cập nhật bản đồ: ' + res.message, 'assistant');
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
            success: res => $('#upload-status').html(`<span class="text-success">${res.message}</span>`),
            error: () => $('#upload-status').html('<span class="text-danger">Lỗi Upload</span>')
        });
    });
});