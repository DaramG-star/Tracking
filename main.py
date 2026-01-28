import cv2
import pandas as pd
from ultralytics import YOLO

import config
import api_helper 
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

            # Local Matching
            best_uid, best_score = None, 1e9
            for uid, info in active_tracks[cam].items():
                dx = abs(cx - info["last_pos"][0])
                dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]: continue
                score = dx + dy * 0.3
                if score < best_score: best_score, best_uid = score, uid

            # Global Matching & API Triggers
            if best_uid:
                mid = active_tracks[cam][best_uid]["master_id"]
                label, color = mid, (0, 255, 0)
            else:
                local_uid_counter[cam] += 1
                best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                match_cam = "RPI_USB3_EOL" if in_eol else cam
                
                # 스캐너 연동 (USB_LOCAL 진입 시)
                s_data = None
                if cam == "USB_LOCAL":
                    # 테스트용 가상 데이터 (실제론 스캐너 입력값 사용)
                    s_data = {"uid": f"PKG_{frame['ts']}", "route_code": "XSEA"}
                    api_helper.api_scan(s_data['uid'], s_data['route_code'])

                mid = matcher.try_match(match_cam, frame["time_s"], (x2 - x1), best_uid, s_data)
                
                if mid:
                    # 위치 업데이트 및 누락 감지
                    api_helper.api_update_position(mid, cam)
                    
                    # XSEA 경로인데 RPI_USB3 영역에 나타나면 누락(Missing)
                    route = matcher.masters[mid]['route_code']
                    if cam == "RPI_USB3" and route == "XSEA":
                        api_helper.api_missing(mid)
                    
                    # EOL 도달 시 Missing 및 데이터 삭제
                    if match_cam == "RPI_USB3_EOL":
                        api_helper.api_missing(mid)
                        api_helper.api_eol(mid)

                label = mid if mid else "UNMATCHED"
                color = (0, 255, 0) if mid else (0, 0, 255)

            new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}
            cv2.rectangle(disp, (x1, y1), (x2, y2), color, 2)
            cv2.putText(disp, label, (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        # --- 상태 변경(Pickup/Disappeared) 감지 로직 ---
        # 이전 프레임에는 있었으나 현재 프레임에서 사라진 객체 확인
        for old_uid, old_info in active_tracks[cam].items():
            if old_uid not in new_active:
                mid = old_info["master_id"]
                if not mid or mid not in matcher.masters: continue
                
                route = matcher.masters[mid]['route_code']
                
                # 1. XSEA 경로: RPI_USB2에서 사라지면 정상 픽업
                if route == "XSEA" and cam == "RPI_USB2":
                    api_helper.api_pickup(mid)
                
                # 2. XSEB 경로: RPI_USB3 ROI에서 사라지면(EOL 가기 전) 정상 픽업
                elif route == "XSEB" and cam == "RPI_USB3":
                    # EOL에 도달하지 않고 소멸했는지 확인 (단순 로직화)
                    api_helper.api_pickup(mid)
                
                # 3. 그 외 구역 소멸: Disappeared (로그 출력)
                elif cam in ["USB_LOCAL", "RPI_USB1"]:
                    print(f"[*] Master {mid} disappeared at {cam}")

        active_tracks[cam] = new_active
        video_mgr.write_frame(cam, disp)

    # 3. 종료 처리
    video_mgr.release_all()
    print("Tracking Completed. Logs saved.")

if __name__ == "__main__":
    main()