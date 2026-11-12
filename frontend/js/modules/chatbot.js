    // ============================================================
    // GLOBAL AI CHATBOT WIDGET (HOẠT ĐỘNG Ở CẢ SCREEN 2 & SCREEN 3)
    // ============================================================
    $('#btn-ai-fab').click(function () {
        $('#ai-map-chat').toggleClass('open');
        if ($('#ai-map-chat').hasClass('open')) {
            $('#ai_map_instruction').focus();
            if (tripState.sessionId) {
                loadChatHistory(tripState.sessionId);
            }
        }
    });

    $('#btn-close-ai-chat').click(function () {
        $('#ai-map-chat').removeClass('open');
    });

    function currentItineraryIds() {
        const itinerary = getActiveItinerary();
        if (itinerary) {
            const list = Array.isArray(itinerary.optimized_route) ? itinerary.optimized_route : (Array.isArray(itinerary.places) ? itinerary.places : []);
            const ids = list
                .map(place => place.id)
                .filter(id => id && id !== 'gps_current' && id !== 'custom_start');
            if (ids.length) return ids.map(String);
        }
        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes) && tripState.currentRoutes.routes.length) {
            const r = tripState.currentRoutes.routes.find(x => x.route_id === tripState.selectedRouteId) || tripState.currentRoutes.routes[0];
            const rPlaces = Array.isArray(r.optimized_route) ? r.optimized_route : (Array.isArray(r.places) ? r.places : []);
            return rPlaces
                .filter(p => p && p.id && p.id !== 'gps_current' && p.id !== 'custom_start' && p.loai_hinh !== 'diem_xuat_phat')
                .map(p => String(p.id));
        }
        return [];
    }

    function escapeHtml(str) {
        if (str == null) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function appendChatBubble(text, who) {
        $('#ai-map-chat-log').append(`<div class="chat-bubble ${who}">${text}</div>`);
        const log = document.getElementById('ai-map-chat-log');
        if (log) log.scrollTop = log.scrollHeight;
    }

    function renderAiRefineOptions(refineRes) {
        if (refineRes.advice_text) {
            appendChatBubble(escapeHtml(refineRes.advice_text), 'assistant');
        }

        const options = refineRes.options || [];
        if (!options.length) return;

        let html = `<div class="ai-refine-options-container">`;
        html += `<div class="ai-options-header"><i class="fa-solid fa-list-check me-1 text-primary"></i> Lựa chọn đề xuất cho bạn:</div>`;

        options.forEach((opt, idx) => {
            const starStr = opt.rating ? `<span class="opt-rating text-warning"><i class="fa-solid fa-star"></i> ${Number(opt.rating).toFixed(1)}</span>` : '';
            const distStr = opt.distance_km != null ? `<span class="opt-dist text-primary ms-2"><i class="fa-solid fa-location-arrow"></i> Cách ${opt.distance_km} km</span>` : '';
            const tagStr = (opt.tags || []).map(t => `<span class="opt-tag-pill">${escapeHtml(t)}</span>`).join(' ');

            html += `
                <div class="ai-option-item" data-place-id="${escapeHtml(opt.place_id)}">
                    <div class="ai-option-item-header">
                        <span class="ai-option-num">${idx + 1}</span>
                        <div class="ai-option-title-block flex-grow-1">
                            <span class="ai-option-name">${escapeHtml(opt.ten || opt.name || opt.title || (tripState.currentLocationData && tripState.currentLocationData.find(p => String(p.id) === String(opt.place_id))?.ten) || 'Địa điểm đề xuất')}</span>
                            <span class="ai-option-cat">${escapeHtml(opt.loai_hinh || (tripState.currentLocationData && tripState.currentLocationData.find(p => String(p.id) === String(opt.place_id))?.loai_hinh) || '')}</span>
                        </div>
                        <div class="ai-option-badges ms-auto">
                            ${starStr}
                            ${distStr}
                        </div>
                    </div>
                    ${tagStr ? `<div class="ai-option-tags-row mt-1">${tagStr}</div>` : ''}
                    <div class="ai-option-reason mt-1">${escapeHtml(opt.reason || '')}</div>
                    <div class="ai-option-action-row mt-2 text-end">
                        <button type="button" class="btn btn-sm btn-outline-primary btn-apply-ai-opt"
                                data-place-id="${escapeHtml(opt.place_id)}"
                                data-place-name="${escapeHtml(opt.ten)}"
                                data-target-id="${escapeHtml(refineRes.target_place_id || '')}"
                                data-target-name="${escapeHtml(refineRes.target_place_name || '')}"
                                data-action="${escapeHtml(opt.action || 'replace')}">
                            <i class="fa-solid fa-check me-1"></i> Chọn địa điểm này
                        </button>
                    </div>
                </div>
            `;
        });
        html += `</div>`;
        $('#ai-map-chat-log').append(html);

        const log = document.getElementById('ai-map-chat-log');
        if (log) log.scrollTop = log.scrollHeight;
    }

    $(document).on('click', '.btn-apply-ai-opt', function () {
        const $btn = $(this);
        const $container = $btn.closest('.ai-refine-options-container');
        const placeId = String($btn.data('place-id'));
        const placeName = String($btn.data('place-name'));
        const targetId = String($btn.data('target-id') || '');
        const targetName = String($btn.data('target-name') || '');
        const action = String($btn.data('action') || 'replace');

        if ($btn.prop('disabled')) return;
        $container.find('.btn-apply-ai-opt').prop('disabled', true).addClass('opacity-50');
        $btn.html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang áp dụng...').removeClass('opacity-50');

        appendChatBubble(`Đã chọn: <b>${escapeHtml(placeName)}</b>`, 'user');

        if (action === 'replace' && targetId) {
            $.ajax({
                url: `${API_BASE}/api/itinerary/replace`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    session_id: tripState.sessionId,
                    old_place_id: targetId,
                    new_place_id: placeId,
                    ai_advice: `Đã thay '${targetName || 'địa điểm cũ'}' bằng '${placeName}'.`
                }),
                success: function (res) {
                    if (res.status === 'success' && res.itinerary) {
                        setCurrentItinerary(res.itinerary);
                        refreshCurrentItinerary();
                        // RELOAD LỘ TRÌNH ĐANG ĐƯỢC CHỌN Ở SCREEN 2
                        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                            renderRouteCards(tripState.currentRoutes.routes);
                            renderComparisonTable(tripState.currentRoutes.routes);
                        }
                        $btn.html('<i class="fa-solid fa-circle-check me-1"></i> Đã chọn');
                        appendChatBubble(`Tuyệt vời! Đã thay <b>${escapeHtml(targetName || 'địa điểm cũ')}</b> bằng <b>${escapeHtml(placeName)}</b>. Bản đồ và timeline đã được cập nhật!`, 'assistant');
                    } else {
                        $btn.html('<i class="fa-solid fa-rotate me-1"></i> Thử lại');
                        $container.find('.btn-apply-ai-opt').prop('disabled', false).removeClass('opacity-50');
                        appendChatBubble('Không thể thay thế địa điểm: ' + firstDefined(res.message, 'Lỗi không xác định.'), 'assistant');
                    }
                },
                error: function (xhr) {
                    $btn.html('<i class="fa-solid fa-rotate me-1"></i> Thử lại');
                    $container.find('.btn-apply-ai-opt').prop('disabled', false).removeClass('opacity-50');
                    appendChatBubble('Lỗi kết nối khi thay thế: ' + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'vui lòng thử lại.'), 'assistant');
                }
            });
        } else {
            // Action is add
            $.ajax({
                url: `${API_BASE}/api/itinerary/add`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    session_id: tripState.sessionId,
                    place_id: placeId,
                    ai_advice: `Đã thêm '${placeName}' vào lịch trình.`
                }),
                success: function (res) {
                    if (res.status === 'success' && res.itinerary) {
                        setCurrentItinerary(res.itinerary);
                        refreshCurrentItinerary();
                        // RELOAD LỘ TRÌNH ĐANG ĐƯỢC CHỌN Ở SCREEN 2
                        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                            renderRouteCards(tripState.currentRoutes.routes);
                            renderComparisonTable(tripState.currentRoutes.routes);
                        }
                        $btn.html('<i class="fa-solid fa-circle-check me-1"></i> Đã chọn');
                        appendChatBubble(`Tuyệt vời! Đã thêm <b>${escapeHtml(placeName)}</b> vào lịch trình của bạn. Lộ trình trên màn hình đã được cập nhật!`, 'assistant');
                    } else {
                        $btn.html('<i class="fa-solid fa-rotate me-1"></i> Thử lại');
                        $container.find('.btn-apply-ai-opt').prop('disabled', false).removeClass('opacity-50');
                        appendChatBubble('Không thể thêm địa điểm: ' + firstDefined(res.message, 'Lỗi không xác định.'), 'assistant');
                    }
                },
                error: function (xhr) {
                    $btn.html('<i class="fa-solid fa-rotate me-1"></i> Thử lại');
                    $container.find('.btn-apply-ai-opt').prop('disabled', false).removeClass('opacity-50');
                    appendChatBubble('Lỗi kết nối khi thêm địa điểm: ' + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'vui lòng thử lại.'), 'assistant');
                }
            });
        }
    });

    function loadChatHistory(sessionId) {
        if (!sessionId) return;
        $.ajax({
            url: `${API_BASE}/api/chat-history?session_id=${encodeURIComponent(sessionId)}`,
            method: 'GET',
            success: function (res) {
                if (res.status === 'success' && Array.isArray(res.chat_history) && res.chat_history.length > 0) {
                    const $log = $('#ai-map-chat-log');
                    const existingCount = $log.children().length;
                    if (existingCount <= 1) {
                        $log.empty();
                        res.chat_history.forEach(msg => {
                            if (msg.role === 'user') {
                                appendChatBubble(escapeHtml(msg.content), 'user');
                            } else if (msg.role === 'assistant') {
                                if (msg.options && msg.options.length) {
                                    renderAiRefineOptions({
                                        advice_text: msg.content,
                                        options: msg.options,
                                        target_place_id: msg.target_place_id,
                                        target_place_name: msg.target_place_name,
                                    });
                                } else {
                                    appendChatBubble(escapeHtml(msg.content), 'assistant');
                                }
                            }
                        });
                    }
                }
            }
        });
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
        appendChatBubble(escapeHtml(instruction), 'user');
        $('#ai_map_instruction').val('').prop('disabled', true);
        $('#btn-send-ai-chat').prop('disabled', true);
        appendChatBubble('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang tìm kiếm phương án phù hợp...', 'assistant loading');

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
            url: `${API_BASE}/api/ai-refine`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify(refinePayload),
            success: function (refineRes) {
                $('#ai-map-chat-log .loading').remove();
                tripState.sessionId = firstDefined(refineRes.session_id, tripState.sessionId);

                // 1. Nếu AI trả về danh sách OPTIONS để user lựa chọn (thay vì tự quyết định một điểm duy nhất)
                if (refineRes.options && refineRes.options.length > 0 && refineRes.intent === 'options') {
                    renderAiRefineOptions(refineRes);
                    isAiUpdating = false;
                    $('#ai_map_instruction').prop('disabled', false);
                    $('#btn-send-ai-chat').prop('disabled', false);
                    return;
                }

                // 2. Nếu là lựa chọn từ turn trước (multi-turn) hoặc direct update
                $.ajax({
                    url: `${API_BASE}/api/itinerary/update`,
                    method: 'POST',
                    contentType: 'application/json',
                    data: JSON.stringify({ session_id: tripState.sessionId }),
                    success: function (res) {
                        if (res.status !== 'success' || !res.itinerary) {
                            appendChatBubble(
                                'Không thể cập nhật lộ trình: ' + firstDefined(res.message, 'Lỗi không xác định.'),
                                'assistant'
                            );
                            return;
                        }

                        setCurrentItinerary(res.itinerary);
                        refreshCurrentItinerary();
                        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
                            renderRouteCards(tripState.currentRoutes.routes);
                            renderComparisonTable(tripState.currentRoutes.routes);
                        }

                        appendChatBubble(
                            firstDefined(refineRes.advice_text, 'Mình đã cập nhật và sắp xếp lại hành trình cho bạn.'),
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
                    'Xin lỗi, mình chưa hiểu ý bạn lắm: ' + formatErrorDetail(xhr, 'vui lòng thử lại.'),
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

