    // ============================================================
    // TOP NAVBAR, SIDEBAR & HIỂN THỊ GLOBAL CHATBOT WIDGET
    // ============================================================
    function setActiveNavTab(screenKey) {
        $('.navbar-center .nav-item-link').removeClass('active').removeAttr('aria-current');
        $(`.navbar-center .nav-item-link[data-screen="${screenKey}"]`)
            .addClass('active')
            .attr('aria-current', 'page');

        // Hiển thị Chatbot ở cả Screen 2 (chat) và Screen 3 (map), ẩn ở Screen 1 (hero)
        if (screenKey === 'chat' || screenKey === 'map') {
            $('#global-ai-assistant').removeClass('d-none');
        } else {
            $('#global-ai-assistant').addClass('d-none');
            $('#ai-map-chat').removeClass('open');
        }
    }

    $('#btn-toggle-sidebar').on('click', function () {
        $('#chat-history-sidebar').toggleClass('collapsed');
        setTimeout(() => map.invalidateSize(), 250);
    });

    $('#btn-new-trip, #nav-logo').on('click', function (e) {
        e.preventDefault();
        $('#chat-section, #map-section').hide();
        $('#hero-section').fadeIn(180);
        setActiveNavTab('hero');
    });

    $('.navbar-center .nav-item-link[data-screen]').on('click', function (e) {
        e.preventDefault();
        const target = $(this).data('screen');
        if (target === 'hero') {
            $('#chat-section, #map-section').hide();
            $('#hero-section').fadeIn(180);
            setActiveNavTab('hero');
        } else if (target === 'chat') {
            if (!tripState.currentRoutes) {
                showCrudBanner('Vui lòng tạo lịch trình ở bước 1 trước nhé!', true);
                return;
            }
            $('#hero-section, #map-section').hide();
            $('#chat-section').fadeIn(180);
            setActiveNavTab('chat');
        } else if (target === 'map') {
            if (!tripState.currentRoutes) {
                showCrudBanner('Vui lòng tạo và chọn một lộ trình trước khi mở bản đồ nhé!', true);
                return;
            }
            enterMapScreen(tripState.selectedRouteId);
            setActiveNavTab('map');
        }
    });

    function getStoredHistory() {
        try {
            return JSON.parse(localStorage.getItem('teravue_trip_history')) || [];
        } catch (e) {
            return [];
        }
    }

    function saveTripToHistory(sessionId, prefText, startTime, endTime, vType) {
        if (!sessionId) return;
        try {
            let history = getStoredHistory();
            history = history.filter(h => h.sessionId !== sessionId);
            history.unshift({
                sessionId: sessionId,
                title: prefText || 'Hành trình khám phá Hà Nội',
                startTime: startTime || '08:00',
                endTime: endTime || '20:00',
                vehicleType: vType || 'xe_may',
                timestamp: Date.now()
            });
            if (history.length > 20) history = history.slice(0, 20);
            localStorage.setItem('teravue_trip_history', JSON.stringify(history));
            localStorage.setItem('teravue_active_session', sessionId);
            renderTripHistoryUI();
        } catch (e) {
            console.warn('Lỗi lưu lịch sử:', e);
        }
    }

    function renderTripHistoryUI() {
        const $list = $('#chat-history-list');
        if (!$list.length) return;
        $list.empty();
        const history = getStoredHistory();
        history.forEach(item => {
            const isActive = item.sessionId === tripState.sessionId;
            const shortTitle = item.title.length > 28 ? item.title.slice(0, 28) + '...' : item.title;
            const vName = VEHICLE_NAMES[item.vehicleType] ? VEHICLE_NAMES[item.vehicleType] : 'Xe máy';
            const $item = $(`
                <li class="history-item ${isActive ? 'active' : ''}" data-session-id="${escapeHtml(item.sessionId)}">
                    <i class="fa-solid fa-route mt-1"></i>
                    <div class="history-item-text">
                        <div class="history-title">${escapeHtml(shortTitle)}</div>
                        <div class="history-meta">${escapeHtml(item.startTime)} - ${escapeHtml(item.endTime)} · ${escapeHtml(vName)}</div>
                    </div>
                </li>
            `);
            $item.on('click', function () {
                const sId = item.sessionId;
                if (tripState.sessionId === sId && tripState.currentRoutes) {
                    $('#hero-section, #map-section').hide();
                    $('#chat-section').fadeIn(180);
                    setActiveNavTab('chat');
                    return;
                }
                $.ajax({
                    url: `${API_BASE}/api/routes?session_id=${encodeURIComponent(sId)}`,
                    method: 'GET',
                    success: function (res) {
                        if (res && res.status === 'success' && res.routes && res.routes.length) {
                            tripState.sessionId = sId;
                            tripState.currentRoutes = res;
                    enrichPlacesWithAccentedReviews(res.routes);
                            tripState.selectedRouteId = res.selected_route_id || res.routes[0].route_id;
                            localStorage.setItem('teravue_active_session', sId);
                            renderRouteCards(res.routes);
                            renderComparisonTable(res.routes);
                            selectRouteOnScreen2(tripState.selectedRouteId);
                            $('#chat-history-list .history-item').removeClass('active');
                            $item.addClass('active');
                            $('#hero-section, #map-section').hide();
                            $('#chat-section').fadeIn(180);
                            setActiveNavTab('chat');
                            showCrudBanner(`Đã tải lại lịch trình: ${shortTitle}`);
                        }
                    },
                    error: function () {
                        showCrudBanner('Phiên lịch trình này đã hết hạn trên máy chủ.', true);
                    }
                });
            });
            $list.append($item);
        });
    }

    function addHistoryEntry(prefText, startTime, endTime, vType) {
        if (tripState.sessionId) {
            saveTripToHistory(tripState.sessionId, prefText, startTime, endTime, vType);
        }
    }

    // ============================================================
    // ============================================================
    // LIVE "AI ĐÃ HIỂU" PREVIEW TRÊN SCREEN 1 (SEMANTIC PARSER INTEGRATION)
    // ============================================================
    var CLIENT_TAG_RULES = [
        { id: 'lively', label: '🔥 Nhộn nhịp, sôi động', test: /(nhộn nhịp|sôi động|náo nhiệt|tấp nập|sầm uất|vui nhộn|đông vui|náo nức|quẩy)/i },
        { id: 'quiet', label: '🍃 Yên tĩnh, thư giãn', test: /(yên tĩnh|vắng vẻ|ít người|thư giãn|nhẹ nhàng|chill|bình yên|tránh đông|không đông|thư thái|tĩnh lặng|an yên|chữa lành)/i },
        { id: 'cafe', label: '☕ Cà phê', test: /(cafe|cà phê|cafè|coffee|café|trà sữa|tiệm trà|quán nước|cf|trà chanh|cà phê trứng|uống trà)/i },
        { id: 'photo', label: '📸 View đẹp / Sống ảo', test: /(chụp ảnh|chụp hình|check in|check-in|checkin|sống ảo|view đẹp|view|instagram|góc đẹp|selfie|photo|hoàng hôn|bình minh|rooftop|ngắm cảnh)/i },
        { id: 'food', label: '🍜 Ẩm thực', test: /(ăn uống|ẩm thực|đồ ăn|quán ăn|ăn trưa|ăn sáng|ăn tối|ăn vặt|phở|bún chả|bún|bánh|đặc sản|nhà hàng|món ngon|ăn ngon|bún đậu|chả cá|bánh mì|ăn đêm)/i },
        { id: 'culture', label: '🏛️ Văn hóa - Lịch sử', test: /(văn hóa|văn hoá|lịch sử|di tích|bảo tàng|đền|chùa|phố cổ|kiến trúc|truyền thống|cổ kính|văn miếu|lăng bác|hỏa lò|tâm linh)/i },
        { id: 'nature', label: '🌳 Thiên nhiên ven hồ', test: /(thiên nhiên|công viên|hồ tây|hồ gươm|ven hồ|cây xanh|vườn|ngoài trời|dạo bộ|hóng gió|tản bộ|đi dạo)/i },
        { id: 'entertainment', label: '🎬 Xem phim / Rạp phim', test: /(xem phim|chiếu phim|rạp phim|rạp chiếu phim|rap phim|chieu phim|coi phim|xem film|cinema|cgv|lotte cinema|bhd|giải trí|vui chơi|trượt băng|bowling|khu vui chơi)/i },
        { id: 'shopping', label: '🛍️ Mua sắm', test: /(mua sắm|shopping|trung tâm thương mại|tttm|mall|siêu thị|quần áo|mua đồ|chợ|chợ đêm)/i },
        { id: 'nightlife', label: '🌙 Về đêm', test: /(về đêm|buổi tối|nightlife|bar|pub|phố đi bộ|chợ đêm|đi đêm|tạ hiện|uống bia|bia hơi)/i },
        { id: 'romantic', label: '❤️ Lãng mạn', test: /(hẹn hò|lãng mạn|người yêu|bạn gái|bạn trai|cặp đôi)/i },
        { id: 'family', label: '👨‍👩‍👧 Gia đình', test: /(gia đình|trẻ em|trẻ con|con nhỏ|bố mẹ|ông bà)/i },
        { id: 'pace_slow', label: '⏳ Thong thả', test: /(thong thả|chậm rãi|từ từ|không vội|thư thái)/i },
        { id: 'pace_fast', label: '⚡ Đi nhiều điểm', test: /(nhiều điểm|nhanh|tranh thủ|càng nhiều càng tốt)/i },
        { id: 'budget_low', label: '💰 Tiết kiệm', test: /(tiết kiệm|bình dân|giá rẻ|hạt dẻ|sinh viên|ít tiền)/i },
        { id: 'budget_high', label: '✨ Sang trọng', test: /(sang trọng|sang chảnh|cao cấp|luxury|đắt tiền)/i },
        { id: 'indoor', label: '🏠 Trong nhà', test: /(trong nhà|điều hòa|điều hoà|máy lạnh|tránh nắng|trời mưa)/i },
        { id: 'outdoor', label: '⛅ Ngoài trời', test: /(ngoài trời|thoáng mát|dã ngoại|hóng gió)/i },
    ];

    const CLIENT_KNOWN_PLACES = [
        { name: 'Cafe Giảng', test: /(cafe gi[aả]ng|c[aà] ph[eê] gi[aả]ng|gi[aả]ng cafe|c[aà] ph[eê] tr[uứ]ng|cafe tr[uứ]ng)/i },
        { name: 'Phở Thìn Bờ Hồ', test: /(ph[oở] th[iì]n)/i },
        { name: 'Bún chả Hương Liên', test: /(b[uú]n ch[aả] h[uư][oơ]ng li[eê]n|b[uú]n ch[aả] obama|huong lien)/i },
        { name: 'Hồ Gươm', test: /(h[oồ] g[uươ]m|h[oồ] ho[aà]n ki[eế]m|b[oờ] h[oồ])/i },
        { name: 'Hồ Tây', test: /(h[oồ] t[aâ]y|ven h[oồ] t[aâ]y|quanh h[oồ] t[aâ]y)/i },
        { name: 'Lăng Bác', test: /(l[aă]ng b[aá]c|l[aă]ng ch[uủ] t[iị]ch)/i },
        { name: 'Văn Miếu', test: /(v[aă]n mi[eế]u|qu[oố]c t[uử] gi[aá]m)/i },
        { name: 'Chùa Trấn Quốc', test: /(ch[uù]a tr[aấ]n qu[oố]c|tr[aấ]n qu[oố]c)/i },
        { name: 'Nhà thờ Lớn', test: /(nh[aà] th[oờ] l[oớ]n)/i },
        { name: 'Hỏa Lò', test: /(ho[aả] l[oò]|h[oỏ]a l[oò]|nh[aà] t[uù] ho[aả] l[oò])/i },
        { name: 'Phố Tạ Hiện', test: /(t[aạ] hi[eệ]n|ph[oố] t[aạ] hi[eệ]n|bia t[aạ] hi[eệ]n)/i },
        { name: 'Tràng Tiền', test: /(tr[aà]ng ti[eề]n|kem tr[aà]ng ti[eề]n)/i },
        { name: 'Chợ Đồng Xuân', test: /(ch[oợ] [dđ][oồ]ng xu[aâ]n|[dđ][oồ]ng xu[aâ]n)/i },
        { name: 'Ban Công Cafe', test: /(ban c[oô]ng cafe|cafe ban c[oô]ng)/i },
        { name: 'Loading T Cafe', test: /(loading t)/i },
        { name: 'Vincom Bà Triệu', test: /(vincom b[aà] tri[eệ]u|vincom)/i },
        { name: 'Lotte Center', test: /(lotte)/i },
        { name: 'Royal City', test: /(royal city)/i },
    ];

    let parsePromptTimer = null;
    function fetchSemanticParse(pref) {
        clearTimeout(parsePromptTimer);
        if (!pref) {
            $('#ai-clarification-box').addClass('d-none');
            $('#ai-understood-detail').addClass('d-none');
            return;
        }
        parsePromptTimer = setTimeout(() => {
            $.ajax({
                url: `${API_BASE}/api/parse-prompt`,
                method: 'POST',
                contentType: 'application/json',
                data: JSON.stringify({
                    prompt: pref,
                    context: {
                        start_time: $('#start_time').val(),
                        end_time: $('#end_time').val(),
                        vehicle_type: $('#vehicle_type').val(),
                        trip_date: $('#trip_date').val(),
                        start_point: $('#start_point').val().trim()
                    },
                    use_llm: false
                }),
                success: function (res) {
                    if (res && res.badge_text) {
                        $('#ai-understood-text').text(res.badge_text);
                    }
                    if (res && res.understood_summary) {
                        $('#ai-understood-detail-text').text(res.understood_summary);
                        $('#ai-understood-detail').removeClass('d-none');
                        $('#ai-understood-badge').attr('title', res.understood_summary);
                    }
                    if (res && res.status === 'need_clarification' && res.clarification_questions && res.clarification_questions.length > 0) {
                        const q = res.clarification_questions[0];
                        $('#ai-clarification-question').text(q.question);
                        const $opts = $('#ai-clarification-options').empty();
                        (q.options || []).forEach(opt => {
                            const $chip = $('<button type="button" class="clarification-chip"></button>').text(opt.label);
                            $chip.click(function () {
                                const curVal = $('#user_preference').val().trim();
                                $('#user_preference').val(curVal + ', ' + opt.value).trigger('input');
                            });
                            $opts.append($chip);
                        });
                        if (q.options && q.options.length > 0) {
                            $('#ai-clarification-box').removeClass('d-none');
                        }
                    } else {
                        $('#ai-clarification-box').addClass('d-none');
                    }
                },
                error: function () {
                    // Giữ kết quả client-side mượt mà nếu offline
                }
            });
        }, 300);
    }

    function updateLiveUnderstoodBar(customSummary) {
        const pref = $('#user_preference').val().trim();
        const sTime = firstDefined($('#start_time').val(), '08:00');
        const eTime = firstDefined($('#end_time').val(), '20:00');
        const vType = firstDefined($('#vehicle_type').val(), 'xe_may');
        const sPoint = $('#start_point').val().trim();
        const ePoint = $('#end_point').val().trim();

        if (!pref && !customSummary) {
            $('#ai-understood-badge').addClass('d-none');
            $('#ai-clarification-box').addClass('d-none');
            $('#ai-understood-detail').addClass('d-none');
            return;
        }

        if (customSummary) {
            $('#ai-understood-text').text(customSummary);
            $('#ai-understood-badge').removeClass('d-none');
            return;
        }

        // 1. Nhận diện sở thích, tag & địa điểm trực tiếp trên client (phản hồi tức thì)
        const matchedItems = [];
        CLIENT_TAG_RULES.forEach(r => {
            if (r.test.test(pref)) {
                matchedItems.push(r.label);
            }
        });
        CLIENT_KNOWN_PLACES.forEach(p => {
            if (p.test.test(pref)) {
                matchedItems.push('📍 ' + p.name);
            }
        });

        // 2. Trích xuất yêu cầu / mong muốn tự do của con người nếu có câu/mệnh đề riêng biệt
        const clauses = pref.split(/[,;.!?\n]|\b(?:và|với|hoặc|cùng|nhưng|mà|để|kèm|thêm|rồi)\b/i);
        clauses.forEach(c => {
            let clean = c.trim().replace(/^["'“”()\[\]]+|["'“”()\[\]]+$/g, '');
            clean = clean.replace(/^(?:thích|muốn|yêu cầu|cần|có|đi|trải nghiệm|khám phá|ghé|xem|uống|ăn|tìm|ngắm|thử|thong thả|nhẹ nhàng|chậm rãi|nhanh)\s+/i, '').trim();
            if (clean.length >= 3) {
                const isCovered = matchedItems.some(item => item.toLowerCase().includes(clean.toLowerCase()));
                if (!isCovered && !matchedItems.includes('✨ ' + clean)) {
                    if (!/^(hà nội|xe máy|ô tô|đi bộ|xe đạp|ngày|giờ|tiếng|hôm nay|ngày mai)$/i.test(clean)) {
                        matchedItems.push('✨ ' + (clean.length > 35 ? clean.slice(0, 35) + '...' : clean));
                    }
                }
            }
        });

        const parts = [`${sTime}–${eTime}`, firstDefined(VEHICLE_NAMES[vType], vType)];
        if (sPoint) parts.push(`Từ: ${sPoint}`);
        if (ePoint) parts.push(`Đến: ${ePoint}`);

        if (matchedItems.length > 0) {
            parts.push(matchedItems.join(' · '));
        } else if (pref) {
            const cleanPref = pref.length > 60 ? pref.slice(0, 60) + '...' : pref;
            parts.push('✨ ' + cleanPref);
        }

        $('#ai-understood-text').text(parts.join(' | '));
        $('#ai-understood-badge').removeClass('d-none');

        // 3. Gọi Semantic Parser Backend đồng bộ ngữ nghĩa & gợi ý câu hỏi làm rõ
        fetchSemanticParse(pref);
    }

    $('#btn-edit-understood').click(function () {
        $('#ai-understood-detail').toggleClass('d-none');
        $('#user_preference').focus();
    });

    $('#user_preference, #start_time, #end_time, #vehicle_type, #start_point, #end_point').on('input change', function () {
        updateLiveUnderstoodBar();
    });

    // ============================================================
    // SCREEN 1 → INPUT & GPS
    // ============================================================
    $('#btn-use-location').click(function (e) {
        if (e && e.preventDefault) e.preventDefault();
        const $status = $('#location-status');
        if (!navigator.geolocation) return $status.html('<span class="text-danger">Trình duyệt không hỗ trợ GPS</span>');
        $status.html('<span class="text-secondary"><i class="fa-solid fa-spinner fa-spin"></i> Đang lấy tọa độ GPS...</span>');
        navigator.geolocation.getCurrentPosition(
            pos => {
                tripState.currentLocationData = { lat: pos.coords.latitude, lon: pos.coords.longitude };
                $('#start_point').val(`Vị trí GPS hiện tại`);
                $status.html('<span class="text-success fw-semibold"><i class="fa-solid fa-check"></i> Đã định vị GPS</span>');
                updateLiveUnderstoodBar();
            },
            () => $status.html('<span class="text-danger">Không lấy được GPS</span>'),
            { timeout: 8000, enableHighAccuracy: true }
        );
    });

    $('#start_point').on('input', function () {
        const val = $(this).val().trim();
        if (!val.includes('GPS')) {
            tripState.currentLocationData = null;
            $('#location-status').empty();
        }
    });

    function parseCoordinates(text) {
        if (!text) return null;
        const m = text.trim().match(/^(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)$/);
        if (!m) return null;
        const lat = parseFloat(m[1]);
        const lon = parseFloat(m[2]);
        if (lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180) {
            return { lat, lon };
        }
        return null;
    }

    function resolveStartAndRun(onReady) {
        const startPointText = $('#start_point').val().trim();
        const isGpsText = startPointText.includes('GPS');
        const usingGPS = !!tripState.currentLocationData;
        const fingerprint = usingGPS
            ? `gps:${tripState.currentLocationData.lat},${tripState.currentLocationData.lon}`
            : `text:${startPointText}`;

        if (tripState.baseParams && tripState.startFingerprint === fingerprint && tripState.baseParams.start_lat != null) {
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

        if (isGpsText && navigator.geolocation) {
            navigator.geolocation.getCurrentPosition(
                pos => {
                    tripState.currentLocationData = { lat: pos.coords.latitude, lon: pos.coords.longitude };
                    tripState.startFingerprint = `gps:${pos.coords.latitude},${pos.coords.longitude}`;
                    onReady({ start_lat: pos.coords.latitude, start_lon: pos.coords.longitude, start_point: '' });
                },
                () => {
                    tripState.startFingerprint = fingerprint;
                    onReady({ start_lat: 21.0285, start_lon: 105.8542, start_point: 'Hồ Hoàn Kiếm, Hà Nội' });
                },
                { timeout: 4000 }
            );
            return;
        }

        if (!startPointText) {
            tripState.startFingerprint = fingerprint;
            onReady({ start_lat: 21.0285, start_lon: 105.8542, start_point: 'Hồ Hoàn Kiếm, Hà Nội' });
            return;
        }

        const coords = parseCoordinates(startPointText);
        if (coords) {
            tripState.startFingerprint = `coords:${coords.lat},${coords.lon}`;
            onReady({ start_lat: coords.lat, start_lon: coords.lon, start_point: '' });
            return;
        }

        const query = (startPointText.includes('Việt Nam') || startPointText.includes('Vietnam'))
            ? startPointText
            : startPointText + ', Hà Nội, Việt Nam';

        $.get('https://nominatim.openstreetmap.org/search', { q: query, format: 'json', limit: 1 })
            .done(res => {
                tripState.startFingerprint = fingerprint;
                if (res && res.length > 0) {
                    onReady({
                        start_lat: parseFloat(res[0].lat),
                        start_lon: parseFloat(res[0].lon),
                        start_point: startPointText
                    });
                } else {
                    onReady({ start_lat: 21.0285, start_lon: 105.8542, start_point: startPointText });
                }
            })
            .fail(() => {
                tripState.startFingerprint = fingerprint;
                onReady({ start_lat: 21.0285, start_lon: 105.8542, start_point: startPointText });
            });
    }

    function applyAdvice(res) {
        tripState.sessionId = firstDefined(res.session_id, tripState.sessionId);
        tripState.aiSuggestedIds = Array.isArray(res.suggested_ids) ? res.suggested_ids : [];
        $('#ai-advice-content').html(String(firstDefined(res.advice_text)).replace(/\n/g, '<br>'));
        renderQuickActions();
    }

