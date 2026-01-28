import re
import cv2

def extract_ts(name):
    m = re.search(r"\d{8}_(\d{6}_\d+)", name)
    return m.group(1) if m else None

def ts_to_seconds(ts):
    h = int(ts[0:2])
    m = int(ts[2:4])
    s = int(ts[4:6])
    ms = int(ts.split('_')[1]) / 1000
    return h * 3600 + m * 60 + s + ms

class VideoManager:
    def __init__(self, video_dir):
        self.video_dir = video_dir
        self.writers = {}

    def init_writer(self, cam, frame_shape):
        if cam not in self.writers:
            h, w = frame_shape[:2]
            path = str(self.video_dir / f"{cam}_FIFO_master.mp4")
            self.writers[cam] = cv2.VideoWriter(
                path, cv2.VideoWriter_fourcc(*"mp4v"), 5, (w, h)
            )

    def write_frame(self, cam, frame):
        if cam in self.writers:
            self.writers[cam].write(frame)

    def release_all(self):
        for w in self.writers.values():
            w.release()