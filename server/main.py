"""
Hybrid Voice Satellite Server
Main entry point for the Python server component.
"""
import asyncio
import logging
import yaml
import signal
import sys
from pathlib import Path
from websocket_server import WebSocketServer
from wyoming_server import WyomingServer


def load_config(config_path: str = "config.yaml") -> dict:
    config_file = Path(config_path)
    if not config_file.exists():
        example_config = Path("config.example.yaml")
        if example_config.exists():
            print(f"Config file not found, using {example_config}")
            config_file = example_config
        else:
            print("No configuration file found!")
            sys.exit(1)
    
    with open(config_file, 'r') as f:
        return yaml.safe_load(f)


def setup_logging(config: dict):
    log_level = config.get('logging', {}).get('level', 'INFO')
    log_file = config.get('logging', {}).get('file')
    
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file) if log_file else logging.NullHandler()
        ]
    )


async def main():
    """Main application entry point."""
    config = load_config()
    setup_logging(config)
    
    logger = logging.getLogger(__name__)
    logger.info("Starting Hybrid Voice Satellite Server")
    
    server_config = config.get('server', {})
    wyoming_config = config.get('wyoming', {})
    
    # Initialize Wyoming Server (Listens for HA)
    wyoming = WyomingServer(
        host=wyoming_config.get('host', '0.0.0.0'),
        port=wyoming_config.get('port', 10700)
    )
    
    # Initialize WebSocket server (Listens for Browsers)
    # Check for SSL certificates in client directory
    ssl_context = None
    # Use absolute path relative to this script
    current_dir = Path(__file__).parent.resolve()
    client_dir = current_dir.parent / "client"
    cert_file = client_dir / "cert.pem"
    key_file = client_dir / "key.pem"
    
    ssl_enabled = server_config.get('ssl', True)
    
    if ssl_enabled and cert_file.exists() and key_file.exists():
        import ssl
        logger.info(f"Loading SSL certificates from {cert_file}")
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(cert_file, key_file)
    else:
        if not ssl_enabled:
             logger.info("SSL disabled in configuration")
        else:
             logger.warning("No SSL certificates found. WebSocket will run in insecure mode (ws://)")
        
    ws_server = WebSocketServer(
        host=server_config.get('host', '0.0.0.0'),
        port=server_config.get('port', 8765),
        wyoming_server=wyoming,
        auth_token=server_config.get('auth_token'),
        ssl_context=ssl_context,
        client_config=config.get('client', {})
    )
    
    # Shutdown handler
    shutdown_event = asyncio.Event()
    
    # Simple signal handler that cancels the current task or sets event
    # Note: asyncio.run() handles SIGINT by default by cancelling the main task,
    # but we want a graceful shutdown sequence.
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        # Check if we are already shutting down
        if shutdown_event.is_set():
            logger.warning("Forced shutdown...")
            sys.exit(1)
        shutdown_event.set()
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start servers
        logger.info(f"Starting services...")
        await wyoming.start()
        await ws_server.start()
        
        # Wait for shutdown event Loop
        while not shutdown_event.is_set():
             await asyncio.sleep(0.1)
        
    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Shutting down...")
        try:
            await ws_server.stop()
            await wyoming.stop()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        logger.info("Shutdown complete")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # This catch block is for the top-level interrupt
        pass
    except Exception as e:
        print(f"Unexpected error: {e}")
