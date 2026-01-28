from collections import deque
import config

class FIFOGlobalMatcher:
    def __init__(self):
        self.counter = 0
        self.masters = {}
        self.queues = {
            "q01": deque(), "q12": deque(), "q23": deque(), "q3e": deque()
        }

    def _new_master(self, cam, time_s, width, uid):
        self.counter += 1
        mid = f"MASTER_{self.counter:03d}"
        self.masters[mid] = {
            "last_cam": cam, "last_time": time_s,
            "last_width": width, "uids": {cam: uid}
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

    def try_match(self, cam, time_s, width, uid):
        if cam == "USB_LOCAL":
            return self._new_master(cam, time_s, width, uid)
        elif cam == "RPI_USB1":
            return self._try_fifo("q01", "USB_LOCAL", cam, time_s, width, uid, "q12")
        elif cam == "RPI_USB2":
            return self._try_fifo("q12", "RPI_USB1", cam, time_s, width, uid, "q23")
        elif cam == "RPI_USB3":
            return self._try_fifo("q23", "RPI_USB2", cam, time_s, width, uid, "q3e")
        elif cam == "RPI_USB3_EOL":
            return self._try_fifo("q3e", "RPI_USB3", cam, time_s, width, uid)
        return None