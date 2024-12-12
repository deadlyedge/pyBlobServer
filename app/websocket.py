from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[
            str, list[WebSocket]
        ] = {}  # the {room_id : [list of websockts for the given room]}

    # make connection
    async def connect(self, websocket: WebSocket, room_id: str):
        await websocket.accept()
        if room_id in self.active_connections.keys():
            self.active_connections[room_id].append(websocket)
        else:
            self.active_connections[room_id] = [websocket]

    def disconnect(self, websocket: WebSocket, room_id: str):
        self.active_connections[room_id].remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str, sender_socket: WebSocket, room_id: str):
        for connection in self.active_connections[room_id]:
            if connection != sender_socket:
                await connection.send_text(message)


manager = ConnectionManager()
