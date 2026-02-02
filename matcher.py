from collections import deque
import heapq
import config

class FIFOGlobalMatcher:
    def __init__(self):
        self.counter = 0
        self.masters = {}
        # q_scan: uid 오름차순 힙큐 (uid, route_code), 나머지는 FIFO deque
        self.queues = {
            "q_scan": [],  # heapq용 list (uid 기준 오름차순)
            "q01": deque(), "q12": deque(), "q23": deque(), "q3e": deque()
        }
        # 디버그를 위한 마지막 매칭 시도 정보 저장 변수
        self.last_match_attempt = None

    def _get_next_cam(self, route, cam):
        """경로(Route)에 따른 다음 예상 카메라 순서 반환"""
        if route == "XSEA":
            order = ["Scanner", "USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3"]
        else:  # XSEB
            order = ["Scanner", "USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3", "RPI_USB3_EOL"]

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
            "start_time": time_s,
            "total_dist": total_dist,
            "pending_from_cam": None
        }
        # q_scan: (uid, route_code)를 uid 오름차순 힙에 추가
        heapq.heappush(self.queues["q_scan"], (mid, route_code))
        print(f"[Matcher] ✅ q_scan 등록 완료: {mid} (Route: {route_code}, Dist:{total_dist}m)")

    def _try_fifo(self, q_key, prev_cam, cam, time_s, width, uid, next_q_key=None):
        is_q_scan = q_key == "q_scan"
        queue = self.queues[q_key]

        if not queue:
            self.last_match_attempt = {"status": "EMPTY_QUEUE", "q_key": q_key}
            return None

        if is_q_scan:
            item = queue[0]  # 힙 최소 요소
            mid = item[0]
        else:
            item = queue[0]
            mid = item[0] if isinstance(item, tuple) else item

        info = self.masters.get(mid)
        if not info:
            return None

        # [시간차 매칭 계산 및 디버그 정보 수집]
        avg_travel = config.AVG_TRAVEL.get((prev_cam, cam), 0)
        expected = info["last_time"] + avg_travel
        margin = config.TIME_MARGIN.get((prev_cam, cam), 2.0)
        diff = time_s - expected

        # 매칭 시도 상세 내역 기록 (main.py에서 활용)
        self.last_match_attempt = {
            "q_key": q_key,
            "mid": mid,
            "target_uid": uid,
            "expected": round(expected, 3),
            "actual": round(time_s, 3),
            "diff": round(diff, 3),
            "margin": margin,
            "prev_cam": prev_cam
        }

        # 매칭 실패 조건 검사
        if abs(diff) > margin:
            self.last_match_attempt["status"] = "OUT_OF_MARGIN"
            return None

        if time_s <= info["last_time"]:
            self.last_match_attempt["status"] = "TIME_REVERSED"
            return None

        # 매칭 성공 처리
        self.last_match_attempt["status"] = "SUCCESS"
        if is_q_scan:
            heapq.heappop(self.queues[q_key])
        else:
            queue.popleft()

        info.update({
            "last_cam": cam,
            "last_time": time_s,
            "last_width": width,
            "status": "TRACKING"
        })
        info["uids"][cam] = uid

        if info["start_time"] is None:
            info["start_time"] = time_s

        if next_q_key:
            self.queues[next_q_key].append(mid)
        return mid

    def try_match(self, cam, time_s, width, uid, scanner_data=None):
        """메인 진입점: 카메라별 매칭 로직 분기"""
        if cam == "USB_LOCAL":
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
        info = self.masters[mid]
        if info["status"] not in ["PENDING", "TRACKING"]:
            return None
        from_cam = info.get("pending_from_cam") or info["last_cam"]
        route = info["route_code"]
        next_cam = self._get_next_cam(route, from_cam)
        if not next_cam: return None
        key = (from_cam, next_cam)
        if key not in config.AVG_TRAVEL: return None
        expected = info["last_time"] + config.AVG_TRAVEL[key] + config.TIME_MARGIN[key]
        if now_s < expected:
            return None
        if route == "XSEA":
            decision = "PICKUP" if next_cam in ["RPI_USB2", "RPI_USB3"] else "DISAPPEAR"
        elif route == "XSEB":
            decision = "PICKUP" if next_cam in ["RPI_USB3", "RPI_USB3_EOL"] else "DISAPPEAR"
        else:
            decision = "DISAPPEAR"
        info["status"] = decision
        self.cancel_pending(from_cam, mid)
        return {"decision": decision, "from_cam": from_cam, "next_cam": next_cam, "expected": expected}

    def cancel_pending(self, from_cam, mid):
        q_map = {"Scanner": "q_scan", "USB_LOCAL": "q01", "RPI_USB1": "q12", "RPI_USB2": "q23", "RPI_USB3": "q3e"}
        q_key = q_map.get(from_cam)
        if q_key and self.queues[q_key]:
            if q_key == "q_scan":
                # 힙 구조이므로 첫번째 요소 확인
                if self.queues[q_key][0][0] == mid:
                    heapq.heappop(self.queues[q_key])
                    print(f"[Matcher] Jam 방지: {q_key}에서 {mid} 제거완료")
                    return True
            else:
                if self.queues[q_key][0] == mid:
                    self.queues[q_key].popleft()
                    print(f"[Matcher] Jam 방지: {q_key}에서 {mid} 제거완료")
                    return True
        return False