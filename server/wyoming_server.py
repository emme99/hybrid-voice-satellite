"""
Wyoming protocol server for integration with Home Assistant.
Home Assistant connects to this server to receive audio and send TTS.
"""
import asyncio
import json
import logging
from typing import Optional, Dict, Any, Set

logger = logging.getLogger(__name__)


import wave
import datetime

class WyomingServer:
    """
    Server for Wyoming protocol.
    Accepts connections from Home Assistant.
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 10700, name: str = "hybrid-voice-satellite"):
        """
        Initialize Wyoming server.
        
        Args:
            host: Host to bind to
            port: Port to bind to (default 10700)
            name: Satellite name to identify in HA
        """
        self.host = host
        self.port = port
        self.name = name
        self.server: Optional[asyncio.AbstractServer] = None
        self.ha_writers: Set[asyncio.StreamWriter] = set()
        self.client_tasks: Set[asyncio.Task] = set()
        self.debug_wav = None
    
    async def start(self):
        """Start the Wyoming TCP server."""
        self.server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        logger.info(f"Wyoming server listening on {self.host}:{self.port}")
    
    async def stop(self):
        """Stop the server."""
        logger.debug("Stopping Wyoming server...")
        
        # 1. Cancel active tasks (Stop processing)
        logger.debug(f"Cancelling {len(self.client_tasks)} active tasks...")
        if self.client_tasks:
            for task in list(self.client_tasks):
                task.cancel()
            
            try:
                await asyncio.wait_for(asyncio.gather(*self.client_tasks, return_exceptions=True), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for tasks to cancel")
            self.client_tasks.clear()

        # 2. Close active connections (Stop I/O)
        logger.debug(f"Closing {len(self.ha_writers)} active connections...")
        for writer in list(self.ha_writers):
            writer.close()
            
        if self.ha_writers:
            tasks = [writer.wait_closed() for writer in self.ha_writers]
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for connections to close")
            self.ha_writers.clear()

        # 3. Stop listener (Stop new connections)
        if self.server:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for server to close")
        
        logger.info("Wyoming server stopped")
        
    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming connection from Home Assistant."""
        task = asyncio.current_task()
        self.client_tasks.add(task)
        
        addr = writer.get_extra_info('peername')
        logger.info(f"Home Assistant connected from {addr}")
        self.ha_writers.add(writer)
        
        try:
            # Send handshake immediately upon connection
            handshake = {
                'type': 'satellite',
                'name': self.name,
                'area': None,
                'description': 'Hybrid Voice Satellite',
                'attribution': {
                    'name': 'Hybrid Voice Satellite',
                    'url': 'https://github.com/emme/hybrid-voice-satellite'
                },
                'installed': True,
                'version': '0.1.0',
                'capabilities': ['wake_word', 'audio_input', 'audio_output']
            }
            logger.debug(f"Sending handshake to {addr}: {handshake}")
            await self.send_json(writer, handshake)
            
            # Message loop
            pending_data = None
            while True:
                line = await reader.readline()
                if not line:
                    logger.info(f"Connection closed by {addr} (EOF)")
                    break
                
                try:
                    # Use ignore to prevent crash on binary data, but likely means we are desynced
                    decoded_line = line.decode('utf-8', errors='ignore').strip()
                    if not decoded_line:
                        continue
                     
                    # Hack: Handle concatenated JSON objects {data}{header} sent by some clients
                    if '}{' in decoded_line:
                         decoded_line = decoded_line.replace('}{', '} {')
                        
                    decoder = json.JSONDecoder(strict=False)
                    pos = 0
                    while pos < len(decoded_line):
                        # Skip whitespace
                        while pos < len(decoded_line) and decoded_line[pos].isspace():
                            pos += 1
                        if pos >= len(decoded_line):
                            break
                        
                        # Verify we are at the start of a JSON object
                        if decoded_line[pos] != '{':
                            # logger.warning(f"Invalid JSON start char '{decoded_line[pos]}' at pos {pos}. Scanning for next '{{'...")
                            next_brace = decoded_line.find('{', pos)
                            if next_brace == -1:
                                logger.debug(f"No JSON object found in remaining text: {repr(decoded_line[pos:])}")
                                break
                            
                            # Found a brace, skip garbage
                            # logger.warning(f"Skipped {next_brace - pos} bytes of garbage. Resuming at {next_brace}")
                            pos = next_brace
                            
                        # Parse JSON object
                        try:
                            message, idx = decoder.raw_decode(decoded_line, pos)
                            pos = idx
                        except json.JSONDecodeError:
                            # The '{' we found might be part of binary garbage. 
                            # Skip it and continue searching.
                            pos += 1
                            continue
                        
                        if not isinstance(message, dict):
                            logger.warning(f"Ignored non-dict message: {message}")
                            continue

                        # Handle detached data (message without type)
                        if not message.get('type'):
                            # This is likely the 'data' payload for the next event
                            pending_data = message
                            continue

                        # If we have pending data and this message expects it
                        if pending_data and message.get('data_length'):
                            message['data'] = pending_data
                            pending_data = None
                        elif pending_data:
                             if not message.get('data'):
                                 message['data'] = pending_data
                             pending_data = None

                        logger.debug(f"Parsed message: {message.get('type')}")
                        
                        # Check for binary payload
                        payload = None
                        payload_length = message.get('payload_length')
                        if payload_length:
                            logger.debug(f"Message expects {payload_length} bytes payload. Reading...")
                            try:
                                payload = await reader.readexactly(payload_length)
                                logger.debug(f"Read {len(payload)} bytes payload.")
                            except asyncio.IncompleteReadError:
                                logger.error(f"Incomplete payload read from {addr}")
                                return # Break outer loop
                        else:
                            logger.debug("No payload_length in message.")
                        
                        await self.handle_message(message, payload, writer)

                except UnicodeDecodeError:
                    logger.error(f"Invalid UTF-8 received from {addr}")
                    return # Close connection
                except Exception as e:
                     logger.error(f"Error processing line from {addr}: {e}", exc_info=True)
                     return # Close connection to be safe
                    
        except asyncio.CancelledError:
            # Handle task cancellation gracefully
            raise
        except ConnectionResetError:
            logger.info(f"Connection reset by {addr}")
        except Exception as e:
            logger.error(f"Error handling HA client {addr}: {e}", exc_info=True)

        finally:
            logger.info(f"Home Assistant disconnected from {addr}")
            self.ha_writers.discard(writer)
            self.client_tasks.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def handle_message(self, message: Dict[str, Any], payload: Optional[bytes], writer: asyncio.StreamWriter):
        """Process incoming message from Home Assistant."""
        msg_type = message.get('type')
        if not msg_type:
             return
             
        logger.info(f"Received from HA: {msg_type} (payload: {len(payload) if payload else 0} bytes)")
        
        if msg_type == 'ping':
            await self.send_json(writer, {'type': 'pong'})

        elif msg_type == 'describe':
            # Resend handshake/info in response to describe
            handshake = {
                'type': 'info',
                'data': {
                    'nickname': self.name,
                    'version': '0.1.0',
                    'attribution': {
                        'name': 'Hybrid Voice Satellite',
                        'url': 'https://github.com/emme/hybrid-voice-satellite'
                    },
                    'installed': True,
                    'satellite': {
                        'name': self.name,
                        'area': 'Office',
                        'description': 'Hybrid Voice Satellite',
                        'attribution': {
                            'name': 'Hybrid Voice Satellite',
                            'url': 'https://github.com/emme/hybrid-voice-satellite'
                        },
                        'installed': True,
                        'version': '0.1.0',
                        'capabilities': ['wake_word', 'audio_input', 'audio_output']
                    }
                }
            }
            logger.info(f"Sending info response: {handshake}")
            await self.send_json(writer, handshake)
            
        elif msg_type in ('audio', 'audio-chunk'):
            # TTS Audio chunk received
            # Prefer binary payload, fallback to hex data
            audio_bytes = payload
            if not audio_bytes:
                data_hex = message.get('data')
                if data_hex:
                    audio_bytes = bytes.fromhex(data_hex)
            
            if audio_bytes and hasattr(self, 'on_tts_audio'):
                await self.on_tts_audio(audio_bytes)
    
        elif msg_type == 'run_pipeline':
            # HA requesting pipeline run (might not be needed if we drive it)
            pass

    async def send_audio(self, audio_data: bytes):
        """Broadcast audio chunk to all connected HA instances."""
        if not self.ha_writers:
            return
        
        # DEBUG: Write to WAV file if open
        if self.debug_wav:
             try:
                 self.debug_wav.writeframes(audio_data)
             except Exception as e:
                 logger.error(f"Failed to write to debug wav: {e}")

        message = {
            'type': 'audio-chunk',
            'data': {
                'rate': 16000,
                'width': 2,
                'channels': 1,
            },
            'payload_length': len(audio_data),
        }
        
        # Broadcast to all
        for writer in list(self.ha_writers):
            try:
                # Send JSON headers
                await self.send_json(writer, message)
                # Send binary payload
                writer.write(audio_data)
                await writer.drain()
            except Exception as e:
                logger.error(f"Failed to send audio to HA: {e}")
                self.ha_writers.discard(writer)

    async def send_wake_word_detected(self):
        """Notify HA to start a pipeline."""
        
        # DEBUG: Start new recording session
        if self.debug_wav:
             try:
                 self.debug_wav.close()
             except:
                 pass
        
        try:
             import datetime
             filename = f"debug_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
             self.debug_wav = wave.open(filename, 'wb')
             self.debug_wav.setnchannels(1)
             self.debug_wav.setsampwidth(2) # 16-bit
             self.debug_wav.setframerate(16000)
             logger.info(f"Started recording debug audio to {filename}")
        except Exception as e:
             logger.error(f"Failed to start debug wav: {e}")
             self.debug_wav = None

        # 1. Start Pipeline
        # We start the pipeline at the 'stt' stage since we already did wake word
        pipeline_message = {
            'type': 'run-pipeline',
            'data': {
                'start_stage': 'asr',
                'end_stage': 'tts',
                'restart_on_end': False,
            }
        }
        
        for writer in list(self.ha_writers):
            try:
                await self.send_json(writer, pipeline_message)
            except Exception:
                pass

    async def send_json(self, writer: asyncio.StreamWriter, message: Dict[str, Any]):
        """Send JSON message to specific writer."""
        # logger.debug(f"Sending to HA: {message}")
        logger.info(f"Sending to HA: {message['type']} ({message.get('payload_length', 0)} bytes payload)")
        data = json.dumps(message).encode() + b'\n'
        writer.write(data)
        await writer.drain()

    # Callback hook
    async def on_tts_audio(self, audio_data: bytes):
        """Override to handle TTS audio."""
        pass
