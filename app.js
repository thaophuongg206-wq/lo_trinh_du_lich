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
        const payload = {
            region: $('#main_location').val(),
            start_time: $('#start_time').val(),
            end_time: $('#end_time').val()
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
                        alert("Không thể tạo lộ trình do quỹ thời gian quá ngắn!");
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
        let msg = `<strong>Tổng thời gian:</strong> ${routeData.total_time_minutes} / ${availableMinutes} phút.<br>`;
        
        if (routeData.dropped_point) {
            msg += `<em>"Thời gian khả dụng quá ngắn, đã tự động loại bỏ điểm xa nhất."</em>`;
        } else {
            msg += `Áp dụng Thuật toán 2-OPT & OBTW thành công!`;
        }
        
        $('#metrics-info').removeClass('alert-info alert-warning alert-success alert-danger').addClass(alertClass).html(msg);

        routeData.optimized_route.forEach((loc, idx) => {
            $timeline.append(`
                <li>
                    <strong class="text-dark">Điểm ${idx + 1}:</strong> 
                    <span class="text-muted">${loc.ten}</span>
                </li>
            `);
        });

        drawMap(routeData.optimized_route);
    }

    function drawMap(routeLocs) {
        mapMarkers.forEach(m => map.removeLayer(m));
        if (polylineRoute) map.removeLayer(polylineRoute);
        mapMarkers = [];
        const latlngs = [];

        if (routeLocs.length === 0) return;

        routeLocs.forEach(loc => {
            const marker = L.marker([loc.lat, loc.lon]).bindPopup(`<b>${loc.ten}</b>`).addTo(map);
            mapMarkers.push(marker);
        });

        if (routeLocs.length > 1) {
            const coordsString = routeLocs.map(loc => `${loc.lon},${loc.lat}`).join(';');
            
            // Đã đổi sang chuẩn HTTPS để tránh lỗi Mixed Content
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