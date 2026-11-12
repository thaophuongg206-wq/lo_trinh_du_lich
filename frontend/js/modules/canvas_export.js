    // ============================================================
    // VẼ HÌNH CHỮ NHẬT BO GÓC TRÊN CANVAS 2D
    // ============================================================
    function roundRect(ctx, x, y, width, height, radius) {
        if (ctx.roundRect) {
            ctx.beginPath();
            ctx.roundRect(x, y, width, height, radius);
            return;
        }
        ctx.beginPath();
        ctx.moveTo(x + radius, y);
        ctx.lineTo(x + width - radius, y);
        ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
        ctx.lineTo(x + width, y + height - radius);
        ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
        ctx.lineTo(x + radius, y + height);
        ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
        ctx.lineTo(x, y + radius);
        ctx.quadraticCurveTo(x, y, x + radius, y);
        ctx.closePath();
    }

    function showImageDownloadSuccessModal(dataUrl, filename) {
        $('#itinerary-preview-img').attr('src', dataUrl);
        $('#modal-btn-download-again').attr('href', dataUrl).attr('download', filename);
        const modalEl = document.getElementById('imagePreviewModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }
        showCrudBanner(`Đã tải xuống ảnh lịch trình: "${filename}"`);
    }

    // ============================================================
    // TẢI XUỐNG ẢNH LỘ TRÌNH DU LỊCH CHẤT LƯỢNG CAO (CANVAS PNG)
    // ============================================================
    function downloadItineraryImage() {
        const itinerary = getActiveItinerary();
        if (!itinerary) {
            showCrudBanner('Vui lòng chọn hoặc tạo một lộ trình trước khi tải ảnh.', true);
            return;
        }
        const routeLocs = Array.isArray(itinerary.optimized_route)
            ? itinerary.optimized_route
            : (Array.isArray(itinerary.places) ? itinerary.places : []);
        if (routeLocs.length === 0) {
            showCrudBanner('Lộ trình chưa có địa điểm nào để xuất ảnh.', true);
            return;
        }

        const vType = tripState.baseParams ? tripState.baseParams.vehicle_type : 'xe_may';
        const vehicleNames = {
            'xe_may': 'Xe máy',
            'xe_dap': 'Xe đạp',
            'di_bo': 'Đi bộ',
            'o_to': 'Ô tô / Taxi'
        };
        const vehicleLabel = vehicleNames[vType] || 'Xe máy';
        const routeName = itinerary.route_name || itinerary.name || 'Lộ trình gợi ý Du Lịch Hà Nội';
        const dateStr = (tripState.baseParams && tripState.baseParams.trip_date)
            ? tripState.baseParams.trip_date
            : new Date().toLocaleDateString('vi-VN');
        const totalTimeHours = itinerary.total_time
            ? (itinerary.total_time >= 60 ? `${Math.floor(itinerary.total_time / 60)}h${Math.round(itinerary.total_time % 60).toString().padStart(2, '0')}` : `${Math.round(itinerary.total_time)} phút`)
            : '--';
        const totalDistKm = itinerary.total_distance
            ? `${(Number(itinerary.total_distance)).toFixed(1)} km`
            : '--';
        const totalCostStr = (itinerary.total_cost || itinerary.estimated_cost)
            ? formatCurrencyVND(itinerary.total_cost || itinerary.estimated_cost)
            : 'Theo chi tiêu thực tế';

        // Độ phân giải cao (Retina 2x)
        const scale = 2;
        const width = 960;
        const headerH = 250;
        const footerH = 140;
        const cardH = 175;
        const connectorH = 50;
        const totalHeight = headerH + (routeLocs.length * cardH) + Math.max(0, (routeLocs.length - 1) * connectorH) + footerH;

        const canvas = document.createElement('canvas');
        canvas.width = width * scale;
        canvas.height = totalHeight * scale;
        const ctx = canvas.getContext('2d');
        ctx.scale(scale, scale);

        // 1. Nền tổng thể
        ctx.fillStyle = '#F8FAFC';
        ctx.fillRect(0, 0, width, totalHeight);

        // 2. Header Card (Gradient xanh navy sang trọng)
        const grad = ctx.createLinearGradient(0, 0, width, headerH);
        grad.addColorStop(0, '#1E3A8A');
        grad.addColorStop(1, '#2563EB');
        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, width, headerH);

        // Họa tiết trang trí header
        ctx.fillStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.beginPath();
        ctx.arc(width - 80, 50, 140, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(80, 200, 80, 0, Math.PI * 2);
        ctx.fill();

        // Brand Title
        ctx.fillStyle = '#93C5FD';
        ctx.font = 'bold 14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText('✦ TERAVUE · HỆ THỐNG LẬP LỘ TRÌNH DU LỊCH THÔNG MINH HÀ NỘI', 40, 45);

        // Tên lộ trình
        ctx.fillStyle = '#FFFFFF';
        ctx.font = 'bold 28px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        let displayTitle = routeName;
        if (ctx.measureText(displayTitle).width > width - 80) {
            while (ctx.measureText(displayTitle + '...').width > width - 80 && displayTitle.length > 10) {
                displayTitle = displayTitle.substring(0, displayTitle.length - 1);
            }
            displayTitle += '...';
        }
        ctx.fillText(displayTitle, 40, 92);

        // Stats Pills trong Header
        const pills = [
            `📅 Ngày: ${dateStr}`,
            `⏱ ${totalTimeHours}`,
            `🚗 ${totalDistKm}`,
            `📍 ${routeLocs.length} điểm`,
            `💰 ${totalCostStr}`
        ];
        
        let pillX = 40;
        const pillY = 125;
        ctx.font = 'bold 14.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        pills.forEach(pill => {
            const pWidth = ctx.measureText(pill).width + 24;
            ctx.fillStyle = 'rgba(255, 255, 255, 0.2)';
            roundRect(ctx, pillX, pillY, pWidth, 36, 18);
            ctx.fill();
            ctx.fillStyle = '#FFFFFF';
            ctx.fillText(pill, pillX + 12, pillY + 23);
            pillX += pWidth + 12;
        });

        // Ghi chú phương tiện
        ctx.fillStyle = 'rgba(255, 255, 255, 0.88)';
        ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText(`Phương tiện: ${vehicleLabel} · Lịch trình đã được tối ưu hóa theo cửa sổ thời gian và khoảng cách di chuyển`, 40, 205);

        // 3. Render danh sách từng địa điểm
        let currentY = headerH + 30;

        routeLocs.forEach((loc, idx) => {
            const arriveT = firstDefined(loc.arrive_time, '--:--');
            const departT = firstDefined(loc.depart_time, '--:--');
            const duration = firstDefined(loc.visit_time, loc.time, 60);
            const catLabel = formatCategoryLabel(loc.loai_hinh);
            const rating = getPlaceRating(loc);
            const address = firstDefined(loc.dia_chi, loc.mo_ta, 'Hà Nội, Việt Nam');
            const price = formatPlacePrice(loc);
            const reviewText = getAccentedReview(loc);
            const isVisited = visitedPlaceIds.has(String(firstDefined(loc.id)));

            // Thẻ card trắng bo tròn
            ctx.fillStyle = '#FFFFFF';
            roundRect(ctx, 40, currentY, width - 80, cardH - 15, 16);
            ctx.fill();

            // Viền card
            ctx.strokeStyle = isVisited ? '#10B981' : '#E2E8F0';
            ctx.lineWidth = isVisited ? 2.5 : 1.2;
            ctx.stroke();

            // Badge tròn số thứ tự
            const badgeColor = isVisited ? '#10B981' : (idx === 0 ? '#2563EB' : (idx === routeLocs.length - 1 ? '#DC2626' : '#3B82F6'));
            ctx.fillStyle = badgeColor;
            ctx.beginPath();
            ctx.arc(85, currentY + 45, 22, 0, Math.PI * 2);
            ctx.fill();

            ctx.fillStyle = '#FFFFFF';
            ctx.font = 'bold 17px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(isVisited ? '✓' : String(idx + 1), 85, currentY + 51);
            ctx.textAlign = 'left';

            // Tên địa điểm
            ctx.fillStyle = '#0F172A';
            ctx.font = 'bold 19px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            let placeName = loc.ten;
            if (ctx.measureText(placeName).width > width - 360) {
                while (ctx.measureText(placeName + '...').width > width - 360 && placeName.length > 5) {
                    placeName = placeName.substring(0, placeName.length - 1);
                }
                placeName += '...';
            }
            ctx.fillText(placeName, 125, currentY + 38);

            // Time slot badge
            const timeBadge = `${arriveT} – ${departT} (${duration}p)`;
            ctx.font = 'bold 13.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            const timeW = ctx.measureText(timeBadge).width + 20;
            ctx.fillStyle = isVisited ? '#ECFDF5' : '#EFF6FF';
            roundRect(ctx, width - 60 - timeW, currentY + 18, timeW, 30, 15);
            ctx.fill();
            ctx.fillStyle = isVisited ? '#059669' : '#2563EB';
            ctx.fillText(timeBadge, width - 60 - timeW + 10, currentY + 38);

            // Thể loại | Đánh giá | Chi phí
            ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            ctx.fillStyle = '#64748B';
            ctx.fillText(`🏷️ ${catLabel}   |   ★ ${rating}   |   💵 ${price}`, 125, currentY + 66);

            // Địa chỉ
            ctx.fillStyle = '#475569';
            ctx.font = '13.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            let addrStr = '📍 ' + address;
            if (ctx.measureText(addrStr).width > width - 170) {
                addrStr = addrStr.substring(0, 75) + '...';
            }
            ctx.fillText(addrStr, 125, currentY + 92);

            // Trích dẫn review tiếng Việt có dấu
            ctx.fillStyle = '#334155';
            ctx.font = 'italic 13.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
            let revStr = '“' + reviewText + '”';
            if (ctx.measureText(revStr).width > width - 170) {
                revStr = revStr.substring(0, 85) + '...”';
            }
            ctx.fillText(revStr, 125, currentY + 120);

            currentY += cardH - 15;

            // Chặng di chuyển nối giữa 2 điểm
            if (idx < routeLocs.length - 1) {
                const hasNext = (loc.travel_to_next > 0) || (loc.distance_to_next > 0);
                const distStr = loc.distance_to_next ? `${loc.distance_to_next.toFixed(1)} km` : '';
                const timeStr = loc.travel_to_next ? `${Math.round(loc.travel_to_next)} phút` : '';
                const travelLabel = hasNext ? `>>> Di chuyển: ~${timeStr} (${distStr}) bằng ${vehicleLabel.toLowerCase()}` : `>>> Di chuyển sang Điểm ${idx + 2}`;

                ctx.strokeStyle = '#CBD5E1';
                ctx.lineWidth = 2;
                ctx.setLineDash([4, 4]);
                ctx.beginPath();
                ctx.moveTo(85, currentY);
                ctx.lineTo(85, currentY + connectorH);
                ctx.stroke();
                ctx.setLineDash([]);

                // Pill chặng
                ctx.fillStyle = '#E2E8F0';
                ctx.font = '13px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                const connW = ctx.measureText(travelLabel).width + 24;
                roundRect(ctx, 125, currentY + 10, connW, 28, 14);
                ctx.fill();
                ctx.fillStyle = '#475569';
                ctx.fillText(travelLabel, 137, currentY + 28);

                currentY += connectorH;
            }
        });

        // 4. Footer
        currentY += 25;
        ctx.fillStyle = '#FFFFFF';
        roundRect(ctx, 40, currentY, width - 80, footerH - 40, 16);
        ctx.fill();
        ctx.strokeStyle = '#E2E8F0';
        ctx.lineWidth = 1;
        ctx.stroke();

        ctx.fillStyle = '#0F172A';
        ctx.font = 'bold 15px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText('💡 Lưu ý hành trình:', 65, currentY + 32);

        ctx.fillStyle = '#64748B';
        ctx.font = '13.5px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        ctx.fillText('• Quý khách vui lòng mặc trang phục lịch sự khi ghé thăm các di tích văn hóa - tâm linh (Lăng Bác, Đền, Chùa).', 65, currentY + 56);
        ctx.fillText('• Chú ý bảo quản tư trang cá nhân tại nơi đông người & chúc bạn có một chuyến đi Hà Nội an toàn, trọn vẹn!', 65, currentY + 78);

        // Xuất file ảnh PNG
        const safeName = routeName.replace(/[\/:*?"<>|]/g, '_').replace(/\s+/g, '_');
        const safeDate = String(dateStr).replace(/[\/:*?"<>|]/g, '-');
        const filename = `Lich_Trinh_Du_Lich_Ha_Noi_${safeName}_${safeDate}.png`;

        try {
            canvas.toBlob(function (blob) {
                if (!blob) {
                    const dataUrl = canvas.toDataURL('image/png');
                    triggerDownload(dataUrl, filename);
                    showImageDownloadSuccessModal(dataUrl, filename);
                    return;
                }
                const url = URL.createObjectURL(blob);
                triggerDownload(url, filename);
                showImageDownloadSuccessModal(url, filename);
            }, 'image/png');
        } catch (err) {
            console.error('Canvas export error:', err);
            const dataUrl = canvas.toDataURL('image/png');
            triggerDownload(dataUrl, filename);
            showImageDownloadSuccessModal(dataUrl, filename);
        }

        function triggerDownload(u, fn) {
            const a = document.createElement('a');
            a.href = u;
            a.download = fn;
            document.body.appendChild(a);
            a.click();
            setTimeout(() => {
                document.body.removeChild(a);
            }, 300);
        }
    }


    $(document).on('click', '#btn-download-itinerary', function () {
        downloadItineraryImage();
    });

    function openChangeTimeModal(loc = null) {
        if (loc) {
            $('#change-time-place-id').val(String(loc.id));
            $('#change-time-place-name').text(loc.ten);
            $('#custom_visit_time_input').val(firstDefined(loc.visit_time, 60));
            $('#change-time-place-box').removeClass('d-none');
        } else {
            $('#change-time-place-id').val('');
            $('#change-time-place-box').addClass('d-none');
        }

        const startDefault = firstDefined(tripState.baseParams && tripState.baseParams.start_time, $('#start_time').val(), '08:00');
        const endDefault = firstDefined(tripState.baseParams && tripState.baseParams.end_time, $('#end_time').val(), '20:00');
        $('#modal_start_time').val(startDefault);
        $('#modal_end_time').val(endDefault);

        const modalEl = document.getElementById('changeTimeModal');
        if (modalEl && window.bootstrap) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }
    }

    $('#btn-open-trip-time').on('click', function () {
        openChangeTimeModal(null);
    });

    $('#btn-confirm-change-time').on('click', function () {
        const pid = $('#change-time-place-id').val() ? $('#change-time-place-id').val() : null;
        const parsedCustom = parseFloat($('#custom_visit_time_input').val());
        const customMins = pid ? (parsedCustom ? parsedCustom : 60) : null;
        const newStart = firstDefined($('#modal_start_time').val(), $('#start_time').val());
        const newEnd = firstDefined($('#modal_end_time').val(), $('#end_time').val());

        $('#start_time').val(newStart);
        $('#end_time').val(newEnd);
        if (tripState.baseParams) {
            tripState.baseParams.start_time = newStart;
            tripState.baseParams.end_time = newEnd;
        }

        $.ajax({
            url: `${API_BASE}/api/itinerary/change-time`,
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                session_id: tripState.sessionId,
                place_id: pid,
                custom_visit_time: customMins,
                start_time: newStart,
                end_time: newEnd
            }),
            success: function (res) {
                const modalEl = document.getElementById('changeTimeModal');
                if (modalEl && window.bootstrap) {
                    window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                }
                applyDirectItineraryUpdate(res, 'Đã cập nhật thời gian và tính lại toàn bộ lịch trình!');
            },
            error: function (xhr) {
                alert(firstDefined(xhr.responseJSON && xhr.responseJSON.detail, 'Không thể cập nhật thời gian.'));
            }
        });
    });

    $('#btn-back-chat').click(function () {
        $('#map-section').hide();
        $('#chat-section').fadeIn(180);
        setActiveNavTab('chat');
    });

