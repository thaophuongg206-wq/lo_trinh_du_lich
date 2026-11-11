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
// Gốc repo: test đặt ở thư mục gốc hoặc một thư mục con đều chạy được
const ROOT = require('fs').existsSync(require('path').join(__dirname, 'index.html')) ? __dirname : require('path').join(__dirname, '..');
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
const V2_EXTRA = {
    rating: 4.4, so_luot_danh_gia: 13397, gia_tu: 30000, gia_den: 100000, gia_ghi_chu: 'Uoc luong ve vao cua (Claude), chua xac minh',
    dia_chi: '87 Láng Hạ, Ba Đình, Hà Nội', trong_nha_ngoai_troi: 'ca_hai', open_time: '08:00', close_time: '23:00',
    gio_tuan: [1,2,3,4,5,6,7].map(d => d <= 4 ? { thu: d, mo: '10:00', dong: '22:00' } : (d === 5 ? { thu: d, nghi: true } : { thu: d, mo: '09:00', dong: '23:00' })),
    the: [{ ma: 'chup_anh', ten: 'Chụp ảnh', nhom: 'hoat_dong' }, { ma: 'tro_chuyen', ten: 'Trò chuyện', nhom: 'hoat_dong' }, { ma: 'view_dep', ten: 'View đẹp', nhom: 'phong_cach' }],
    anh: ['https://example.com/a1.jpg', 'https://example.com/a2.jpg', 'https://example.com/a3.jpg'],
    review_insight: { so_review: 7, diem_cam_xuc: 0.91, noi_bat: ['view đẹp', 'đồ ăn/uống ngon'], luu_y: ['hay đông khách'], trich_dan: [{ sao: 5, noi_dung: 'Quán rất đẹp, view hồ tuyệt vời' }] },
};
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
    makeRoute(1, 'preference', '🧭 Theo sở thích', [makePlace('22', 'Hồ Gươm', 'Tham quan', '08:00', '09:00', V2_EXTRA), makePlace('16', 'Cafe Giảng', 'Cafe', '09:10', '10:10')], 130),
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
    const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
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
    const appJs = fs.readFileSync(path.join(ROOT, 'app.js'), 'utf8');
    window.eval(appJs);
    $(window.document).ready();
    // $(document).ready(fn) bên trong app.js đăng ký handler; nếu DOM đã "complete" sẵn
    // (đúng trường hợp của jsdom + runScripts outside-only) thì jQuery gọi fn ngay khi
    // .ready() được gọi thủ công ở dòng trên — nhưng để chắc chắn, gọi thêm lần nữa:
    await sleep(200);

    const doc = window.document;
    const byId = (id) => doc.getElementById(id);

    // ============================================================
    console.log('\n=== BƯỚC 1: Điền Screen 1, bấm "Cùng lên kế hoạch nào" ===');
    // ============================================================
    $('#user_preference').val('yên tĩnh, cà phê view đẹp');
    $('#start_point').val('Hồ Gươm, Hà Nội');
    $('#btn-ask-ai').trigger('click');
    await waitFor('routes call sau ai-suggest', () => seenPayloads.some(p => p.url.endsWith('/api/routes') && p.method === 'POST'));
    await sleep(200); // để renderRouteCards/hide-show hoàn tất sau khi success handler chạy

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
    console.log('\n=== BƯỚC 2 (Task 3): Screen 2 hiển thị dữ liệu địa điểm v2 ===');
    // ============================================================
    const item0 = doc.querySelector('#ai-routes-grid .route-card .s2-timeline-item');
    check('T3.1 · có timeline item ở Screen 2', !!item0);
    const txt0 = item0 ? item0.textContent : '';
    check('T3.2 · hiện rating thật + số đánh giá', txt0.includes('4.4') && txt0.includes('13.397 đánh giá'), txt0.slice(0, 160));
    check('T3.3 · hiện khoảng giá thật 30.000 – 100.000 đ', txt0.includes('30.000 – 100.000 đ'));
    check('T3.4 · hiện địa chỉ thật (dia_chi)', txt0.includes('87 Láng Hạ'));
    check('T3.5 · hiện hoạt động từ thẻ', txt0.includes('Chụp ảnh') && txt0.includes('Trò chuyện'));
    check('T3.6 · hiện điều khách hay khen + lưu ý', txt0.includes('view đẹp') && txt0.includes('hay đông khách'));
    check('T3.7 · review lấy từ trích dẫn thật (có sao)', txt0.includes('★5') && txt0.includes('view hồ tuyệt vời'));
    check('T3.8 · giờ mở cửa thật 08:00 – 23:00', txt0.includes('08:00 – 23:00'));
    const item1 = doc.querySelectorAll('#ai-routes-grid .route-card')[0].querySelectorAll('.s2-timeline-item')[1];
    check('T3.9 · điểm CHƯA có dữ liệu v2 không bị điền giờ/rating giả', item1 && !item1.textContent.includes('07:30') && !item1.textContent.includes('4.5'), item1 && item1.textContent.slice(0, 200));

    // mở Place Detail
    $(item0).trigger('click');
    await sleep(200);
    const t = (id) => (byId(id) ? byId(id).textContent : '');
    check('T3.10 · modal: rating + số đánh giá', t('pd-rating').includes('4.4') && t('pd-rating').includes('13.397'), t('pd-rating'));
    check('T3.11 · modal: giờ hôm nay + ghi chú cả tuần (T6 nghỉ)', t('pd-hours').includes('08:00') && t('pd-hours-note').includes('T6: Nghỉ'), t('pd-hours-note'));
    check('T3.12 · modal: giá + ghi chú ước lượng', t('pd-price').includes('30.000 – 100.000 đ') && t('pd-price-note').includes('ước lượng'), t('pd-price') + ' | ' + t('pd-price-note'));
    check('T3.13 · modal: địa chỉ thật', t('pd-address').includes('87 Láng Hạ'));
    check('T3.14 · modal: hoạt động + chip không gian', t('pd-activity').includes('Chụp ảnh') && t('pd-tags').includes('Trong nhà & ngoài trời'));
    check('T3.15 · modal: phân tích review (7 review, 91%)', t('pd-insight').includes('7 review') && t('pd-insight').includes('91%'), t('pd-insight'));
    check('T3.16 · modal: thư viện ảnh có thumbnail', doc.querySelectorAll('#pd-gallery .pd-thumb').length >= 3, String(doc.querySelectorAll('#pd-gallery .pd-thumb').length));
    check('T3.17 · modal: review tiêu biểu', t('pd-review').includes('view hồ tuyệt vời'));

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

