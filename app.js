$(document).ready(function () {
    const map = L.map('map').setView([21.0285, 105.8542], 13);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap'
    }).addTo(map);

    let mapMarkers = [], polylineRoutes = [];
    let isAiUpdating = false;

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

        // SOURCE OF TRUTH của Screen 3
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

        tripState.selectedRouteId = itinerary.route_id || tripState.selectedRouteId;
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

        renderTimelineAndMap(itinerary, vehicleType, availableMinutes);
        renderItinerarySummary(itinerary);
    }

    const today = new Date(new Date().getTime() - (new Date().getTimezoneOffset() * 60000))
        .toISOString().split('T')[0];
    $('#trip_date').val(today).attr('min', today);

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

        if (vType === 'di_bo') {
            return `🚶 Bạn sẽ đi bộ khoảng ${distStr} km, mất khoảng ${minsStr} phút.`;
        }
        if (vType === 'xe_dap') {
            return `🚲 Bạn đạp xe khoảng ${distStr} km, mất khoảng ${minsStr} phút.`;
        }
        if (vType === 'o_to') {
            return `🚗 Di chuyển khoảng ${distStr} km bằng ô tô, mất khoảng ${minsStr} phút.`;
        }
        if (vType === 'xe_16_cho') {
            return `🚌 Di chuyển khoảng ${distStr} km bằng xe 16 chỗ, mất khoảng ${minsStr} phút.`;
        }
        if (vType === 'xe_29_cho') {
            return `🚌 Di chuyển khoảng ${distStr} km bằng xe 29 chỗ, mất khoảng ${minsStr} phút.`;
        }
        return `🏍️ Di chuyển khoảng ${distStr} km bằng xe máy, mất khoảng ${minsStr} phút.`;
    }

    function durationLabel(mins) {
        mins = Math.round(mins || 0);
        const h = Math.floor(mins / 60), m = mins % 60;
        if (h <= 0) return `${m} phút`;
        return m > 0 ? `${h}h${m}` : `${h}h`;
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
            },
            () => $status.html('<span class="text-danger">Lỗi định vị</span>')
        );
    });

    $('#start_point').on('input', function () {
        tripState.currentLocationData = null;
        $('#location-status').empty();
    });

    function resolveStartAndRun(onReady) {
        const startPointText = $('#start_point').val().trim();
        const usingGPS = !!tripState.currentLocationData;
        const fingerprint = usingGPS
            ? `gps:${tripState.currentLocationData.lat},${tripState.currentLocationData.lon}`
            : `text:${startPointText}`;

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
            onReady({
                start_lat: tripState.currentLocationData.lat,
                start_lon: tripState.currentLocationData.lon,
                start_point: ''
            });
            return;
        }

        $.get('https://nominatim.openstreetmap.org/search', {
            q: startPointText + ', Việt Nam',
            format: 'json',
            limit: 1
        })
            .done(res => {
                tripState.startFingerprint = fingerprint;
                if (res && res.length > 0) {
                    onReady({
                        start_lat: parseFloat(res[0].lat),
                        start_lon: parseFloat(res[0].lon),
                        start_point: ''
                    });
                } else {
                    onReady({ start_point: startPointText });
                }
            })
            .fail(() => {
                tripState.startFingerprint = fingerprint;
                onReady({ start_point: startPointText });
            });
    }

    function applyAdvice(res) {
        tripState.sessionId = res.session_id || tripState.sessionId;
        tripState.aiSuggestedIds = res.suggested_ids || [];
        $('#ai-advice-content').html((res.advice_text || '').replace(/\n/g, '<br>'));
        renderQuickActions();
    }

    function fetchRoutes(onComplete) {
        const payload = Object.assign({}, tripState.baseParams, {
            session_id: tripState.sessionId,
            ai_selected_ids: tripState.aiSuggestedIds,
            num_routes: 5,
        });
        $.ajax({
            url: 'http://127.0.0.1:8000/api/routes',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(payload),
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
        const $grid =$('#ai-routes-grid').empty();
        routes.forEach(r => {
            const isSelected = r.route_id === tripState.selectedRouteId;
            
            // Xây dựng Timeline mở rộng cho Screen 2
            let timelineHtml = '<ul class="timeline-ui mt-3 pt-3 border-top" style="display: none;">';
            if (r.timeline && r.timeline.length > 0) {
                r.timeline.forEach(item => {
                    if (item.type === 'visit') {
                        const loc = r.places.find(p => p.id === item.place_id) || {};
                        const imgHtml = loc.url_hinh_anh ? `<img src="${loc.url_hinh_anh}" class="timeline-image mt-2 mb-2" style="height: 120px;">` : '';
                        timelineHtml += `
                            <li>
                                <span class="badge bg-dark mb-1">${item.start} - ${item.end}</span>
                                <h6 class="fw-bold mb-1">${item.order}. ${item.name}</h6>
                                ${imgHtml}
                            </li>
                        `;
                    } else if (item.type === 'travel') {
                        timelineHtml += `<div class="mt-1 mb-2 ms-4 small fw-bold text-muted border-start ps-3" style="border-color: #d87c4f !important;">🚗 Di chuyển ${item.duration} phút</div>`;
                    }
                });
            }
            timelineHtml += '</ul>';

            const $card =$('<div class="route-card"></div>')
                .toggleClass('selected', isSelected)
                .attr('data-route-id', r.route_id)
                .html(`
                    <div class="route-card-theme">${r.theme_label || ''}</div>
                    <h6 class="fw-bold mb-1">${r.name || r.route_id}</h6>
                    <p class="route-card-desc">${r.description || ''}</p>
                    <div class="route-card-stats"><i class="fa-regular fa-clock"></i> ${r.place_count || (r.places || []).length} điểm · ${durationLabel(r.total_duration)}</div>
                    ${timelineHtml}
                `);

            // Nếu đang được chọn thì mở timeline ra
            if (isSelected) $card.find('.timeline-ui').show();

            $card.on('click', function () {
                // Đóng tất cả và bỏ chọn
                tripState.selectedRouteId = r.route_id;
                $('#ai-routes-grid .route-card').removeClass('selected');
                $('#ai-routes-grid .timeline-ui').slideUp('fast');
                
                // Chọn thẻ hiện tại và mở timeline
                $(this).addClass('selected');$(this).find('.timeline-ui').slideDown('fast');
                
                // Báo cho backend biết đang chọn lộ trình nào
                $.ajax({
                    url: 'http://127.0.0.1:8000/api/routes/select',
                    method: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({ session_id: tripState.sessionId, route_id: r.route_id })
                });
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
            const $chip = $(`<button type="button" class="quick-action-chip">${qa.label}</button>`);
            $chip.on('click', function () { runQuickAction(qa.text, $chip); });
            $wrap.append($chip);
        });
    }

function runQuickAction(instructionText, $chip) {
        if (!tripState.selectedRouteId) return alert("Vui lòng chọn một lộ trình bên dưới trước khi chỉnh sửa!");

        $('#ai-quick-actions .quick-action-chip').prop('disabled', true);
        
        // Tạo Popup Loading khóa màn hình
        const loadingHtml = `
            <div id="quick-action-loading" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(255,255,255,0.85); z-index: 9999; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                <div class="spinner-border text-danger" style="width: 3rem; height: 3rem;" role="status"></div>
                <h5 class="mt-3 fw-bold text-dark">Đang tinh chỉnh địa điểm cho phù hợp...</h5>
            </div>`;
        $('body').append(loadingHtml);

        // 1. Lấy ID của DUY NHẤT lộ trình đang được User chọn
        const currentRoute = tripState.currentRoutes.routes.find(r => r.route_id === tripState.selectedRouteId);
        const currentIds = currentRoute.places.map(p => p.id).filter(id => id !== 'gps_current');

        const payload = {
            region: 'Hanoi',
            vehicle_type: $('#vehicle_type').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            user_preference: $('#user_preference').val(),
            current_ids: currentIds, // Context bây giờ là chuẩn 100%
            instruction: instructionText,
            session_id: tripState.sessionId,
        };

        $.ajax({
            url: 'http://127.0.0.1:8000/api/ai-refine',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(payload),
            success: function (res) {
                tripState.sessionId = res.session_id || tripState.sessionId;
                
                // 2. Chỉ tính lại lộ trình đang thao tác, không phá vỡ 4 lộ trình còn lại
                $.ajax({
                    url: 'http://127.0.0.1:8000/api/itinerary/update',
                    method: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({ 
                        session_id: tripState.sessionId,
                        ai_advice: res.advice_text
                    }),
                    success: function (updateRes) {
                        if (updateRes.status === 'success') {
                            // Ghi đè vào đúng vị trí lộ trình cũ trong State
                            const idx = tripState.currentRoutes.routes.findIndex(r => r.route_id === tripState.selectedRouteId);
                            if(idx >= 0) tripState.currentRoutes.routes[idx] = updateRes.itinerary;
                            
                            // Render lại danh sách, thẻ nào đang select sẽ tự động trượt mở Timeline
                            renderRouteCards(tripState.currentRoutes.routes);
                        }
                    },
                    complete: function() {
                        $('#quick-action-loading').remove();
                        $('#ai-quick-actions .quick-action-chip').prop('disabled', false);
                    }
                });
            },
            error: (xhr) => {
                alert("Không cập nhật được gợi ý: " + (xhr.responseJSON?.detail || "Kiểm tra Backend."));
                $('#quick-action-loading').remove();
                $('#ai-quick-actions .quick-action-chip').prop('disabled', false);
            }
        });
    }

    // ============================================================
    // SCREEN 1 → SCREEN 2
    // ============================================================
    $('#btn-ask-ai, #btn-reject-route').click(function () {
        const userPref = $('#user_preference').val().trim();
        if (!userPref) {
            alert("Hãy nhập mong muốn của bạn!");
            $('#user_preference').focus();
            return;
        }
        if (!tripState.currentLocationData && !$('#start_point').val().trim()) {
            alert('Vui lòng nhập điểm xuất phát!');
            return;
        }

        const $askBtn = $('#btn-ask-ai');
        const $rejectBtn = $('#btn-reject-route');
        const isReject = $(this).is($rejectBtn);

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
                url: 'http://127.0.0.1:8000/api/ai-suggest',
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    region: 'Hanoi',
                    user_preference: userPref,
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

    $('#btn-back-hero').click(function () {
        $('#chat-section').hide();
        $('#hero-section').fadeIn();
    });

    // ============================================================
    // SCREEN 2 → SCREEN 3
    // ============================================================
    $('#btn-accept-route').click(function () {
        if (!tripState.currentRoutes || !tripState.selectedRouteId) {
            alert('Chưa có lộ trình nào để chốt, vui lòng thử lại.');
            return;
        }
        const $btn = $(this);
        $btn.html('<i class="fa-solid fa-spinner fa-spin"></i> Đang tính toán...').prop('disabled', true);

        $.ajax({
            url: 'http://127.0.0.1:8000/api/routes/select',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                route_id: tripState.selectedRouteId
            }),
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
        const startTime = places[0]?.arrive_time || '--:--';
        const endTime = places[places.length - 1]?.depart_time || '--:--';
        const totalTime = itinerary.total_time_minutes || 0;

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

        $('#chat-section').hide();
        $('#map-section').fadeIn();
        setTimeout(() => map.invalidateSize(), 100);

        setCurrentItinerary(routes[idx]);
        refreshCurrentItinerary();
    }

    function renderTimelineAndMap(routeData, vType, totalMins) {
        let vIcon = '<i class="fa-solid fa-car-side text-primary"></i>';
        if (vType === 'xe_may') vIcon = '<i class="fa-solid fa-motorcycle text-primary"></i>';
        if (vType === 'di_bo') vIcon = '<i class="fa-solid fa-person-walking text-primary"></i>';
        if (vType === 'xe_dap') vIcon = '<i class="fa-solid fa-bicycle text-primary"></i>';
        if (vType && vType.includes('cho')) vIcon = '<i class="fa-solid fa-bus text-primary"></i>';

        const $timeline = $('#timeline-list').empty();

        // Không ghi đè #metrics-info nữa — để renderItinerarySummary phụ trách

        if (routeData.timeline && routeData.timeline.length > 0) {
            routeData.timeline.forEach(item => {
                if (item.type === 'visit') {
                    const loc = routeData.optimized_route.find(p => p.id === item.place_id) || {};
                    const review = (loc.review || '').trim();
                    const reviewHtml = review ? `<div class="timeline-review mt-2">★ “${review}”</div>` : '';
                    const imgHtml = loc.url_hinh_anh ? `<img src="${loc.url_hinh_anh}" class="timeline-image mt-2 mb-2">` : ''; // Thêm ảnh
                    
                    $timeline.append(`
                        <li>
                            <span class="badge bg-dark mb-1">${item.start} - ${item.end}</span>
                            <h6 class="fw-bold mb-1">${item.order}. ${item.name}</h6>
                            <div class="text-muted small"><i class="fa-solid fa-camera"></i> Tham quan: ${item.duration} phút</div>
                            ${imgHtml}
                            ${reviewHtml}
                        </li>
                    `);
                } else if (item.type === 'travel') {
                    $timeline.append(`
                        <div class="mt-2 mb-3 ms-4 small fw-bold text-muted border-start ps-3" style="border-color: #d87c4f !important;">
                            ${vehicleSentence(vType, item.distance_km, item.duration)}
                        </div>
                    `);
                }
            });
        }

        drawMultiColorMap(routeData.optimized_route, vType);
    }

    function drawMultiColorMap(routeLocs, vType) {
        mapMarkers.forEach(m => map.removeLayer(m));
        polylineRoutes.forEach(p => map.removeLayer(p));
        mapMarkers = [];
        polylineRoutes = [];

        const colors = ['#FF3B30', '#4CD964', '#007AFF', '#FFCC00', '#5856D6'];

        routeLocs.forEach((loc, idx) => {
            const style = categoryStyle(loc.loai_hinh);
            const iconHtml = `<div class="map-marker-icon" style="background:${style.color};"><i class="fa-solid ${style.icon}"></i></div>`;

            const review = (loc.review || '').trim();
            const reviewHtml = review ? `<div class="mt-1 small fst-italic">★ “${review}”</div>` : '';

            // Rating (nếu backend có trả)
            const rating = loc.rating ?? loc.google_rating ?? loc.diem_danh_gia;
            const ratingHtml =
                rating !== undefined && rating !== null && rating !== ''
                    ? `<div class="small">⭐ ${rating}</div>`
                    : '';

            const popupHtml = `
                <b style="font-size:14px;">${idx + 1}. ${loc.ten}</b>
                <div class="small text-muted">${loc.loai_hinh || ''}</div>
                ${ratingHtml}
                ${reviewHtml}
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
            let osrmProfile = vType === 'di_bo' ? 'foot' : (vType === 'xe_dap' ? 'cycling' : 'driving');
            let promises = [];
            for (let i = 0; i < routeLocs.length - 1; i++) {
                const p1 = routeLocs[i], p2 = routeLocs[i + 1];
                const color = colors[i % colors.length];
                let req = $.get(`https://router.project-osrm.org/route/v1/${osrmProfile}/${p1.lon},${p1.lat};${p2.lon},${p2.lat}?overview=full&geometries=geojson`)
                    .then(res => polylineRoutes.push(
                        L.polyline(res.routes[0].geometry.coordinates.map(c => [c[1], c[0]]), {
                            color: color,
                            weight: 6,
                            opacity: 0.8
                        }).addTo(map)
                    ))
                    .catch(() => polylineRoutes.push(
                        L.polyline([[p1.lat, p1.lon], [p2.lat, p2.lon]], {
                            color: color,
                            weight: 4,
                            dashArray: '5,5'
                        }).addTo(map)
                    ));
                promises.push(req);
            }
            Promise.all(promises).then(() =>
                map.fitBounds(new L.featureGroup(mapMarkers).getBounds(), { padding: [50, 50] })
            );
        }
    }

    $('#btn-back-chat').click(function () {
        $('#map-section').hide();
        $('#chat-section').fadeIn();
    });

    // ============================================================
    // AI FAB TRÊN MAP
    // ============================================================
    $('#btn-ai-fab').click(function () {
        $('#ai-map-chat').addClass('open');
        $('#ai_map_instruction').focus();
    });
    $('#btn-close-ai-chat').click(function () {
        $('#ai-map-chat').removeClass('open');
    });

    function currentItineraryIds() {
        const itinerary = getActiveItinerary();
        if (!itinerary) return [];
        return (itinerary.optimized_route || [])
            .map(place => place.id)
            .filter(id => id && id !== 'gps_current');
    }

    function appendChatBubble(text, who) {
        $('#ai-map-chat-log').append(`<div class="chat-bubble ${who}">${text}</div>`);
        const log = document.getElementById('ai-map-chat-log');
        log.scrollTop = log.scrollHeight;
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
            url: 'http://127.0.0.1:8000/api/ai-refine',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(refinePayload),
            success: function (refineRes) {
                $('#ai-map-chat-log .loading').remove();
                tripState.sessionId = refineRes.session_id || tripState.sessionId;

                // Chỉ sau khi backend recalculate xong mới báo thành công
                $.ajax({
                    url: 'http://127.0.0.1:8000/api/itinerary/update',
                    method: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({ 
                        session_id: tripState.sessionId,
                        ai_advice: refineRes.advice_text // Gửi câu từ của AI xuống
                    }),
                    success: function (res) {
                        if (res.status !== 'success' || !res.itinerary) {
                            appendChatBubble(
                                'Không thể cập nhật lộ trình: ' + (res.message || 'Lỗi không xác định.'),
                                'assistant'
                            );
                            return;
                        }

                        // 1. Cập nhật SOURCE OF TRUTH
                        setCurrentItinerary(res.itinerary);

                        // 2. Đồng bộ currentRoutes (phòng trường hợp setCurrent chưa bắt được)
                        if (tripState.currentRoutes?.routes && tripState.activeRouteIndex >= 0) {
                            tripState.currentRoutes.routes[tripState.activeRouteIndex] = res.itinerary;
                        }

                        // 3. Render toàn bộ Screen 3
                        refreshCurrentItinerary();

                        // 4. Báo thành công SAU khi đã cập nhật xong
                        appendChatBubble(
                            refineRes.advice_text ||
                            'Mình đã cập nhật và sắp xếp lại hành trình cho bạn.',
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
                    'Xin lỗi, mình chưa hiểu ý bạn lắm: ' + (xhr.responseJSON?.detail || 'vui lòng thử lại.'),
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
            url: 'http://127.0.0.1:8000/api/admin/import-excel',
            method: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: res => $('#upload-status').html(`<span class="text-success">${res.message}</span>`),
            error: () => $('#upload-status').html('<span class="text-danger">Lỗi Upload</span>')
        });
    });
}); 