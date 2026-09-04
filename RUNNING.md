Open environment with:
source ~/venv/bin/activate

Run with:
uvicorn controls:app --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem