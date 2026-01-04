"""
WebSocket server for handling browser client connections.
"""
import asyncio
import json
import logging
import websockets
from typing import Set
from audio_buffer import AudioBuffer
from wyoming_server import WyomingServer

logger = logging.getLogger(__name__)


class WebSocketServer:
    """
    WebSocket server handling browser connections and audio streaming.
    """
    
    def __init__(self, host: str, port: int, wyoming_server: WyomingServer, auth_token: str = None, ssl_context=None):
        """
        Initialize WebSocket server.
        
        Args:
            host: Server host address
            port: Server port
            wyoming_server: Wyoming Server instance
            auth_token: Optional authentication token
            ssl_context: Optional SSL context for WSS
        """
        self.host = host
        self.port = port
        self.wyoming_server = wyoming_server
        self.auth_token = auth_token
        self.ssl_context = ssl_context
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.audio_buffer = AudioBuffer(sample_rate=16000)
        
        # Register TTS callbacks
        self.wyoming_server.on_tts_audio = self.broadcast_audio
        self.wyoming_server.on_tts_start = self.broadcast_audio_start
    
    async def start(self):
        """Start the WebSocket server."""
        self.server = await websockets.serve(
            self.handler, 
            self.host, 
            self.port,
            ssl=self.ssl_context,
            process_request=self.process_request
        )
        protocol = "wss" if self.ssl_context else "ws"
        logger.info(f"WebSocket server running on {protocol}://{self.host}:{self.port}")
        logger.info(f"Client available at https://{self.host}:{self.port}/")

    async def process_request(self, path, request_headers):
        """
        Handle HTTP requests to serve static client files.
        This allows serving the client on the same port as the WebSocket,
        resolving mixed content and SSL trust issues.
        """
        try:
            logger.debug(f"Handling HTTP request for path: {path}")
            
            if path == '/':
                path = '/index.html'
            
            # Strip query string if present
            path = path.split('?')[0]
            
            # Allow WebSocket upgrades to pass through
            if "Upgrade" in request_headers and request_headers["Upgrade"].lower() == "websocket":
                return None
            
            # Simple security check
            if '..' in path:
                return (403, [], b'403 Forbidden')
            
            # Locate file in client directory
            from pathlib import Path
            import mimetypes
            
            # Assuming 'client' is sibling to 'server'
            client_dir = Path(__file__).parent.parent / "client"
            file_path = client_dir / path.lstrip('/')
            
            if file_path.exists() and file_path.is_file():
                mime_type, _ = mimetypes.guess_type(file_path)
                if not mime_type:
                    mime_type = 'application/octet-stream'
                
                with open(file_path, 'rb') as f:
                    content = f.read()
                    
                return (
                    200,
                    [
                        ('Content-Type', mime_type),
                        ('Content-Length', str(len(content))),
                        ('Access-Control-Allow-Origin', '*')
                    ],
                    content
                )
            
            return (404, [], b'404 Not Found')
            
        except Exception as e:
            logger.error(f"Error serving HTTP request: {e}")
            return (500, [], b'500 Internal Server Error')
    
    async def register_client(self, websocket: websockets.WebSocketServerProtocol):
        """Register a new client connection."""
        self.clients.add(websocket)
        logger.info(f"Client connected: {websocket.remote_address}. Total clients: {len(self.clients)}")
    
    async def unregister_client(self, websocket: websockets.WebSocketServerProtocol):
        """Unregister a client connection."""
        self.clients.discard(websocket)
        logger.info(f"Client disconnected: {websocket.remote_address}. Total clients: {len(self.clients)}")
    
    async def authenticate(self, websocket: websockets.WebSocketServerProtocol) -> bool:
        """Authenticate incoming WebSocket connection."""
        if not self.auth_token:
            return True
        
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            data = json.loads(message)
            
            if data.get('type') == 'auth' and data.get('token') == self.auth_token:
                await websocket.send(json.dumps({'type': 'auth_ok'}))
                return True
            else:
                await websocket.send(json.dumps({'type': 'auth_failed'}))
                return False
        except Exception:
            return False
    
    async def handler(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """Handle incoming WebSocket connections."""
        if self.auth_token and not await self.authenticate(websocket):
            logger.warning(f"Authentication failed for {websocket.remote_address}")
            await websocket.close(code=1008)
            return
        
        await self.register_client(websocket)
        
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Binary audio data -> Forward to Wyoming
                    # logger.debug(f"Received audio chunk: {len(message)} bytes") # Too noisy for prod, good for debug
                    self.audio_buffer.add(message)
                    await self.wyoming_server.send_audio(message)
                else:
                    await self.handle_control_message(message, websocket)
                    
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"Error in WebSocket handler: {e}")
        finally:
            await self.unregister_client(websocket)
    
    async def handle_control_message(self, message: str, websocket: websockets.WebSocketServerProtocol):
        """Process control/JSON messages from browser."""
        try:
            data = json.loads(message)
            msg_type = data.get('type')
            
            if msg_type == 'wake_detected':
                logger.info("Wake word detected by client")
                await self.wyoming_server.send_wake_word_detected()
                
            elif msg_type == 'ping':
                await websocket.send(json.dumps({'type': 'pong'}))
                
            elif msg_type == 'status_request':
                await websocket.send(json.dumps({
                    'type': 'status',
                    'clients': len(self.clients),
                    'ha_connected': len(self.wyoming_server.ha_writers) > 0
                }))
                
        except Exception as e:
            logger.error(f"Error handling control message: {e}")
            
    async def broadcast_audio_start(self, rate: int):
        """Notify clients of incoming TTS stream format."""
        if self.clients:
            message = json.dumps({
                'type': 'audio_start',
                'rate': rate
            })
            await asyncio.gather(
                *[client.send(message) for client in self.clients],
                return_exceptions=True
            )
            logger.debug(f"Broadcasted audio_start (rate={rate}) to {len(self.clients)} clients")

    async def broadcast_audio(self, audio_data: bytes):
        """Broadcast audio received from HA (TTS) to all browser clients."""
        if self.clients:
            # Send as binary
            await asyncio.gather(
                *[client.send(audio_data) for client in self.clients],
                return_exceptions=True
            )
            logger.debug(f"Broadcasted TTS audio ({len(audio_data)} bytes) to {len(self.clients)} clients")



    async def stop(self):
        """Stop the WebSocket server."""
        if hasattr(self, 'server') and self.server:
            self.server.close()
            await self.server.wait_closed()
        
        # Close all active connections
        if self.clients:
            await asyncio.gather(
                *[client.close() for client in self.clients],
                return_exceptions=True
            )
            self.clients.clear()
        
        logger.info("WebSocket server stopped")
