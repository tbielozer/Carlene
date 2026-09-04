# Carlene

Carlene is a Raspberry Pi controller for a driveable kid car. It provides a
FastAPI web interface, two camera feeds, GPIO motor and steering control, a
rotary encoder, and basic visual mapping.

## Requirements

Run the application on the Raspberry Pi connected to the car hardware. The
project currently expects:

- Raspberry Pi OS with Python 3
- Two cameras supported by `rpicam-vid`
- GPIO-connected motor/steering hardware
- A rotary encoder connected to GPIO pins 16 and 26
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
sudo apt install -y python3 python3-venv python3-pip rpicam-apps openssl
```

Confirm that the camera command is available:

```bash
rpicam-vid --help
```

Connect both cameras, then verify that the Raspberry Pi can detect them before
starting the server.

## Create a Python environment

From the project directory, create and activate a virtual environment:

```bash
python3 -m venv ~/venv
source ~/venv/bin/activate
python -m pip install --upgrade pip
```

Install the Python dependencies:

```bash
python -m pip install fastapi "uvicorn[standard]" gpiozero opencv-python numpy
```

The dependencies are used as follows:

- `fastapi` and `uvicorn` run the web application.
- `gpiozero` controls the motors and reads the rotary encoder.
- `opencv-python` and `numpy` process camera frames for visual mapping.

## Required files and paths

Before starting the application, make sure the logo file exists at:

```text
/home/wifidriver/Downloads/shopr8v3.png
```

These paths are currently hard-coded in `controls.py`. Either use the
`wifidriver` home directory on the Pi or update the paths in the code for your
installation.

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
- GPIO permission errors: run on the Raspberry Pi with the expected GPIO
	access, and verify the wiring and pin numbers.
- Missing logo or log errors: check the hard-coded paths listed above.
- Port `8000` already in use: stop the other server or choose another port and
	use that port in the browser URL.
