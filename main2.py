import sys
import cv2
import csv
import config
import api_helper
import time
import numpy as np
from matcher import FIFOGlobalMatcher
from detector import YOLODetector
from scanner_listener import ScannerListener
from pathlib import Path
from datetime import datetime

# track 공용 NFS 저장 헬퍼 사용 (200번 서버 → /mnt/thumbnails)
_track_root = Path(__file__).resolve().parent.parent.parent / "track"
if _track_root.exists() and str(_track_root) not in sys.path:
    sys.path.insert(0, str(_track_root))
try:
    from logic.utils import save_thumbnail_to_nfs
except ImportError:
    save_thumbnail_to_nfs = None

# --- 설정 옵션 ---
SHOW_WINDOW = True  
TARGET_FPS = 4      
FRAME_DELAY = 1.0 / TARGET_FPS

def get_day_seconds():
    now = datetime.now()
    return now.hour * 3600 + now.minute * 60 + now.second + now.microsecond / 1000000

def get_base64_image(img):
    """Crop 이미지를 80×80 리사이즈, JPEG 품질 70으로 인코딩 후 base64 반환."""
    try:
        if img is None or img.size == 0:
            return None
        resized = cv2.resize(img, (80, 80))
        _, buffer = cv2.imencode('.jpg', resized, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return base64.b64encode(buffer).decode('utf-8')
    except Exception as e:
        print(f"[오류] Base64 변환 실패: {e}")
        return None

def main2():
    config.OUT_DIR.mkdir(exist_ok=True)
    detector = YOLODetector(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    
    cap = cv2.VideoCapture(0) 
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    if not cap.isOpened():
        print("[오류] 웹캠을 열 수 없습니다.")
        return

    # --- [추가] 영상 저장 설정 ---
    now_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    video_path = str(config.OUT_DIR / f"tracking_record_{now_str}.mp4")
    # 90도 회전했으므로 가로 720, 세로 1280 크기로 저장
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out_video = cv2.VideoWriter(video_path, fourcc, TARGET_FPS, (720, 1280))
    print(f"[시스템] 영상 저장 시작: {video_path}")

    scanner_listener = ScannerListener(matcher, host="192.168.1.200", port=3000)
    scanner_listener.start()

    log_path = config.OUT_DIR / "tracking_logs_live.csv"
    log_f = open(log_path, 'w', newline='', encoding='utf-8', buffering=1)
    fieldnames = ['timestamp', 'filename', 'cam', 'local_uid', 'master_id', 'x1', 'y1', 'x2', 'y2', 'event']
    log_writer = csv.DictWriter(log_f, fieldnames=fieldnames)
    log_writer.writeheader()

    active_tracks = {"USB_LOCAL": {}}
    local_uid_counter = {"USB_LOCAL": 0}
    cam = "USB_LOCAL"
    cfg = config.CAM_SETTINGS[cam]

    try:
        while cap.isOpened():
            loop_start = time.time()
            ret, raw_frame = cap.read()
            if not ret: continue

            frame_img = cv2.rotate(raw_frame, cv2.ROTATE_90_CLOCKWISE)
            now_day_seconds = get_day_seconds()
            now_dt = datetime.now()
            now_ts = now_dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            virtual_filename = now_dt.strftime('%H%M%S_%f')[:-3] + ".jpg"
            
            # ROI 영역 시각화 (녹화본에도 포함됨)
            roi_y, margin = cfg["roi_y"], cfg["roi_margin"]
            top_y, bottom_y = roi_y - margin, roi_y + margin
            overlay = frame_img.copy()
            cv2.rectangle(overlay, (0, top_y), (720, bottom_y), (255, 255, 0), -1)
            cv2.addWeighted(overlay, 0.2, frame_img, 0.8, 0, frame_img)
            cv2.rectangle(frame_img, (0, top_y), (720, bottom_y), (255, 255, 0), 2)
            cv2.putText(frame_img, f"REC: {now_ts}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

            # 2. YOLO 탐지
            valid_detections = detector.get_detections(frame_img, cfg, cam)
            new_active = {}

            for det in valid_detections:
                x1, y1, x2, y2 = det["box"]
                cx, cy = det["center"]
                
                best_uid, best_score = None, 1e9
                for uid, info in active_tracks[cam].items():
                    dx = abs(cx - info["last_pos"][0])
                    dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                    if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]: continue
                    score = dx + dy * 0.3
                    if score < best_score:
                        best_uid, best_score = uid, score

                mid, is_just_matched = None, False
                event_type = "TRACKING"

                if not best_uid:
                    local_uid_counter[cam] += 1
                    best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                    mid = matcher.try_match(cam, now_day_seconds, det["width"], best_uid)
                    if mid: 
                        is_just_matched = True
                        event_type = "MATCHED"
                    else:
                        event_type = "DETECT_ONLY"
                else:
                    mid = active_tracks[cam][best_uid]["master_id"]

                # 로그 기록
                log_writer.writerow({
                    'timestamp': now_ts, 'filename': virtual_filename, 'cam': cam,
                    'local_uid': best_uid, 'master_id': mid if mid else "NONE",
                    'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'event': event_type
                })
                log_f.flush()

                if mid:
                    new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}
                    h, w = frame_img.shape[:2]
                    crop_img = frame_img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
                    if crop_img.size > 0 and save_thumbnail_to_nfs:
                        save_thumbnail_to_nfs(mid, crop_img)  # NFS에만 저장 (100 서버는 /thumbnails/{uid}.jpg 로 제공, API에는 미전송)

                    # 박스 시각화 (녹화본 포함)
                    color = (0, 255, 0) if is_just_matched else (0, 200, 0)
                    cv2.rectangle(frame_img, (x1, y1), (x2, y2), color, 4 if is_just_matched else 2)
                    cv2.putText(frame_img, f"ID: {mid}" if not is_just_matched else "SUCCESS", 
                                (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

            # --- [추가] 프레임 비디오 파일에 쓰기 ---
            out_video.write(frame_img)

            # 3. 거리 업데이트
            for m_id, m_info in matcher.masters.items():
                if m_info.get("start_time") is not None:
                    elapsed = now_day_seconds - m_info["start_time"]
                    total_dist = m_info.get("total_dist", 15.0)
                    rem_dist = max(0.0, total_dist - (elapsed * config.BELT_SPEED))
                    step_dist = round(rem_dist / 0.5) * 0.5
                    if m_info.get("last_sent_dist") != step_dist:
                        api_helper.api_update_position(m_id, step_dist)
                        m_info["last_sent_dist"] = step_dist

            active_tracks[cam] = new_active
            
            if SHOW_WINDOW:
                show_img = cv2.resize(frame_img, (0, 0), fx=0.6, fy=0.6)
                cv2.imshow("Tracking Watch", show_img)
                if cv2.waitKey(1) & 0xFF == ord('q'): break
            
            time.sleep(max(0, FRAME_DELAY - (time.time() - loop_start)))
++*+*++-+
    except KeyboardInterrupt:
        print("\n[시스템] 중지됨")
    finally:
        log_f.close()
        out_video.release() # [중요] 영상 파일 닫기
        cap.release()
        cv2.destroyAllWindows()
        scanner_listener.stop()
        print(f"[시스템] 녹화 완료: {video_path}")

if __name__ == "__main__":
    main2()