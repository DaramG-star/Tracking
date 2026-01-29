import cv2
import csv
import config
import api_helper
from matcher import FIFOGlobalMatcher
from loader import get_sorted_frames
from detector import YOLODetector
from visualizer import TrackingVisualizer  # 시각화 도구 불러오기

def main():
    # 1. 초기화 및 폴더 준비
    config.OUT_DIR.mkdir(exist_ok=True)
    config.CROP_DIR.mkdir(exist_ok=True)
    
    detector = YOLODetector(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    visualizer = TrackingVisualizer() # 비디오 제작 및 그리기 전담

    # CSV 헤더 및 파일 오픈
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

            # ---------------- [Detection] ----------------
            # detector.py를 사용하여 영역 필터링까지 한 번에 수행
            detections = detector.get_detections(img, cfg, cam)
            new_active = {}

            for det in detections:
                x1, y1, x2, y2 = det["box"]
                cx, cy = det["center"]
                in_eol = det["in_eol"]

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
                    local_uid_counter[cam] += 1
                    best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                    match_cam = "RPI_USB3_EOL" if in_eol else cam

                    s_data = {"uid": f"PKG_{frame['ts']}", "route_code": "XSEA"} if cam == "USB_LOCAL" else None
                    if s_data: api_helper.api_scan(s_data["uid"], s_data["route_code"])

                    mid = matcher.try_match(match_cam, frame["time_s"], det["width"], best_uid, s_data)

                    if mid and mid in matcher.masters:
                        route = matcher.masters[mid]["route_code"]
                        # Missing 판정 및 중복 방지
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

            # 3. [Pending Logic] disappearance → PENDING
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

            # 5. [Visualization] 비디오 그리기 및 저장 호출
            visualizer.draw_and_write(cam, img, detections, matcher.masters, frame["ts"])
            active_tracks[cam] = new_active

    # 6. 종료 처리
    debug_f.close()
    visualizer.release_all()

if __name__ == "__main__":
    main()