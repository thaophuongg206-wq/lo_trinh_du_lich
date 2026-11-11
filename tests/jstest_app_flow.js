// ============================================================
// SMOKE TEST app.js TRONG DOM THẬT (jsdom) — không phải chỉ syntax check.
//
// Nạp đúng index.html + app.js, giả lập L (Leaflet) và $.ajax/$.get, rồi mô
// phỏng đúng chuỗi thao tác của người dùng: nhập Screen 1 → click "Cùng lên
// kế hoạch nào" → Screen 2 hiện route cards → chọn route_2 → "Chốt lộ trình"
// → Screen 3 hiện đúng route_2 → chuyển tab sang route_1 → gửi lệnh AI FAB
// trên map. Fixture trả về khớp đúng schema đã được 48 test pytest xác nhận,
// nên bài test này chỉ soi lỗi TRONG app.js (DOM wiring, state, payload) —
// không test lại backend.
// ============================================================
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const PASS = [], FAIL = [];
function check(name, cond, detail) {
    if (cond) { PASS.push(name); console.log('  \u2705 ' + name); }
    else { FAIL.push([name, detail]); console.log('  \u274c ' + name + ' \u2014 ' + (detail || '')); }
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
async function waitFor(label, condFn, timeoutMs = 3000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
        if (condFn()) return true;
        await sleep(10);
    }
    console.log(`  \u26a0\ufe0f  waitFor timeout: ${label}`);
    return false;
}

// ---------------- fixture: đúng schema đã verify bằng pytest ----------------
function makePlace(id, ten, loai_hinh, arrive, depart, extra) {
    return Object.assign({
        id, ten, lat: 21.03 + Number(id) * 0.001, lon: 105.85 + Number(id) * 0.001,
        loai_hinh, arrive_time: arrive, depart_time: depart, visit_time: 60,
        travel_to_next: 10, distance_to_next: 1.2, wait_time: 0,
        review: 'Rất đẹp', url_hinh_anh: 'https://example.com/x.jpg',
    }, extra || {});
}

function makeRoute(idx, theme, themeLabel, places, totalMin) {
    const optimized_route = places;
    return {
        route_id: `route_${idx}`, name: `Lộ trình ${theme}`, theme, theme_label: themeLabel,
        description: `Mô tả ${theme}.`, places: optimized_route, timeline: [],
        total_duration: totalMin, travel_time: 20, visit_time: totalMin - 20, wait_time: 0,
        distance_km: 3.0, place_count: optimized_route.length,
        strategy: themeLabel, route_name: `Lộ trình ${theme}`,
        optimized_route, total_time_minutes: totalMin,
    };
}

const ROUTES_5 = [
    makeRoute(1, 'preference', '🧭 Theo sở thích', [makePlace('22', 'Hồ Gươm', 'Tham quan', '08:00', '09:00'), makePlace('16', 'Cafe Giảng', 'Cafe', '09:10', '10:10')], 130),
    makeRoute(2, 'food', '🍜 Ẩm thực', [makePlace('23', 'Phở Thìn', 'Ăn uống', '08:00', '09:30'), makePlace('25', 'Xôi Yến', 'Ăn uống', '09:40', '11:10')], 190),
    makeRoute(3, 'time_optimized', '🏃 Tiết kiệm thời gian', [makePlace('17', 'Văn Miếu', 'Tham quan', '08:00', '09:00')], 60),
    makeRoute(4, 'culture', '🏛️ Văn hoá', [makePlace('24', 'Bảo tàng', 'Tham quan', '08:00', '09:30'), makePlace('26', 'Nhà thờ Lớn', 'Checkin', '09:40', '10:10')], 130),
    makeRoute(5, 'relax', '🌿 Thong thả', [makePlace('27', 'Hồ Tây', 'Tham quan', '08:00', '10:00')], 120),
];

let SESSION_ID = 'sess-test-abc';
const seenPayloads = []; // ghi lại mọi payload gửi lên backend để assert sau

