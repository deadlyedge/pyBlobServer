import asyncio
import websockets
# import os


async def send_file():
    file_name = "source_file/IMG_6843.jpeg"

    # Read the file into memory and convert to bytes
    with open(file_name, "rb") as file:
        file_data = file.read()

    headers: websockets.HeadersLike = [
        ("Authorization", "Bearer 6b8cd6fc-917e-4f04-a428-c6a0ac94ddff"),
        # ("Authorization", "Bearer 6b8cd6fc-917e-4f04-a428-c6a0ac94ddff"),
    ]
    # Connect to the WebSocket and send the file
    async with websockets.connect(
        "ws://localhost:8000/upload"
    ) as websocket:
        await websocket.send("6b8cd6fc-917e-4f04-a428-c6a0ac94ddff")
        await websocket.send(file_name)  # Send the renamed file name
        await websocket.send(file_data)  # Send the file data
        await websocket.send(b"END_OF_FILE")  # Send end-of-file marker
        print(await websocket.recv())

    # Close the connection after sending
    await websocket.close()


# To run the test function
if __name__ == "__main__":
    asyncio.run(send_file())
