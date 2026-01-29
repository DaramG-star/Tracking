"""
스캐너 데이터 Socket.io 리스너
MongoDB Change Stream에서 발동된 데이터를 192.168.1.200 서버로부터 받아서
matcher의 q_scan에 추가하는 모듈
"""
import socketio
import json
import threading
import time
import re
from datetime import datetime
from matcher import FIFOGlobalMatcher

# websocket-client가 없어도 polling transport로 연결 가능하지만, websocket이 더 효율적
try:
    import websocket_client
    print("[ScannerListener] websocket-client 패키지 확인됨")
except ImportError:
    print("[ScannerListener] ⚠️  websocket-client 패키지가 없습니다. polling transport만 사용 가능합니다.")
    print("[ScannerListener] 설치 방법: pip install --user websocket-client")


class ScannerListener:
    def __init__(self, matcher: FIFOGlobalMatcher, host="192.168.1.200", port=3000, 
                 max_retry_time=300, retry_interval=5):
        """
        Args:
            matcher: FIFOGlobalMatcher 인스턴스
            host: Socket.io 서버 주소 (기본값: 192.168.1.200)
            port: Socket.io 서버 포트 (기본값: 3000)
            max_retry_time: 최대 재시도 시간(초). 이 시간 동안 연결 실패 시 자동 중지 (기본값: 300초=5분)
                           None이면 무한 재시도
            retry_interval: 재시도 간격(초) (기본값: 5초)
        """
        self.matcher = matcher
        self.host = host
        self.port = port
        self.max_retry_time = max_retry_time
        self.retry_interval = retry_interval
        self.running = False
        self.thread = None
        self.first_retry_time = None
        
        # Socket.io 클라이언트 생성
        # Socket.io는 자동으로 ping/pong 메커니즘으로 연결을 유지합니다
        # 데이터가 없어도 연결이 유지되며, 기본값으로 ping_interval=25초, ping_timeout=60초 사용
        self.sio = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,  # 무한 재시도 (max_retry_time으로 제어)
            reconnection_delay=self.retry_interval,
            reconnection_delay_max=self.retry_interval * 2
        )
        
        # 이벤트 핸들러 등록
        self._register_handlers()
        
    def _register_handlers(self):
        """Socket.io 이벤트 핸들러 등록"""
        
        @self.sio.event
        def connect():
            print(f"[ScannerListener] Socket.io 연결 성공: {self.host}:{self.port}")
            print(f"[ScannerListener] 연결 상태: connected={self.sio.connected}, sid={self.sio.sid}")
            print(f"[ScannerListener] 연결 유지: ping/pong 메커니즘으로 데이터 없어도 연결 유지됨")
            self.first_retry_time = None  # 연결 성공 시 재시도 시간 초기화
        
        @self.sio.event
        def disconnect():
            print("[ScannerListener] Socket.io 연결 끊어짐")
            # 정상 종료인지 확인 (stop() 호출로 인한 끊김인지)
            if not self.running:
                print("[ScannerListener] 정상 종료로 인한 연결 끊김")
            else:
                print("[ScannerListener] ⚠️  예기치 않은 연결 끊김 - 재연결 시도 예정")
                print("[ScannerListener] 가능한 원인: 서버 측 타임아웃, 네트워크 문제, ping 응답 실패")
            if self.first_retry_time is None:
                self.first_retry_time = time.time()
        
        @self.sio.event
        def connect_error(data):
            if self.first_retry_time is None:
                self.first_retry_time = time.time()
            
            # 타임아웃 체크
            if self.max_retry_time is not None:
                elapsed = time.time() - self.first_retry_time
                if elapsed >= self.max_retry_time:
                    print(f"[ScannerListener] ⚠️  {self.max_retry_time}초 동안 연결 실패. 자동 중지합니다.")
                    print(f"[ScannerListener] 연결 시도 시간: {elapsed:.1f}초")
                    self.running = False
                    self.sio.disconnect()
                    return
            
            remaining = self.max_retry_time - (time.time() - self.first_retry_time) if self.max_retry_time else None
            if remaining and remaining > 0:
                print(f"[ScannerListener] 연결 오류: {data}, 재시도 중... (남은 시간: {remaining:.0f}초)")
            else:
                print(f"[ScannerListener] 연결 오류: {data}, 재시도 중...")
        
        # MongoDB Change Stream 데이터를 받는 이벤트 핸들러
        # parcelUpdate 이벤트에서 operationType이 insert일 때만 처리
        @self.sio.on('parcelUpdate')
        def on_parcel_update(data):
            try:
                # operationType이 insert인 경우만 처리
                operation_type = data.get('type') if isinstance(data, dict) else None
                
                if operation_type == 'insert':
                    print(f"\n[ScannerListener] ✅ operationType='insert' → 처리합니다")
                    self._handle_message(data)
                else:
                    # insert가 아닌 경우 로그만 출력
                    if operation_type:
                        print(f"[ScannerListener] ⏭️  operationType '{operation_type}'는 무시됩니다. (insert만 처리)")
                    else:
                        print(f"[ScannerListener] ⚠️  operationType이 없습니다. 데이터: {data}")
            except Exception as e:
                import traceback
                print(f"[ScannerListener] ❌ parcelUpdate 핸들러 오류: {e}")
                traceback.print_exc()
        
    
    def _parse_timestamp(self, ts_str):
        """
        [수정됨] UID 문자열(예: 20260127_081946_617)에서 시간을 추출하여 
        영상 시간과 동일한 '하루 중 초' 단위로 변환합니다.
        """
        try:
            # UID 포맷에서 시간 정보(HHMMSS_mmm) 추출 시도
            m = re.search(r"(?:\d{8}_)?(\d{6}_\d+)", str(ts_str))
            if m:
                ts_part = m.group(1) # '081946_617'
                h = int(ts_part[0:2])
                m_val = int(ts_part[2:4])
                s = int(ts_part[4:6])
                ms = int(ts_part.split('_')[1]) / 1000
                
                # 영상 프레임 시간과 일치하도록 하루 중 총 경과 초로 변환
                return h * 3600 + m_val * 60 + s + ms

            # 다양한 형식 지원 (Fallback)
            if isinstance(ts_str, (int, float)):
                return float(ts_str)
            
            if 'T' in str(ts_str):
                # ISO 형식: "2025-01-28T08:19:52.961"
                dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
                # Epoch가 아닌 하루 중 초로 변환하여 일관성 유지
                return dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1000000
            
            return time.time()
        except Exception as e:
            print(f"[ScannerListener] 타임스탬프 파싱 오류: {e}, 원본: {ts_str}")
            return time.time()
    
    def _handle_message(self, data):
        """받은 메시지를 처리하여 matcher에 추가"""
        try:
            # 데이터가 이미 dict인 경우와 JSON 문자열인 경우 모두 처리
            if isinstance(data, str):
                message = json.loads(data)
            elif isinstance(data, dict):
                message = data
            else:
                return
            
            # MongoDB Change Stream의 data에서 데이터 추출
            data_dict = message.get('data') or message.get('fullDocument') or message
            
            # 필수 필드 확인 (data 내부에서 찾기)
            if isinstance(data_dict, dict):
                uid = data_dict.get('uid') or data_dict.get('_id')
                route_code = data_dict.get('route_code') or data_dict.get('route')
            else:
                uid = None
                route_code = None
            
            # fallback: 최상위 레벨에서 찾기
            if not uid:
                uid = message.get('uid') or message.get('_id') or message.get('id')
            if not route_code:
                route_code = message.get('route_code') or message.get('route')
            
            if not uid or not route_code:
                print(f"[ScannerListener] 필수 필드 누락 (uid 또는 route_code): {message}")
                return
            
            # [수정됨] 외부 DB의 timestamp 필드 대신 UID 자체에서 영상 기준 시간을 계산
            time_s = self._parse_timestamp(uid)
            
            # matcher에 추가
            self.matcher.add_scanner_data(uid, route_code, time_s)
            
            print(f"[ScannerListener] 📦 q_scan 등록 완료: uid={uid}, route={route_code}, time={time_s}")
            
        except Exception as e:
            print(f"\n[ScannerListener] ❌ 메시지 처리 오류: {e}")
            import traceback
            traceback.print_exc()
    
    def _connect_loop(self):
        """연결 루프"""
        url = f"http://{self.host}:{self.port}"
        
        while self.running:
            try:
                if not self.sio.connected:
                    if self.first_retry_time is None:
                        self.first_retry_time = time.time()
                    
                    # 타임아웃 체크
                    if self.max_retry_time is not None:
                        elapsed = time.time() - self.first_retry_time
                        if elapsed >= self.max_retry_time:
                            print(f"[ScannerListener] ⚠️  {self.max_retry_time}초 동안 연결 실패. 자동 중지합니다.")
                            self.running = False
                            break
                    
                    print(f"[ScannerListener] Socket.io 서버 연결 시도: {url}")
                    try:
                        self.sio.connect(
                            url,
                            wait_timeout=10,
                            socketio_path="/socket.io",
                            transports=["websocket", "polling"]
                        )
                        
                        if self.sio.connected:
                            print(f"[ScannerListener] ✅ 연결 성공! sid={self.sio.sid}")
                            self.first_retry_time = None
                        
                    except Exception as e:
                        print(f"[ScannerListener] 연결 실패: {e}, {self.retry_interval}초 후 재시도...")
                        time.sleep(self.retry_interval)
                else:
                    time.sleep(1)
                    
            except Exception as e:
                print(f"[ScannerListener] 연결 루프 오류: {e}")
                time.sleep(self.retry_interval)
    
    def start(self):
        """리스너 시작"""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._connect_loop, daemon=True)
        self.thread.start()
        print(f"[ScannerListener] Socket.io 리스너 시작: {self.host}:{self.port}")
    
    def stop(self):
        """리스너 중지"""
        self.running = False
        if self.sio.connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass
        if self.thread:
            self.thread.join(timeout=2)
        print("[ScannerListener] Socket.io 리스너 중지 완료")