function handleAjax(url, method, payload) {
    seenPayloads.push({ url, method, payload });

    if (url.includes('/api/ai-suggest')) {
        return { advice_text: 'Gợi ý cho bạn hôm nay.', suggested_ids: ['22', '16', '23', '25'], invalid_ids: [], removed_ids: [], excluded_ids: [], session_id: SESSION_ID };
    }
    if (url.includes('/api/ai-refine')) {
        return { advice_text: 'Mình đã cập nhật rồi nhé.', suggested_ids: payload.current_ids.filter(id => id !== '16'), invalid_ids: [], removed_ids: ['16'], excluded_ids: ['16'], session_id: SESSION_ID };
    }
    if (url.includes('/api/routes/select')) {
        const r = ROUTES_5.find(r => r.route_id === payload.route_id) || ROUTES_5[0];
        return { status: 'success', session_id: SESSION_ID, route_id: r.route_id, itinerary: r, excluded_ids: [], routes: [r] };
    }
    if (url.includes('/api/itinerary/update')) {
        const r = Object.assign({}, ROUTES_5[1], { name: 'Lộ trình food (đã cập nhật)' });
        return { status: 'success', available_minutes: 720, itinerary: r, routes: [r], session_id: SESSION_ID, removed_ids: ['16'], selected_route_id: r.route_id, excluded_ids: ['16'] };
    }
    if (url.includes('/api/routes')) {
        return { status: 'success', available_minutes: 720, trip_date: '2026-10-01', vehicle_type: payload.vehicle_type, vehicle_note: '', accessible_locations_count: 30, user_preference: payload.user_preference, weight: 50, excluded_ids: [], candidates_generated: 12, routes: ROUTES_5, session_id: SESSION_ID };
    }
    throw new Error('MOCK: không có handler cho ' + url);
}

