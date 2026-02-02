#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
USB_LOCAL 웹캠만 사용해 트래킹 로직 검증용 스크립트.
zMQ/SharedMemory 없이 cv2.VideoCapture(0)로 프레임을 받아,
YOLODetector + FIFOGlobalMatcher + ScannerListener + api_helper 동작을 확인한다.
"""

import sys
import time
import logging
from pathlib import Path

_apsr_root = Path(__file__).resolve().parent.parent
if str(_apsr_root) not in sys.path:
    sys.path.insert(0, str(_apsr_root))

import cv2

from Tracking import config
from Tracking import api_helper
from Tracking.matcher import FIFOGlobalMatcher
from Tracking.detector import YOLODetector
from Tracking.scanner_listener import ScannerListener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# True: API 호출 대신 로그만 출력 (서버 부담 없이 로직 검증)
TEST_MODE = True

# 카메라 인덱스 (0 = 기본 웹캠). config.USB_LOCAL에 device 없으면 0 사용
CAM_INDEX = 0


def draw_roi_lines(frame, roi_top, roi_bot, color=(0, 255, 255), thickness=2):
    """ROI 상/하단을 화면에 가로선으로 그린다."""
    h, w = frame.shape[:2]
    cv2.line(frame, (0, roi_top), (w, roi_top), color, thickness)
    cv2.line(frame, (0, roi_bot), (w, roi_bot), color, thickness)
    cv2.putText(
        frame, f"ROI {roi_top}-{roi_bot}",
        (10, roi_top - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1,
    )


def main():
    print(f"[USB_LOCAL] TEST_MODE={TEST_MODE} (True=API 호출 생략, False=실제 API 호출)")
    cam_id = "USB_LOCAL"
    cfg = config.CAM_SETTINGS[cam_id]
    roi_top = cfg["roi_y"] - cfg["roi_margin"]
    roi_bot = cfg["roi_y"] + cfg["roi_margin"]

    config.OUT_DIR.mkdir(exist_ok=True)

    detector = YOLODetector(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    scanner_listener = ScannerListener(matcher, host="192.168.1.200", port=3000)
    scanner_listener.start()
    logger.info("ScannerListener started (192.168.1.200:3000)")

    print("[USB_LOCAL] q_scan에 스캐너 데이터가 들어올 때까지 대기 중...")
    while len(matcher.queues["q_scan"]) == 0:
        time.sleep(0.1)
    print(f"[USB_LOCAL] 스캐너 데이터 수신: {list(matcher.queues['q_scan'])}")

    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        logger.error("VideoCapture(%s) 실패. 카메라 연결을 확인하세요.", CAM_INDEX)
        return
    logger.info("VideoCapture(%s) 열림, USB_LOCAL 트래킹 루프 시작 (q 종료)", CAM_INDEX)

    active_tracks = {}
    local_uid_counter = 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            time_s = time.time()
            detections = detector.get_detections(frame, cfg, cam_id)

            # ---- 시각화: ROI 영역 선 ----
            disp = frame.copy()
            draw_roi_lines(disp, roi_top, roi_bot)

            if detections:
                print(f"[탐지] USB_LOCAL에서 {len(detections)}개 객체 탐지 (ROI/EOL 내)")

            new_active = {}
            for det in detections:
                x1, y1, x2, y2 = det["box"]
                cx, cy = det["center"]

                if det.get("in_roi"):
                    print(f"[ROI] 객체가 ROI 영역 통과 (center_y={cy:.0f}, roi_top={roi_top}, roi_bot={roi_bot})")

                best_uid, best_score = None, 1e9
                for uid, info in active_tracks.items():
                    dx = abs(cx - info["last_pos"][0])
                    dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                    if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]:
                        continue
                    score = dx + dy * 0.3
                    if score < best_score:
                        best_uid, best_score = uid, score

                route, mid, event_type = "UNKNOWN", None, "UNMATCHED"

                if best_uid:
                    mid = active_tracks[best_uid]["master_id"]
                    if mid and mid in matcher.masters:
                        route = matcher.masters[mid]["route_code"]
                        if matcher.masters[mid]["status"] == "MISSING":
                            continue
                        event_type = "TRACKING"
                else:
                    local_uid_counter += 1
                    best_uid = f"{cam_id}_{local_uid_counter:03d}"
                    mid = matcher.try_match(cam_id, time_s, det["width"], best_uid)

                    if mid and mid in matcher.masters:
                        route = matcher.masters[mid]["route_code"]
                        if (route == "XSEA" and cam_id == "RPI_USB3") or (
                            route == "XSEB" and cam_id == "RPI_USB3_EOL"
                        ):
                            if matcher.masters[mid]["status"] != "MISSING":
                                matcher.masters[mid]["status"] = "MISSING"
                                if TEST_MODE:
                                    print(f"[TEST_MODE] would api_missing(mid={mid})")
                                else:
                                    api_helper.api_missing(mid)
                            event_type = "MISSING"
                        else:
                            matcher.masters[mid]["status"] = "TRACKING"
                            if TEST_MODE:
                                print(f"[MATCHED] mid={mid}, route={route} -> would api_update_position(mid={mid}, pos={cfg['dist']})")
                            else:
                                api_helper.api_update_position(mid, cfg["dist"])
                                print(f"[MATCHED] mid={mid}, route={route} -> api_update_position 호출됨")
                            event_type = "MATCHED"

                color = (0, 255, 0) if event_type == "MATCHED" else (0, 255, 255) if event_type == "TRACKING" else (0, 0, 255)
                label = f"{event_type} {mid or best_uid}"
                cv2.rectangle(disp, (x1, y1), (x2, y2), color, 2)
                cv2.putText(disp, label, (x1, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

                if event_type != "MISSING":
                    new_active[best_uid] = {"last_pos": (cx, cy), "master_id": mid}

            for old_uid, old_info in active_tracks.items():
                if old_uid not in new_active:
                    mid = old_info["master_id"]
                    if mid and mid in matcher.masters and matcher.masters[mid]["status"] == "TRACKING":
                        matcher.masters[mid]["status"] = "PENDING"
                        matcher.masters[mid]["pending_from_cam"] = cam_id

            for mid in list(matcher.masters.keys()):
                result = matcher.resolve_pending(mid, time_s)
                if result:
                    decision = result["decision"]
                    if decision == "PICKUP":
                        if TEST_MODE:
                            print(f"[TEST_MODE] would api_pickup(mid={mid})")
                        else:
                            api_helper.api_pickup(mid)
                            print(f"[PICKUP] mid={mid} -> api_pickup 호출됨")

            active_tracks = new_active

            cv2.imshow("USB_LOCAL Tracking Test", disp)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

            time.sleep(0.02)

    except KeyboardInterrupt:
        print("\nCtrl+C로 종료")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        try:
            scanner_listener.stop()
        except Exception:
            pass
        logger.info("USB_LOCAL 테스트 종료 (TEST_MODE=%s)", TEST_MODE)


if __name__ == "__main__":
    main()
