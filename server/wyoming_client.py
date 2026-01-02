"""
Wyoming protocol client for communication with Home Assistant.
"""
import asyncio
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class WyomingClient:
    """
    Client for Wyoming protocol communication with Home Assistant.
    Handles audio streaming and TTS responses.
    """
    
    def __init__(self, host: str, port: int = 10700, name: str = "hybrid-voice-satellite"):
        """
        Initialize Wyoming client.
        
        Args:
            host: Home Assistant hostname or IP
            port: Wyoming protocol port (default 10700)
            name: Satellite name to identify in HA
        """
        self.host = host
        self.port = port
        self.name = name
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
    
    async def connect(self):
        """Establish connection to Home Assistant Wyoming server."""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )
            self.connected = True
            logger.info(f"Connected to Wyoming server at {self.host}:{self.port}")
            
            # Send handshake
            await self.send_message({
                'type': 'satellite',
                'name': self.name,
                'capabilities': ['wake_word', 'audio_input', 'audio_output']
            })
            
            # Wait for acknowledgment
            response = await self.receive_message()
            if response and response.get('type') == 'ack':
                logger.info(f"Satellite '{self.name}' registered with Home Assistant")
            else:
                logger.warning(f"Unexpected handshake response: {response}")
                
        except Exception as e:
            logger.error(f"Failed to connect to Wyoming server: {e}")
            self.connected = False
            raise
    
    async def disconnect(self):
        """Close connection to Home Assistant."""
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        self.connected = False
        logger.info("Disconnected from Wyoming server")
    
    async def send_audio(self, audio_data: bytes):
        """
        Send audio chunk to Home Assistant.
        
        Args:
            audio_data: Raw audio bytes (16-bit PCM, mono, 16kHz)
        """
        if not self.connected:
            logger.warning("Cannot send audio: not connected")
            return
        
        message = {
            'type': 'audio_chunk',
            'data': audio_data.hex(),
            'rate': 16000,
            'channels': 1,
            'format': 'pcm16'
        }
        await self.send_message(message)
    
    async def send_wake_word_detected(self):
        """Notify Home Assistant that wake word was detected."""
        message = {
            'type': 'wake_word_detected',
            'timestamp': asyncio.get_event_loop().time()
        }
        await self.send_message(message)
        logger.info("Wake word detection sent to HA")
    
    async def get_tts_audio(self) -> Optional[bytes]:
        """
        Receive TTS audio from Home Assistant (non-blocking).
        
        Returns:
            TTS audio bytes or None if no audio available
        """
        try:
            # Non-blocking check for incoming messages
            if self.reader and not self.reader.at_eof():
                message = await asyncio.wait_for(
                    self.receive_message(), 
                    timeout=0.01  # 10ms timeout
                )
                if message and message.get('type') == 'tts_audio':
                    return bytes.fromhex(message['data'])
        except asyncio.TimeoutError:
            pass  # No message available
        except Exception as e:
            logger.error(f"Error receiving TTS audio: {e}")
        
        return None
    
    async def send_message(self, message: Dict[str, Any]):
        """
        Send JSON message over Wyoming protocol.
        
        Args:
            message: Dictionary to send as JSON
        """
        if not self.writer:
            logger.error("Cannot send message: writer not initialized")
            return
        
        try:
            data = json.dumps(message).encode() + b'\n'
            self.writer.write(data)
            await self.writer.drain()
            logger.debug(f"Sent message: {message['type']}")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            self.connected = False
    
    async def receive_message(self) -> Optional[Dict[str, Any]]:
        """
        Receive JSON message from Wyoming protocol.
        
        Returns:
            Parsed JSON message or None if error/EOF
        """
        if not self.reader:
            logger.error("Cannot receive message: reader not initialized")
            return None
        
        try:
            line = await self.reader.readline()
            if not line:
                logger.warning("Received empty line (connection closed?)")
                self.connected = False
                return None
            
            message = json.loads(line.decode().strip())
            logger.debug(f"Received message: {message.get('type', 'unknown')}")
            return message
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON message: {e}")
        except Exception as e:
            logger.error(f"Failed to receive message: {e}")
            self.connected = False
        
        return None
    
    async def keep_alive(self):
        """Send periodic keep-alive messages."""
        while self.connected:
            await asyncio.sleep(30)  # Send every 30 seconds
            if self.connected:
                await self.send_message({'type': 'ping'})
