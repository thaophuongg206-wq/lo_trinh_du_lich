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
    var QUICK_ACTIONS = [
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

