"""
WebSocket server for handling browser client connections.
"""
import asyncio
import json
import logging
import websockets
from typing import Set
from audio_buffer import AudioBuffer
from wyoming_client import WyomingClient

logger = logging.getLogger(__name__)


class WebSocketServer:
    """
    WebSocket server handling browser connections and audio streaming.
    """
    
    def __init__(self, host: str, port: int, wyoming_client: WyomingClient, auth_token: str = None):
        """
        Initialize WebSocket server.
        
        Args:
            host: Server host address
            port: Server port
            wyoming_client: Wyoming client instance
            auth_token: Optional authentication token
        """
        self.host = host
        self.port = port
        self.wyoming_client = wyoming_client
        self.auth_token = auth_token
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.audio_buffer = AudioBuffer(sample_rate=16000)
    
    async def register_client(self, websocket: websockets.WebSocketServerProtocol):
        """Register a new client connection."""
        self.clients.add(websocket)
        logger.info(f"Client connected: {websocket.remote_address}. Total clients: {len(self.clients)}")
    
    async def unregister_client(self, websocket: websockets.WebSocketServerProtocol):
        """Unregister a client connection."""
        self.clients.discard(websocket)
        logger.info(f"Client disconnected: {websocket.remote_address}. Total clients: {len(self.clients)}")
    
    async def authenticate(self, websocket: websockets.WebSocketServerProtocol) -> bool:
        """
        Authenticate incoming WebSocket connection.
        
        Args:
            websocket: WebSocket connection
            
        Returns:
            True if authenticated, False otherwise
        """
        if not self.auth_token:
            return True  # No authentication required
        
        try:
            # Wait for auth message
            message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            data = json.loads(message)
            
            if data.get('type') == 'auth' and data.get('token') == self.auth_token:
                await websocket.send(json.dumps({'type': 'auth_ok'}))
                return True
            else:
                await websocket.send(json.dumps({'type': 'auth_failed'}))
                return False
                
        except asyncio.TimeoutError:
            logger.warning(f"Authentication timeout for {websocket.remote_address}")
            return False
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False
    
    async def handler(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """
        Handle incoming WebSocket connections.
        
        Args:
            websocket: WebSocket connection
            path: Request path
        """
        # Authenticate if required
        if self.auth_token and not await self.authenticate(websocket):
            logger.warning(f"Authentication failed for {websocket.remote_address}")
            await websocket.close(code=1008, reason="Authentication failed")
            return
        
        # Register client
        await self.register_client(websocket)
        
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    # Binary audio data
                    await self.handle_audio_data(message, websocket)
                else:
                    # Text/JSON control message
                    await self.handle_control_message(message, websocket)
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Connection closed: {websocket.remote_address}")
        except Exception as e:
            logger.error(f"Error in WebSocket handler: {e}", exc_info=True)
        finally:
            await self.unregister_client(websocket)
    
    async def handle_audio_data(self, data: bytes, websocket: websockets.WebSocketServerProtocol):
        """
        Process audio data from browser and forward to Wyoming.
        
        Args:
            data: Raw audio bytes
            websocket: Client WebSocket connection
        """
        # Buffer audio
        self.audio_buffer.add(data)
        
        # Forward to Home Assistant via Wyoming
        if self.wyoming_client.connected:
            await self.wyoming_client.send_audio(data)
        else:
            logger.warning("Wyoming client not connected, audio data dropped")
        
        # Check for TTS response
        tts_audio = await self.wyoming_client.get_tts_audio()
        if tts_audio:
            try:
                await websocket.send(tts_audio)
                logger.debug(f"Sent TTS audio to client: {len(tts_audio)} bytes")
            except Exception as e:
                logger.error(f"Failed to send TTS audio: {e}")
    
    async def handle_control_message(self, message: str, websocket: websockets.WebSocketServerProtocol):
        """
        Process control/JSON messages from browser.
        
        Args:
            message: JSON message string
            websocket: Client WebSocket connection
        """
        try:
            data = json.loads(message)
            msg_type = data.get('type')
            
            if msg_type == 'wake_detected':
                logger.info("Wake word detected by client")
                await self.wyoming_client.send_wake_word_detected()
                
            elif msg_type == 'ping':
                await websocket.send(json.dumps({'type': 'pong'}))
                
            elif msg_type == 'status_request':
                await websocket.send(json.dumps({
                    'type': 'status',
                    'wyoming_connected': self.wyoming_client.connected,
                    'clients': len(self.clients),
                    'buffered_bytes': self.audio_buffer.buffered_bytes
                }))
                
            else:
                logger.warning(f"Unknown message type: {msg_type}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON message: {e}")
        except Exception as e:
            logger.error(f"Error handling control message: {e}", exc_info=True)
    
    async def broadcast(self, message: str):
        """
        Broadcast message to all connected clients.
        
        Args:
            message: Message to broadcast
        """
        if self.clients:
            await asyncio.gather(
                *[client.send(message) for client in self.clients],
                return_exceptions=True
            )
    
    async def serve(self):
        """Start the WebSocket server."""
        async with websockets.serve(self.handler, self.host, self.port):
            logger.info(f"WebSocket server running on {self.host}:{self.port}")
            await asyncio.Future()  # Run forever
