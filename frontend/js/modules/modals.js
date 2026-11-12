    // ============================================================
    // PLACE DETAIL MODAL (Ảnh, Rating, Review, Giờ mở cửa, Giá, Hoạt động, Favorite)
    // ============================================================
    function openPlaceDetailModal(place) {
        if (!place) return;
        const pid = String(firstDefined(place.id));
        const imgUrl = firstDefined(place.url_hinh_anh, DEFAULT_PLACE_IMG);
        const catLabel = formatCategoryLabel(place.loai_hinh);
        const ratingStr = getPlaceRating(place);
        const openHoursStr = formatOpeningHours(place);
        const priceStr = formatPlacePrice(place);
        const slotStr = place.arrive_time
            ? `${place.arrive_time} – ${firstDefined(place.depart_time, '--:--')} (${firstDefined(place.visit_time, 60)}p)`
            : `Khoảng ${firstDefined(place.visit_time, place.time, 60)} phút`;
        const activityStr = firstDefined(place.phu_hop, CATEGORY_ACTIVITY_LABEL[place.loai_hinh], 'Tham quan, khám phá & trải nghiệm không gian');
        const addressStr = firstDefined(place.dia_chi, place.mo_ta, 'Hà Nội, Việt Nam');
        const reviewStr = reviewSnippet(place) || 'Địa điểm được nhiều du khách yêu thích tại Hà Nội.';
        const tg = placeTagGroups(place);
        const ins = place.review_insight || {};

        $('#pd-image').attr('src', imgUrl).attr('alt', place.ten).off('error').on('error', function () {
            $(this).attr('src', DEFAULT_PLACE_IMG);
        });
        $('#pd-category').text(catLabel);
        const cntStr = getPlaceRatingCount(place);
        $('#pd-rating').text(`★ ${ratingStr}${cntStr ? ' · ' + cntStr : ''}`);
        $('#pd-title').text(place.ten);
        $('#pd-address span').text(addressStr);
        $('#pd-hours').text(openHoursStr);
        $('#pd-hours-note').text(weekHoursNote(place));
        $('#pd-price').text(priceStr);
        $('#pd-price-note').text(priceSourceNote(place));
        $('#pd-slot').text(slotStr);
        $('#pd-activity').text(tg.hoatDong.length ? tg.hoatDong.join(' · ') : activityStr);
        $('#pd-tags').html(buildInsightChipsHtml(place));
        // Review: chip khách hay khen / lưu ý + độ tích cực + review tiêu biểu
        const sentiment = (ins.so_review && ins.diem_cam_xuc !== null && ins.diem_cam_xuc !== undefined)
            ? `Phân tích từ ${ins.so_review} review thật · cảm xúc tích cực ${Math.round(ins.diem_cam_xuc * 100)}%` : '';
        $('#pd-insight').text(sentiment);
        // Thư viện ảnh
        const gallery = Array.from(new Set([imgUrl].concat(Array.isArray(place.anh) ? place.anh : []).filter(Boolean)));
        const $gal = $('#pd-gallery').empty();
        if (gallery.length > 1) {
            gallery.slice(0, 6).forEach((u, i) => {
                $('<img class="pd-thumb' + (i === 0 ? ' active' : '') + '" loading="lazy" referrerpolicy="no-referrer">').attr('src', u).attr('alt', place.ten)
                    .on('error', function () { $(this).remove(); })
                    .on('click', function () {
                        $('#pd-image').attr('src', u);
                        $gal.find('.pd-thumb').removeClass('active'); $(this).addClass('active');
                    }).appendTo($gal);
            });
        }
        $('#pd-context-note').html(buildContextualExperience(place));
        $('#pd-review').text(`“${reviewStr}”`);

        const updateModalFavBtn = () => {
            const isFav = favoritePlaceIds.has(pid);
            $('#pd-fav-btn')
                .toggleClass('active', isFav)
                .html(isFav ? '❤️ Đã yêu thích' : '❤️ Yêu thích');
        };
        updateModalFavBtn();

        $('#pd-fav-btn').off('click').on('click', function () {
            const $btn = $(this);
            $btn.prop('disabled', true);
            toggleUserFavorite(pid, function (isFav) {
                $btn.prop('disabled', false);
                updateModalFavBtn();
                refreshCurrentItinerary();
            });
        });

        const modalEl = document.getElementById('placeDetailModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }
    }

    function getActiveItinerary() {
        if (tripState.currentItinerary) return tripState.currentItinerary;
        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
            const found = tripState.currentRoutes.routes.find(r => r.route_id === tripState.selectedRouteId);
            return found ? found : tripState.currentRoutes.routes[0];
        }
        return null;
    }

    function setCurrentItinerary(itinerary) {
        if (!itinerary) return;
        tripState.currentItinerary = itinerary;

        if (tripState.currentRoutes && Array.isArray(tripState.currentRoutes.routes)) {
            const index = tripState.currentRoutes.routes.findIndex(
                r => r.route_id === itinerary.route_id
            );
            if (index >= 0) {
                tripState.activeRouteIndex = index;
                tripState.currentRoutes.routes[index] = itinerary;
            } else if (tripState.currentRoutes.routes[tripState.activeRouteIndex]) {
                tripState.currentRoutes.routes[tripState.activeRouteIndex] = itinerary;
            }
        }
        tripState.selectedRouteId = firstDefined(itinerary.route_id, tripState.selectedRouteId);
    }

    function refreshCurrentItinerary() {
        const itinerary = getActiveItinerary();
        if (!itinerary) return;

        const vehicleType = firstDefined(tripState.baseParams && tripState.baseParams.vehicle_type, $('#vehicle_type').val());
        const availableMinutes = firstDefined(tripState.currentRoutes && tripState.currentRoutes.available_minutes, itinerary.available_minutes, 0);

        renderItinerarySummary(itinerary);
        renderTimelineAndMap(itinerary, vehicleType, availableMinutes);
    }

    function showCrudBanner(msg, isWarning = false) {
        const $banner = $('#crud-feedback-banner');
        if (!$banner.length) return;
        $banner
            .removeClass('d-none')
            .css({
                backgroundColor: isWarning ? 'rgba(254, 245, 231, 0.98)' : 'rgba(230, 244, 238, 0.98)',
                color: isWarning ? 'var(--status-warning)' : 'var(--status-success)',
                borderColor: isWarning ? '#F6E0B5' : '#B7E4CE'
            })
            .html(`
                <i class="fa-solid ${isWarning ? 'fa-triangle-exclamation text-warning' : 'fa-circle-check text-success'} fs-5 flex-shrink-0"></i>
                <div class="flex-grow-1" style="line-height: 1.45;">${msg}</div>
                <button type="button" class="btn-close btn-close-sm ms-2 flex-shrink-0" style="font-size: 0.7rem;" aria-label="Đóng" onclick="$(this).closest('.crud-feedback-banner').addClass('d-none');"></button>
            `);
        clearTimeout($banner.data('timer'));
        const timer = setTimeout(() => {
            $banner.fadeOut(250, function () {
                $(this).addClass('d-none').show();
            });
        }, 4500);
        $banner.data('timer', timer);
    }

    // Khởi tạo ngày mặc định là hôm nay
    const today = new Date(new Date().getTime() - (new Date().getTimezoneOffset() * 60000))
        .toISOString().split('T')[0];
    $('#trip_date').val(today).attr('min', today);

    if (new URLSearchParams(window.location.search).get('admin') === '1') {
        $('.admin-trigger').removeClass('d-none');
    }

    const CATEGORY_ICON = {
        "Cafe":           { icon: "fa-mug-saucer",   color: "#3978D4" },
        "Ăn uống":        { icon: "fa-utensils",     color: "#23845B" },
        "Tham quan":      { icon: "fa-landmark",     color: "#3978D4" },
        "TTTM":           { icon: "fa-film",         color: "#2F68BD" },
        "Checkin":        { icon: "fa-camera",       color: "#3978D4" },
        "diem_xuat_phat": { icon: "fa-flag",         color: "#26364B" },
    };

    function categoryStyle(loaiHinh) {
        return CATEGORY_ICON[loaiHinh] ? CATEGORY_ICON[loaiHinh] : { icon: "fa-location-dot", color: "#3978D4" };
    }

    function formatCategoryLabel(loaiHinh) {
        if (!loaiHinh) return 'Điểm đến';
        if (loaiHinh === 'TTTM') return 'Rạp phim & TTTM';
        return loaiHinh;
    }

    function vehicleSentence(vType, distKm, mins) {
        const distStr = (Math.round((distKm ? distKm : 0) * 10) / 10).toString().replace('.', ',');
        const minsStr = Math.round(mins ? mins : 0);
        const vLabel = (VEHICLE_NAMES[vType] ? VEHICLE_NAMES[vType] : 'Xe máy').toLowerCase();
        return `Di chuyển ${distStr} km bằng ${vLabel} · khoảng ${minsStr} phút`;
    }

    function durationLabel(mins) {
        mins = Math.round(mins ? mins : 0);
        const h = Math.floor(mins / 60), m = mins % 60;
        if (h <= 0) return `${m} phút`;
        return m > 0 ? `${h}h${m < 10 ? '0' + m : m}` : `${h}h`;
    }

    // ============================================================
    // 1. XÓA LỊCH SỬ CHAT TRONG PHIÊN (#btn-clear-ai-chat)
    // ============================================================
    $('#btn-clear-ai-chat').on('click', function () {
        if (!tripState.sessionId) {
            $('#ai-map-chat-log').html(
                '<div class="chat-bubble assistant">Chào bạn! Bạn có thể nhắn mình thêm quán ăn, đổi điểm tham quan hoặc điều chỉnh nhịp độ bất cứ lúc nào nhé.</div>'
            );
            return;
        }

        if (!confirm('Bạn có chắc muốn làm mới lịch sử hội thoại AI trong phiên này?')) return;

        $.ajax({
            url: `${API_BASE}/api/chat-history?session_id=${encodeURIComponent(tripState.sessionId)}`,
            method: 'DELETE',
            success: function () {
                $('#ai-map-chat-log').html(
                    '<div class="chat-bubble assistant">Đã làm mới lịch sử trò chuyện. Mình sẵn sàng hỗ trợ bạn tinh chỉnh lộ trình tiếp theo!</div>'
                );
            },
            error: function () {
                $('#ai-map-chat-log').html(
                    '<div class="chat-bubble assistant">Đã làm mới lịch sử trò chuyện.</div>'
                );
            }
        });
    });

    // ============================================================
    // 2. MODAL YÊU THÍCH (#favoritesModal)
    // ============================================================
    function renderFavoritesModal() {
        const $container = $('#favorites-list-container');
        if (!currentUserId) {
            $container.html(`
                <div class="text-center py-5">
                    <div class="mb-3 text-secondary" style="font-size: 3rem;">
                        <i class="fa-regular fa-heart text-danger opacity-75"></i>
                    </div>
                    <h6 class="fw-bold mb-2">Đăng nhập để xem địa điểm yêu thích</h6>
                    <p class="text-secondary small mb-3">Lưu lại những quán cà phê, góc check-in và quán ăn bạn yêu thích để dễ dàng lên lịch trình bất cứ lúc nào.</p>
                    <button type="button" class="btn btn-primary-cta px-4 py-2" data-bs-toggle="modal" data-bs-target="#loginModal">
                        <i class="fa-solid fa-right-to-bracket me-2"></i>Đăng nhập ngay
                    </button>
                </div>
            `);
            return;
        }
        $container.html(`
            <div class="text-center py-5 text-secondary">
                <i class="fa-solid fa-spinner fa-spin fa-2x mb-2 text-primary"></i>
                <div>Đang tải danh sách yêu thích...</div>
            </div>
        `);

        $.ajax({
            url: `${API_BASE}/api/user/favorites?user_id=${encodeURIComponent(currentUserId)}`,
            method: 'GET',
            success: function (res) {
                if (!res || !Array.isArray(res.data) || res.data.length === 0) {
                    $container.html(`
                        <div class="text-center py-5 text-secondary">
                            <i class="fa-regular fa-heart fa-3x mb-3 text-muted"></i>
                            <h6 class="fw-bold text-main">Chưa có địa điểm yêu thích nào</h6>
                            <p class="small text-secondary mb-0">
                                Hãy bấm vào biểu tượng trái tim <span class="text-danger">❤️</span> trên chi tiết địa điểm hoặc thanh Timeline để lưu lại vào đây nhé!
                            </p>
                        </div>
                    `);
                    return;
                }

                $container.empty();
                res.data.forEach(p => {
                    const pid = String(p.id);
                    const img = p.url_hinh_anh || DEFAULT_PLACE_IMG;
                    const cat = p.loai_hinh || 'Điểm đến';
                    const rating = (p.rating !== null && p.rating !== undefined) ? Number(p.rating).toFixed(1) : '4.5';
                    const addr = p.dia_chi || 'Hà Nội';
                    const openH = p.gio_mo_cua && p.gio_dong_cua ? `${p.gio_mo_cua} – ${p.gio_dong_cua}` : '08:00 – 21:00';

                    const $card = $(`
                        <div class="fav-item-card" data-id="${escapeHtml(pid)}">
                            <img src="${escapeHtml(img)}" alt="${escapeHtml(p.ten)}" class="fav-item-img" onerror="this.src='${DEFAULT_PLACE_IMG}'">
                            <div class="fav-item-info">
                                <div class="fav-item-title">${escapeHtml(p.ten)}</div>
                                <div class="fav-item-meta">
                                    <span class="badge bg-light text-dark border">${escapeHtml(cat)}</span>
                                    <span><i class="fa-solid fa-star text-warning me-1"></i>${rating}</span>
                                    <span><i class="fa-regular fa-clock text-secondary me-1"></i>${openH}</span>
                                </div>
                                <div class="small text-secondary text-truncate mt-1" style="max-width: 380px;">
                                    <i class="fa-solid fa-location-dot text-danger me-1"></i>${escapeHtml(addr)}
                                </div>
                            </div>
                            <div class="fav-item-actions">
                                <button type="button" class="btn btn-sm btn-outline-primary btn-add-fav-to-tour" data-id="${escapeHtml(pid)}" data-name="${escapeHtml(p.ten)}" title="Thêm điểm này vào lịch trình đang xem">
                                    <i class="fa-solid fa-plus me-1"></i> Thêm vào tour
                                </button>
                                <button type="button" class="btn btn-sm btn-outline-danger btn-remove-fav-item" data-id="${escapeHtml(pid)}" title="Bỏ khỏi yêu thích">
                                    <i class="fa-regular fa-trash-can me-1"></i> Bỏ thích
                                </button>
                            </div>
                        </div>
                    `);

                    // Thêm vào tour
                    $card.find('.btn-add-fav-to-tour').on('click', function () {
                        const $btn = $(this);
                        if (!tripState.sessionId) {
                            alert('Vui lòng tạo hoặc chọn một lịch trình ở màn hình chính trước khi thêm điểm.');
                            return;
                        }
                        $btn.prop('disabled', true).html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang thêm...');
                        $.ajax({
                            url: `${API_BASE}/api/itinerary/add`,
                            method: 'POST',
                            contentType: 'application/json',
                            data: JSON.stringify({
                                session_id: tripState.sessionId,
                                place_id: pid,
                                ai_advice: `Đã thêm điểm yêu thích '${p.ten}' vào lịch trình.`
                            }),
                            success: function (addRes) {
                                if (addRes.status === 'success' && addRes.itinerary) {
                                    setCurrentItinerary(addRes.itinerary);
                                    refreshCurrentItinerary();
                                    $btn.removeClass('btn-outline-primary').addClass('btn-success').html('<i class="fa-solid fa-check me-1"></i> Đã thêm');
                                    showCrudBanner(`Đã thêm "${p.ten}" từ mục Yêu thích vào lịch trình!`);
                                } else {
                                    $btn.prop('disabled', false).html('<i class="fa-solid fa-plus me-1"></i> Thêm vào tour');
                                    alert('Không thể thêm địa điểm: ' + firstDefined(addRes.message, 'Lỗi không xác định.'));
                                }
                            },
                            error: function () {
                                $btn.prop('disabled', false).html('<i class="fa-solid fa-plus me-1"></i> Thêm vào tour');
                                alert('Lỗi kết nối khi thêm địa điểm vào lịch trình.');
                            }
                        });
                    });

                    // Xóa khỏi favorites
                    $card.find('.btn-remove-fav-item').on('click', function () {
                        toggleUserFavorite(pid, function () {
                            $card.fadeOut(250, function () {
                                $(this).remove();
                                if ($container.children().length === 0) {
                                    renderFavoritesModal();
                                }
                            });
                            refreshCurrentItinerary();
                        });
                    });

                    $container.append($card);
                });
            },
            error: function () {
                $container.html(`
                    <div class="text-center py-5 text-danger">
                        <i class="fa-solid fa-triangle-exclamation fa-2x mb-2"></i>
                        <div>Không thể tải danh sách yêu thích lúc này. Vui lòng thử lại.</div>
                    </div>
                `);
            }
        });
    }

    $('#favoritesModal').on('show.bs.modal', function () {
        renderFavoritesModal();
    });

    // ============================================================
    // 3. PROFILE & LOGIN (ACCOUNT MANAGEMENT)
    // ============================================================
    function loadUserProfile() {
        if (!currentUserId) {
            $('#nav-user-name').text('Đăng nhập');
            $('#nav-user-handle').text('Tài khoản Teravue');
            $('#nav-user-avatar').attr('src', 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=120&q=80');
            $('#nav-item-profile').hide();
            $('#nav-item-logout').hide();
            $('#nav-login-label').text('Đăng nhập');
            updateFavoriteBadges(0);
            return;
        }

        $('#nav-item-profile').show();
        $('#nav-item-logout').show();
        $('#nav-login-label').text('Đổi tài khoản');

        $.ajax({
            url: `${API_BASE}/api/user/profile?user_id=${encodeURIComponent(currentUserId)}`,
            method: 'GET',
            success: function (res) {
                if (res.status === 'success' && res.data) {
                    const u = res.data;
                    $('#nav-user-name').text(u.name || 'Du khách Hà Nội');
                    $('#nav-user-handle').text('@' + (u.id || 'traveler_hn'));
                    if (u.avatar_url) {
                        $('#nav-user-avatar').attr('src', u.avatar_url);
                        $('#profile-avatar-img').attr('src', u.avatar_url);
                    }
                    $('#profile-display-name').text(u.name || 'Du khách Hà Nội');
                    $('#profile-display-email').text(u.email || 'traveler@teravue.vn');
                    $('#profile_name_input').val(u.name || 'Du khách Hà Nội');
                    $('#profile_pref').val(u.default_preference || '');
                    updateFavoriteBadges(u.favorites_count || favoritePlaceIds.size);
                }
            }
        });
    }

    // Tự động cập nhật UI auth ngay khi khởi tạo
    loadUserProfile();

    $('#accountModal').on('show.bs.modal', function (e) {
        if (!currentUserId) {
            e.preventDefault();
            const loginModalEl = document.getElementById('loginModal');
            if (loginModalEl && window.bootstrap) {
                window.bootstrap.Modal.getOrCreateInstance(loginModalEl).show();
            }
            showCrudBanner('Vui lòng đăng nhập để xem hồ sơ cá nhân.', 'warning');
            return;
        }
        loadUserProfile();
        $('#profile-save-feedback').empty();
    });

    $('#btn-save-profile').on('click', function () {
        if (!currentUserId) {
            showCrudBanner('Vui lòng đăng nhập để lưu thông tin hồ sơ.', 'warning');
            return;
        }
        const $btn = $(this);
        const newName = $('#profile_name_input').val().trim();
        const newPref = $('#profile_pref').val().trim();
        $btn.prop('disabled', true).html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang lưu...');

        $.ajax({
            url: `${API_BASE}/api/user/profile`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                user_id: currentUserId,
                name: newName,
                default_preference: newPref
            }),
            success: function (res) {
                $btn.prop('disabled', false).html('<i class="fa-solid fa-floppy-disk me-1"></i> Lưu thông tin');
                $('#profile-save-feedback').html('<span class="text-success"><i class="fa-solid fa-check me-1"></i>Đã cập nhật hồ sơ thành công!</span>');
                loadUserProfile();
                setTimeout(() => {
                    const m = bootstrap.Modal.getInstance(document.getElementById('accountModal'));
                    if (m) m.hide();
                }, 900);
            },
            error: function () {
                $btn.prop('disabled', false).html('<i class="fa-solid fa-floppy-disk me-1"></i> Lưu thông tin');
                $('#profile-save-feedback').html('<span class="text-danger">Không thể lưu thông tin lúc này.</span>');
            }
        });
    });

    $('#login-form').on('submit', function (e) {
        e.preventDefault();
        const email = $('#login_email').val().trim();
        const $btn = $('#btn-submit-login');
        $btn.prop('disabled', true).html('<i class="fa-solid fa-spinner fa-spin me-1"></i> Đang đăng nhập...');

        $.ajax({
            url: `${API_BASE}/api/user/login`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ email: email }),
            success: function (res) {
                $btn.prop('disabled', false).html('<i class="fa-solid fa-right-to-bracket me-1"></i> Đăng nhập');
                if (res.status === 'success' && res.data) {
                    currentUserId = res.data.id;
                    try {
                        localStorage.setItem('teravue_current_user_id', currentUserId);
                    } catch (e) {}
                    $('#login-feedback').html('<span class="text-success"><i class="fa-solid fa-check me-1"></i>Đăng nhập thành công!</span>');
                    loadUserProfile();
                    syncFavoritesWithServer();
                    refreshCurrentItinerary();
                    showCrudBanner(`Chào mừng ${res.data.name || res.data.id} đã đăng nhập! ❤️`);
                    setTimeout(() => {
                        const m = bootstrap.Modal.getInstance(document.getElementById('loginModal'));
                        if (m) m.hide();
                        $('#login-feedback').empty();
                    }, 800);
                }
            },
            error: function () {
                $btn.prop('disabled', false).html('<i class="fa-solid fa-right-to-bracket me-1"></i> Đăng nhập');
                $('#login-feedback').html('<span class="text-danger">Lỗi kết nối khi đăng nhập.</span>');
            }
        });
    });

    // Xử lý Đăng xuất tài khoản
    $(document).on('click', '#nav-btn-logout', function (e) {
        e.preventDefault();
        currentUserId = null;
        try {
            localStorage.removeItem('teravue_current_user_id');
            localStorage.removeItem('teravue_fav_places');
        } catch (e) {}
        favoritePlaceIds.clear();
        loadUserProfile();
        updateFavoriteBadges(0);
        refreshCurrentItinerary();
        showCrudBanner('Bạn đã đăng xuất tài khoản thành công.');
    });

    // ============================================================
    // 4. CƠ CHẾ THÍCH ỨNG LỘ TRÌNH (ADAPTIVE REPLANNING) & GIẢI THÍCH AI
    // ============================================================
    function simulateAdaptiveReplan(disruptionType) {
        if (!tripState.sessionId) {
            alert('Vui lòng tạo hoặc chọn một lịch trình trước khi thử nghiệm cơ chế thích ứng AI.');
            return;
        }

        const iconMap = { rain: '🌧️', traffic: '🚗', delay: '⏰' };
        const titleMap = {
            rain: 'AI Đề xuất Thích ứng: Tránh Mưa Gió 🌧️',
            traffic: 'AI Đề xuất Thích ứng: Né Ùn Tắc Giao Thông 🚗',
            delay: 'AI Đề xuất Thích ứng: Điều Chỉnh Giờ Mở Cửa ⏰'
        };

        const $banner = $('#adaptive-replan-banner');
        $('#adaptive-banner-icon').text(iconMap[disruptionType] || '🔄');
        $('#adaptive-banner-title').text(titleMap[disruptionType] || 'AI Đề xuất Thích ứng Lộ trình');
        $('#adaptive-banner-desc').html('<i class="fa-solid fa-spinner fa-spin me-1"></i> AI đang phân tích dữ liệu thực tế và tính toán lại lộ trình tối ưu...');
        $banner.removeClass('d-none');

        $.ajax({
            url: `${API_BASE}/api/replan/simulate`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                disruption_type: disruptionType
            }),
            success: function (res) {
                if (res.status === 'success' && res.itinerary) {
                    setCurrentItinerary(res.itinerary);
                    refreshCurrentItinerary();

                    const explanation = res.ai_explanation || 'AI đã tái tối ưu lại lịch trình cho phù hợp với điều kiện thực tế.';
                    const firstSentence = explanation.split('\n')[0].replace(/\*\*/g, '');
                    $('#adaptive-banner-desc').text(firstSentence);

                    // Mở khung chat AI và ghi lời giải thích chi tiết
                    $('#global-ai-assistant').removeClass('d-none');
                    $('#ai-map-chat').addClass('open');
                    appendChatBubble(
                        `<div class="fw-bold mb-1 text-primary"><i class="fa-solid fa-wand-magic-sparkles me-1"></i> Thông báo Thích ứng AI (${iconMap[disruptionType] || '🔄'})</div>${explanation.replace(/\n\n/g, '<br><br>').replace(/\*\*(.*?)\*\*/g, '<b>$1</b>')}`,
                        'assistant'
                    );

                    showCrudBanner(`AI đã cập nhật lộ trình thích ứng theo điều kiện thực tế!`);
                } else {
                    $('#adaptive-banner-desc').text('Không có thay đổi cần áp dụng hoặc lịch trình hiện tại đã tối ưu.');
                }
            },
            error: function (xhr) {
                $('#adaptive-banner-desc').html(
                    '<span class="text-danger">Không thể thực hiện thích ứng: ' + firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'Lỗi kết nối.') + '</span>'
                );
            }
        });
    }

    $('.btn-sim-replan').on('click', function () {
        const dtype = $(this).data('type') || 'rain';
        simulateAdaptiveReplan(dtype);
    });

    $('#btn-close-adaptive-banner').on('click', function () {
        $('#adaptive-replan-banner').addClass('d-none');
    });

    $('#btn-open-adaptive-chat').on('click', function () {
        $('#global-ai-assistant').removeClass('d-none');
        $('#ai-map-chat').addClass('open');
        const $log = $('#ai-map-chat-log');
        $log.scrollTop($log[0].scrollHeight);
    });

    // Khởi tạo tải profile ban đầu
    loadUserProfile();

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
            url: `${API_BASE}/api/admin/import-excel`,
            method: 'POST',
            data: formData,
            processData: false,
            contentType: false,
            success: res => $('#upload-status').html(`<span class="text-success">${res.message}</span>`),
            error: () => $('#upload-status').html('<span class="text-danger">Lỗi Upload</span>')
        });
    });

    // ============================================================
    // TỰ ĐỘNG KHÔI PHỤC PHIÊN VÀ RENDER LỊCH SỬ KHI TẢI LẠI TRANG (BUG-005)
    // ============================================================
    function restoreSessionIfAny() {
        renderTripHistoryUI();
        const activeSessionId = localStorage.getItem('teravue_active_session');
        if (!activeSessionId) return;

        $.ajax({
            url: `${API_BASE}/api/routes?session_id=${encodeURIComponent(activeSessionId)}`,
            method: 'GET',
            success: function (res) {
                if (res && res.status === 'success' && res.routes && res.routes.length) {
                    tripState.sessionId = activeSessionId;
                    tripState.currentRoutes = res;
                    tripState.selectedRouteId = res.selected_route_id || res.routes[0].route_id;

                    $.ajax({
                        url: `${API_BASE}/api/itinerary?session_id=${encodeURIComponent(activeSessionId)}`,
                        method: 'GET',
                        success: function (itinRes) {
                            if (itinRes && itinRes.status === 'success' && itinRes.itinerary) {
                                setCurrentItinerary(itinRes.itinerary);
                            } else {
                                setCurrentItinerary(res.routes[0]);
                            }
                            renderRouteCards(res.routes);
                            renderComparisonTable(res.routes);
                            selectRouteOnScreen2(tripState.selectedRouteId);
                            $('#hero-section, #map-section').hide();
                            $('#chat-section').fadeIn(180);
                            setActiveNavTab('chat');
                            showCrudBanner('Đã tự động khôi phục hành trình gần nhất của bạn.');
                        },
                        error: function () {
                            setCurrentItinerary(res.routes[0]);
                            renderRouteCards(res.routes);
                            renderComparisonTable(res.routes);
                            selectRouteOnScreen2(tripState.selectedRouteId);
                            $('#hero-section, #map-section').hide();
                            $('#chat-section').fadeIn(180);
                            setActiveNavTab('chat');
                        }
                    });
                }
            },
            error: function () {
                localStorage.removeItem('teravue_active_session');
            }
        });
    }

    restoreSessionIfAny();
