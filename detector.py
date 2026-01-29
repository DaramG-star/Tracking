# detector.py
import cv2
from ultralytics import YOLO
import config

class YOLODetector:
    def __init__(self, model_path):
        # 모델 로드 (config에서 경로를 가져오도록 기본값 설정 가능)
        self.model = YOLO(model_path)

    def get_detections(self, img, cam_cfg, cam_id):
        """
        이미지에서 객체를 탐지하고 ROI/EOL 영역에 있는 것들만 필터링하여 반환합니다.
        """
        # 1. YOLO 추론 (conf, iou 등 파라미터를 조절하여 정확도 향상 가능)
        results = self.model(img, conf=0.25, iou=0.45, verbose=False)[0]
        
        # 2. 카메라별 ROI/EOL 설정값 계산
        roi_top = cam_cfg["roi_y"] - cam_cfg["roi_margin"]
        roi_bot = cam_cfg["roi_y"] + cam_cfg["roi_margin"]
        
        # EOL 영역 계산 (RPI_USB3 전용)
        eol_top = eol_bot = None
        if cam_id == "RPI_USB3":
            eol_top = cam_cfg.get("eol_y", 0) - cam_cfg.get("eol_margin", 0)
            eol_bot = cam_cfg.get("eol_y", 0) + cam_cfg.get("eol_margin", 0)

        filtered_detections = []

        # 3. 탐지 결과 필터링
        for b in results.boxes:
            x1, y1, x2, y2 = map(int, b.xyxy[0])
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2

            # 영역 판정
            in_roi = roi_top < cy < roi_bot
            in_eol = (cam_id == "RPI_USB3" and eol_top < cy < eol_bot) if eol_top is not None else False

        
            if in_roi or in_eol:
                filtered_detections.append({
                    "box": (x1, y1, x2, y2),
                    "center": (cx, cy),
                    "in_roi": in_roi,    
                    "in_eol": in_eol,    
                    "width": (x2 - x1)
                })

        return filtered_detections