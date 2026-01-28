from pathlib import Path

# 경로 설정
MODEL_PATH = r"C:\Users\User\Desktop\paymentinapp\parcel_ver0123.pt"
BASE_DIR = Path(r"C:\Users\User\Desktop\paymentinapp\0127Tracking")
OUT_DIR = Path("Parcel_Integration_Log_FIFO")
VIDEO_DIR = OUT_DIR / "videos"

# 카메라별 물리적 파라미터 및 ROI
CAM_SETTINGS = {
    "USB_LOCAL": {
        "path": BASE_DIR / "usb_usb_local" / "images",
        "roi_y": 750, "roi_margin": 100,
        "dist_eps": 60, "max_dy": 120,
        "forward_sign": -1,
        "parts": [("081952_961", "082029_085")]
    },
    "RPI_USB1": {
        "path": BASE_DIR / "rpi_rpi1_usb1" / "images",
        "roi_y": 400, "roi_margin": 30,
        "dist_eps": 35, "max_dy": 80,
        "forward_sign": 1,
        "parts": [("082008_660", "082046_675")]
    },
    "RPI_USB2": {
        "path": BASE_DIR / "rpi_rpi1_usb2" / "images",
        "roi_y": 160, "roi_margin": 30,
        "dist_eps": 35, "max_dy": 80,
        "forward_sign": -1,
        "parts": [("082018_216", "082054_026")]
    },
    "RPI_USB3": {
        "path": BASE_DIR / "rpi_rpi2_usb3" / "images",
        "roi_y": 400, "roi_margin": 40,
        "eol_y": 690, "eol_margin": 30,
        "dist_eps": 35, "max_dy": 80,
        "forward_sign": 1,
        "parts": [("082028_591", "082107_610")]
    }
}

# 이동 시간 및 허용 오차
AVG_TRAVEL = {
    ('USB_LOCAL', 'RPI_USB1'): 16.06,
    ('RPI_USB1', 'RPI_USB2'): 9.8,
    ('RPI_USB2', 'RPI_USB3'): 9.1,
    ('RPI_USB3', 'RPI_USB3_EOL'): 3.5
}

TIME_MARGIN = {
    ('USB_LOCAL', 'RPI_USB1'): 1.0,
    ('RPI_USB1', 'RPI_USB2'): 1.0,
    ('RPI_USB2', 'RPI_USB3'): 1.0,
    ('RPI_USB3', 'RPI_USB3_EOL'): 1.5
}