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
    
    if cert_file.exists() and key_file.exists():
        import ssl
        logger.info(f"Loading SSL certificates from {cert_file}")
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(cert_file, key_file)
    else:
        logger.warning("No SSL certificates found. WebSocket will run in insecure mode (ws://)")
        
    ws_server = WebSocketServer(
        host=server_config.get('host', '0.0.0.0'),
        port=server_config.get('port', 8765),
        wyoming_server=wyoming,
        auth_token=server_config.get('auth_token'),
        ssl_context=ssl_context
    )
    
    # Shutdown handler
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        shutdown_event.set()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        # Start servers
        logger.info(f"Starting services...")
        await wyoming.start()
        await ws_server.start()
        
        # Wait for shutdown signal
        await shutdown_event.wait()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Shutting down...")
        await ws_server.stop()
        await wyoming.stop()
        logger.info("Shutdown complete")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
