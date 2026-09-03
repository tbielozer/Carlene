import asyncio
import subprocess
import threading
import time
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from starlette.websockets import WebSocketDisconnect
import json

from gpiozero import DigitalOutputDevice, PWMOutputDevice, RotaryEncoder

from time import sleep

import cv2
import numpy as np
from mapping import VisualMapper

forNum =6
revNum = 5
revRight = 27
leftPin = 22

#GPIO Stuff
for_pin = DigitalOutputDevice(6)
for_pin.off()

rev_pin = DigitalOutputDevice(5)
rev_pin.off()

right_pin = DigitalOutputDevice(27)
right_pin.off()

left_pin = DigitalOutputDevice(22)
left_pin.off()

app = FastAPI()

# Microphone configuration
MIC_SOURCE = "bluez_input.00:6A:8E:0E:E7:32"
CAR_AUDIO_SAMPLE_RATE = 16000  # good enough for voice, keeps bandwidth low

# Rotary encoder
encoder = RotaryEncoder(16, 26)

steering_angle = 0
STEERING_STEP = 1
MAX_STEERING = 45

map_log = open(
    "/home/wifidriver/cam_out.txt",
    "w",
    buffering=1
)

class Camera:
    def __init__(self, camera_id):
        self.camera_id = camera_id

        self.process = None
        self.thread = None

        self.latest_frame = None
        self.lock = threading.Lock()

        self.running = False

    def start(self):
        print(f"Starting persistent camera {self.camera_id}")
        
        cmd = [
            "rpicam-vid",
            "--camera", str(self.camera_id),
            "--width", "640",
            "--height", "360",
            "--framerate", "15",
            "--codec", "mjpeg",
            "--quality", "25",
            "--nopreview",
            "--timeout", "0",
            "--output", "-",
        ]


        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
        )

        self.running = True

        self.thread = threading.Thread(
            target=self._read_frames,
            daemon=True,
        )

        self.thread.start()

        print(
            f"Camera {self.camera_id} started "
            f"(PID {self.process.pid})"
        )

    def _read_frames(self):
        SOI = b"\xff\xd8"
        EOI = b"\xff\xd9"

        buffer = bytearray()

        while self.running and self.process:

            chunk = self.process.stdout.read(16 * 1024)

            if not chunk:
                print(
                    f"Camera {self.camera_id} stopped producing data"
                )
                break

            buffer.extend(chunk)

            while True:

                start = buffer.find(SOI)

                if start < 0:
                    if len(buffer) > 1:
                        del buffer[:-1]
                    break

                end = buffer.find(EOI, start + 2)

                if end < 0:
                    if start > 0:
                        del buffer[:start]
                    break

                jpg = bytes(buffer[start:end + 2])

                del buffer[:end + 2]

                with self.lock:
                    self.latest_frame = jpg

    def get_frame(self):
        with self.lock:
            return self.latest_frame

    def stop(self):
        print(f"Stopping persistent camera {self.camera_id}")

        self.running = False

        if self.process and self.process.poll() is None:

            self.process.terminate()

            try:
                self.process.wait(timeout=2)

            except subprocess.TimeoutExpired:
                print(
                    f"Camera {self.camera_id} did not stop, killing"
                )

                self.process.kill()
                self.process.wait()

        if self.thread:
            self.thread.join(timeout=2)

        print(f"Camera {self.camera_id} stopped")
        
camera0 = Camera(0)
camera1 = Camera(1)
mapper = VisualMapper()

mapper_thread = None
mapping_stop = threading.Event()
def mapping_loop():

    print("Mapping thread started")

    while not mapping_stop.is_set():

        jpg = camera1.get_frame()

        if jpg is None:
            time.sleep(0.1)
            continue

        # Convert JPEG bytes into an OpenCV image
        frame = cv2.imdecode(
            np.frombuffer(jpg, dtype=np.uint8),
            cv2.IMREAD_COLOR
        )

        if frame is None:
            continue

        # Give frame to mapper
        mapper.process_frame(frame)

        #print(
        #    f"Mapper position: "
        #    f"X={mapper.x:.3f}, "
        #    f"Y={mapper.y:.3f}"
        #)
        print(
            f"{mapper.x:.3f},{mapper.y:.3f}",
            file=map_log
        )

        # Don't run mapping at 30 FPS initially
        mapping_stop.wait(0.1)

