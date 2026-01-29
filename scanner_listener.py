"""
스캐너 데이터 Socket.io 리스너
MongoDB Change Stream에서 발동된 데이터를 192.168.1.200 서버로부터 받아서
matcher의 q_scan에 추가하는 모듈
"""
import socketio
import json
import threading
import time
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
                print("[ScannerListener] 가능한ㄹ 원인: 서버 측 타임아웃, 네트워크 문제, ping 응답 실패")
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
            
            remaining = self.max_retry_time - elapsed if self.max_retry_time else None
            if remaining:
                print(f"[ScannerListener] 연결 오류: {data}, 재시도 중... (남은 시간: {remaining:.0f}초)")
            else:
                print(f"[ScannerListener] 연결 오류: {data}, 재시도 중...")
        
        # MongoDB Change Stream 데이터를 받는 이벤트 핸들러
        # parcelUpdate 이벤트에서 operationType이 insert일 때만 처리
        @self.sio.on('parcelUpdate')
        def on_parcel_update(data):
            try:
                import json
                print(f"\n{'='*80}")
                print(f"[ScannerListener] parcelUpdate 이벤트 수신!")
                print(f"[ScannerListener] 데이터 타입: {type(data)}")
                print(f"[ScannerListener] 원본 데이터:")
                if isinstance(data, dict):
                    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
                else:
                    print(data)
                print(f"{'='*80}\n")
                
                # operationType이 insert인 경우만 처리
                operation_type = data.get('operationType') if isinstance(data, dict) else None
                
                if operation_type == 'insert':
                    print(f"\n[ScannerListener] ✅ operationType='insert' → 처리합니다")
                    print(f"[ScannerListener] 🔄 _handle_message() 호출 직전 - 데이터: {data}")
                    print(f"[ScannerListener] 🔄 _handle_message() 호출 시작...\n")
                    self._handle_message(data)
                    print(f"\n[ScannerListener] ✅ _handle_message() 호출 완료\n")
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
        """타임스탬프 문자열을 초 단위로 변환"""
        try:
            # 다양한 형식 지원
            if isinstance(ts_str, (int, float)):
                return float(ts_str)
            
            if 'T' in str(ts_str):
                # ISO 형식: "2025-01-28T08:19:52.961"
                dt = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
                return dt.timestamp()
            else:
                # 간단한 형식: "081952_961" -> 초 변환
                parts = str(ts_str).split('_')
                if len(parts) >= 2:
                    time_str = parts[0]
                    ms = int(parts[1]) / 1000
                    h = int(time_str[0:2])
                    m = int(time_str[2:4])
                    s = int(time_str[4:6])
                    return h * 3600 + m * 60 + s + ms
            return time.time()
        except Exception as e:
            print(f"[ScannerListener] 타임스탬프 파싱 오류: {e}, 원본: {ts_str}")
            return time.time()
    
    def _handle_message(self, data):
        """받은 메시지를 처리하여 matcher에 추가"""
        print(f"\n{'#'*80}")
        print(f"[ScannerListener] 🚀 _handle_message() 함수 시작!")
        print(f"[ScannerListener] 받은 데이터 타입: {type(data)}")
        print(f"[ScannerListener] 받은 데이터: {data}")
        print(f"{'#'*80}\n")
        
        try:
            import json
            # 데이터가 이미 dict인 경우와 JSON 문자열인 경우 모두 처리
            if isinstance(data, str):
                message = json.loads(data)
            elif isinstance(data, dict):
                message = data
            else:
                print(f"[ScannerListener] 알 수 없는 데이터 형식: {type(data)}")
                return
            
            print(f"\n[ScannerListener] _handle_message 호출됨")
            print(f"[ScannerListener] 파싱된 메시지:")
            print(json.dumps(message, indent=2, ensure_ascii=False, default=str))
            
            # MongoDB Change Stream의 fullDocument에서 데이터 추출
            # Change Stream 형식: { operationType: 'insert', fullDocument: { ... } }
            full_document = message.get('fullDocument') or message
            print(f"\n[ScannerListener] fullDocument 추출:")
            if isinstance(full_document, dict):
                print(json.dumps(full_document, indent=2, ensure_ascii=False, default=str))
            else:
                print(full_document)
            
            # 필수 필드 확인 (fullDocument 내부 또는 최상위 레벨에서 찾기)
            uid = (full_document.get('uid') or full_document.get('_id') or 
                   message.get('uid') or message.get('_id') or message.get('id'))
            
            route_code = (full_document.get('route_code') or full_document.get('route') or
                         message.get('route_code') or message.get('route'))
            
            # 타임스탬프는 fullDocument나 message에서 찾기
            timestamp = (full_document.get('timestamp') or full_document.get('time') or 
                        full_document.get('created_at') or full_document.get('createdAt') or
                        message.get('timestamp') or message.get('time') or 
                        message.get('created_at') or message.get('createdAt'))
            
            if not uid or not route_code:
                print(f"[ScannerListener] 필수 필드 누락 (uid 또는 route_code): {message}")
                return
            
            # 타임스탬프 변환 (없으면 현재 시간 사용)
            if timestamp:
                time_s = self._parse_timestamp(timestamp)
            else:
                time_s = time.time()
                print(f"[ScannerListener] 타임스탬프 없음, 현재 시간 사용: {time_s}")
            
            # matcher에 추가
            print(f"\n{'='*80}")
            print(f"[ScannerListener] ⚡ add_scanner_data() 호출 직전")
            print(f"  - uid: {uid}")
            print(f"  - route_code: {route_code}")
            print(f"  - time_s: {time_s}")
            print(f"{'='*80}\n")
            
            self.matcher.add_scanner_data(uid, route_code, time_s)
            
            # q_scan 상태 출력 (반드시 실행되도록)
            q_scan = self.matcher.queues["q_scan"]
            print(f"\n{'='*80}")
            print(f"[ScannerListener] 📦 q_scan 상태 (ChangeStream 수신 후):")
            print(f"  - q_scan 크기: {len(q_scan)}개")
            print(f"  - q_scan 내용: {list(q_scan)}")
            print(f"  - q_scan 타입: {type(q_scan)}")
            print(f"[ScannerListener] 스캐너 데이터 처리 완료: uid={uid}, route={route_code}, time={time_s}")
            print(f"{'='*80}\n")
            
        except json.JSONDecodeError as e:
            print(f"\n[ScannerListener] ❌ JSON 파싱 오류: {e}")
            print(f"[ScannerListener] 데이터: {data}")
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"\n[ScannerListener] ❌ 메시지 처리 오류: {e}")
            print(f"[ScannerListener] 데이터: {data}")
            import traceback
            traceback.print_exc()
        finally:
            print(f"[ScannerListener] _handle_message() 함수 종료\n")
    
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
                        # socketio_path와 transports는 connect() 메서드에 전달
                        self.sio.connect(
                            url,
                            wait_timeout=10,
                            socketio_path="/socket.io",  # FastAPI에서 사용하는 기본 path
                            transports=["websocket", "polling"]
                        )
                        
                        # 연결 성공 확인
                        if self.sio.connected:
                            print(f"[ScannerListener] ✅ 연결 성공! connected={self.sio.connected}, sid={self.sio.sid}")
                        else:
                            print(f"[ScannerListener] ⚠️  연결 시도했지만 connected=False 상태입니다.")
                            time.sleep(self.retry_interval)
                            continue
                            
                    except socketio.exceptions.ConnectionError as e:
                        print(f"[ScannerListener] ❌ 연결 오류 (ConnectionError): {e}")
                        remaining = self.max_retry_time - (time.time() - self.first_retry_time) if self.max_retry_time else None
                        if remaining:
                            print(f"[ScannerListener] {self.retry_interval}초 후 재시도... (남은 시간: {remaining:.0f}초)")
                        else:
                            print(f"[ScannerListener] {self.retry_interval}초 후 재시도...")
                        time.sleep(self.retry_interval)
                    except Exception as e:
                        import traceback
                        print(f"[ScannerListener] ❌ 연결 예외 발생:")
                        traceback.print_exc()
                        remaining = self.max_retry_time - (time.time() - self.first_retry_time) if self.max_retry_time else None
                        if remaining:
                            print(f"[ScannerListener] 연결 실패: {e}, {self.retry_interval}초 후 재시도... (남은 시간: {remaining:.0f}초)")
                        else:
                            print(f"[ScannerListener] 연결 실패: {e}, {self.retry_interval}초 후 재시도...")
                        time.sleep(self.retry_interval)
                else:
                    # 연결되어 있으면 대기
                    time.sleep(1)
                    
            except Exception as e:
                print(f"[ScannerListener] 연결 루프 오류: {e}")
                time.sleep(self.retry_interval)
    
    def start(self):
        """리스너 시작"""
        if self.running:
            print("[ScannerListener] 이미 실행 중입니다.")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._connect_loop, daemon=True)
        self.thread.start()
        print(f"[ScannerListener] Socket.io 리스너 시작: {self.host}:{self.port}")
    
    def stop(self):
        """리스너 중지"""
        print("[ScannerListener] 리스너 중지 요청됨...")
        self.running = False
        if self.sio.connected:
            try:
                self.sio.disconnect()
            except Exception as e:
                print(f"[ScannerListener] 연결 종료 중 오류 (무시 가능): {e}")
        if self.thread:
            self.thread.join(timeout=2)
        print("[ScannerListener] Socket.io 리스너 중지 완료")
