# Carlene

Carlene is a Raspberry Pi controller for a driveable kid car. It provides a
FastAPI web interface, a camera feed, GPIO motor and steering control, a
rotary encoder, and two-way audio streaming.

## Requirements

Run the application on the Raspberry Pi connected to the car hardware. The
project currently expects:

- Raspberry Pi OS with Python 3
- A camera supported by `rpicam-vid`
- GPIO-connected motor/steering hardware
- A rotary encoder connected to GPIO pins 16 and 26
- A Bluetooth microphone connected to the Raspberry Pi for car audio
- The project files copied to the Pi

The GPIO pins used by the application are:

| Function | GPIO pin |
| --- | ---: |
| Forward | 6 |
| Reverse | 5 |
| Right | 27 |
| Left | 22 |
| Rotary encoder | 16 and 26 |

## Raspberry Pi system setup

Update the system and install the camera tools and Python environment support:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip rpicam-apps ffmpeg openssl
```

Confirm that the camera command is available:

```bash
rpicam-vid --help
```

Connect the camera, then verify that the Raspberry Pi can detect it before
starting the server. The active camera is camera index `1` in `controls.py`.

The audio features also require a working PulseAudio/PipeWire-compatible audio
setup and a paired Bluetooth microphone. The microphone source name currently
configured in `controls.py` is:

```text
bluez_input.00:6A:8E:0E:E7:32
```

## Create a Python environment

From the project directory, create and activate a virtual environment:

```bash
python3 -m venv ~/venv
source ~/venv/bin/activate
python -m pip install --upgrade pip
```

Install the Python dependencies in the environment:

```bash
python -m pip install fastapi "uvicorn[standard]" gpiozero opencv-python numpy
```

The dependencies are used as follows:

- `fastapi` and `uvicorn` run the web application.
- `gpiozero` controls the motors and reads the rotary encoder.
- `opencv-python` and `numpy` process camera frames for visual mapping.

## Required files and paths

Before starting the application, make sure the logo file exists relative to the
project directory at:

```text
Images/shopr8v3.png
```

This path is currently hard-coded in `controls.py`. Start Uvicorn from the
project directory, or update the path in the code for your installation.

## Create a local HTTPS certificate

The launch command below uses HTTPS. From the project directory, create a
self-signed certificate for local use:

```bash
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout key.pem -out cert.pem -days 365 \
  -subj "/CN=carlene.local"
```

Do not use this self-signed certificate for an internet-facing deployment.

## Start the controller

Activate the environment whenever opening a new shell:

```bash
source ~/venv/bin/activate
```

Start the FastAPI application from the project directory:

```bash
uvicorn controls:app --host 0.0.0.0 --port 8000 \
  --ssl-keyfile key.pem --ssl-certfile cert.pem
```

### To use the two camera feed: 
- Change `controls:app` to `controls2:app`
- Controls2.py currently doesn't have two way audio and the wheel angle
encoder implemented.

## Connect to webpage
When the server is running, open this address from a device on the same
network, replacing the IP address with the Raspberry Pi's address:

```text
https://<raspberry-pi-ip>:8000/
```

Your browser will warn that the certificate is self-signed. The warning is
expected for this local certificate.

## Troubleshooting

- `ModuleNotFoundError`: activate `~/venv` and rerun the dependency install.
- `rpicam-vid: command not found`: install `rpicam-apps` and check the camera
	connection.
- `ffmpeg: command not found`: install `ffmpeg` and confirm it is available with
	`ffmpeg -version`.
- GPIO permission errors: run on the Raspberry Pi with the expected GPIO
	access, and verify the wiring and pin numbers.
- Missing logo errors: check that `Images/shopr8v3.png` exists and that the
	server was started from the project directory.
- Bluetooth audio errors: verify that the microphone is paired and that its
	PulseAudio source name matches `MIC_SOURCE` in `controls.py`.
- Port `8000` already in use: stop the other server or choose another port and
	use that port in the browser URL.
- To find the pi ip enter `ipconfig` into the terminal
- The current pi uses an antenna to improve signal, disconnect from byu-devices 
    under Broadcom Wi-Fi so only MediaTek Wi-Fi is used
