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

