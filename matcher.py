from collections import deque
import config

class FIFOGlobalMatcher:
    def __init__(self):
        self.counter = 0
        self.masters = {}
        # 각 구간별 FIFO 큐
        self.queues = {
            "q01": deque(), "q12": deque(), "q23": deque(), "q3e": deque()
        }

    def _get_next_cam(self, route, cam):
        """경로(Route)에 따른 다음 예상 카메라 순서 반환"""
        if route == "XSEA":
            order = ["USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3"]
        else:  # XSEB
            order = ["USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3", "RPI_USB3_EOL"]

        if cam not in order:
            return None
        idx = order.index(cam)
        return order[idx + 1] if idx + 1 < len(order) else None

    def _new_master(self, cam, time_s, width, uid, route_code):
        """새로운 마스터 객체 생성 및 첫 번째 큐 등록"""
        mid = uid 
        self.masters[mid] = {
            "last_cam": cam,
            "last_time": time_s,
            "last_width": width,
            "uids": {cam: uid},
            "route_code": route_code,
            "status": "TRACKING",        # 상태: TRACKING / PENDING / PICKUP / DISAPPEAR / MISSING
            "pending_from_cam": None
        }
        self.queues["q01"].append(mid)
        return mid

    def _try_fifo(self, q_key, prev_cam, cam, time_s, width, uid, next_q_key=None):
        """FIFO 큐를 이용한 이전 카메라와의 매칭 시도"""
        queue = self.queues[q_key]
        if not queue: return None

        mid = queue[0]
        info = self.masters[mid]
        
        # 예상 도착 시간 계산 및 오차 범위 확인
        expected = info["last_time"] + config.AVG_TRAVEL.get((prev_cam, cam), 0)
        margin = config.TIME_MARGIN.get((prev_cam, cam), 1.0)

        # 시간 범위를 벗어나거나 과거 데이터면 매칭 실패
        if abs(time_s - expected) > margin or time_s <= info["last_time"]:
            return None

        # 매칭 성공: 큐에서 제거하고 정보 업데이트
        queue.popleft()
        info.update({
            "last_cam": cam, 
            "last_time": time_s, 
            "last_width": width,
            "status": "TRACKING" # 다시 나타났으므로 상태 복구
        })
        info["uids"][cam] = uid
        
        # 다음 구간 큐가 있다면 이동
        if next_q_key:
            self.queues[next_q_key].append(mid)
        return mid

    def try_match(self, cam, time_s, width, uid, scanner_data=None):
        """메인 진입점: 카메라별 매칭 로직 분기"""
        if cam == "USB_LOCAL":
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

    def resolve_pending(self, mid, now_s):
        """
        PENDING 상태인 객체가 다음 카메라에 나타날 시간을 넘겼는지 판단하여 결과 반환.
        """
        info = self.masters[mid]
        if info["status"] != "PENDING":
            return None

        route = info["route_code"]
        from_cam = info["pending_from_cam"]
        next_cam = self._get_next_cam(route, from_cam)

        if not next_cam: return None

        key = (from_cam, next_cam)
        if key not in config.AVG_TRAVEL: return None

        # 다음 카메라에 나타나야 할 최대 예상 시간
        expected = info["last_time"] + config.AVG_TRAVEL[key] + config.TIME_MARGIN[key]

        # 아직 기다려볼 만한 시간이면 유지
        if now_s < expected:
            return None

        # 시간이 초과됨 -> 경로에 따른 최종 결정 (PICKUP 또는 DISAPPEAR)
        if route == "XSEA":
            decision = "PICKUP" if next_cam in ["RPI_USB2", "RPI_USB3"] else "DISAPPEAR"
        elif route == "XSEB":
            decision = "PICKUP" if next_cam in ["RPI_USB3", "RPI_USB3_EOL"] else "DISAPPEAR"
        else:
            return None

        # 상태 업데이트 및 큐 청소
        info["status"] = decision
        self.cancel_pending(from_cam, mid)

        return {
            "decision": decision,
            "from_cam": from_cam,
            "next_cam": next_cam,
            "expected": expected
        }

    def cancel_pending(self, from_cam, mid):
        """매칭되지 않고 사라진 객체를 큐에서 제거하여 다음 물체 매칭 방해 방지 (Deadlock 방지)"""
        q_map = {
            "USB_LOCAL": "q01", "RPI_USB1": "q12", "RPI_USB2": "q23", "RPI_USB3": "q3e"
        }
        q_key = q_map.get(from_cam)
        
        if q_key and self.queues[q_key] and self.queues[q_key][0] == mid:
            self.queues[q_key].popleft()
            return True
        return False