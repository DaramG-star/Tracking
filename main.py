import cv2
import csv
import config
import api_helper
import time
from matcher import FIFOGlobalMatcher
from loader import get_sorted_frames
from detector import YOLODetector
from visualizer import TrackingVisualizer
from scanner_listener import ScannerListener

def main():
    # 1. 초기화 및 폴더 준비
    config.OUT_DIR.mkdir(exist_ok=True)
    config.CROP_DIR.mkdir(exist_ok=True)
    
    detector = YOLODetector(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    visualizer = TrackingVisualizer()

    # ScannerListener 시작
    scanner_listener = ScannerListener(matcher, host="192.168.1.200", port=3000)
    scanner_listener.start()
    print("[시스템] ScannerListener 시작됨 - MongoDB 데이터를 실시간 대기합니다.")

    # 매칭 디버그용 CSV 설정
    match_debug_header = ["timestamp", "cam", "local_uid", "master_id", "expected", "actual", "diff", "margin", "status"]
    m_debug_f = open(config.OUT_DIR / "debug_scanner_match.csv", "w", newline="", encoding="utf-8")
    m_debug_writer = csv.DictWriter(m_debug_f, fieldnames=match_debug_header)
    m_debug_writer.writeheader()

    print("[시스템] 첫 번째 스캐너 데이터 수신 대기 중...")
    while len(matcher.queues["q_scan"]) == 0:
        time.sleep(0.1) 
    print(f"[시스템] 데이터 수신 확인: {list(matcher.queues['q_scan'])}")

    # 기존 히스토리 로직용 헤더
    csv_header = ['timestamp', 'cam', 'local_uid', 'master_id', 'route', 'x1', 'y1', 'x2', 'y2', 'event']
    debug_header = ["timestamp", "master_id", "route", "from_cam", "next_cam", "last_seen_time", "expected_time", "now_time", "delay_sec", "decision"]
    
    debug_f = open(config.OUT_DIR / "debug_pending.csv", "w", newline="", encoding="utf-8")
    debug_writer = csv.DictWriter(debug_f, fieldnames=debug_header)
    debug_writer.writeheader()

    all_frames = get_sorted_frames()
    active_tracks = {cam: {} for cam in config.CAM_SETTINGS}
    local_uid_counter = {cam: 0 for cam in config.CAM_SETTINGS}

    with open(config.LOG_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=csv_header)
        writer.writeheader()

        # 2. 메인 프레임 루프
        for frame in all_frames:
            cam = frame["cam"]
            cfg = config.CAM_SETTINGS[cam]
            img = cv2.imread(str(frame["path"]))
            if img is None: continue

            detections = detector.get_detections(img, cfg, cam)
            new_active = {}

            for det in detections:
                x1, y1, x2, y2 = det["box"]
                cx, cy = det["center"]

                # ---------------- [Local Tracking] ----------------
                best_uid, best_score = None, 1e9
                for uid, info in active_tracks[cam].items():
                    dx = abs(cx - info["last_pos"][0])
                    dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                    if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]: continue
                    
                    score = dx + dy * 0.3
                    if score < best_score:
                        best_uid, best_score = uid, score

                route, mid, event_type = "UNKNOWN", None, "UNMATCHED"

                # ---------------- [Global Matching] ----------------
                if best_uid:
                    mid = active_tracks[cam][best_uid]["master_id"]
                    if mid and mid in matcher.masters:
                        route = matcher.masters[mid]["route_code"]
                        if matcher.masters[mid]["status"] == "MISSING": continue
                        event_type = "TRACKING"
                else:
                    # 신규 객체 발견 시
                    local_uid_counter[cam] += 1
                    best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                    match_cam = "RPI_USB3_EOL" if det.get("in_eol") else cam

                    # 매칭 시도
                    mid = matcher.try_match(match_cam, frame["time_s"], det["width"], best_uid)

                    # [디버그 매칭 결과 기록]
                    if hasattr(matcher, 'last_match_attempt') and matcher.last_match_attempt:
                        attempt = matcher.last_match_attempt
                        m_debug_writer.writerow({
                            "timestamp": frame["ts"], "cam": cam, "local_uid": best_uid,
                            "master_id": attempt.get("mid", "N/A"),
                            "expected": attempt.get("expected", 0),
                            "actual": attempt.get("actual", 0),
                            "diff": attempt.get("diff", 0),
                            "margin": attempt.get("margin", 0),
                            "status": attempt.get("status", "UNKNOWN")
                        })
                        matcher.last_match_attempt = None

                    if mid and mid in matcher.masters:
                        route = matcher.masters[mid]["route_code"]
                        if (route == "XSEA" and cam == "RPI_USB3") or (route == "XSEB" and match_cam == "RPI_USB3_EOL"):
                            if matcher.masters[mid]["status"] != "MISSING":
                                matcher.masters[mid]["status"] = "MISSING"
                                api_helper.api_missing(mid)
                            event_type = "MISSING"
                        else:
                            matcher.masters[mid]["status"] = "TRACKING"
                            # 고정 위치 전송 대신 실시간 계산 로직(아래 4.5)에서 통합 관리
                            # api_helper.api_update_position(mid, cfg["dist"])
                            event_type = "MATCHED"

                writer.writerow({'timestamp': frame['ts'], 'cam': cam, 'local_uid': best_uid, 'master_id': mid, 'route': route, 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'event': event_type})
                if event_type != "MISSING":
                    new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}

            # 3. [Pending Logic] 화면에서 사라진 물체 상태 변경
            for old_uid, old_info in active_tracks[cam].items():
                if old_uid not in new_active:
                    mid = old_info["master_id"]
                    if mid and mid in matcher.masters and matcher.masters[mid]["status"] == "TRACKING":
                        matcher.masters[mid]["status"] = "PENDING"
                        matcher.masters[mid]["pending_from_cam"] = cam

            # 4. [Resolve Pending & Jam 방지]
            for mid in list(matcher.masters.keys()):
                result = matcher.resolve_pending(mid, frame["time_s"])
                if result:
                    decision = result["decision"]
                    if decision == "PICKUP":
                        api_helper.api_pickup(mid)
                        writer.writerow({'timestamp': frame['ts'], 'cam': result["from_cam"], 'local_uid': "", 'master_id': mid, 'route': matcher.masters[mid]["route_code"], 'event': "PICKUP"})
                    elif decision == "DISAPPEAR":
                        print(f"[시스템] 객체 {mid} 유실(DISAPPEAR) 처리됨.")

                    debug_writer.writerow({
                        "timestamp": frame["ts"], "master_id": mid, "route": matcher.masters[mid]["route_code"],
                        "from_cam": result["from_cam"], "next_cam": result["next_cam"],
                        "last_seen_time": matcher.masters[mid]["last_time"], "expected_time": round(result["expected"], 3),
                        "now_time": round(frame["time_s"], 3), "delay_sec": round(frame["time_s"] - result["expected"], 3),
                        "decision": decision
                    })

            # 4.5 [실시간 거리 추계 및 API 전송]
            # 카메라 ROI 외부(PENDING) 포함, 실시간으로 목적지까지 남은 거리를 계산합니다.
            for mid, m_info in matcher.masters.items():
                if m_info["status"] in ["TRACKING", "PENDING"] and m_info.get("start_time") is not None:
                    # 노선별 총 목적지 거리 (XSEA: 9.5m, XSEB: 12.8m)
                    total_dist = 9.5 if m_info["route_code"] == "XSEA" else 12.8
                    
                    # USB_LOCAL 매칭 시점으로부터 경과 시간 및 이동 거리 계산
                    elapsed_time = frame["time_s"] - m_info["start_time"]
                    moved_dist = elapsed_time * config.BELT_SPEED # 0.366 m/s
                    
                    # 남은 거리 (0m 이하 방지)
                    remaining_dist = max(0.0, total_dist - moved_dist)
                    
                    # 0.5m 단위로 양자화 (Stepping)
                    step = 0.5
                    stepped_dist = round(remaining_dist / step) * step
                    
                    # 값이 변했을 때만 API 호출하여 중복 전송 방지
                    if m_info.get("last_sent_dist") != stepped_dist:
                        api_helper.api_update_position(mid, stepped_dist)
                        m_info["last_sent_dist"] = stepped_dist
                        # print(f"[거리 갱신] {mid} ({m_info['route_code']}): 남은 거리 {stepped_dist}m")

            # 5. [Visualization]
            visualizer.draw_and_write(cam, img, detections, matcher.masters, frame["ts"], active_tracks)
            active_tracks[cam] = new_active

    # 6. 종료 처리
    m_debug_f.close()
    debug_f.close()
    visualizer.release_all()
    
    print("[시스템] 프레임 처리 완료.")
    try:
        while scanner_listener.running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[시스템] 종료 중...")
    finally:
        scanner_listener.stop()

if __name__ == "__main__":
    main()