@app.on_event("startup")
async def startup_event():
    camera0.start()
    camera1.start()
    
    global mapper_thread

    mapper_thread = threading.Thread(
        target=mapping_loop,
        daemon=True
    )

    mapper_thread.start()

    print("Mapper started")
    
    encoder_thread = threading.Thread(
        target=encoder_loop,
        daemon=True
    )

    encoder_thread.start()
    print("Encoder started")
    
@app.on_event("shutdown")
async def shutdown_event():
    camera0.stop()
    camera1.stop()    
    mapping_stop.set()

    global mapper_thread

    if mapper_thread:
        mapper_thread.join(timeout=2)
        mapper_thread = None

    camera0.stop()
    camera1.stop()

    map_log.close()

    print("=== SHUTDOWN COMPLETE ===")

@app.get("/car-logo")
async def car_logo():
    return FileResponse("/home/wifidriver/Downloads/shopr8v3.png")

INDEX_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Child's Car</title>
    <style>
      body { 
        font-family: sans-serif; 
        background-color: navy;
      }
      img { width: 100%; max-width: 480px; height: auto; }
      pre { background: #111; color: #0f0; padding: 10px; }
      
      /* Cameras Side By Side */
      .camera-grid {
        display: flex;
        flex-direction: row;
        gap: 20px;
        width: 100%;
        margin: 0 auto;
      }

      .camera {
        flex: 1 1 0;
        min-width: 0;
      }

      .camera h3 {
        margin: 8px 0 8px 0;
        color: tan;
        font-size: 18px;
      }

		.camera img {
			display: block;
			width: 50%;
			max-width: none;
			height: auto;
			margin: 0 auto;
			border-radius: 6px;
		}
      
      .drive-status {
        width: 400px;
        padding: 20px;
        margin: 20px auto;
        background: tan;
        color: navy;
        border: 2px solid navy;
        border-radius: 12px;
        font-family: monospace;
        font-size: 24px;
        font-weight: bold;
        text-align: center;
        box-shadow: 0 0 15px rgba(0, 255, 136, 0.25);
      }
            
      .car-header {
        display: flex;
        justify-content: space-between;
        align-items: center;

        width: 100%;
        box-sizing: border-box;
        padding: 20px;

        background: white;
        border-radius: 12px;
        min-height: 150px;
      }

      .car-header h2 {
        margin: 0;
        color: navy;
        font-size: 30px;
      }

      .car-header img {
        width: 200px;
        height: 150px;
        object-fit: contain;
      }
      
      /* =========================
   MINI CAR
   Car faces RIGHT →
   ========================= */

    #car {
        position: relative;

        width: 300px;
        height: 180px;

        margin: 30px auto;

        /* Just so you can see the area */
        /* background: rgba(255, 255, 255, 0.08); */

        border-radius: 15px;
    }


    /* =========================
       CAR BODY
       ========================= */

    #car .body {
        position: absolute;

        left: 45px;
        top: 40px;

        width: 210px;
        height: 100px;

        background: #d32f2f;

        border-radius: 20px 35px 35px 20px;

        box-shadow:
            0 4px 8px rgba(0,0,0,0.4);
    }

    /* =========================
       WHEELS
       ========================= */

    #car .wheel {
        position: absolute;

        width: 40px;
        height: 20px;

        background: #111;

        border-radius: 6px;

        box-shadow:
            inset 0 0 0 3px #333,
            0 2px 4px rgba(0,0,0,0.5);
    }


    /* =========================
       REAR WHEELS
       ========================= */

    #car .rear-left {
        position: absolute;

        left: 60px;
        top: 30px;
    }

    #car .rear-right {
        position: absolute;

        left: 60px;
        top: 130px;
    }


    /* =========================
       FRONT WHEEL PIVOTS
       ========================= */

    #car .wheel-pivot {
        position: absolute;

        width: 18px;
        height: 50px;

        transform-origin:  20px 10px;

        z-index: 5;
    }


    /* Front = RIGHT side of car */

    #car .front-left {
        left: 185px;
        top: 30px;
    }

    #car .front-right {
        left: 185px;
        top: 130px;
    }
    </style>
  </head>
  <body>
    <div class="car-header">
      <h2>Carlene</h2>
      <div id="driveStatus" class="drive-status">STOP</div>
      <button id="carAudioBtn">🔊 Listen to car</button>
      <img src="/car-logo" />
    </div>
    

    <div class="camera-grid">      
      <div class="camera">
        <h3>Forward Feed</h3>
        <img src="/video_feed/1" />
      </div>
    </div>
    
    <div id="car">
        <div class="body"></div>

        <div class="wheel-pivot front-left">
            <div class="wheel"></div>
        </div>

        <div class="wheel-pivot front-right">
            <div class="wheel"></div>
        </div>

        <div class="wheel rear-left"></div>
        <div class="wheel rear-right"></div>
    </div>
    

    <pre id="log"></pre>
      <script>
      const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
      const log = document.getElementById('log');
        const wsUrl = `${wsProtocol}//${location.host}/ws`;

      console.log("Connecting to:", wsUrl);
      log.textContent += "Connecting to " + wsUrl + "\\n";

      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        console.log("=== WEBSOCKET CONNECTED ===");
        log.textContent += "WebSocket connected\\n";
      };

      ws.onmessage = (ev) => {
        console.log("RX:", ev.data);
        log.textContent += "RX: " + ev.data + "\\n";
        
        const data = JSON.parse(ev.data);

        const angle = data.steering;

        document.querySelector(".front-left")
            .style.transform = `rotate(${angle}deg)`;

        document.querySelector(".front-right")
            .style.transform = `rotate(${angle}deg)`;
      };

      ws.onclose = (ev) => {
        console.log("=== WEBSOCKET CLOSED ===", ev.code, ev.reason);
        log.textContent += "WebSocket closed: " + ev.code + "\\n";
      };

      ws.onerror = (ev) => {
        console.error("=== WEBSOCKET ERROR ===", ev);
        log.textContent += "WebSocket error\\n";
      };
      
      //Audio functions
      let audioStream = null;
      let audioWs = null;
      let mediaRecorder = null;
      let audioStreaming = false;

        async function startAudio() {
            if (audioStreaming) return;
            audioStreaming = true;   // set immediately, before any await
            console.log("START AUDIO");
            try {
                const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
                audioWs = new WebSocket(`${wsProtocol}//${location.host}/audio`);
                audioWs.binaryType = "arraybuffer";

                audioWs.onopen = async () => {
                    try {
                        audioStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
                        mediaRecorder = new MediaRecorder(audioStream, { mimeType: "audio/webm;codecs=opus" });
                        mediaRecorder.ondataavailable = (event) => {
                            if (event.data.size > 0 && audioWs.readyState === WebSocket.OPEN) {
                                audioWs.send(event.data);
                            }
                        };
                        mediaRecorder.start(100);
                    } catch (err) {
                        console.error("MICROPHONE ERROR:", err.name, err.message);
                        audioStreaming = false;
                    }
                };

                audioWs.onerror = (event) => {
                    console.error("AUDIO WEBSOCKET ERROR", event);
                    audioStreaming = false;
                };

                audioWs.onclose = (event) => {
                    console.log("AUDIO WEBSOCKET CLOSED", event.code, event.reason);
                    audioStreaming = false;
                };

            } catch (err) {
                audioStreaming = false;
                console.error("AUDIO START ERROR:", err);
            }
        }

      function stopAudio() {
          if (!audioStreaming) return;

          audioStreaming = false;

          if (mediaRecorder) {
              mediaRecorder.stop();
              mediaRecorder = null;
          }

          if (audioWs) {
              audioWs.close();
              audioWs = null;
          }

          if (audioStream) {
              audioStream.getTracks().forEach(track => track.stop());
              audioStream = null;
          }

          console.log("🔇 Audio stopped");
      }


      // Track currently held arrow keys
      const keys = {
        ArrowUp: false,
        ArrowDown: false,
        ArrowLeft: false,
        ArrowRight: false
      };

      function sendDrive() {

        const message = {
          type: "drive",
          forward: keys.ArrowUp,
          backward: keys.ArrowDown,
          left: keys.ArrowLeft,
          right: keys.ArrowRight
        };
           
        const directions = [];

        if (message.forward) {
          directions.push("FORWARD");
        } else if (message.backward) {
          directions.push("BACKWARD");
        }

        if (message.left) {
          directions.push("LEFT");
        } else if (message.right) {
          directions.push("RIGHT");
        }

        const display = directions.length ? directions.join(" + ") : "STOP";

        console.log(`🚗 ${display}`);

        document.getElementById("driveStatus").textContent = "STATUS: " + display;

        console.log("SENDING DRIVE:", message);

        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify(message));
        } else {
          log.textContent += "WS NOT OPEN: " + ws.readyState + "\\n";
        }
      }


      // KEY DOWN
      window.addEventListener("keydown", function(event) {

        if (event.code === "Space") {
            event.preventDefault();

            if (!event.repeat) {
                startAudio();
            }

            return;
        }

        // Is this one of our arrow keys?
        if (!Object.prototype.hasOwnProperty.call(keys, event.key)) {
          return;
        }

        event.preventDefault();

        // Ignore key-repeat
        if (keys[event.key] === true) {
          return;
        }

        keys[event.key] = true;

        sendDrive();
      });


      // KEY UP
      window.addEventListener("keyup", function(event) {
        if (event.code === "Space") {
            event.preventDefault();
            stopAudio();
            return;
        }

        console.log("KEY UP:", event.key);

        if (!Object.prototype.hasOwnProperty.call(keys, event.key)) {
          return;
        }

        event.preventDefault();

        keys[event.key] = false;

        sendDrive();
      });

      let carAudioCtx = null;
        let carAudioWs = null;
        let carAudioNextTime = 0;
        const CAR_AUDIO_SAMPLE_RATE = 16000;

        function startCarAudio() {
            if (carAudioWs) return;

            carAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
            carAudioNextTime = carAudioCtx.currentTime;

            const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
            carAudioWs = new WebSocket(`${wsProtocol}//${location.host}/car_audio`);
            carAudioWs.binaryType = "arraybuffer";

            carAudioWs.onopen = () => {
                console.log("Car audio connected");
                document.getElementById("carAudioBtn").textContent = "🔊 Listening...";
            };

            carAudioWs.onmessage = (ev) => {
                const int16 = new Int16Array(ev.data);
                const float32 = new Float32Array(int16.length);
                for (let i = 0; i < int16.length; i++) {
                    float32[i] = int16[i] / 32768;
                }

                const buffer = carAudioCtx.createBuffer(1, float32.length, CAR_AUDIO_SAMPLE_RATE);
                buffer.copyToChannel(float32, 0);

                const source = carAudioCtx.createBufferSource();
                source.buffer = buffer;
                source.connect(carAudioCtx.destination);

                // Schedule back-to-back so chunks don't overlap or gap
                const startAt = Math.max(carAudioNextTime, carAudioCtx.currentTime);
                source.start(startAt);
                carAudioNextTime = startAt + buffer.duration;
            };

            carAudioWs.onclose = () => {
                console.log("Car audio disconnected");
                carAudioWs = null;
                document.getElementById("carAudioBtn").textContent = "🔊 Listen to car";
            };

            carAudioWs.onerror = (err) => console.error("Car audio error", err);
        }

        function stopCarAudio() {
            if (carAudioWs) {
                carAudioWs.close();
                carAudioWs = null;
            }
            if (carAudioCtx) {
                carAudioCtx.close();
                carAudioCtx = null;
            }
            document.getElementById("carAudioBtn").textContent = "🔊 Listen to car";
        }

        document.getElementById("carAudioBtn").addEventListener("click", () => {
            if (carAudioWs) {
                stopCarAudio();
            } else {
                startCarAudio();
            }
        });
	</script>
  </body>
