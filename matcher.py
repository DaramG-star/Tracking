from collections import deque
import config

class FIFOGlobalMatcher:
    def __init__(self):
        self.counter = 0
        self.masters = {}
        # 각 구간별 FIFO 큐
        self.queues = {
            "q_scan": deque(),
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
    
    def add_scanner_data(self, uid, route_code, time_s):
        """ScannerListener가 호출하는 함수: 스캐너 데이터를 시스템에 등록"""
        mid = uid
        total_dist = config.ROUTE_TOTAL_DIST.get(route_code, 14.08)
        
        self.masters[mid] = {
            "last_cam": "Scanner",
            "last_time": time_s,
            "last_width": 0,
            "uids": {},
            "route_code": route_code,
            "status": "TRACKING",
            "start_time": None, # USB_LOCAL 통과 시 설정
            "total_dist": total_dist,
            "pending_from_cam": None
        }
        # 큐에는 매칭을 위해 (mid, route_code) 튜플 형태로 저장
        self.queues["q_scan"].append((mid, route_code))
        print(f"[Matcher] ✅ q_scan 등록 완료: {mid} (Route: {route_code})")

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
        queue = self.queues[q_key]
        if not queue: return None

        # 큐의 첫 번째 항목 추출 (q_scan은 튜플, 나머지는 문자열)
        item = queue[0]
        mid = item[0] if isinstance(item, tuple) else item
        
        info = self.masters.get(mid)
        if not info: return None

        # 시간 기반 매칭 검증
        expected = info["last_time"] + config.AVG_TRAVEL.get((prev_cam, cam), 0)
        margin = config.TIME_MARGIN.get((prev_cam, cam), 2.0) # 마진은 상황에 맞게 조절

        if abs(time_s - expected) > margin or time_s <= info["last_time"]:
            return None

        # 매칭 성공 시 처리
        queue.popleft()
        info.update({
            "last_cam": cam,
            "last_time": time_s,
            "last_width": width,
            "status": "TRACKING"
        })
        info["uids"][cam] = uid
        
        # 첫 번째 카메라(USB_LOCAL) 통과 시 시작 시간 기록
        if info["start_time"] is None:
            info["start_time"] = time_s

        if next_q_key:
            self.queues[next_q_key].append(mid)
        return mid

    def try_match(self, cam, time_s, width, uid, scanner_data=None):
        """메인 진입점: 카메라별 매칭 로직 분기"""
        if cam == "USB_LOCAL":
            # q_scan에서 매칭 시도
            return self._try_fifo("q_scan", "Scanner", cam, time_s, width, uid, "q01")
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
            "Scanner":"q-scan", "USB_LOCAL": "q01", "RPI_USB1": "q12", "RPI_USB2": "q23", "RPI_USB3": "q3e"
        }
        q_key = q_map.get(from_cam)
        
        if q_key and self.queues[q_key] and self.queues[q_key][0] == mid:
            self.queues[q_key].popleft()
            return True
        return False