async function main() {
    const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
    const dom = new JSDOM(html, { url: 'http://localhost/', runScripts: 'outside-only', pretendToBeVisual: true });
    const { window } = dom;

    // ---- Leaflet stub: đủ để không crash, không cần vẽ bản đồ thật ----
    function FeatureGroup(markers) { this.markers = markers; }
    FeatureGroup.prototype.getBounds = function () { return {}; };
    window.L = {
        map: () => ({ setView() { return this; }, invalidateSize() {}, fitBounds() {}, removeLayer() {} }),
        tileLayer: () => ({ addTo() { return this; } }),
        marker: () => ({ bindPopup() { return this; }, addTo() { return this; } }),
        divIcon: () => ({}),
        polyline: () => ({ addTo() { return this; } }),
        featureGroup: FeatureGroup,
    };

    // ---- jQuery 4.x: module.exports được quyết định NGAY LÚC require() dựa
    // trên global.document, nên phải set global.window/document TRƯỚC khi
    // require('jquery') — gọi require('jquery')(window) như bản cũ sẽ ném lỗi.
    global.window = window;
    global.document = window.document;
    global.navigator = window.navigator;
    delete require.cache[require.resolve('jquery')];
    const $ = require('jquery');
    window.$ = window.jQuery = $;

    // ---- Mock $.ajax / $.get: không gọi mạng thật, trả fixture đồng bộ hoá schema với pytest ----
    $.ajax = function (opts) {
        const url = opts.url || '';
        const method = (opts.method || 'GET').toUpperCase();
        let payload = {};
        try { payload = opts.data ? JSON.parse(opts.data) : {}; } catch (e) { payload = {}; }
        setTimeout(() => {
            try {
                const result = handleAjax(url, method, payload);
                if (opts.success) opts.success(result);
            } catch (err) {
                if (opts.error) opts.error({ responseJSON: { detail: String(err.message || err) } });
            } finally {
                if (opts.complete) opts.complete();
            }
        }, 0);
        return { done() { return this; }, fail() { return this; } };
    };
    $.get = function (url, data) {
        let resolveFn, rejectFn;
        const p = new Promise((res, rej) => { resolveFn = res; rejectFn = rej; });
        p.done = (fn) => { p.then(fn); return p; };
        p.fail = (fn) => { p.catch(fn); return p; };
        setTimeout(() => {
            if (url.includes('nominatim')) resolveFn([{ lat: '21.0285', lon: '105.8542' }]);
            else rejectFn(new Error('mock osrm polyline failure (exercises fallback path)'));
        }, 0);
        return p;
    };

    // ---- navigator.geolocation không dùng trong kịch bản này (dùng start_point text) ----
    window.navigator.geolocation = { getCurrentPosition: (ok, err) => err && err() };

    // ---- Nạp app.js thật vào window (không require, để nó thấy đúng $ /L / document toàn cục) ----
    const appJs = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
    window.eval(appJs);
    $(window.document).ready();
    // $(document).ready(fn) bên trong app.js đăng ký handler; nếu DOM đã "complete" sẵn
    // (đúng trường hợp của jsdom + runScripts outside-only) thì jQuery gọi fn ngay khi
    // .ready() được gọi thủ công ở dòng trên — nhưng để chắc chắn, gọi thêm lần nữa:
    await sleep(5);

    const doc = window.document;
    const byId = (id) => doc.getElementById(id);

    // ============================================================
    console.log('\n=== BƯỚC 1: Điền Screen 1, bấm "Cùng lên kế hoạch nào" ===');
    // ============================================================
    $('#user_preference').val('yên tĩnh, cà phê view đẹp');
    $('#start_point').val('Hồ Gươm, Hà Nội');
    $('#btn-ask-ai').trigger('click');
    await waitFor('routes call sau ai-suggest', () => seenPayloads.some(p => p.url.endsWith('/api/routes') && p.method === 'POST'));
    await sleep(30); // để renderRouteCards/hide-show hoàn tất sau khi success handler chạy

    const suggestCall = seenPayloads.find(p => p.url.includes('ai-suggest'));
    check('1.1 · đã gọi /api/ai-suggest', !!suggestCall, 'không thấy request nào');
    check('1.2 · ai-suggest KHÔNG cần session_id lần đầu (đúng None)', suggestCall && suggestCall.payload.session_id == null);

    const routesCall = seenPayloads.find(p => p.url.includes('/api/routes') && p.method === 'POST' && !p.url.includes('select'));
    check('1.3 · đã gọi /api/routes ngay sau ai-suggest (mục 2: route sinh TRƯỚC Screen 2)', !!routesCall);
    check('1.4 · /api/routes nhận đúng start_lat/lon đã geocode', routesCall && Math.abs(routesCall.payload.start_lat - 21.0285) < 1e-6 && Math.abs(routesCall.payload.start_lon - 105.8542) < 1e-6, JSON.stringify(routesCall && routesCall.payload));
    check('1.5 · ai_selected_ids = suggested_ids từ ai-suggest', routesCall && JSON.stringify(routesCall.payload.ai_selected_ids) === JSON.stringify(['22', '16', '23', '25']));
    check('1.6 · num_routes = 5', routesCall && routesCall.payload.num_routes === 5);

    check('1.7 · Screen 2 (chat-section) hiển thị', doc.getElementById('chat-section').style.display !== 'none' && $('#chat-section').css('display') !== 'none');
    check('1.8 · Screen 1 (hero-section) đã ẩn', $('#hero-section').css('display') === 'none');

    const cards = doc.querySelectorAll('#ai-routes-grid .route-card');
    check('1.9 · Screen 2 hiển thị ĐÚNG 5 route CARD (không phải rổ địa điểm) — mục 2', cards.length === 5, `thấy ${cards.length} card`);
    check('1.10 · card đầu tiên có class "selected" mặc định', cards[0] && cards[0].classList.contains('selected'));
    check('1.11 · nội dung card lấy từ route.name/description thật', cards[0] && cards[0].innerHTML.includes('Lộ trình preference') && cards[0].innerHTML.includes('Mô tả preference'));

    const askBtnBusy = $('#btn-ask-ai').prop('disabled');
    check('1.12 · nút "Cùng lên kế hoạch" đã được bật lại sau khi xong', askBtnBusy === false, `disabled=${askBtnBusy}`);

    // ============================================================
    console.log('\n=== BƯỚC 2: Chọn route_2 trên Screen 2 ===');
    // ============================================================
    $(cards[1]).trigger('click');
    check('2.1 · card route_2 được đánh dấu selected', cards[1].classList.contains('selected'));
    check('2.2 · card route_1 KHÔNG còn selected', !cards[0].classList.contains('selected'));

    // ============================================================
    console.log('\n=== BƯỚC 3: "Chốt lộ trình" → Screen 3 phải hiện ĐÚNG route_2 (mục 10) ===');
    // ============================================================
    seenPayloads.length = 0;
    $('#btn-accept-route').trigger('click');
    await waitFor('routes/select sau khi Chốt lộ trình', () => seenPayloads.some(p => p.url.includes('/api/routes/select')));
    await sleep(20);

    const selectCall = seenPayloads.find(p => p.url.includes('/api/routes/select'));
    check('3.1 · đã gọi /api/routes/select với route_2', selectCall && selectCall.payload.route_id === 'route_2', JSON.stringify(selectCall && selectCall.payload));
    check('3.2 · KHÔNG gọi lại /api/routes (không regenerate — mục 10)', !seenPayloads.some(p => p.url.endsWith('/api/routes') && p.method === 'POST'));

    check('3.3 · Screen 3 (map-section) hiển thị', $('#map-section').css('display') !== 'none');
    check('3.4 · Screen 2 đã ẩn', $('#chat-section').css('display') === 'none');

    const timelineItems = doc.querySelectorAll('#timeline-list li');
    check('3.5 · timeline có đúng 2 điểm của route_2 (Phở Thìn, Xôi Yến)', timelineItems.length === 2, `thấy ${timelineItems.length}`);
    check('3.6 · timeline hiển thị đúng tên địa điểm của route_2', doc.getElementById('timeline-list').innerHTML.includes('Phở Thìn') && doc.getElementById('timeline-list').innerHTML.includes('Xôi Yến'));

    const tabs = doc.querySelectorAll('.route-tab');
    check('3.7 · Screen 3 vẫn có đủ 5 tab để lướt qua route khác', tabs.length === 5, `thấy ${tabs.length}`);
    check('3.8 · tab route_2 đang active (btn-dark)', tabs[1] && tabs[1].classList.contains('btn-dark'));

    // ============================================================
    console.log('\n=== BƯỚC 4: Chuyển tab sang route_1 — không gọi lại thuật toán, chỉ đồng bộ lựa chọn ===');
    // ============================================================
    seenPayloads.length = 0;
    $(tabs[0]).trigger('click');
    await waitFor('routes/select sau khi đổi tab', () => seenPayloads.some(p => p.url.includes('/api/routes/select')));
    await sleep(20);

    check('4.1 · timeline đổi sang nội dung route_1 (Hồ Gươm)', doc.getElementById('timeline-list').innerHTML.includes('Hồ Gươm'));
    check('4.2 · tab route_1 giờ active, route_2 hết active', tabs[0].classList.contains('btn-dark') && !tabs[1].classList.contains('btn-dark'));
    const syncCall = seenPayloads.find(p => p.url.includes('/api/routes/select'));
    check('4.3 · đã đồng bộ lựa chọn về backend (mục 11: source of truth)', syncCall && syncCall.payload.route_id === 'route_1', JSON.stringify(syncCall && syncCall.payload));

    // ============================================================
    console.log('\n=== BƯỚC 5: AI FAB trên map — "Bỏ Cafe Giảng" ===');
    // ============================================================
    seenPayloads.length = 0;
    $('#btn-ai-fab').trigger('click');
    check('5.1 · panel chat AI mở ra', $('#ai-map-chat').hasClass('open'));

    $('#ai_map_instruction').val('Bỏ Cafe Giảng đi');
    $('#btn-send-ai-chat').trigger('click');
    await waitFor('ai-refine rồi itinerary/update sau khi gửi chat AI',
        () => seenPayloads.some(p => p.url.includes('/api/ai-refine')) && seenPayloads.some(p => p.url.includes('/api/itinerary/update')));
    await sleep(20);

    const refineCall = seenPayloads.find(p => p.url.includes('/api/ai-refine'));
    check('5.2 · đã gọi /api/ai-refine kèm session_id', refineCall && refineCall.payload.session_id === SESSION_ID, JSON.stringify(refineCall && refineCall.payload));
    check('5.3 · current_ids gửi đúng itinerary ĐANG XEM (route_1: Hồ Gươm, Cafe Giảng)', refineCall && JSON.stringify(refineCall.payload.current_ids) === JSON.stringify(['22', '16']), JSON.stringify(refineCall && refineCall.payload.current_ids));

    const updateCall = seenPayloads.find(p => p.url.includes('/api/itinerary/update'));
    check('5.4 · sau đó gọi /api/itinerary/update (TÍNH LẠI, không sinh 5 route mới — mục 9)', !!updateCall);
    check('5.5 · KHÔNG gọi lại /api/optimize-route hay /api/routes (mục 9/10)', !seenPayloads.some(p => p.url.includes('optimize-route') || (p.url.endsWith('/api/routes') && p.method === 'POST')));

    const chatBubbles = doc.querySelectorAll('#ai-map-chat-log .chat-bubble');
    check('5.6 · chat log có lời AI phản hồi', Array.from(chatBubbles).some(b => b.textContent.includes('cập nhật')));
    check('5.7 · timeline được vẽ lại theo itinerary mới trả về', doc.getElementById('timeline-list').innerHTML.includes('cập nhật') === false && doc.getElementById('timeline-list').querySelectorAll('li').length === ROUTES_5[1].optimized_route.length);

    const sendBtnBusy = $('#btn-send-ai-chat').prop('disabled');
    check('5.8 · nút gửi chat được mở lại sau khi xong', sendBtnBusy === false);

    // ============================================================
    console.log('\n=== BƯỚC 6: quay lại Screen 2, bấm quick action "− Bớt một điểm" ===');
    // ============================================================
    $('#btn-back-chat').trigger('click');
    check('6.0 · quay lại Screen 2', $('#chat-section').css('display') !== 'none');

    seenPayloads.length = 0;
    const $lessChip = $('#ai-quick-actions .quick-action-chip').filter((_, el) => el.textContent.includes('Bớt')).first();
    check('6.1 · tìm thấy chip "− Bớt một điểm"', $lessChip.length === 1);
    $lessChip.trigger('click');
    await waitFor('ai-refine rồi routes sau quick action',
        () => seenPayloads.some(p => p.url.includes('/api/ai-refine')) && seenPayloads.some(p => p.url.endsWith('/api/routes') && p.method === 'POST'));
    await sleep(30);

    const qaRefine = seenPayloads.find(p => p.url.includes('/api/ai-refine'));
    check('6.2 · quick action gọi ai-refine kèm session_id', qaRefine && qaRefine.payload.session_id === SESSION_ID);
    const qaRoutes = seenPayloads.find(p => p.url.endsWith('/api/routes') && p.method === 'POST');
    check('6.3 · sau đó gọi lại /api/routes để làm mới 3–5 route theo wishlist mới (mục 2)', !!qaRoutes);
    check('6.4 · wishlist gửi lên đã loại bỏ id vừa bị bớt (16)', qaRoutes && !qaRoutes.payload.ai_selected_ids.includes('16'), JSON.stringify(qaRoutes && qaRoutes.payload.ai_selected_ids));
    const cardsAfterQA = doc.querySelectorAll('#ai-routes-grid .route-card');
    check('6.5 · route cards được vẽ lại (vẫn 5 card)', cardsAfterQA.length === 5, `thấy ${cardsAfterQA.length}`);
    const chipBusy = $('#ai-quick-actions .quick-action-chip').first().prop('disabled');
    check('6.6 · các chip được mở khoá lại sau khi xong', chipBusy === false);

    // ============================================================
    console.log('\n=== BƯỚC 7: "Nghĩ lộ trình khác" — KHÔNG geocode lại vì điểm xuất phát chưa đổi ===');
    // ============================================================
    let nominatimCalls = 0;
    const originalGet = $.get;
    $.get = function (url, data) {
        if (url.includes('nominatim')) nominatimCalls++;
        return originalGet.call(this, url, data);
    };
    seenPayloads.length = 0;
    $('#btn-reject-route').trigger('click');
    await waitFor('routes call sau "Nghĩ lộ trình khác"', () => seenPayloads.some(p => p.url.endsWith('/api/routes') && p.method === 'POST'));
    await sleep(30);
    check('7.1 · KHÔNG gọi lại Nominatim (start point không đổi, dùng cache)', nominatimCalls === 0, `gọi ${nominatimCalls} lần`);
    check('7.2 · vẫn sinh được route mới bình thường', seenPayloads.some(p => p.url.endsWith('/api/routes') && p.method === 'POST'));
    $.get = originalGet;

    // ============================================================
    console.log('\n' + '='.repeat(60));
    console.log(`KẾT QUẢ jsdom: ${PASS.length} PASS \u00b7 ${FAIL.length} FAIL`);
    if (FAIL.length) {
        console.log('\nCÁC TEST HỎNG:');
        FAIL.forEach(([n, d]) => console.log(`  \u274c ${n}\n     ${d}`));
    }
    console.log('='.repeat(60));
    process.exit(FAIL.length ? 1 : 0);
}

main().catch(err => { console.error('LỖI KHÔNG BẮT ĐƯỢC:', err); process.exit(1); });
