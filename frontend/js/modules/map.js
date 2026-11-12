    var API_BASE = (function () {
        if (window.location.protocol === 'file:') return 'http://127.0.0.1:8000';
        if (window.location.port === '8000') return '';
        const host = window.location.hostname ? window.location.hostname : '127.0.0.1';
        return `${window.location.protocol}//${host}:8000`;
    })();

    // Khởi tạo bản đồ nền Esri World Street Map & OSM Hot (Miễn phí, không watermark, không chặn file:/// 403)
    var map = L.map('map', { zoomControl: true }).setView([21.0285, 105.8542], 13);
    
    // Layer 1: Esri World Street Map (Sạch sẽ, chi tiết đường phố Hà Nội, không bao giờ bị chặn 403 khi chạy file:///)
    var esriStreetLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19,
        attribution: 'Tiles &copy; Esri &mdash; Source: Esri, DeLorme, NAVTEQ, USGS, TomTom'
    });

    // Layer 2: OSM Humanitarian Hot (Bản đồ phong cách du lịch, màu sắc tươi sáng)
    var osmHotLayer = L.tileLayer('https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors, Tiles style by Humanitarian OSM'
    });

    // Mặc định kích hoạt Esri World Street Map
    esriStreetLayer.addTo(map);

    // Bổ sung bộ chuyển đổi Layer bản đồ trực quan
    L.control.layers({
        "Bản đồ Đường phố (Esri)": esriStreetLayer,
        "Bản đồ Du lịch (OSM Hot)": osmHotLayer
    }, null, { position: 'topright' }).addTo(map);

    var mapMarkers = [], polylineRoutes = [];
    var isAiUpdating = false;
    var cachedAllLocations = null;
    var dragSrcIndex = null;

    // Bảng màu riêng biệt cho từng chặng di chuyển (1->2, 2->3, 3->4...)
    var LEG_COLORS = [
        '#3978D4', // Chặng 1 -> 2: Primary Blue
        '#0D9488', // Chặng 2 -> 3: Teal Natural
        '#F59E0B', // Chặng 3 -> 4: Warm Amber
        '#8B5CF6', // Chặng 4 -> 5: Soft Indigo
        '#EC4899', // Chặng 5 -> 6: Rose Coral
        '#10B981', // Chặng 6 -> 7: Emerald Green
    ];

    // Quản lý người dùng & Bộ sưu tập yêu thích (Account & Favorite)
    var currentUserId = null;
    try {
        const savedUser = localStorage.getItem('teravue_current_user_id');
        if (savedUser && savedUser.trim() && savedUser !== 'guest' && savedUser !== 'null') {
            currentUserId = savedUser.trim();
        }
    } catch (e) {
        currentUserId = null;
    }

    var favoritePlaceIds = new Set();
    try {
        const savedFavs = JSON.parse(localStorage.getItem('teravue_fav_places') ? localStorage.getItem('teravue_fav_places') : '[]');
        if (Array.isArray(savedFavs)) favoritePlaceIds = new Set(savedFavs.map(String));
    } catch (e) {
        favoritePlaceIds = new Set();
    }

    function updateFavoriteBadges(count) {
        const c = count !== undefined ? count : favoritePlaceIds.size;
        $('#nav-favorites-badge').text(c);
        const $qBadge = $('#quick-fav-badge');
        if (c > 0) {
            $qBadge.text(c).show();
        } else {
            $qBadge.hide();
        }
    }

    function saveFavorites() {
        try {
            localStorage.setItem('teravue_fav_places', JSON.stringify(Array.from(favoritePlaceIds)));
        } catch (e) {}
        updateFavoriteBadges();
    }

    function syncFavoritesWithServer() {
        if (!currentUserId) {
            favoritePlaceIds.clear();
            updateFavoriteBadges(0);
            return;
        }
        $.ajax({
            url: `${API_BASE}/api/user/favorites?user_id=${encodeURIComponent(currentUserId)}`,
            method: 'GET',
            success: function (res) {
                if (res.status === 'success' && Array.isArray(res.data)) {
                    favoritePlaceIds = new Set(res.data.map(p => String(p.id)));
                    saveFavorites();
                    updateFavoriteBadges(res.favorites_count || favoritePlaceIds.size);
                } else if (res.status === 'unauthenticated') {
                    favoritePlaceIds.clear();
                    updateFavoriteBadges(0);
                }
            }
        });
    }

    function toggleUserFavorite(placeId, callback) {
        if (!currentUserId) {
            showCrudBanner('Vui lòng đăng nhập để lưu địa điểm yêu thích vào tài khoản ❤️', 'warning');
            const loginModalEl = document.getElementById('loginModal');
            if (loginModalEl && window.bootstrap) {
                window.bootstrap.Modal.getOrCreateInstance(loginModalEl).show();
            }
            if (typeof callback === 'function') callback(false);
            return;
        }
        const pid = String(placeId);
        $.ajax({
            url: `${API_BASE}/api/user/favorites/toggle`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ user_id: currentUserId, place_id: pid }),
            success: function (res) {
                if (res.status === 'success') {
                    if (res.is_favorite) {
                        favoritePlaceIds.add(pid);
                    } else {
                        favoritePlaceIds.delete(pid);
                    }
                    saveFavorites();
                    updateFavoriteBadges(res.favorites_count);
                    if (typeof callback === 'function') callback(res.is_favorite);
                }
            },
            error: function (xhr) {
                if (xhr && xhr.status === 401) {
                    showCrudBanner('Vui lòng đăng nhập để lưu địa điểm yêu thích ❤️', 'warning');
                    const loginModalEl = document.getElementById('loginModal');
                    if (loginModalEl && window.bootstrap) {
                        window.bootstrap.Modal.getOrCreateInstance(loginModalEl).show();
                    }
                    if (typeof callback === 'function') callback(false);
                    return;
                }
                showCrudBanner('Không thể cập nhật danh sách yêu thích lúc này.', 'danger');
                if (typeof callback === 'function') callback(favoritePlaceIds.has(pid));
            }
        });
    }

    // Đồng bộ danh sách yêu thích và trạng thái đăng nhập ngay khi load trang
    syncFavoritesWithServer();

    // Đọc danh sách địa điểm ĐÃ ĐI (On-Trip Visited) từ localStorage
    let visitedPlaceIds = new Set();
    try {
        const savedVisited = JSON.parse(localStorage.getItem('teravue_visited_places') ? localStorage.getItem('teravue_visited_places') : '[]');
        if (Array.isArray(savedVisited)) visitedPlaceIds = new Set(savedVisited.map(String));
    } catch (e) {
        visitedPlaceIds = new Set();
    }

    function saveVisitedPlaces() {
        try {
            localStorage.setItem('teravue_visited_places', JSON.stringify(Array.from(visitedPlaceIds)));
        } catch (e) {}
    }

