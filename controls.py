import asyncio  # Provides the asynchronous WebSocket and subprocess APIs.
import subprocess  # Starts rpicam-vid and reads its output as a byte stream.
import threading  # Keeps camera capture and GPIO polling off the web event loop.
import time  # Supplies short delays that prevent busy loops.
from fastapi import FastAPI, WebSocket  # Defines HTTP and WebSocket endpoints.
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse  # Builds endpoint responses.
from starlette.websockets import WebSocketDisconnect  # Identifies a client that closed its socket.
import json  # Decodes drive messages received from the browser.

from gpiozero import DigitalOutputDevice, RotaryEncoder  # Controls GPIO outputs and reads the encoder.

from time import sleep  # Imported for the existing control code and future timing needs.


# GPIO output assignments. These are BCM GPIO numbers, not physical header pins.
forNum = 6  # Forward motor-control output.
revNum = 5  # Reverse motor-control output.
revRight = 27  # Right-steering output.
leftPin = 22  # Left-steering output.

# Create each output device and immediately switch it off so startup cannot
# leave a motor or steering actuator active from a previous process.
for_pin = DigitalOutputDevice(forNum)
for_pin.off()
rev_pin = DigitalOutputDevice(revNum)
rev_pin.off()
right_pin = DigitalOutputDevice(revRight)
right_pin.off()
left_pin = DigitalOutputDevice(leftPin)
left_pin.off()

# FastAPI uses this application object as the ASGI entry point for Uvicorn.
app = FastAPI()

# Microphone configuration
MIC_SOURCE = "bluez_input.00:6A:8E:0E:E7:32"
CAR_AUDIO_SAMPLE_RATE = 16000  # good enough for voice, keeps bandwidth low

# The encoder reports steering-wheel movement through two GPIO channels.
encoder = RotaryEncoder(16, 26)
steering_angle = 0  # Current steering angle sent to the browser, in degrees. Start assuing wheels are straight
STEERING_STEP = 1  # Degrees to change for one encoder step.
MAX_STEERING = 45  # Prevents the steering display and actuator from exceeding this angle.

class Camera:
    """Own one persistent rpicam-vid process and its most recent JPEG frame."""

    #Create camera object with camera id
    def __init__(self, camera_id):
        self.camera_id = camera_id  # Camera index passed to rpicam-vid.

        self.process = None  # Subprocess object created by start().
        self.thread = None  # Background thread that extracts complete JPEGs.

        self.latest_frame = None  # Latest complete JPEG, or None before the first frame.
        self.lock = threading.Lock()  # Protects latest_frame during reads and writes.

        self.running = False  # Signals the frame-reading thread to stop.

    # Start the camera process and its frame-reading thread.
    def start(self):
        """Start camera capture and a thread that separates JPEGs from stdout."""
        print(f"Starting persistent camera {self.camera_id}")

        # rpicam-vid continuously writes an MJPEG stream to stdout. A zero
        # timeout keeps it running until stop() terminates the process.
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


        # stdout is piped so Python can publish individual JPEG frames through
        # the HTTP streaming endpoint instead of writing a video file.
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
        )

        self.running = True  # Let _read_frames() begin consuming stdout.

        # A daemon thread ends with the server if an unexpected shutdown occurs.
        self.thread = threading.Thread(
            target=self._read_frames,
            daemon=True,
        )

        self.thread.start()

        print(
            f"Camera {self.camera_id} started "
            f"(PID {self.process.pid})"
        )
    """Extract complete JPEG images from rpicam-vid's arbitrary chunks."""
    def _read_frames(self): 
        # JPEG start/end markers may be split across reads, so retain bytes in
        # a buffer until both markers for a complete image are available.
        SOI = b"\xff\xd8"
        EOI = b"\xff\xd9"

        buffer = bytearray()  # Mutable buffer avoids copying on each append.

        while self.running and self.process:
            # The pipe does not promise one frame per read; 16 KiB is only the
            # amount requested from the operating system at a time.
            chunk = self.process.stdout.read(16 * 1024)

            if not chunk:
                print(
                    f"Camera {self.camera_id} stopped producing data"
                )
                break

            buffer.extend(chunk)  # Add the new data to any partial JPEG.

            while True:
                start = buffer.find(SOI)  # Locate the next JPEG start marker.

                if start < 0:
                    if len(buffer) > 1:
                        del buffer[:-1]  # Preserve a possible split marker byte.
                    break

                end = buffer.find(EOI, start + 2)  # Search after the start marker.

                if end < 0:
                    if start > 0:
                        del buffer[:start]  # Discard non-JPEG noise before the frame.
                    break

                jpg = bytes(buffer[start:end + 2])  # Copy the complete JPEG out.

                del buffer[:end + 2]  # Leave later frames for the next iteration.

                # The lock prevents a reader from seeing a partially replaced
                # frame while this thread publishes the new one.
                with self.lock:
                    self.latest_frame = jpg

    def get_frame(self):
        """Return the newest complete JPEG frame, if one has arrived."""
        with self.lock:
            return self.latest_frame

    def stop(self):
        """Stop the camera process and wait briefly for its reader thread."""
        print(f"Stopping persistent camera {self.camera_id}")

        self.running = False  # Causes the reader loop to exit after its read returns.

        if self.process and self.process.poll() is None:
            self.process.terminate()  # Ask rpicam-vid to close its output cleanly.

            try:
                self.process.wait(timeout=2)

            except subprocess.TimeoutExpired:
                print(
                    f"Camera {self.camera_id} did not stop, killing"
                )

                self.process.kill()  # Force termination if it ignored the request.
                self.process.wait()

        if self.thread:
            self.thread.join(timeout=2)  # Avoid hanging shutdown on a blocked reader.

        print(f"Camera {self.camera_id} stopped")
        
