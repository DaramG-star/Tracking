import cv2
import config

class TrackingVisualizer:
    def __init__(self):
        self.enabled = config.SAVE_VIDEO
        self.writers = {}

    def draw_and_write(self, cam, img, detections, masters, frame_ts, active_tracks):
        if not self.enabled: return
        
        disp = img.copy()
        
        # 1. 감지된 모든 물체 순회
        for det in detections:
            x1, y1, x2, y2 = det['box']
            cx, cy = det['center']
            
            # 기본 색상 및 텍스트
            color = (0, 0, 255) # Red
            display_text = "Unmatched"
            
            # 현재 감지된 박스의 UID/MID 정보 찾기
            for uid, info in active_tracks.get(cam, {}).items():
                if info["last_pos"] == (cx, cy):
                    mid = info["master_id"]
                    if mid and mid in masters:
                        status = masters[mid].get("status")
                        
                        if status == "MISSING":
                            # 나타나면 안 되는 곳에 나타난 경우 (MISSING/오분류)
                            color = (255, 0, 255) # 보라색 (강조)
                            display_text = f"!! MISSING !! ID: {mid}"
                        else:
                            # 정상 트래킹 중
                            color = (0, 255, 0) # Green
                            display_text = f"ID: {mid}"
                    else:
                        # 로컬 UID만 부여된 경우
                        color = (0, 255, 255) # Yellow
                        display_text = uid
                    break

            # 바운딩 박스 그리기
            cv2.rectangle(disp, (x1, y1), (x2, y2), color, 3 if display_text.startswith("!!") else 2)
            
            # 라벨 표시 (배경색을 넣어 가독성 높임)
            (w, h), _ = cv2.getTextSize(display_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(disp, (x1, y1 - 25), (x1 + w, y1), color, -1)
            cv2.putText(disp, display_text, (x1, y1 - 7), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # 2. 비디오 저장
        if cam not in self.writers:
            h, w = disp.shape[:2]
            self.writers[cam] = cv2.VideoWriter(
                str(config.VIDEO_DIR / f"{cam}_output.mp4"),
                cv2.VideoWriter_fourcc(*"mp4v"), 5, (w, h)
            )
        self.writers[cam].write(disp)

    def release_all(self):
        for w in self.writers.values():
            w.release()