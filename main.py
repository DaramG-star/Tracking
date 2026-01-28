import cv2
import pandas as pd
import csv
import os
from ultralytics import YOLO
from pathlib import Path

import config
import api_helper 
from utils import VideoManager
from matcher import FIFOGlobalMatcher
from loader import get_sorted_frames

def main():
    # 1. 초기화 및 저장 디렉토리 생성
    config.OUT_DIR.mkdir(exist_ok=True)
    config.VIDEO_DIR.mkdir(exist_ok=True)
    config.CROP_DIR.mkdir(exist_ok=True) # Crop 이미지 저장 폴더 생성
    
    print(f"\n[시스템 시작] 저장을 시작합니다.")
    print(f" - 이미지 저장 경로: {config.CROP_DIR.absolute()}")
    print(f" - 로그 저장 경로: {config.LOG_CSV.absolute()}\n")
    
    model = YOLO(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    video_mgr = VideoManager(config.VIDEO_DIR)
    
    # CSV 파일 헤더 정의
    csv_header = ['timestamp', 'cam', 'local_uid', 'master_id', 'route', 'x1', 'y1', 'x2', 'y2', 'event']
    
    all_frames = get_sorted_frames()
    active_tracks = {cam: {} for cam in config.CAM_SETTINGS}
    local_uid_counter = {cam: 0 for cam in config.CAM_SETTINGS}

    # CSV 파일 오픈
    with open(config.LOG_CSV, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=csv_header)
        writer.writeheader()

        # 2. 메인 루프 (모든 프레임 순회)
        for frame_idx, frame in enumerate(all_frames):
            cam = frame["cam"]
            cfg = config.CAM_SETTINGS[cam]
            img = cv2.imread(str(frame["path"]))
            if img is None:
                continue
            
            disp = img.copy()
            video_mgr.init_writer(cam, img.shape)

            # ROI(노란색) 및 EOL(자색) 영역 설정 및 표시
            roi_top = cfg["roi_y"] - cfg["roi_margin"]
            roi_bot = cfg["roi_y"] + cfg["roi_margin"]
            cv2.rectangle(disp, (0, roi_top), (disp.shape[1], roi_bot), (0, 255, 255), 2)
            
            eol_top, eol_bot = 0, 0
            if cam == "RPI_USB3":
                eol_top = cfg["eol_y"] - cfg["eol_margin"]
                eol_bot = cfg["eol_y"] + cfg["eol_margin"]
                cv2.rectangle(disp, (0, eol_top), (disp.shape[1], eol_bot), (255, 0, 255), 2)

            # YOLO 탐지 실행
            results = model(img, conf=0.25, verbose=False)[0]
            new_active = {}

            for b in results.boxes:
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                
                # ROI 혹은 EOL 영역 안에 있는지 확인
                in_xseb = roi_top < cy < roi_bot
                in_eol = (cam == "RPI_USB3" and eol_top < cy < eol_bot)
                
                if not (in_xseb or in_eol):
                    continue

                # --- Local Matching (이전 프레임 객체와 연결) ---
                best_uid, best_score = None, 1e9
                for uid, info in active_tracks[cam].items():
                    dx = abs(cx - info["last_pos"][0])
                    dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                    if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]:
                        continue
                    score = dx + dy * 0.3
                    if score < best_score:
                        best_score, best_uid = score, uid

                # --- Global Matching & API 이벤트 처리 ---
                route = "UNKNOWN"
                if best_uid:
                    # 기존 추적 객체인 경우
                    mid = active_tracks[cam][best_uid]["master_id"]
                    if mid in matcher.masters:
                        route = matcher.masters[mid]['route_code']
                    label, color = mid, (0, 255, 0)
                    event_type = "TRACKING"
                else:
                    # 신규 객체인 경우 (Global Match 시도)
                    local_uid_counter[cam] += 1
                    best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                    match_cam = "RPI_USB3_EOL" if in_eol else cam
                    
                    s_data = None
                    if cam == "USB_LOCAL":
                        # 스캐너 가상 데이터 생성
                        s_data = {"uid": f"PKG_{frame['ts']}", "route_code": "XSEA"}
                        api_helper.api_scan(s_data['uid'], s_data['route_code'])

                    mid = matcher.try_match(match_cam, frame["time_s"], (x2 - x1), best_uid, s_data)
                    
                    if mid:
                        api_helper.api_update_position(mid, cam)
                        route = matcher.masters[mid]['route_code']
                        
                        if cam == "RPI_USB3" and route == "XSEA":
                            api_helper.api_missing(mid)
                            event_type = "MISSING_DETECTED"
                        elif match_cam == "RPI_USB3_EOL":
                            api_helper.api_missing(mid)
                            api_helper.api_eol(mid)
                            event_type = "EOL_REACHED"
                        else:
                            event_type = "MATCHED"
                    else:
                        event_type = "UNMATCHED"

                    label = mid if mid else "UNMATCHED"
                    color = (0, 255, 0) if mid else (0, 0, 255)

                # --- 실시간 저장 및 로그 기록 ---
                # 1. Crop 이미지 저장
                crop_img = img[y1:y2, x1:x2]
                crop_filename = f"{frame['ts']}_{cam}_{label}.jpg"
                crop_path = config.CROP_DIR / crop_filename
                cv2.imwrite(str(crop_path), crop_img)
                
                # 2. CSV 로그 기록
                writer.writerow({
                    'timestamp': frame['ts'], 'cam': cam, 'local_uid': best_uid,
                    'master_id': label, 'route': route, 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                    'event': event_type
                })

                # 콘솔 출력 (저장 확인용)
                print(f"[{frame['ts']}] {cam} >> {label} 저장됨 ({event_type})")

                # 다음 프레임을 위한 정보 업데이트
                new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid, "route": route}
                
                # 영상에 텍스트 및 박스 그리기
                cv2.rectangle(disp, (x1, y1), (x2, y2), color, 2)
                cv2.putText(disp, f"{label}({route})", (x1, y1 - 8), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

            # --- 3. 객체 소멸(Pickup/Disappeared) 감지 로직 ---
            for old_uid, old_info in active_tracks[cam].items():
                if old_uid not in new_active:
                    mid = old_info["master_id"]
                    if not mid or mid not in matcher.masters:
                        continue
                    
                    route = old_info["route"]
                    event_status = ""
                    
                    # 경로별 정상 픽업 조건
                    if route == "XSEA" and cam == "RPI_USB2":
                        api_helper.api_pickup(mid)
                        event_status = "PICKUP_SUCCESS"
                    elif route == "XSEB" and cam == "RPI_USB3":
                        api_helper.api_pickup(mid)
                        event_status = "PICKUP_SUCCESS"
                    elif cam in ["USB_LOCAL", "RPI_USB1"]:
                        event_status = "DISAPPEARED"
                    
                    if event_status:
                        writer.writerow({
                            'timestamp': frame['ts'], 'cam': cam, 'local_uid': old_uid,
                            'master_id': mid, 'route': route, 'event': event_status
                        })
                        print(f"[*] {mid} 상태 변경: {event_status} at {cam}")

            active_tracks[cam] = new_active
            video_mgr.write_frame(cam, disp)

            if frame_idx % 100 == 0:
                print(f"--- 진행도: {frame_idx}/{len(all_frames)} 프레임 완료 ---")

    # 3. 종료 처리
    video_mgr.release_all()
    print("\n" + "="*50)
    print("트래킹 완료!")
    print(f"- 총 생성된 이미지: {len(list(config.CROP_DIR.glob('*.jpg')))}개")
    print(f"- 로그 파일: {config.LOG_CSV.absolute()}")
    print("="*50)

if __name__ == "__main__":
    main()