</html>
"""


@app.get("/")
async def index():
    return HTMLResponse(INDEX_HTML)

def mjpeg_generator(camera):

    while True:

        frame = camera.get_frame()

        if frame is None:
            time.sleep(0.01)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: "
            + str(len(frame)).encode()
            + b"\r\n\r\n"
            + frame
            + b"\r\n"
        )

        # Don't hammer the browser with duplicate frames
        time.sleep(1 / 30)

@app.get("/video_feed/1")
def video_feed_1():

    return StreamingResponse(
        mjpeg_generator(camera1),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
	
    print("WebSocket connected")
    
    last_sent_angle = None
    left_clicked = False
	
    async def steering_sender():
        global stop_left_turn, turning_left
        last_sent_angle = None
        global angle_send
        try:
            while True:
                angle = steering_angle

                if angle != last_sent_angle:
                    await websocket.send_json({
                        "steering": angle
                    })
                    print("STEERING TX:", angle)
                    last_sent_angle = angle
                    

                await asyncio.sleep(0.01)

        except WebSocketDisconnect:
            pass

    steering_task = asyncio.create_task(steering_sender())
    try:
        while True:
            global stop_left_turn, turning_left
            try:
                msg = await websocket.receive_text()

                print("RX:", msg)

                data = json.loads(msg)

                if data.get("type") == "ping":

                    await websocket.send_text(
                        json.dumps({"type": "pong"})
                    )

                elif data.get("type") == "drive":

                    print(
                        "DRIVE:",
                        "F=", data.get("forward"),
                        "B=", data.get("backward"),
                        "L=", data.get("left"),
                        "R=", data.get("right")
                    )
                    if data.get("backward"):
                      rev_pin.on()
                    else:
                      rev_pin.off()
                    if data.get("forward"):
                      for_pin.on()
                    else:
                      for_pin.off()
                    if data.get("right"):
                      right_pin.on()
                    else:
                      right_pin.off()
                    if data.get("left"):
                      left_pin.on()                         
                    else:
                      left_pin.off()
            except asyncio.TimeoutError:
                # Nothing received this iteration.
                # Go around the loop and check steering_angle again.
                pass

    except WebSocketDisconnect:
        print("WebSocket disconnected")

    except Exception as e:
        print("WebSocket error:", e)
        
        
@app.websocket("/audio")
async def audio_endpoint(websocket: WebSocket):
    await websocket.accept()

    print("=== AUDIO CONNECTED ===")

    ffmpeg = None
    stderr_task = None

    try:
        ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-loglevel", "warning",

            # Browser sends WebM/Opus
            "-f", "webm",
            "-i", "pipe:0",

            "-vn",

            # Explicitly produce normal PCM
            "-ac", "2",
            "-ar", "48000",
            "-sample_fmt", "s16",

            # Send to PipeWire through PulseAudio compatibility
            "-f", "pulse",
            "default",

            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        print("=== FFMPEG AUDIO STARTED ===")

        async def show_errors():
            while True:
                line = await ffmpeg.stderr.readline()

                if not line:
                    break

                print(
                    "FFMPEG:",
                    line.decode(errors="replace").rstrip()
                )

        stderr_task = asyncio.create_task(show_errors())

        while True:
            data = await websocket.receive_bytes()

            if not data:
                continue

            print(f"AUDIO RX: {len(data)} bytes")

            ffmpeg.stdin.write(data)
            await ffmpeg.stdin.drain()

    except WebSocketDisconnect:
        print("=== AUDIO DISCONNECTED ===")

    except Exception as e:
        print("=== AUDIO ERROR ===", repr(e))

    finally:
        print("=== CLEANING UP AUDIO ===")

        if ffmpeg:
            try:
                if ffmpeg.stdin:
                    ffmpeg.stdin.close()
            except Exception:
                pass

            try:
                await asyncio.wait_for(
                    ffmpeg.wait(),
                    timeout=2
                )
            except asyncio.TimeoutError:
                print("FFmpeg didn't exit; killing")

                try:
                    ffmpeg.kill()
                except Exception:
                    pass

        if stderr_task:
            stderr_task.cancel()

        print("=== AUDIO STOPPED ===")
        
        
def encoder_loop():
    global steering_angle

    last_steps = encoder.steps

    print("Encoder thread started")

    while True:
        current_steps = encoder.steps

        if current_steps != last_steps:

            change = current_steps - last_steps

            steering_angle += change * STEERING_STEP

            # Limit steering
            steering_angle = max(
                -MAX_STEERING,
                min(MAX_STEERING, steering_angle)
            )

            print(
                f"Encoder: {current_steps}, "
                f"Steering: {steering_angle:.1f}°"
            )

            last_steps = current_steps

        time.sleep(0.01)

@app.websocket("/car_audio")
async def car_audio_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("=== CAR AUDIO CLIENT CONNECTED ===")

    ffmpeg = None
    stderr_task = None

    async def show_errors():
        while True:
            line = await ffmpeg.stderr.readline()
            if not line:
                break
            print("FFMPEG(car_audio):", line.decode(errors="replace").rstrip())

    try:
        ffmpeg = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-loglevel", "warning",

            # Capture from the car's Bluetooth mic via PulseAudio
            "-f", "pulse",
            "-i", MIC_SOURCE,

            "-ac", "1",
            "-ar", str(CAR_AUDIO_SAMPLE_RATE),

            # Raw PCM out, easy to play on the browser side
            "-f", "s16le",
            "pipe:1",

            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_task = asyncio.create_task(show_errors())

        # ~100ms chunks: sample_rate * 2 bytes/sample * 0.1s
        CHUNK_SIZE = int(CAR_AUDIO_SAMPLE_RATE * 2 * 0.1)

        while True:
            chunk = await ffmpeg.stdout.read(CHUNK_SIZE)
            if not chunk:
                break
            await websocket.send_bytes(chunk)

    except WebSocketDisconnect:
        print("=== CAR AUDIO CLIENT DISCONNECTED ===")

    except Exception as e:
        print("=== CAR AUDIO ERROR ===", repr(e))

    finally:
        if ffmpeg and ffmpeg.returncode is None:
            try:
                ffmpeg.kill()
            except Exception:
                pass
            await ffmpeg.wait()

        if stderr_task:
            stderr_task.cancel()

        print("=== CAR AUDIO STREAM STOPPED ===")