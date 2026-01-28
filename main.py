import cv2
import csv
from ultralytics import YOLO

import config
import api_helper
from utils import VideoManager
from matcher import FIFOGlobalMatcher
from loader import get_sorted_frames


# ------------------------------
# 단계 정의 유틸
# ------------------------------
def get_next_cam(route, cam):
    if route == "XSEA":
        order = ["USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3"]
    else:  # XSEB
        order = ["USB_LOCAL", "RPI_USB1", "RPI_USB2", "RPI_USB3", "RPI_USB3_EOL"]

    if cam not in order:
        return None
    idx = order.index(cam)
    return order[idx + 1] if idx + 1 < len(order) else None


def resolve_pending(matcher, mid, now_s):
    info = matcher.masters[mid]

    # ❗ Missing 은 여기 오면 안 됨
    if info["status"] != "PENDING":
        return None

    route = info["route_code"]
    from_cam = info["pending_from_cam"]

    next_cam = get_next_cam(route, from_cam)
    if not next_cam:
        return None

    key = (from_cam, next_cam)
    if key not in config.AVG_TRAVEL:
        return None

    expected = (
        info["last_time"]
        + config.AVG_TRAVEL[key]
        + config.TIME_MARGIN[key]
    )

    if now_s < expected:
        return None

    if route == "XSEA":
        decision = "PICKUP" if next_cam in ["RPI_USB2", "RPI_USB3"] else "DISAPPEAR"
    elif route == "XSEB":
        decision = "PICKUP" if next_cam == "RPI_USB3_EOL" else "DISAPPEAR"
    else:
        return None

    return {
        "decision": decision,
        "from_cam": from_cam,
        "next_cam": next_cam,
        "expected": expected
    }


