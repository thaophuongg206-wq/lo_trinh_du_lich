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

    // Tự động set ngày hôm nay cho ô Input Ngày khởi hành
    const today = new Date().toISOString().split('T')[0];
    $('#trip_date').val(today);

    $.ajax({
        url: 'http://127.0.0.1:8000/api/locations',
        method: 'GET',
        success: function (res) {
            if (res.status === "success") {
                originalLocations = res.data;
            }
        }
    });

    $('#btn-optimize').on('click', function () {
        // BỔ SUNG: Gói thêm 3 dữ liệu mới vào Payload để chờ Backend xử lý
        const payload = {
            region: $('#main_location').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val(),
            trip_date: $('#trip_date').val(),
            start_point: $('#start_point').val(),
            vehicle_type: $('#vehicle_type').val()
        };

        const $btn = $(this);
        $btn.html('<span class="spinner-border spinner-border-sm"></span> Đang tạo lộ trình...');
        $btn.prop('disabled', true);

        $.ajax({
            url: 'http://127.0.0.1:8000/api/optimize-route',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(payload),
            success: function (res) {
                if (res.status === "success") {
                    generatedRoutes = res.routes;
                    availableMinutes = res.available_minutes;
                    
                    if(generatedRoutes.length === 0){
                        alert("Quỹ thời gian của bạn quá ngắn để thực hiện chuyến đi này!");
                        return;
                    }

                    const $selector = $('#route_selector').empty();
                    generatedRoutes.forEach((route, index) => {
                        $selector.append(`<option value="${index}">🌟 Lộ trình ${index + 1} (${route.total_time_minutes} phút)</option>`);
                    });

                    $('#result-panel').removeClass('d-none');
                    renderSelectedRoute(0);
                } else {
                    alert(res.message);
                }
            },
            error: function () {
                alert("Lỗi máy chủ nội bộ. Kiểm tra Backend!");
            },
            complete: function () {
                $btn.html('<i class="fa-solid fa-wand-magic-sparkles"></i> Tìm lộ trình tối ưu');
                $btn.prop('disabled', false);
            }
        });
    });

    $('#route_selector').on('change', function() {
        const selectedIndex = $(this).val();
        renderSelectedRoute(selectedIndex);
    });

    function renderSelectedRoute(index) {
        const routeData = generatedRoutes[index];
        const $timeline = $('#timeline-list').empty();

        let alertClass = routeData.dropped_point ? 'alert-warning' : 'alert-success';
        let msg = `<strong>Tổng thời gian:</strong> ${routeData.total_time_minutes} / ${availableMinutes} phút.`;
        
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

            $timeline.append(`
                <li>
                    <strong class="text-dark">Điểm ${idx + 1}:</strong> 
                    <span class="text-dark fw-bold">${loc.ten}</span>
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

            const marker = L.marker([loc.lat, loc.lon], {icon: customIcon})
                            .bindPopup(`<b>Điểm ${idx + 1}: ${loc.ten}</b>`)
                            .addTo(map);
            mapMarkers.push(marker);
        });

        if (routeLocs.length > 1) {
            const coordsString = routeLocs.map(loc => `${loc.lon},${loc.lat}`).join(';');
            const osrmUrl = `https://router.project-osrm.org/route/v1/driving/${coordsString}?overview=full&geometries=geojson`;

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
});