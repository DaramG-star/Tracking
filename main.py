import cv2
import csv
from ultralytics import YOLO

import config
import api_helper
from utils import VideoManager
from matcher import FIFOGlobalMatcher
from loader import get_sorted_frames

# ------------------------------
# main
# ------------------------------
def main():
    # 저장 폴더 생성
    config.OUT_DIR.mkdir(exist_ok=True)
    config.VIDEO_DIR.mkdir(exist_ok=True)
    config.CROP_DIR.mkdir(exist_ok=True)

    # 모델 및 매니저 초기화
    model = YOLO(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    video_mgr = VideoManager(config.VIDEO_DIR)

    # CSV 헤더 설정
    csv_header = [
        'timestamp', 'cam', 'local_uid',
        'master_id', 'route', 'x1', 'y1', 'x2', 'y2', 'event'
    ]
    debug_header = [
        "timestamp", "master_id", "route",
        "from_cam", "next_cam",
        "last_seen_time", "expected_time",
        "now_time", "delay_sec", "decision"
    ]

    all_frames = get_sorted_frames()
    active_tracks = {cam: {} for cam in config.CAM_SETTINGS}
    local_uid_counter = {cam: 0 for cam in config.CAM_SETTINGS}

    # 디버그 및 로그 파일 오픈
    debug_f = open(config.OUT_DIR / "debug_pending.csv", "w", newline="", encoding="utf-8")
    debug_writer = csv.DictWriter(debug_f, fieldnames=debug_header)
    debug_writer.writeheader()

    with open(config.LOG_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=csv_header)
        writer.writeheader()

        for frame in all_frames:
            cam = frame["cam"]
            cfg = config.CAM_SETTINGS[cam]
            img = cv2.imread(str(frame["path"]))
            if img is None:
                continue

            # 비디오 라이터 초기화 (config.SAVE_VIDEO가 True일 때만 동작)
            video_mgr.init_writer(cam, img.shape)

            # ROI 및 EOL 영역 계산
            roi_top = cfg["roi_y"] - cfg["roi_margin"]
            roi_bot = cfg["roi_y"] + cfg["roi_margin"]
            eol_top = cfg["eol_y"] - cfg["eol_margin"] if cam == "RPI_USB3" else None
            eol_bot = cfg["eol_y"] + cfg["eol_margin"] if cam == "RPI_USB3" else None

            # 1. Object Detection
            results = model(img, conf=0.25, verbose=False)[0]
            new_active = {}

            # ---------------- detection loop ----------------
            for b in results.boxes:
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

                in_roi = roi_top < cy < roi_bot
                in_eol = cam == "RPI_USB3" and eol_top < cy < eol_bot if eol_top else False
                if not (in_roi or in_eol):
                    continue

                # 로컬 트래킹 (이전 프레임 객체와 매칭)
                best_uid, best_score = None, 1e9
                for uid, info in active_tracks[cam].items():
                    dx = abs(cx - info["last_pos"][0])
                    dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                    if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]:
                        continue
                    score = dx + dy * 0.3
                    if score < best_score:
                        best_uid, best_score = uid, score

                route, mid, event_type = "UNKNOWN", None, "UNMATCHED"

                if best_uid:
                    # 기존에 트래킹 중인 객체인 경우
                    mid = active_tracks[cam][best_uid]["master_id"]
                    if mid and mid in matcher.masters:
                        route = matcher.masters[mid]["route_code"]
                        if matcher.masters[mid]["status"] == "MISSING":
                            continue
                        event_type = "TRACKING"
                else:
                    # 새로운 객체 발견 시 글로벌 매칭 시도
                    local_uid_counter[cam] += 1
                    best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                    match_cam = "RPI_USB3_EOL" if in_eol else cam

                    s_data = {"uid": f"PKG_{frame['ts']}", "route_code": "XSEA"} if cam == "USB_LOCAL" else None
                    if s_data:
                        api_helper.api_scan(s_data["uid"], s_data["route_code"])

                    mid = matcher.try_match(match_cam, frame["time_s"], (x2 - x1), best_uid, s_data)

                    if mid and mid in matcher.masters:
                        route = matcher.masters[mid]["route_code"]
                        # 도착 지점 검증 (Missing 여부 확인)
                        if (route == "XSEA" and cam == "RPI_USB3") or (route == "XSEB" and match_cam == "RPI_USB3_EOL"):
                            matcher.masters[mid]["status"] = "MISSING"
                            api_helper.api_missing(mid)
                            event_type = "MISSING"
                        else:
                            matcher.masters[mid]["status"] = "TRACKING"
                            api_helper.api_update_position(mid, cfg["dist"])
                            event_type = "MATCHED"

                # 결과 기록
                writer.writerow({
                    'timestamp': frame['ts'], 'cam': cam,
                    'local_uid': best_uid, 'master_id': mid,
                    'route': route, 'x1': x1, 'y1': y1,
                    'x2': x2, 'y2': y2, 'event': event_type
                })

                if event_type != "MISSING":
                    new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}

            # 2. disappearance → PENDING 상태 전환
            for old_uid, old_info in active_tracks[cam].items():
                if old_uid not in new_active:
                    mid = old_info["master_id"]
                    if mid and mid in matcher.masters and matcher.masters[mid]["status"] == "TRACKING":
                        matcher.masters[mid]["status"] = "PENDING"
                        matcher.masters[mid]["pending_from_cam"] = cam

            # 3. resolve pending (매처 내부 로직 호출)
            for mid in list(matcher.masters.keys()):
                result = matcher.resolve_pending(mid, frame["time_s"])
                if result:
                    decision = result["decision"]
                    if decision == "PICKUP":
                        api_helper.api_pickup(mid)
                        writer.writerow({
                            'timestamp': frame['ts'], 'cam': result["from_cam"],
                            'local_uid': "", 'master_id': mid,
                            'route': matcher.masters[mid]["route_code"], 'event': "PICKUP"
                        })

                    # 디버그 정보 기록
                    debug_writer.writerow({
                        "timestamp": frame["ts"], "master_id": mid,
                        "route": matcher.masters[mid]["route_code"],
                        "from_cam": result["from_cam"], "next_cam": result["next_cam"],
                        "last_seen_time": matcher.masters[mid]["last_time"],
                        "expected_time": round(result["expected"], 3),
                        "now_time": round(frame["time_s"], 3),
                        "delay_sec": round(frame["time_s"] - result["expected"], 3),
                        "decision": decision
                    })

            active_tracks[cam] = new_active
            video_mgr.write_frame(cam, img)

    debug_f.close()
    video_mgr.release_all()

if __name__ == "__main__":
    main()