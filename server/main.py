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
from wyoming_client import WyomingClient


def load_config(config_path: str = "config.yaml") -> dict:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to config file
        
    Returns:
        Configuration dictionary
    """
    config_file = Path(config_path)
    
    # Try example config if main config doesn't exist
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
    """
    Setup logging configuration.
    
    Args:
        config: Configuration dictionary
    """
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
    # Load configuration
    config = load_config()
    setup_logging(config)
    
    logger = logging.getLogger(__name__)
    logger.info("Starting Hybrid Voice Satellite Server")
    
    # Extract configuration
    server_config = config.get('server', {})
    ha_config = config.get('home_assistant', {})
    
    # Initialize Wyoming client
    wyoming = WyomingClient(
        host=ha_config.get('host', 'homeassistant.local'),
        port=ha_config.get('port', 10700)
    )
    
    # Initialize WebSocket server
    ws_server = WebSocketServer(
        host=server_config.get('host', '0.0.0.0'),
        port=server_config.get('port', 8765),
        wyoming_client=wyoming,
        auth_token=server_config.get('auth_token')
    )
    
    # Shutdown handler
    shutdown_event = asyncio.Event()
    
    def signal_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        shutdown_event.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Connect to Home Assistant
        logger.info(f"Connecting to Home Assistant at {ha_config.get('host')}:{ha_config.get('port')}")
        await wyoming.connect()
        
        # Start servers
        logger.info("Starting WebSocket server and Wyoming keep-alive")
        await asyncio.gather(
            ws_server.serve(),
            wyoming.keep_alive(),
            shutdown_event.wait()
        )
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        logger.info("Shutting down...")
        await wyoming.disconnect()
        logger.info("Shutdown complete")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
