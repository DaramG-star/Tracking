import requests

BASE_URL = "http://localhost:3000/api"

def api_scan(uid, route_code):
    """POST /api/scan: 스캐너에서 인식된 택배 등록"""
    try:
        payload = {"uid": uid, "route_code": route_code}
        requests.post(f"{BASE_URL}/scan", json=payload, timeout=2)
    except Exception as e:
        print(f"API Error (Scan): {e}")

def api_update_position(uid, cam_id):
    """PATCH /api/detect-position: 현재 카메라 위치 전송"""
    # 프론트엔드 표시 기준(10m~0m)에 맞춘 카메라별 가상 거리
    pos_map = {"USB_LOCAL": 10.0, "RPI_USB1": 7.5, "RPI_USB2": 5.0, "RPI_USB3": 2.5}
    pos = pos_map.get(cam_id, 0.0)
    try:
        requests.patch(f"{BASE_URL}/detect-position", json={"uid": uid, "position": pos}, timeout=2)
    except Exception as e:
        print(f"API Error (Position): {e}")

def api_pickup(uid):
    """PATCH /api/detect-pickup: 정상 픽업 처리"""
    try:
        requests.patch(f"{BASE_URL}/detect-pickup", json={"uid": uid, "received": True}, timeout=2)
    except Exception as e:
        print(f"API Error (Pickup): {e}")

def api_missing(uid):
    """PATCH /api/detect-missing: 누락(오분류) 처리"""
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