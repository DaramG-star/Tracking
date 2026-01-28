import config
import utils

def get_sorted_frames():
    all_frames = []
    for cam_id, cfg in config.CAM_SETTINGS.items():
        for start_t, end_t in cfg["parts"]:
            for p in cfg["path"].glob("*.jpg"):
                ts = utils.extract_ts(p.name)
                if ts and start_t <= ts <= end_t:
                    all_frames.append({
                        "cam": cam_id,
                        "path": p,
                        "ts": ts,
                        "time_s": utils.ts_to_seconds(ts)
                    })
    all_frames.sort(key=lambda x: x["time_s"])
    return all_frames