# ------------------------------
# main
# ------------------------------
def main():
    config.OUT_DIR.mkdir(exist_ok=True)
    config.VIDEO_DIR.mkdir(exist_ok=True)
    config.CROP_DIR.mkdir(exist_ok=True)

    model = YOLO(config.MODEL_PATH)
    matcher = FIFOGlobalMatcher()
    video_mgr = VideoManager(config.VIDEO_DIR)

    csv_header = [
        'timestamp', 'cam', 'local_uid',
        'master_id', 'route', 'x1', 'y1', 'x2', 'y2', 'event'
    ]

    debug_header = [
        "timestamp", "master_id", "route",
        "from_cam", "next_cam",
        "last_seen_time", "expected_time",
        "now_time", "delay_sec", "decision"
    ]

    all_frames = get_sorted_frames()
    active_tracks = {cam: {} for cam in config.CAM_SETTINGS}
    local_uid_counter = {cam: 0 for cam in config.CAM_SETTINGS}

    debug_f = open(config.OUT_DIR / "debug_pending.csv", "w", newline="", encoding="utf-8")
    debug_writer = csv.DictWriter(debug_f, fieldnames=debug_header)
    debug_writer.writeheader()

    with open(config.LOG_CSV, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=csv_header)
        writer.writeheader()

        for frame in all_frames:
            cam = frame["cam"]
            cfg = config.CAM_SETTINGS[cam]
            img = cv2.imread(str(frame["path"]))
            if img is None:
                continue

            disp = img.copy()
            video_mgr.init_writer(cam, img.shape)

            roi_top = cfg["roi_y"] - cfg["roi_margin"]
            roi_bot = cfg["roi_y"] + cfg["roi_margin"]

            eol_top = eol_bot = None
            if cam == "RPI_USB3":
                eol_top = cfg["eol_y"] - cfg["eol_margin"]
                eol_bot = cfg["eol_y"] + cfg["eol_margin"]

            results = model(img, conf=0.25, verbose=False)[0]
            new_active = {}

            # ---------------- detection ----------------
            for b in results.boxes:
                x1, y1, x2, y2 = map(int, b.xyxy[0])
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

                in_roi = roi_top < cy < roi_bot
                in_eol = cam == "RPI_USB3" and eol_top < cy < eol_bot if eol_top else False
                if not (in_roi or in_eol):
                    continue

                best_uid, best_score = None, 1e9
                for uid, info in active_tracks[cam].items():
                    dx = abs(cx - info["last_pos"][0])
                    dy = (cy - info["last_pos"][1]) * cfg["forward_sign"]
                    if dx > cfg["dist_eps"] or dy < -5 or dy > cfg["max_dy"]:
                        continue
                    score = dx + dy * 0.3
                    if score < best_score:
                        best_uid, best_score = uid, score

                route = "UNKNOWN"
                mid = None
                event_type = "UNMATCHED"

                if best_uid:
                    prev_mid = active_tracks[cam][best_uid]["master_id"]
                    if prev_mid and prev_mid in matcher.masters:
                        mid = prev_mid
                        route = matcher.masters[mid]["route_code"]

                        # 🔴 이미 Missing이면 아무것도 안 함
                        if matcher.masters[mid]["status"] == "MISSING":
                            continue

                        event_type = "TRACKING"

                else:
                    local_uid_counter[cam] += 1
                    best_uid = f"{cam}_{local_uid_counter[cam]:03d}"
                    match_cam = "RPI_USB3_EOL" if in_eol else cam

                    s_data = None
                    if cam == "USB_LOCAL":
                        s_data = {"uid": f"PKG_{frame['ts']}", "route_code": "XSEA"}
                        api_helper.api_scan(s_data["uid"], s_data["route_code"])

                    tmp_mid = matcher.try_match(
                        match_cam,
                        frame["time_s"],
                        (x2 - x1),
                        best_uid,
                        s_data
                    )

                    if tmp_mid and tmp_mid in matcher.masters:
                        mid = tmp_mid
                        route = matcher.masters[mid]["route_code"]

                        # 🔥 Missing 즉시 판정 🔥
                        if route == "XSEA" and cam == "RPI_USB3":

                            # ✅ 이미 missing이면 재호출 금지
                            if matcher.masters[mid]["status"] == "MISSING":
                                continue

                            matcher.masters[mid]["status"] = "MISSING"
                            api_helper.api_missing(mid)
                            event_type = "MISSING"

                        elif route == "XSEB" and match_cam == "RPI_USB3_EOL":

                            if matcher.masters[mid]["status"] == "MISSING":
                                continue

                            matcher.masters[mid]["status"] = "MISSING"
                            api_helper.api_missing(mid)
                            event_type = "MISSING"


                        else:
                            matcher.masters[mid]["status"] = "TRACKING"
                            api_helper.api_update_position(mid, cfg["dist"])
                            event_type = "MATCHED"

                writer.writerow({
                    'timestamp': frame['ts'], 'cam': cam,
                    'local_uid': best_uid, 'master_id': mid,
                    'route': route, 'x1': x1, 'y1': y1,
                    'x2': x2, 'y2': y2, 'event': event_type
                })

                if event_type != "MISSING":
                    new_active[best_uid] = {
                        "last_pos": (cx, cy),
                        "master_id": mid
                    }

            # -------- disappearance → PENDING --------
            for old_uid, old_info in active_tracks[cam].items():
                if old_uid in new_active:
                    continue

                mid = old_info["master_id"]
                if not mid or mid not in matcher.masters:
                    continue

                info = matcher.masters[mid]
                if info["status"] != "TRACKING":
                    continue

                info["status"] = "PENDING"
                info["pending_from_cam"] = cam

            # -------- resolve pending --------
            for mid, info in matcher.masters.items():
                result = resolve_pending(matcher, mid, frame["time_s"])
                if not result:
                    continue

                decision = result["decision"]
                info["status"] = decision

                if decision == "PICKUP":
                    api_helper.api_pickup(mid)

                    writer.writerow({
                        'timestamp': frame['ts'],
                        'cam': info["pending_from_cam"],
                        'local_uid': "",
                        'master_id': mid,
                        'route': info["route_code"],
                        'event': "PICKUP"
                    })

                debug_writer.writerow({
                    "timestamp": frame["ts"],
                    "master_id": mid,
                    "route": info["route_code"],
                    "from_cam": result["from_cam"],
                    "next_cam": result["next_cam"],
                    "last_seen_time": info["last_time"],
                    "expected_time": round(result["expected"], 3),
                    "now_time": round(frame["time_s"], 3),
                    "delay_sec": round(frame["time_s"] - result["expected"], 3),
                    "decision": decision
                })

            active_tracks[cam] = new_active
            video_mgr.write_frame(cam, disp)

    debug_f.close()
    video_mgr.release_all()


if __name__ == "__main__":
    main()
