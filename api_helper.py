import requests

BASE_URL = "http://192.168.1.200:3000/api"

# detect-missing API 호출 횟수 추적
_missing_api_count = 0

def api_scan(uid, route_code):
    """POST /api/scan: 스캐너에서 인식된 택배 등록"""
    try:
        payload = {"uid": uid, "route_code": route_code}
        requests.post(f"{BASE_URL}/track", json=payload, timeout=2)
    except Exception as e:
        print(f"API Error (Scan): {e}")

def api_update_position(uid, pos):
    """PATCH /api/detect-position: 실시간 남은 거리 전송"""  
    try:
        requests.patch(
            f"{BASE_URL}/detect-position", 
            json={"uid": uid, "position": pos}, 
            timeout=2
        )
    except Exception as e:
        print(f"API Error (Position Update): {e}")

def api_pickup(uid):
    """PATCH /api/detect-pickup: 정상 픽업 처리"""
    try:
        requests.patch(f"{BASE_URL}/detect-pickup", json={"uid": uid, "received": True}, timeout=2)
    except Exception as e:
        print(f"API Error (Pickup): {e}")

def api_missing(uid):
    """PATCH /api/detect-missing: 누락(오분류) 처리"""
    global _missing_api_count
    _missing_api_count += 1
    print(f"[API 호출 #{_missing_api_count}] detect-missing: uid={uid}")
    try:
        requests.patch(f"{BASE_URL}/detect-missing", json={"uid": uid, "missed": True}, timeout=2)
    except Exception as e:
        print(f"API Error (Missing): {e}")

def api_eol(uid):
    """DELETE /api/detect-eol/{uid}: 최종 라인 도달 시 삭제"""
    try:
        requests.delete(f"{BASE_URL}/detect-eol/{uid}", timeout=2)
    except Exception as e:
        print(f"API Error (EOL): {e}")

def get_missing_api_count():
    """detect-missing API 호출 총 횟수 반환"""
    return _missing_api_count