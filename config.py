from pathlib import Path

# 1. 기본 경로 설정 
MODEL_PATH = r"/home/piapp/apsr/trackingLogic/Tracking_test1/parcel_ver0123.pt"
BASE_DIR = Path(r"/home/piapp/apsr/trackingLogic/Tracking_test1/0127Tracking")
OUT_DIR = Path("Parcel_Integration_Log_FIFO")
VIDEO_DIR = OUT_DIR / "videos"
CROP_DIR = OUT_DIR / "crops"           # 탐지된 객체 이미지 저장 폴더
LOG_CSV = OUT_DIR / "tracking_logs.csv" # 전체 트래킹 히스토리 저장 파일
# ----------------------------------

# 2. 카메라별 물리적 파라미터 및 ROI
CAM_SETTINGS = {
    "Scanner": {"dist": -2.3},
    "USB_LOCAL": {
        "path": BASE_DIR / "usb_usb_local" / "images",
        "roi_y": 750, "roi_margin": 100,
        "dist_eps": 60, "max_dy": 120,
        "forward_sign": -1,
        "dist": 0.0,  
        "parts": [("081952_961", "082029_085")]
    },
    "RPI_USB1": {
        "path": BASE_DIR / "rpi_rpi1_usb1" / "images",
        "roi_y": 400, "roi_margin": 30,
        "dist_eps": 35, "max_dy": 80,
        "forward_sign": 1,
        "dist": 5.88,  
        "parts": [("082008_660", "082046_675")]
    },
    "RPI_USB2": {
        "path": BASE_DIR / "rpi_rpi1_usb2" / "images",
        "roi_y": 160, "roi_margin": 30,
        "dist_eps": 35, "max_dy": 80,
        "forward_sign": -1,
        "dist": 9.47,  
        "parts": [("082018_216", "082054_026")]
    },
    "RPI_USB3": {
        "path": BASE_DIR / "rpi_rpi2_usb3" / "images",
        "roi_y": 400, "roi_margin": 40,
        "eol_y": 690, "eol_margin": 30,
        "dist_eps": 35, "max_dy": 80,
        "forward_sign": 1,
        "dist": 12.8,  
        "parts": [("082028_591", "082107_610")]
    }
}

# 3. 이동 시간 및 허용 오차 
AVG_TRAVEL = {
    ('Scanner', 'USB_LOCAL'): 6.5,
    ('USB_LOCAL', 'RPI_USB1'): 16.06,
    ('RPI_USB1', 'RPI_USB2'): 9.8,
    ('RPI_USB2', 'RPI_USB3'): 9.1,
    ('RPI_USB3', 'RPI_USB3_EOL'): 3.5
}

TIME_MARGIN = {
    ('Scanner', 'USB_LOCAL'): 3.0,
    ('USB_LOCAL', 'RPI_USB1'): 1.0,
    ('RPI_USB1', 'RPI_USB2'): 1.0,
    ('RPI_USB2', 'RPI_USB3'): 1.2,
    ('RPI_USB3', 'RPI_USB3_EOL'): 1.5
}

# 실시간 거리 추계용 설정 
BELT_SPEED = 0.366  # m/s (벨트 속도)

# 경로별 시작점(USB_LOCAL)부터 최종 목적지까지의 총 거리
ROUTE_TOTAL_DIST = {
    "XSEA": 11.77,   
    "XSEB": 15.1   
}

SAVE_VIDEO = False
