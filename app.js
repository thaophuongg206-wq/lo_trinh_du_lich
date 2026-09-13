$(document).ready(function () {
    const map = L.map('map').setView([21.0285, 105.8542], 13);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', { attribution: '&copy; OpenStreetMap' }).addTo(map);

    let mapMarkers = [], polylineRoutes = [];
    let dbLocations = []; 
    let currentLocationData = null;
    let aiSuggestedIds = [];

    const today = new Date(new Date().getTime() - (new Date().getTimezoneOffset() * 60000)).toISOString().split('T')[0];
    $('#trip_date').val(today).attr('min', today);

    $.get('http://127.0.0.1:8000/api/locations', res => { if (res.status === "success") dbLocations = res.data; });

    $('#btn-use-location').click(function () {
        const $status = $('#location-status');
        if (!navigator.geolocation) return $status.html('<span class="text-danger">Không hỗ trợ GPS</span>');
        $status.html('<span class="text-warning"><i class="fa-solid fa-spinner fa-spin"></i> Định vị...</span>');
        navigator.geolocation.getCurrentPosition(
            pos => {
                currentLocationData = { lat: pos.coords.latitude, lon: pos.coords.longitude };
                $('#start_point').val(`📍 Vị trí GPS hiện tại`);
                $status.html('<span class="text-success"><i class="fa-solid fa-check"></i> Đã định vị</span>');
            }, () => $status.html('<span class="text-danger">Lỗi định vị</span>')
        );
    });

    $('#start_point').on('input', function() { currentLocationData = null; $('#location-status').empty(); });

    $('#btn-ask-ai, #btn-reject-route').click(function () {
        const userPref = $('#user_preference').val().trim();
        if (!userPref) { alert("Hãy nhập mong muốn của bạn!"); $('#user_preference').focus(); return; }

        const payload = { region: 'Hanoi', user_preference: userPref, vehicle_type: $('#vehicle_type').val() };
        $('#btn-ask-ai').html('<i class="fa-solid fa-spinner fa-spin"></i> Đang suy nghĩ...').prop('disabled', true);

        $.ajax({
            url: 'http://127.0.0.1:8000/api/ai-suggest', method: 'POST',
            contentType: 'application/json', data: JSON.stringify(payload),
            success: function(res) {
                // Đã gỡ bỏ lệnh if (res.status === "success") gây lỗi undefined
                aiSuggestedIds = res.suggested_ids;
                $('#hero-section, #map-section').hide(); $('#chat-section').fadeIn();
                $('#ai-advice-content').html(res.advice_text.replace(/\n/g, '<br>'));

                const $grid = $('#ai-places-grid').empty();
                aiSuggestedIds.forEach(id => {
                    const place = dbLocations.find(p => p.id === id);
                    if(place) {
                        const img = place.url_hinh_anh || "https://images.unsplash.com/photo-1511884642898-4c92249e20b6?auto=format&fit=crop&w=500&q=80";
                        const rating = place.rating ? `${place.rating} ⭐` : 'Mới';
                        $grid.append(`<div class="col-md-4"><div class="place-card"><div class="rating-badge">${rating}</div><img src="${img}"><div class="p-3"><h6 class="fw-bold mb-1">${place.ten}</h6><small class="text-muted"><i class="fa-solid fa-tag"></i> ${place.loai_hinh}</small></div></div></div>`);
                    }
                });
            },
            error: (xhr) => alert("Lỗi gọi AI: " + (xhr.responseJSON?.detail || "Kiểm tra Backend.")),
            complete: () => $('#btn-ask-ai').html('<i class="fa-solid fa-wand-magic-sparkles me-2"></i> Lên lịch trình bằng AI').prop('disabled', false)
        });
    });

    $('#btn-accept-route').click(function() {
        const startPointText = $('#start_point').val().trim();
        if (!currentLocationData && !startPointText) return alert('Vui lòng nhập điểm xuất phát!');

        $(this).html('<i class="fa-solid fa-spinner fa-spin"></i> Đang tính toán...').prop('disabled', true);
        const payload = {
            region: 'Hanoi', start_time: $('#start_time').val(), end_time: $('#end_time').val(),
            trip_date: $('#trip_date').val(), vehicle_type: $('#vehicle_type').val(),
            user_preference: $('#user_preference').val(), weight: 50, ai_selected_ids: aiSuggestedIds
        };

        const executeOptimize = (data) => {
            $.ajax({
                url: 'http://127.0.0.1:8000/api/optimize-route', method: 'POST',
                contentType: 'application/json', data: JSON.stringify(data),
                success: function (res) {
                    if (res.status === "success") {
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
                            renderTimelineAndMap(res.routes[$(this).data('idx')], data.vehicle_type, res.available_minutes);
                        });

                        renderTimelineAndMap(res.routes[0], data.vehicle_type, res.available_minutes);
                    } else alert(res.message);
                },
                error: (xhr) => alert("Lỗi Thuật toán: " + (xhr.responseJSON?.detail || "Kiểm tra Backend")),
                complete: () => $('#btn-accept-route').html('<i class="fa-solid fa-check me-2"></i> Chốt lộ trình & Vẽ bản đồ').prop('disabled', false)
            });
        };

        if (currentLocationData) { payload.start_lat = currentLocationData.lat; payload.start_lon = currentLocationData.lon; payload.start_point = ''; executeOptimize(payload); } 
        else {
            $.get('https://nominatim.openstreetmap.org/search', { q: startPointText + ', Việt Nam', format: 'json', limit: 1 }, res => {
                if (res && res.length > 0) { payload.start_lat = parseFloat(res[0].lat); payload.start_lon = parseFloat(res[0].lon); payload.start_point = ''; } 
                else payload.start_point = startPointText;
                executeOptimize(payload);
            }).fail(() => { payload.start_point = startPointText; executeOptimize(payload); });
        }
    });

    function renderTimelineAndMap(routeData, vType, totalMins) {
        let vIcon = '<i class="fa-solid fa-car-side text-primary"></i>';
        if (vType === 'xe_may') vIcon = '<i class="fa-solid fa-motorcycle text-primary"></i>';
        if (vType === 'di_bo') vIcon = '<i class="fa-solid fa-person-walking text-primary"></i>';
        if (vType === 'xe_dap') vIcon = '<i class="fa-solid fa-bicycle text-primary"></i>';
        if (vType.includes('cho')) vIcon = '<i class="fa-solid fa-bus text-primary"></i>';

        const $timeline = $('#timeline-list').empty();
        $('#metrics-info').html(`<strong><i class="fa-regular fa-clock"></i> Bắt đầu:</strong> ${routeData.optimized_route[0]?.arrive_time || '--:--'} <br> <strong><i class="fa-solid fa-flag-checkered"></i> Kết thúc:</strong> ${routeData.optimized_route[routeData.optimized_route.length - 1]?.depart_time || '--:--'} <br> Tổng: ${routeData.total_time_minutes} / ${totalMins} phút.`);

        routeData.optimized_route.forEach((loc, idx) => {
            let travelInfo = idx < routeData.optimized_route.length - 1 ? `<div class="mt-2 small fw-bold text-muted">${vIcon} Di chuyển: ${loc.travel_to_next} phút (~${loc.distance_to_next}km)</div>` : '';
            $timeline.append(`<li><span class="badge bg-dark mb-1">${loc.arrive_time} - ${loc.depart_time}</span><h6 class="fw-bold mb-1">${idx + 1}. ${loc.ten}</h6><div class="text-muted small"><i class="fa-solid fa-camera"></i> Tham quan: ${loc.visit_time} phút</div>${travelInfo}</li>`);
        });

        drawMultiColorMap(routeData.optimized_route, vType);
    }

    function drawMultiColorMap(routeLocs, vType) {
        mapMarkers.forEach(m => map.removeLayer(m)); polylineRoutes.forEach(p => map.removeLayer(p));
        mapMarkers = []; polylineRoutes = [];
        
        const colors = ['#FF3B30', '#4CD964', '#007AFF', '#FFCC00', '#5856D6'];

        routeLocs.forEach((loc, idx) => {
            const iconHtml = `<div style="background:#2D3748;color:white;border-radius:50%;width:28px;height:28px;display:flex;align-items:center;justify-content:center;font-weight:bold;border:2px solid white;">${idx + 1}</div>`;
            const marker = L.marker([loc.lat, loc.lon], {icon: L.divIcon({html: iconHtml, className: '', iconSize: [28,28], iconAnchor: [14,14]})}).bindPopup(`<b style="font-size:14px;">${loc.ten}</b>`).addTo(map);
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