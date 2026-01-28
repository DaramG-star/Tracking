from collections import deque
import config

class FIFOGlobalMatcher:
    def __init__(self):
        self.counter = 0
        self.masters = {}
        self.queues = {
            "q01": deque(), "q12": deque(), "q23": deque(), "q3e": deque()
        }

    # matcher.py

    def _new_master(self, cam, time_s, width, uid, route_code):
        # 스캐너에서 받은 실제 UID를 시스템 마스터 ID로 사용
        mid = uid 
        
        # [신규] 경로별 총 거리 가져오기 (config.py에 정의한 값)
        total_dist = config.ROUTE_TOTAL_DIST.get(route_code, 14.08)

        self.masters[mid] = {
            "last_cam": cam,
            "last_time": time_s,
            "last_width": width,
            "uids": {cam: uid},
            "route_code": route_code,
            "status": "TRACKING",        # TRACKING / PENDING / DISAPPEAR / PICKUP / MISSING
            "pending_since": None,
            "pending_from_cam": None
        }
        self.queues["q01"].append(mid)
        return mid

    def _try_fifo(self, q_key, prev_cam, cam, time_s, width, uid, next_q_key=None):
        queue = self.queues[q_key]
        if not queue: return None

        mid = queue[0]
        info = self.masters[mid]
        expected = info["last_time"] + config.AVG_TRAVEL.get((prev_cam, cam), 0)
        margin = config.TIME_MARGIN.get((prev_cam, cam), 1.0)

        if abs(time_s - expected) > margin or time_s <= info["last_time"]:
            return None

        queue.popleft()
        info.update({"last_cam": cam, "last_time": time_s, "last_width": width})
        info["uids"][cam] = uid
        if next_q_key:
            self.queues[next_q_key].append(mid)
        return mid

    def try_match(self, cam, time_s, width, uid, scanner_data=None):
        if cam == "USB_LOCAL":
            # 입구에서는 스캐너 데이터(UID, Route)를 바탕으로 마스터 생성
            u = scanner_data['uid'] if scanner_data else f"TEMP_{self.counter}"
            r = scanner_data['route_code'] if scanner_data else "XSEB"
            return self._new_master(cam, time_s, width, u, r)
        
        elif cam == "RPI_USB1":
            return self._try_fifo("q01", "USB_LOCAL", cam, time_s, width, uid, "q12")
        elif cam == "RPI_USB2":
            return self._try_fifo("q12", "RPI_USB1", cam, time_s, width, uid, "q23")
        elif cam == "RPI_USB3":
            return self._try_fifo("q23", "RPI_USB2", cam, time_s, width, uid, "q3e")
        elif cam == "RPI_USB3_EOL":
            return self._try_fifo("q3e", "RPI_USB3", cam, time_s, width, uid)
        return None