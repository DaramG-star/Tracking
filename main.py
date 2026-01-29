import cv2
import csv
import config
import api_helper
import time  # 추가
from matcher import FIFOGlobalMatcher
from loader import get_sorted_frames
from detector import YOLODetector
from visualizer import TrackingVisualizer
from scanner_listener import ScannerListener  # 추가

def main():
    # 1. 초기화 및 폴더 준비
    config.OUT_DIR.mkdir(exist_ok=True)
    config.CROP_DIR.mkdir(exist_ok=True)
    
    detector = YOLODetector(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    visualizer = TrackingVisualizer()

    # [추가] ScannerListener 시작 (멀티스레드로 백그라운드에서 q_scan 채움)
    scanner_listener = ScannerListener(matcher, host="192.168.1.200", port=3000)
    scanner_listener.start()
    print("[시스템] ScannerListener 시작됨 - MongoDB 데이터를 실시간 대기합니다.")

    # CSV 헤더 설정
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

                    # 이제 ScannerListener가 q_scan에 데이터를 넣어두었으므로 try_match 내부에서 매칭됨
                    mid = matcher.try_match(match_cam, frame["time_s"], det["width"], best_uid)

                    if mid and mid in matcher.masters:
                        route = matcher.masters[mid]["route_code"]
                        
                        # Missing/Matched 판정
                        if (route == "XSEA" and cam == "RPI_USB3") or (route == "XSEB" and match_cam == "RPI_USB3_EOL"):
                            if matcher.masters[mid]["status"] != "MISSING":
                                matcher.masters[mid]["status"] = "MISSING"
                                api_helper.api_missing(mid)
                            event_type = "MISSING"
                        else:
                            matcher.masters[mid]["status"] = "TRACKING"
                            api_helper.api_update_position(mid, cfg["dist"])
                            event_type = "MATCHED"

                # 결과 기록
                writer.writerow({'timestamp': frame['ts'], 'cam': cam, 'local_uid': best_uid, 'master_id': mid, 'route': route, 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'event': event_type})
                if event_type != "MISSING":
                    new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}

            # 3. [Pending Logic]
            for old_uid, old_info in active_tracks[cam].items():
                if old_uid not in new_active:
                    mid = old_info["master_id"]
                    if mid and mid in matcher.masters and matcher.masters[mid]["status"] == "TRACKING":
                        matcher.masters[mid]["status"] = "PENDING"
                        matcher.masters[mid]["pending_from_cam"] = cam

            # 4. [Resolve Pending]
            for mid in list(matcher.masters.keys()):
                result = matcher.resolve_pending(mid, frame["time_s"])
                if result:
                    # cancel_pending 호출 (FIFO Deadlock 방지)
                    matcher.cancel_pending(result["from_cam"], mid)
                    
                    decision = result["decision"]
                    if decision == "PICKUP":
                        api_helper.api_pickup(mid)
                        writer.writerow({'timestamp': frame['ts'], 'cam': result["from_cam"], 'local_uid': "", 'master_id': mid, 'route': matcher.masters[mid]["route_code"], 'event': "PICKUP"})

                    debug_writer.writerow({
                        "timestamp": frame["ts"], "master_id": mid, "route": matcher.masters[mid]["route_code"],
                        "from_cam": result["from_cam"], "next_cam": result["next_cam"],
                        "last_seen_time": matcher.masters[mid]["last_time"], "expected_time": round(result["expected"], 3),
                        "now_time": round(frame["time_s"], 3), "delay_sec": round(frame["time_s"] - result["expected"], 3),
                        "decision": decision
                    })

            # 5. [Visualization]
            visualizer.draw_and_write(cam, img, detections, matcher.masters, frame["ts"])
            active_tracks[cam] = new_active

    # 6. 종료 처리
    debug_f.close()
    visualizer.release_all()
    
    # [추가] 종료 루프
    print("[시스템] 프레임 처리 완료. 리스너 종료를 위해 Ctrl+C를 누르세요.")
    try:
        while scanner_listener.running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[시스템] 종료 중...")
    finally:
        scanner_listener.stop()

if __name__ == "__main__":
    main()