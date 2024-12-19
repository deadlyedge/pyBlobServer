from fastapi import FastAPI, WebSocket
import os
import time
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

app = FastAPI()

# Create the data directory if it doesn't exist
os.makedirs("app/data", exist_ok=True)


@app.websocket("/uploadfile")
async def upload_file(websocket: WebSocket):
    await websocket.accept()
    file_name = await websocket.receive_text()  # Receive the file name

    # Log the received file name
    logging.info(f"Received file name: {file_name}")

    # Extract only the base file name to avoid directory issues
    file_name = os.path.basename(file_name)
    file_path = f"app/data/{file_name}"

    # Ensure a new file is created if it already exists
    if os.path.exists(file_path):
        base, extension = os.path.splitext(file_name)
        file_name = f"{base}_{int(time.time())}{extension}"
        file_path = f"app/data/{file_name}"

    try:
        # Create the file before writing to it
        with open(file_path, "wb") as file:
            while True:
                data = await websocket.receive_bytes()  # Receive binary data
                if not data or data == b"END_OF_FILE":
                    break
                file.write(data)  # Write data to file
                await websocket.send_text("FILE_RECEIVED")  # Send acknowledgement
        logging.info(f"File saved successfully: {file_path}")
    except Exception as e:
        logging.error(f"Error saving file: {e}")
    finally:
        try:
            await websocket.close()
        except Exception as e:
            if (
                isinstance(e, RuntimeError)
                and str(e) == "Unexpected ASGI message 'websocket.close'"
            ):
                logging.info("WebSocket closed normally.")
            else:
                logging.error(f"Error closing websocket: {e}")

    logging.info("WebSocket connection closed.")
