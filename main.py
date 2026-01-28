import cv2
import pandas as pd
from ultralytics import YOLO

import config
from utils import VideoManager
from matcher import FIFOGlobalMatcher
from loader import get_sorted_frames

def main():
    # 1. 초기화
    config.OUT_DIR.mkdir(exist_ok=True)
    config.VIDEO_DIR.mkdir(exist_ok=True)
    
    model = YOLO(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    video_mgr = VideoManager(config.VIDEO_DIR)
    
    all_frames = get_sorted_frames()
    active_tracks = {cam: {} for cam in config.CAM_SETTINGS}
    local_uid_counter = {cam: 0 for cam in config.CAM_SETTINGS}
    global_events = []

    # 2. 메인 루프
    for frame in all_frames:
        cam = frame["cam"]
        cfg = config.CAM_SETTINGS[cam]
        img = cv2.imread(str(frame["path"]))
        disp = img.copy()
        video_mgr.init_writer(cam, img.shape)

        # ROI 설정
        roi_top, roi_bot = cfg["roi_y"] - cfg["roi_margin"], cfg["roi_y"] + cfg["roi_margin"]
        cv2.rectangle(disp, (0, roi_top), (disp.shape[1], roi_bot), (0, 255, 255), 2)
        
        eol_top, eol_bot = 0, 0
        if cam == "RPI_USB3":
            eol_top, eol_bot = cfg["eol_y"] - cfg["eol_margin"], cfg["eol_y"] + cfg["eol_margin"]
            cv2.rectangle(disp, (0, eol_top), (disp.shape[1], eol_bot), (255, 0, 255), 2)

        # Detection
        results = model(img, conf=0.25, verbose=False)[0]
        new_active = {}

        for b in results.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0])
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            
            in_xseb = roi_top < cy < roi_bot
            in_eol = (cam == "RPI_USB3" and eol_top < cy < eol_bot)
            if not (in_xseb or in_eol): continue

            # Local Distance Matching
            best_uid = None
            best_score = 1e9
            for uid, info in active_tracks[cam].items():
                dx = abs(cx - info["last_pos"][0])
                dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]: continue
                
                score = dx + dy * 0.3
                if score < best_score:
                    best_score, best_uid = score, uid

            # Global Matching
            if best_uid:
                mid = active_tracks[cam][best_uid]["master_id"]
                label, color = mid, (0, 255, 0)
            else:
                local_uid_counter[cam] += 1
                best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                match_cam = "RPI_USB3_EOL" if in_eol else cam
                mid = matcher.try_match(match_cam, frame["time_s"], (x2 - x1), best_uid)
                
                global_events.append({
                    "Master_ID": mid, "Camera": match_cam, "UID": best_uid, "Time": frame["ts"]
                })
                label = mid if mid else "UNMATCHED"
                color = (0, 255, 0) if mid else (0, 0, 255)

            new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}
            cv2.rectangle(disp, (x1, y1), (x2, y2), color, 2)
            cv2.putText(disp, label, (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        active_tracks[cam] = new_active
        video_mgr.write_frame(cam, disp)

    # 3. 종료 처리
    video_mgr.release_all()
    pd.DataFrame(global_events).to_csv(config.OUT_DIR / "parcel_master_tracking_fifo_with_EOL.csv", index=False)

if __name__ == "__main__":
    main()