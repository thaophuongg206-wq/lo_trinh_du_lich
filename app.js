$(document).ready(function () {
    const map = L.map('map').setView([21.0285, 105.8542], 13);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap'
    }).addTo(map);

    let mapMarkers = [];
    let polylineRoute = null;
    let originalLocations = [];
    let generatedRoutes = []; 
    let availableMinutes = 0;
    let currentLocationMarker = null;  // Marker vị trí hiện tại của người dùng (GPS hoặc Geocode địa chỉ)
    let currentLocationData = null;    // { lat, lon } nếu dùng GPS

    // Tự động set ngày hôm nay + chặn không cho chọn ngày quá khứ (Chuẩn giờ Local)
    const now = new Date();
    // Bù trừ độ lệch múi giờ (Timezone Offset) để luôn ra đúng ngày VN
    const today = new Date(now.getTime() - (now.getTimezoneOffset() * 60000)).toISOString().split('T')[0];
    $('#trip_date').val(today);
    $('#trip_date').attr('min', today);  // Ngày tối thiểu = hôm nay

    $.ajax({
        url: 'http://127.0.0.1:8000/api/locations',
        method: 'GET',
        success: function (res) {
            if (res.status === "success") {
                originalLocations = res.data;
            }
        }
    });

    // Reset GPS data nếu người dùng chủ động gõ chữ lại vào input
    $('#start_point').on('input', function() {
        if (currentLocationData && !$(this).val().startsWith('📍')) {
            currentLocationData = null;
            if (currentLocationMarker) {
                map.removeLayer(currentLocationMarker);
                currentLocationMarker = null;
            }
            $('#location-status').empty();
        }
    });

    // Hiển thị gợi ý/cảnh báo ngay khi chọn phương tiện
    const vehicleNotes = {
        'xe_45_cho': '<span class="text-warning"><i class="fa-solid fa-triangle-exclamation"></i> Xe 45 chỗ: Chỉ gợi ý các điểm có bãi đỗ xe lớn (TTTM, Di tích lớn).</span>',
        'xe_29_cho': '<span class="text-warning"><i class="fa-solid fa-triangle-exclamation"></i> Xe 29 chỗ: Chỉ gợi ý các điểm có bãi đỗ xe lớn.</span>',
        'xe_16_cho': '<span class="text-warning"><i class="fa-solid fa-triangle-exclamation"></i> Xe 16 chỗ: Chỉ gợi ý các điểm có bãi đỗ xe rộng.</span>',
        'o_to': '<span class="text-info"><i class="fa-solid fa-info-circle"></i> Ô tô: Tự động loại các quán trong hẻm/ngõ hẹp.</span>',
        'xe_may': '<span class="text-muted"><i class="fa-solid fa-check"></i> Xe máy: Đi được tất cả các địa điểm và ngõ ngách.</span>',
        'xe_dap': '<span class="text-muted"><i class="fa-solid fa-check"></i> Xe đạp: Đi được tất cả các địa điểm.</span>',
        'di_bo': '<span class="text-muted"><i class="fa-solid fa-person-walking"></i> Đi bộ: Thích hợp khám phá khu vực gần.</span>'
    };

    $('#vehicle_type').on('change', function() {
        const vType = $(this).val();
        $('#vehicle-hint').html(vehicleNotes[vType] || '');
    }).trigger('change');

    // ================================================================
    // NÚT 📍 "Dùng vị trí hiện tại" → gọi GPS trình duyệt
    // ================================================================
    $('#btn-use-location').on('click', function () {
        const $status = $('#location-status');
        const $btn = $(this);

        if (!navigator.geolocation) {
            $status.html('<span class="text-danger"><i class="fa-solid fa-circle-xmark"></i> Trình duyệt không hỗ trợ GPS!</span>');
            return;
        }

        $btn.prop('disabled', true);
        $status.html('<span class="text-muted"><i class="fa-solid fa-spinner fa-spin"></i> Đang xác định vị trí...</span>');

        navigator.geolocation.getCurrentPosition(
            function (position) {
                const lat = position.coords.latitude;
                const lon = position.coords.longitude;
                currentLocationData = { lat, lon };

                if (currentLocationMarker) map.removeLayer(currentLocationMarker);

                const gpsIcon = L.divIcon({
                    html: `<div style="
                        background: #28a745; color: white;
                        border-radius: 50%; width: 28px; height: 28px;
                        display: flex; align-items: center; justify-content: center;
                        border: 3px solid white; box-shadow: 0 0 8px rgba(40,167,69,0.8);
                        font-size: 14px;
                    "><i class="fa-solid fa-location-dot"></i></div>`,
                    className: '', iconSize: [28, 28], iconAnchor: [14, 14]
                });

                currentLocationMarker = L.marker([lat, lon], { icon: gpsIcon })
                    .bindPopup('<b>📍 Vị trí hiện tại của bạn</b>')
                    .addTo(map);

                map.setView([lat, lon], 15);

                $('#start_point').val(`📍 Vị trí hiện tại (${lat.toFixed(5)}, ${lon.toFixed(5)})`);
                $status.html('<span class="text-success"><i class="fa-solid fa-circle-check"></i> Đã xác định vị trí GPS!</span>');
                $btn.prop('disabled', false);
            },
            function (error) {
                let msg = 'Không lấy được vị trí!';
                if (error.code === 1) msg = 'Bạn đã từ chối quyền GPS trên trình duyệt!';
                if (error.code === 2) msg = 'Không tìm thấy tín hiệu vị trí!';
                if (error.code === 3) msg = 'Hết thời gian chờ GPS!';
                $status.html(`<span class="text-danger"><i class="fa-solid fa-circle-xmark"></i> ${msg}</span>`);
                $btn.prop('disabled', false);
            },
            { timeout: 10000, maximumAge: 60000 }
        );
    });

    // ================================================================
    // NÚT TÌM LỘ TRÌNH TỐI ƯU
    // ================================================================
    $('#btn-optimize').on('click', function () {
        const $btn = $(this);
        const startPointText = $('#start_point').val().trim();

        $btn.html('<span class="spinner-border spinner-border-sm"></span> Đang tạo lộ trình...');
        $btn.prop('disabled', true);

        const payload = {
            region: $('#main_location').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            trip_date: $('#trip_date').val(),
            vehicle_type: $('#vehicle_type').val(),
            user_preference: $('#user_preference').val(),
            weight: $('#preference_weight').val()
        };

        if (!currentLocationData && !startPointText) {
            $('#location-status').html('<span class="text-danger"><i class="fa-solid fa-circle-xmark"></i> Vui lòng nhập điểm xuất phát hoặc bấm 🎯 Định vị!</span>');
            $('#start_point').addClass('is-invalid').focus();
            $btn.html('<i class="fa-solid fa-wand-magic-sparkles"></i> Tìm lộ trình tối ưu');
            $btn.prop('disabled', false);
            return;
        }
        $('#start_point').removeClass('is-invalid');

        if (currentLocationData) {
            payload.start_lat = currentLocationData.lat;
            payload.start_lon = currentLocationData.lon;
            payload.start_point = '';
            sendOptimizeRequest(payload, $btn);

        } else if (startPointText && !startPointText.startsWith('📍')) {
            $('#location-status').html('<span class="text-muted"><i class="fa-solid fa-spinner fa-spin"></i> Đang tìm tọa độ địa chỉ...</span>');

            $.ajax({
                url: 'https://nominatim.openstreetmap.org/search',
                method: 'GET',
                data: {
                    q: startPointText + ', Việt Nam',
                    format: 'json',
                    limit: 1
                },
                headers: { 'Accept-Language': 'vi' },
                success: function (results) {
                    if (results && results.length > 0) {
                        const lat = parseFloat(results[0].lat);
                        const lon = parseFloat(results[0].lon);

                        payload.start_lat = lat;
                        payload.start_lon = lon;
                        payload.start_point = '';

                        if (currentLocationMarker) map.removeLayer(currentLocationMarker);
                        const addrIcon = L.divIcon({
                            html: `<div style="
                                background: #ff6b35; color: white;
                                border-radius: 50%; width: 28px; height: 28px;
                                display: flex; align-items: center; justify-content: center;
                                border: 3px solid white; box-shadow: 0 0 8px rgba(255,107,53,0.8);
                                font-size: 14px;
                            "><i class="fa-solid fa-location-dot"></i></div>`,
                            className: '', iconSize: [28, 28], iconAnchor: [14, 14]
                        });

                        currentLocationMarker = L.marker([lat, lon], { icon: addrIcon })
                            .bindPopup(`<b>📍 ${startPointText}</b><br><small>${results[0].display_name}</small>`)
                            .addTo(map);

                        map.setView([lat, lon], 15);
                        $('#location-status').html('<span class="text-success"><i class="fa-solid fa-circle-check"></i> Đã định vị địa chỉ!</span>');
                        sendOptimizeRequest(payload, $btn);
                    } else {
                        $('#location-status').html('<span class="text-info"><i class="fa-solid fa-info-circle"></i> Tìm theo tên địa điểm trong DB...</span>');
                        payload.start_point = startPointText;
                        sendOptimizeRequest(payload, $btn);
                    }
                },
                error: function () {
                    payload.start_point = startPointText;
                    sendOptimizeRequest(payload, $btn);
                }
            });
        } else {
            payload.start_point = '';
            sendOptimizeRequest(payload, $btn);
        }
    });

    function sendOptimizeRequest(payload, $btn) {
        $.ajax({
            url: 'http://127.0.0.1:8000/api/optimize-route',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(payload),
            success: function (res) {
                if (res.status === "success") {
                    generatedRoutes = res.routes;
                    availableMinutes = res.available_minutes;

                    if (generatedRoutes.length === 0) {
                        alert("Quỹ thời gian của bạn quá ngắn để thực hiện chuyến đi này!");
                        return;
                    }

                    // Hiển thị thông báo xe lớn nếu có
                    $('#vehicle-note-banner').remove();
                    if (res.vehicle_note) {
                        const count = res.accessible_locations_count || 0;
                        const banner = `
                            <div id="vehicle-note-banner" class="alert alert-warning py-2 px-3 mb-2 d-flex align-items-start gap-2" style="font-size:0.82rem; border-left: 4px solid #f0a500;">
                                <i class="fa-solid fa-triangle-exclamation mt-1 text-warning"></i>
                                <div>
                                    <strong>Lưu ý phương tiện:</strong> ${res.vehicle_note}
                                    <br><span class="text-muted">Tìm thấy <strong>${count}</strong> địa điểm phù hợp.</span>
                                </div>
                            </div>`;
                        $('#emotion-cards-overlay').prepend(banner);
                    }

                    const $selector = $('#route_selector').empty();
                    $('#emotion-cards-overlay').removeClass('d-none'); 
                    $('#result-panel').addClass('d-none');             
                    const $container = $('#cards-container').empty();

                    const emotionTags = [
                        "Hơi thở thiên nhiên & Sống chậm",
                        "Không gian hoài niệm & Chữa lành",
                        "Khám phá góc phố & Check-in",
                        "Trải nghiệm trọn vẹn nhịp sống đô thị"
                    ];

                   const defaultImg = "https://images.unsplash.com/photo-1511884642898-4c92249e20b6?auto=format&fit=crop&w=500&q=80";

                    let imagesHtml = '';
                    if (visitSpots.length === 0) {
                        imagesHtml = `<img src="${defaultImg}" style="width: 100%; object-fit: cover;" alt="Mặc định">`;
                    } else if (visitSpots.length === 1) {
                        imagesHtml = `<img src="${visitSpots[0].url_hinh_anh || defaultImg}" style="width: 100%; object-fit: cover;" alt="Điểm 1">`;
                    } else if (visitSpots.length === 2) {
                        imagesHtml = `
                            <img src="${visitSpots[0].url_hinh_anh || defaultImg}" style="width: 50%; object-fit: cover;" alt="Điểm 1">
                            <img src="${visitSpots[1].url_hinh_anh || defaultImg}" style="width: 50%; object-fit: cover; border-left: 2px solid white;" alt="Điểm 2">
                        `;
                    } else {
                        imagesHtml = `
                            <img src="${visitSpots[0].url_hinh_anh || defaultImg}" style="width: 33.33%; object-fit: cover;" alt="Điểm 1">
                            <img src="${visitSpots[1].url_hinh_anh || defaultImg}" style="width: 33.33%; object-fit: cover; border-left: 2px solid white;" alt="Điểm 2">
                            <img src="${visitSpots[2].url_hinh_anh || defaultImg}" style="width: 33.33%; object-fit: cover; border-left: 2px solid white;" alt="Điểm 3">
                        `;
                    }

                    generatedRoutes.forEach((route, index) => {
                        // BUG 3 FIX: tên lộ trình do Backend quyết định dựa trên đặc điểm
                        // thực tế của route (route.route_name), KHÔNG còn xoay vòng qua một
                        // mảng emotionTags cố định theo index. Frontend chỉ hiển thị.
                        const title = `Lộ trình ${index + 1}: ${route.route_name || 'Lộ trình ' + (index + 1)}`;
                        const time = route.total_time_minutes;
                        const numPoints = route.optimized_route.length;
                        
                        // 1. Gắn tên cảm xúc vào Dropdown
                        $selector.append(`<option value="${index}">${title} — ${numPoints} điểm · ${time} phút</option>`);
                        
                        // 2. Lấy tên 3 địa điểm đầu tiên làm tóm tắt
                        let summarySpots = route.optimized_route.map(p => p.ten).slice(0, 3).join(" ➔ ");
                        if(numPoints > 3) summarySpots += " ...";
                        
                        // 3. Lọc bỏ "Vị trí của bạn" để chỉ lấy ảnh các điểm tham quan thực tế
                        const visitSpots = route.optimized_route.filter(p => p.id !== "gps_current" && p.loai_hinh !== "diem_xuat_phat");
                        
                        let imagesHtml = '';
                        if (visitSpots.length === 0) {
                            imagesHtml = `<img src="https://images.unsplash.com/photo-1511884642898-4c92249e20b6?auto=format&fit=crop&w=500&q=80" style="width: 100%; object-fit: cover;" alt="Mặc định">`;
                        } else if (visitSpots.length === 1) {
                            imagesHtml = `<img src="${visitSpots[0].url_hinh_anh || 'https://images.unsplash.com/photo-1511884642898-4c92249e20b6?auto=format&fit=crop&w=500&q=80'}" style="width: 100%; object-fit: cover;" alt="Điểm 1">`;
                        } else if (visitSpots.length === 2) {
                            const img1 = visitSpots[0].url_hinh_anh || 'https://images.unsplash.com/photo-1511884642898-4c92249e20b6?auto=format&fit=crop&w=300&q=80';
                            const img2 = visitSpots[1].url_hinh_anh || 'https://images.unsplash.com/photo-1497935586351-b67a49e012bf?auto=format&fit=crop&w=300&q=80';
                            imagesHtml = `
                                <img src="${img1}" style="width: 50%; object-fit: cover;" alt="Điểm 1">
                                <img src="${img2}" style="width: 50%; object-fit: cover; border-left: 2px solid white;" alt="Điểm 2">
                            `;
                        } else {
                            const img1 = visitSpots[0].url_hinh_anh || 'https://images.unsplash.com/photo-1511884642898-4c92249e20b6?auto=format&fit=crop&w=300&q=80';
                            const img2 = visitSpots[1].url_hinh_anh || 'https://images.unsplash.com/photo-1497935586351-b67a49e012bf?auto=format&fit=crop&w=300&q=80';
                            const img3 = visitSpots[2].url_hinh_anh || 'https://images.unsplash.com/photo-1509042239860-f550ce710b93?auto=format&fit=crop&w=300&q=80';
                            imagesHtml = `
                                <img src="${img1}" style="width: 33.33%; object-fit: cover;" alt="Điểm 1">
                                <img src="${img2}" style="width: 33.33%; object-fit: cover; border-left: 2px solid white;" alt="Điểm 2">
                                <img src="${img3}" style="width: 33.33%; object-fit: cover; border-left: 2px solid white;" alt="Điểm 3">
                            `;
                        }

                        // 4. Render Card với thiết kế ảnh động
                        $container.append(`
                            <div class="col-md-6 mb-3">
                                <div class="card h-100 shadow-sm emotion-card border-0 rounded-4 overflow-hidden" data-index="${index}" style="cursor: pointer;">
                                    <div class="d-flex" style="height: 140px;">
                                        ${imagesHtml}
                                    </div>
                                    <div class="card-body p-3 bg-white">
                                        <h6 class="card-title fw-bold text-dark mb-2">${title}</h6>
                                        <p class="card-text text-muted mb-2" style="font-size: 0.8rem;">
                                            <i class="fa-solid fa-map-pin text-danger me-1"></i> ${summarySpots}
                                        </p>
                                        <div class="d-flex justify-content-between align-items-center mt-3">
                                            <span class="badge bg-light text-dark border"><i class="fa-regular fa-clock"></i> ${time} phút</span>
                                            <span class="text-warning" style="font-size: 0.85rem; font-weight: 500;">Chi tiết &rarr;</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        `);
                    });

                } else {
                    alert(res.message);
                }
            },
            error: function (xhr) {
                // BACKEND CONTRACT UPDATE: các lỗi do request không hợp lệ (điểm xuất
                // phát không tìm thấy, sai định dạng giờ, weight ngoài khoảng 0-100...)
                // giờ trả về HTTP 400/422 kèm { detail: "..." } thay vì luôn 200 với
                // { status: "error" }. Hiển thị đúng thông điệp lỗi từ Backend thay vì
                // một câu chung chung không giúp ích gì cho người dùng.
                let msg = "Lỗi máy chủ nội bộ. Kiểm tra Backend!";
                if (xhr.responseJSON) {
                    if (xhr.responseJSON.detail) {
                        msg = typeof xhr.responseJSON.detail === 'string'
                            ? xhr.responseJSON.detail
                            : JSON.stringify(xhr.responseJSON.detail);
                    } else if (xhr.responseJSON.message) {
                        msg = xhr.responseJSON.message;
                    }
                }
                alert(msg);
            },
            complete: function () {
                $btn.html('<i class="fa-solid fa-wand-magic-sparkles"></i> Tìm lộ trình tối ưu');
                $btn.prop('disabled', false);
            }
        });
    }

    $('#route_selector').on('change', function() {
        const selectedIndex = $(this).val();
        renderSelectedRoute(selectedIndex);
    });

    function renderSelectedRoute(index) {
        const routeData = generatedRoutes[index];
        const $timeline = $('#timeline-list').empty();

        const firstPoint = routeData.optimized_route[0];
        const lastPoint  = routeData.optimized_route[routeData.optimized_route.length - 1];
        const startStr   = firstPoint ? firstPoint.arrive_time : '--:--';
        const endStr     = lastPoint  ? lastPoint.depart_time  : '--:--';

        let alertClass = routeData.dropped_point ? 'alert-warning' : 'alert-success';
        let msg = `<strong>🕐 Bắt đầu:</strong> ${startStr} &nbsp;→&nbsp; <strong>🕔 Kết thúc:</strong> ${endStr} &nbsp;|&nbsp; <strong>Tổng:</strong> ${routeData.total_time_minutes} / ${availableMinutes} phút.`;
        
        if (routeData.dropped_point) {
            msg += `<br><em style="font-size: 0.85rem;">* Hệ thống đã tự động loại bỏ điểm xa nhất do không đủ quỹ thời gian.</em>`;
        }
        
        $('#metrics-info').removeClass('alert-info alert-warning alert-success alert-danger').addClass(alertClass).html(msg);

        routeData.optimized_route.forEach((loc, idx) => {
            let travelInfo = '';
            if (idx < routeData.optimized_route.length - 1) {
                travelInfo = `
                    <div class="mt-1 mb-2" style="font-size: 0.85rem; color: #d87c4f;">
                        <i class="fa-solid fa-car-side"></i> Di chuyển: ~${loc.distance_to_next} km (${loc.travel_to_next} phút)
                    </div>
                `;
            }

            let waitInfo = '';
            if (loc.wait_time > 0) {
                waitInfo = `
                    <div class="mt-1" style="font-size: 0.85rem; color: #17a2b8;">
                        <i class="fa-solid fa-mug-hot"></i> Chờ mở cửa: ${loc.wait_time} phút
                    </div>
                `;
            }

            const timeTag = (loc.arrive_time && loc.depart_time)
                ? `<span class="badge text-bg-secondary ms-2" style="font-size:0.78rem; font-weight:500;">
                       ${loc.arrive_time} – ${loc.depart_time}
                   </span>`
                : '';

            $timeline.append(`
                <li>
                    <strong class="text-dark">Điểm ${idx + 1}:</strong> 
                    <span class="text-dark fw-bold">${loc.ten}</span>${timeTag}
                    <div class="text-muted mt-1" style="font-size: 0.85rem;">
                        <i class="fa-regular fa-clock"></i> Tham quan: ${loc.visit_time} phút
                    </div>
                    ${waitInfo}
                    ${travelInfo}
                </li>
            `);
        });

        drawMap(routeData.optimized_route);
    }

    function drawMap(routeLocs) {
        mapMarkers.forEach(m => map.removeLayer(m));
        if (polylineRoute) map.removeLayer(polylineRoute);
        mapMarkers = [];
        
        if (routeLocs.length === 0) return;

        routeLocs.forEach((loc, idx) => {
            const markerHtml = `
                <div style="
                    background-color: #d87c4f; 
                    color: white; 
                    border-radius: 50%; 
                    width: 25px; 
                    height: 25px; 
                    display: flex; 
                    align-items: center; 
                    justify-content: center; 
                    font-weight: bold; 
                    border: 2px solid white; 
                    box-shadow: 0 0 5px rgba(0,0,0,0.5);
                    font-size: 14px;
                ">${idx + 1}</div>
            `;
            
            const customIcon = L.divIcon({
                html: markerHtml,
                className: '', 
                iconSize: [25, 25],
                iconAnchor: [12.5, 12.5],
                popupAnchor: [0, -12]
            });

            // Giao diện Popup chi tiết địa điểm
            const loaiHinhBadge = loc.loai_hinh
                ? `<span class="badge mb-2" style="background:#d87c4f; font-size:0.75rem;">${loc.loai_hinh}</span>`
                : '';

            const chiTietHtml = loc.thong_tin_chi_tiet
                ? `<p class="mb-2" style="font-size: 0.82rem; color:#444;">${loc.thong_tin_chi_tiet}</p>`
                : (loc.mo_ta ? `<p class="mb-2" style="font-size: 0.82rem; color:#666;">${loc.mo_ta}</p>` : '');

            const phuHopHtml = loc.phu_hop
                ? `<div class="mb-2" style="font-size: 0.8rem;">
                       <i class="fa-solid fa-heart text-danger me-1"></i>
                       <strong>Phù hợp:</strong> ${loc.phu_hop}
                   </div>`
                : '';

            const reviewHtml = loc.review
                ? `<div class="bg-light p-2 rounded border" style="font-size: 0.8rem; font-style: italic; border-left: 3px solid #d87c4f !important;">
                       <i class="fa-solid fa-quote-left text-muted"></i> ${loc.review}
                   </div>`
                : '';

            const timeHtml = `<div class="mb-2" style="font-size: 0.8rem; color:#555;">
                <i class="fa-regular fa-clock me-1"></i> Tham quan: <strong>${loc.visit_time} phút</strong>
                &nbsp;|&nbsp; <i class="fa-solid fa-door-open me-1"></i>
                ${loc.arrive_time} – ${loc.depart_time}
            </div>`;

            const popupContent = `
                <div style="min-width: 260px; max-width: 300px;">
                    <h6 class="fw-bold mb-1" style="color: #d87c4f;">📍 ${idx + 1}. ${loc.ten}</h6>
                    ${loaiHinhBadge}
                    <hr class="my-1">
                    ${timeHtml}
                    ${chiTietHtml}
                    ${phuHopHtml}
                    ${reviewHtml}
                </div>
            `;

            const marker = L.marker([loc.lat, loc.lon], {icon: customIcon})
                            .bindPopup(popupContent)
                            .addTo(map);
            mapMarkers.push(marker);
        });

        if (routeLocs.length > 1) {
                    const coordsString = routeLocs.map(loc => `${loc.lon},${loc.lat}`).join(';');
                    
                    // Kiểm tra phương tiện đang chọn để gọi đúng Profile của OSRM
                    let osrmProfile = 'driving'; // Mặc định cho ô tô / xe máy
                    const selectedVehicle = $('#vehicle_type').val();
                    
                    if (selectedVehicle === 'di_bo') {
                        osrmProfile = 'foot';
                    } else if (selectedVehicle === 'xe_dap') {
                        osrmProfile = 'cycling';
                    }

                    // Gắn osrmProfile động vào link API
                    const osrmUrl = `https://router.project-osrm.org/route/v1/${osrmProfile}/${coordsString}?overview=full&geometries=geojson`;

            $.ajax({
                url: osrmUrl,
                method: 'GET',
                success: function (res) {
                    if (res.routes && res.routes.length > 0) {
                        const geojsonCoords = res.routes[0].geometry.coordinates;
                        const validLatlngs = geojsonCoords.map(c => [c[1], c[0]]);
                        
                        polylineRoute = L.polyline(validLatlngs, { color: '#d87c4f', weight: 5 }).addTo(map);
                        map.fitBounds(polylineRoute.getBounds(), { padding: [50, 50] });
                    }
                },
                error: function() {
                    const fallbackLatlngs = routeLocs.map(loc => [loc.lat, loc.lon]);
                    polylineRoute = L.polyline(fallbackLatlngs, { color: '#d87c4f', weight: 4, dashArray: '5, 10' }).addTo(map);
                    map.fitBounds(polylineRoute.getBounds(), { padding: [50, 50] });
                }
            });
        }
    }

    $(document).on('click', '.emotion-card', function() {
        const selectedIndex = $(this).data('index');
        
        $('#emotion-cards-overlay').addClass('d-none');
        $('#result-panel').removeClass('d-none');
        
        $('#route_selector').val(selectedIndex);
        renderSelectedRoute(selectedIndex); 
    });

    $(document).on('mouseenter', '.emotion-card', function() {
        $(this).css({'transform': 'translateY(-5px)', 'border-color': '#d87c4f', 'transition': 'all 0.2s'});
    }).on('mouseleave', '.emotion-card', function() {
        $(this).css({'transform': 'translateY(0)', 'border-color': 'transparent'});
    });
    
    // Sự kiện nút Quay lại màn hình Card
    $(document).on('click', '#btn-back-cards', function() {
        $('#result-panel').addClass('d-none');
        $('#emotion-cards-overlay').removeClass('d-none');
    });

    // --- ADMIN TOOL: UPLOAD EXCEL ---
    $('#btn-upload-excel').on('click', function() {
        const fileInput = document.getElementById('excel_file');
        if (!fileInput || fileInput.files.length === 0) {
            alert('Vui lòng chọn file Excel chứa danh sách địa điểm!');
            return;
        }
        
        const formData = new FormData();
        formData.append("file", fileInput.files[0]);
        
        const $status = $('#upload-status');
        $status.html('<span class="text-primary"><i class="fa-solid fa-spinner fa-spin"></i> Đang xử lý CSDL...</span>');
        
        $.ajax({
            url: 'http://127.0.0.1:8000/api/admin/import-excel',
            method: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: function(res) {
                if(res.status === "success") {
                    $status.html(`<span class="text-success fw-bold"><i class="fa-solid fa-check"></i> ${res.message}</span>`);
                    fileInput.value = ''; // Xóa file đã chọn sau khi up xong
                } else {
                    $status.html(`<span class="text-danger fw-bold"><i class="fa-solid fa-xmark"></i> ${res.message}</span>`);
                }
            },
            error: function() {
                $status.html('<span class="text-danger fw-bold"><i class="fa-solid fa-xmark"></i> Lỗi kết nối Backend!</span>');
            }
        });
    });
});