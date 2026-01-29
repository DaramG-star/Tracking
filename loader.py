import config
import utils

def get_sorted_frames():
    """
    config.CAM_SETTINGS에 정의된 카메라별 경로에서 
    설정된 시간 범위(parts) 내의 이미지만 수집하여 시간순으로 정렬해 반환합니다.
    """
    all_frames = []
    
    for cam_id, cfg in config.CAM_SETTINGS.items():
        # 'Scanner'는 이미지 분석 대상이 아닌 데이터 소스이므로 건너뜁니다.
        if cam_id == "Scanner":
            continue
            
        # 설정된 각 영상 구간(parts)에 대해 이미지 수집
        for start_t, end_t in cfg["parts"]:
            # 해당 카메라 경로의 모든 jpg 파일 검사
            for p in cfg["path"].glob("*.jpg"):
                ts = utils.extract_ts(p.name)
                
                # 타임스탬프가 유효하고 설정된 시작/종료 시간 범위 내에 있는 경우만 추가
                if ts and start_t <= ts <= end_t:
                    all_frames.append({
                        "cam": cam_id,
                        "path": p,
                        "ts": ts,
                        "time_s": utils.ts_to_seconds(ts)
                    })
                    
    # 모든 카메라의 프레임을 실제 시간(초) 기준으로 정렬
    all_frames.sort(key=lambda x: x["time_s"])
    
    return all_frames