import re
import cv2
import config

def extract_ts(name):
    """
    파일명이나 UID에서 시간 정보(HHMMSS_mmm)를 추출합니다.
    - 예: '20260127_081946_617' -> '081946_617'
    - 예: '081946_617.jpg' -> '081946_617'
    """
    # (?:\d{8}_)? : 8자리 날짜와 언더바가 있을 수도 있고 없을 수도 있음 (비캡처 그룹)
    # (\d{6}_\d+) : 실제 필요한 시간 정보(6자리 숫자 + 언더바 + 밀리초) 추출
    m = re.search(r"(?:\d{8}_)?(\d{6}_\d+)", name)
    return m.group(1) if m else None

def ts_to_seconds(ts):
    """추출된 시간 문자열을 하루 중 경과된 '초' 단위로 변환합니다."""
    try:
        h = int(ts[0:2])
        m = int(ts[2:4])
        s = int(ts[4:6])
        ms_part = ts.split('_')[1]
        ms = int(ms_part) / 1000
        return h * 3600 + m * 60 + s + ms
    except (IndexError, ValueError):
        return 0.0

class VideoManager:
    def __init__(self, video_dir):
        self.video_dir = video_dir
        self.writers = {}
        self.enabled = config.SAVE_VIDEO 

    def init_writer(self, cam, frame_shape):
        if not self.enabled: return 
        if cam not in self.writers:
            h, w = frame_shape[:2]
            path = str(self.video_dir / f"{cam}_FIFO_master.mp4")
            self.writers[cam] = cv2.VideoWriter(
                path, cv2.VideoWriter_fourcc(*"mp4v"), 5, (w, h)
            )

    def write_frame(self, cam, frame):
        if not self.enabled or cam not in self.writers: return
        self.writers[cam].write(frame)

    def release_all(self):
        for w in self.writers.values():
            w.release()