# Create both camera owners once; the lifecycle hooks start and stop them.
# camera0 = Camera(0)
camera1 = Camera(1)

@app.on_event("startup")
async def startup_event():
    """Start hardware services when Uvicorn finishes initializing the app."""
    # camera0.start()
    camera1.start()
    
    # Encoder polling is blocking and continuous, so it must not run inside
    # FastAPI's asyncio event loop.
    encoder_thread = threading.Thread(
        target=encoder_loop,
        daemon=True
    )

    encoder_thread.start()
    print("Encoder started")
    
@app.on_event("shutdown")
async def shutdown_event():
    """Release camera processes and GPIO resources during server shutdown."""
    # camera0.stop()
    camera1.stop()    

    # camera0.stop()
    camera1.stop()

    print("=== SHUTDOWN COMPLETE ===")

@app.get("/car-logo")
async def car_logo():
    """Serve the logo image used by the embedded controller page."""
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

            // Mute car audio while driving, resume once stopped
        const isMoving = message.forward || message.backward;

        if (isMoving) {
            stopCarAudio();
        } else {
            startCarAudio();
        }
      }


      // KEY DOWN
      window.addEventListener("keydown", function(event) {

        if (event.code === "Space") {
            event.preventDefault();

            if (!event.repeat) {
                startAudio();
                stopCarAudio();
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
            startCarAudio();
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
    """Return the single-page browser controller."""
    return HTMLResponse(INDEX_HTML)

def mjpeg_generator(camera):
    """Yield camera frames in the multipart format understood by browsers."""
    while True:
        frame = camera.get_frame()  # Get one complete JPEG from the camera thread.

        if frame is None:
            time.sleep(0.01)  # Wait briefly until the camera has produced a frame.
            continue

        # Each multipart section contains one JPEG and its byte length. The
        # browser keeps the connection open and replaces the displayed image
        # whenever the next boundary arrives.
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: "
            + str(len(frame)).encode()
            + b"\r\n\r\n"
            + frame
            + b"\r\n"
        )

        time.sleep(1 / 30)  # Cap delivery at roughly 30 frames per second.

@app.get("/video_feed/1")
def video_feed_1():
    """Stream frames from camera 1 as a live MJPEG response."""
    return StreamingResponse(
        mjpeg_generator(camera1),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    """Receive drive commands and send steering-angle updates."""
    await websocket.accept()
	
    print("WebSocket connected")
    
    # last_sent_angle = None  # Retained for compatibility with the handler state.
    # left_clicked = False  # Retained for compatibility with the handler state.
	
    async def steering_sender():
        """Push an update only when the physical steering angle changes."""
        global stop_left_turn, turning_left
        last_sent_angle = None
        global angle_send
        try:
            while True:
                angle = steering_angle  # Read the latest value from encoder_loop().

                if angle != last_sent_angle:
                    # The page uses this message to rotate its front-wheel
                    # illustration so the UI follows the physical encoder.
                    await websocket.send_json({
                        "steering": angle
                    })
                    print("STEERING TX:", angle)
                    last_sent_angle = angle
                    

                await asyncio.sleep(0.01)  # Yield to incoming commands and other clients.

        except WebSocketDisconnect:
            pass

    # Run outbound steering updates alongside inbound browser messages.
    # steering_task = asyncio.create_task(steering_sender())
    try:
        while True:
            global stop_left_turn, turning_left
            try:
                msg = await websocket.receive_text()  # Wait for the next browser command.

                print("RX:", msg)

                data = json.loads(msg)  # Convert the JSON text into a Python dictionary.

                if data.get("type") == "ping":  # Lightweight connectivity check.

                    await websocket.send_text(
                        json.dumps({"type": "pong"})
                    )

                elif data.get("type") == "drive":  # Arrow-key state from the controller.

                    print(
                        "DRIVE:",
                        "F=", data.get("forward"),
                        "B=", data.get("backward"),
                        "L=", data.get("left"),
                        "R=", data.get("right")
                    )
                    # Each output is set explicitly on every message so a key-up
                    # event reliably turns the corresponding actuator off.
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
    """Forward browser microphone audio through FFmpeg to PulseAudio."""
    await websocket.accept()

    print("=== AUDIO CONNECTED ===")

    ffmpeg = None
    stderr_task = None

    try:
        # The browser sends WebM/Opus chunks. FFmpeg decodes them and writes
        # regular stereo PCM to the Pi's default PulseAudio output.
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
            """Keep FFmpeg's diagnostic pipe from filling and blocking it."""
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
            data = await websocket.receive_bytes()  # Read the next recorder chunk.

            if not data:
                continue

            print(f"AUDIO RX: {len(data)} bytes")

            ffmpeg.stdin.write(data)  # Feed encoded audio into FFmpeg's stdin.
            await ffmpeg.stdin.drain()  # Apply backpressure if FFmpeg falls behind.

    except WebSocketDisconnect:
        print("=== AUDIO DISCONNECTED ===")

    except Exception as e:
        print("=== AUDIO ERROR ===", repr(e))

    finally:
        print("=== CLEANING UP AUDIO ===")

        if ffmpeg:
            try:
                if ffmpeg.stdin:
                    ffmpeg.stdin.close()  # Tell FFmpeg that no more audio is coming.
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
                    ffmpeg.kill()  # Do not leave an orphaned audio process behind.
                except Exception:
                    pass

        if stderr_task:
            stderr_task.cancel()

        print("=== AUDIO STOPPED ===")
        
        
def encoder_loop():
    """Poll the rotary encoder and keep the shared angle within safe limits."""
    global steering_angle

    last_steps = encoder.steps  # Establish a baseline without moving the wheels.

    print("Encoder thread started")

    while True:
        current_steps = encoder.steps  # Read the encoder's current accumulated count.

        if current_steps != last_steps:
            change = current_steps - last_steps  # Determine movement since last poll.

            steering_angle += change * STEERING_STEP  # Convert steps to degrees.

            # Clamp the value so repeated encoder turns cannot request an unsafe
            # angle from the steering mechanism or the browser display.
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