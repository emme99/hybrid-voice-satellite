/**
 * Hybrid Voice Satellite Client
 * Browser-based voice control with wake word detection
 */

// Configuration
const CONFIG = {
    wsUrl: localStorage.getItem('wsUrl') || 'ws://localhost:8765',
    wakeWord: localStorage.getItem('wakeWord') || 'ok_nabu',
    authToken: localStorage.getItem('authToken') || '',
    sampleRate: 16000,
    channels: 1
};

// Application state
const STATE = {
    ws: null,
    audioContext: null,
    mediaStream: null,
    audioWorkletNode: null,
    isActive: false,
    isListening: false,
    reconnectAttempts: 0,
    maxReconnectAttempts: 5
};

// DOM Elements
const elements = {
    activateBtn: document.getElementById('activate-btn'),
    settingsBtn: document.getElementById('settings-btn'),
    settingsPanel: document.getElementById('settings-panel'),
    saveSettings: document.getElementById('save-settings'),
    clearLogBtn: document.getElementById('clear-log-btn'),
    wsStatus: document.getElementById('ws-status'),
    wyomingStatus: document.getElementById('wyoming-status'),
    micStatus: document.getElementById('mic-status'),
    stateText: document.getElementById('state-text'),
    micVisualizer: document.getElementById('mic-visualizer'),
    debugLog: document.getElementById('debug-log'),
    wsUrlInput: document.getElementById('ws-url'),
    wakeWordSelect: document.getElementById('wake-word-select'),
    authTokenInput: document.getElementById('auth-token')
};

/**
 * Initialize the application
 */
async function init() {
    log('Application starting...', 'info');
    
    // Load saved settings
    elements.wsUrlInput.value = CONFIG.wsUrl;
    elements.wakeWordSelect.value = CONFIG.wakeWord;
    elements.authTokenInput.value = CONFIG.authToken;
    
    // Setup event listeners
    elements.activateBtn.addEventListener('click', toggleActivation);
    elements.settingsBtn.addEventListener('click', toggleSettings);
    elements.saveSettings.addEventListener('click', saveSettings);
    elements.clearLogBtn.addEventListener('click', clearLog);
    
    log('Application initialized', 'success');
}

/**
 * Toggle voice control activation
 */
async function toggleActivation() {
    if (!STATE.isActive) {
        await activate();
    } else {
        await deactivate();
    }
}

/**
 * Activate voice control
 */
async function activate() {
    try {
        log('Activating voice control...', 'info');
        
        // Initialize Audio Context
        await initAudioContext();
        
        // Connect to WebSocket server
        await connectWebSocket();
        
        // Request microphone access
        await requestMicrophone();
        
        // Load wake word model
        await loadWakeWordModel();
        
        STATE.isActive = true;
        updateUI();
        
        elements.activateBtn.innerHTML = '<span>Deactivate</span>';
        elements.activateBtn.classList.add('active');
        
        log('Voice control activated', 'success');
        
    } catch (error) {
        log(`Activation failed: ${error.message}`, 'error');
        await deactivate();
    }
}

/**
 * Deactivate voice control
 */
async function deactivate() {
    log('Deactivating voice control...', 'info');
    
    if (STATE.audioWorkletNode) {
        STATE.audioWorkletNode.disconnect();
        STATE.audioWorkletNode = null;
    }
    
    if (STATE.mediaStream) {
        STATE.mediaStream.getTracks().forEach(track => track.stop());
        STATE.mediaStream = null;
    }
    
    if (STATE.audioContext) {
        await STATE.audioContext.close();
        STATE.audioContext = null;
    }
    
    if (STATE.ws) {
        STATE.ws.close();
        STATE.ws = null;
    }
    
    STATE.isActive = false;
    STATE.isListening = false;
    
    updateUI();
    
    elements.activateBtn.innerHTML = '<span>Activate Voice Control</span>';
    elements.activateBtn.classList.remove('active');
    
    log('Voice control deactivated', 'info');
}

/**
 * Initialize Web Audio Context
 */
async function initAudioContext() {
    if (!STATE.audioContext) {
        STATE.audioContext = new (window.AudioContext || window.webkitAudioContext)({
            sampleRate: CONFIG.sampleRate
        });
        log('Audio context initialized', 'info');
    }
    
    if (STATE.audioContext.state === 'suspended') {
        await STATE.audioContext.resume();
    }
}

/**
 * Connect to WebSocket server
 */
function connectWebSocket() {
    return new Promise((resolve, reject) => {
        log(`Connecting to ${CONFIG.wsUrl}...`, 'info');
        
        STATE.ws = new WebSocket(CONFIG.wsUrl);
        
        STATE.ws.onopen = async () => {
            log('WebSocket connected', 'success');
            updateStatus('ws-status', 'connected', 'Connected');
            
            // Authenticate if token is set
            if (CONFIG.authToken) {
                STATE.ws.send(JSON.stringify({
                    type: 'auth',
                    token: CONFIG.authToken
                }));
            }
            
            STATE.reconnectAttempts = 0;
            resolve();
        };
        
        STATE.ws.onclose = () => {
            log('WebSocket disconnected', 'warning');
            updateStatus('ws-status', 'disconnected', 'Disconnected');
            
            // Attempt reconnection
            if (STATE.isActive && STATE.reconnectAttempts < STATE.maxReconnectAttempts) {
                STATE.reconnectAttempts++;
                log(`Reconnecting (attempt ${STATE.reconnectAttempts})...`, 'info');
                setTimeout(() => connectWebSocket(), 2000 * STATE.reconnectAttempts);
            }
        };
        
        STATE.ws.onerror = (error) => {
            log(`WebSocket error: ${error}`, 'error');
            reject(new Error('WebSocket connection failed'));
        };
        
        STATE.ws.onmessage = handleWebSocketMessage;
        
        // Timeout after 5 seconds
        setTimeout(() => {
            if (STATE.ws.readyState !== WebSocket.OPEN) {
                reject(new Error('Connection timeout'));
            }
        }, 5000);
    });
}

/**
 * Handle incoming WebSocket messages
 */
async function handleWebSocketMessage(event) {
    if (event.data instanceof Blob) {
        // Binary audio data (TTS response)
        const arrayBuffer = await event.data.arrayBuffer();
        await playAudioResponse(arrayBuffer);
    } else {
        // Text/JSON message
        try {
            const message = JSON.parse(event.data);
            handleControlMessage(message);
        } catch (e) {
            log(`Invalid message: ${e}`, 'error');
        }
    }
}

/**
 * Handle control messages from server
 */
function handleControlMessage(message) {
    switch (message.type) {
        case 'auth_ok':
            log('Authentication successful', 'success');
            break;
        case 'auth_failed':
            log('Authentication failed', 'error');
            deactivate();
            break;
        case 'pong':
            // Keep-alive response
            break;
        case 'status':
            updateStatus('wyoming-status', 
                message.wyoming_connected ? 'connected' : 'disconnected',
                message.wyoming_connected ? 'Connected' : 'Disconnected'
            );
            log(`Server status: ${message.clients} clients connected`, 'info');
            break;
        default:
            log(`Unknown message type: ${message.type}`, 'warning');
    }
}

/**
 * Request microphone access
 */
async function requestMicrophone() {
    try {
        log('Requesting microphone access...', 'info');
        
        STATE.mediaStream = await navigator.mediaDevices.getUserMedia({
            audio: {
                sampleRate: CONFIG.sampleRate,
                channelCount: CONFIG.channels,
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true
            }
        });
        
        updateStatus('mic-status', 'active', 'Active');
        log('Microphone access granted', 'success');
        
        // Create audio source
        const source = STATE.audioContext.createMediaStreamSource(STATE.mediaStream);
        
        // Connect to audio processing (will be replaced with AudioWorklet)
        await setupAudioProcessing(source);
        
    } catch (error) {
        throw new Error(`Microphone access denied: ${error.message}`);
    }
}

/**
 * Setup audio processing for wake word detection
 */
async function setupAudioProcessing(source) {
    // For now, create a simple script processor
    // TODO: Implement proper AudioWorklet with wake word detection
    
    const processor = STATE.audioContext.createScriptProcessor(4096, 1, 1);
    
    processor.onaudioprocess = (event) => {
        if (!STATE.isActive || !STATE.ws || STATE.ws.readyState !== WebSocket.OPEN) {
            return;
        }
        
        // Get audio data
        const inputData = event.inputBuffer.getChannelData(0);
        
        // Convert float32 to int16
        const int16Data = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
            const s = Math.max(-1, Math.min(1, inputData[i]));
            int16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        
        // Send to server (simplified - normally would only send after wake word)
        if (STATE.isListening) {
            STATE.ws.send(int16Data.buffer);
        }
    };
    
    source.connect(processor);
    processor.connect(STATE.audioContext.destination);
    
    STATE.audioWorkletNode = processor;
    
    log('Audio processing configured', 'info');
}

/**
 * Load wake word model (placeholder)
 */
async function loadWakeWordModel() {
    // TODO: Implement ONNX Runtime Web wake word detection
    // For now, simulate detection with a button
    log(`Wake word model "${CONFIG.wakeWord}" loaded (simulated)`, 'info');
    
    // Simulate wake word detection for testing
    document.addEventListener('keydown', (e) => {
        if (e.key === ' ' && STATE.isActive) {
            e.preventDefault();
            simulateWakeWord();
        }
    });
    
    log('Press SPACEBAR to simulate wake word detection', 'info');
}

/**
 * Simulate wake word detection (for testing)
 */
function simulateWakeWord() {
    if (!STATE.isListening) {
        log('Wake word detected!', 'success');
        STATE.isListening = true;
        updateUI();
        
        // Notify server
        if (STATE.ws && STATE.ws.readyState === WebSocket.OPEN) {
            STATE.ws.send(JSON.stringify({ type: 'wake_detected' }));
        }
        
        // Stop listening after 5 seconds
        setTimeout(() => {
            STATE.isListening = false;
            updateUI();
            log('Listening timeout', 'info');
        }, 5000);
    }
}

/**
 * Play audio response from server
 */
async function playAudioResponse(arrayBuffer) {
    try {
        const audioBuffer = await STATE.audioContext.decodeAudioData(arrayBuffer);
        const source = STATE.audioContext.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(STATE.audioContext.destination);
        source.start();
        
        log('Playing TTS response', 'info');
    } catch (error) {
        log(`Failed to play audio: ${error.message}`, 'error');
    }
}

/**
 * Update UI based on current state
 */
function updateUI() {
    if (STATE.isListening) {
        elements.micVisualizer.classList.remove('active');
        elements.micVisualizer.classList.add('listening');
        elements.stateText.textContent = 'Listening...';
        elements.stateText.className = 'state-text listening';
    } else if (STATE.isActive) {
        elements.micVisualizer.classList.add('active');
        elements.micVisualizer.classList.remove('listening');
        elements.stateText.textContent = 'Ready (Press SPACE)';
        elements.stateText.className = 'state-text active';
    } else {
        elements.micVisualizer.classList.remove('active', 'listening');
        elements.stateText.textContent = 'Click to activate';
        elements.stateText.className = 'state-text';
    }
}

/**
 * Update status badge
 */
function updateStatus(elementId, status, text) {
    const element = document.getElementById(elementId);
    element.className = `status-badge ${status}`;
    element.textContent = text;
}

/**
 * Toggle settings panel
 */
function toggleSettings() {
    elements.settingsPanel.classList.toggle('hidden');
}

/**
 * Save settings
 */
function saveSettings() {
    CONFIG.wsUrl = elements.wsUrlInput.value;
    CONFIG.wakeWord = elements.wakeWordSelect.value;
    CONFIG.authToken = elements.authTokenInput.value;
    
    localStorage.setItem('wsUrl', CONFIG.wsUrl);
    localStorage.setItem('wakeWord', CONFIG.wakeWord);
    localStorage.setItem('authToken', CONFIG.authToken);
    
    log('Settings saved', 'success');
    toggleSettings();
}

/**
 * Log message to debug panel
 */
function log(message, type = 'info') {
    const time = new Date().toLocaleTimeString();
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `<span class="log-time">${time}</span><span class="log-${type}">${message}</span>`;
    elements.debugLog.appendChild(entry);
    elements.debugLog.scrollTop = elements.debugLog.scrollHeight;
    
    console.log(`[${type.toUpperCase()}] ${message}`);
}

/**
 * Clear debug log
 */
function clearLog() {
    elements.debugLog.innerHTML = '';
    log('Log cleared', 'info');
}

/**
 * Send periodic keep-alive ping
 */
function startKeepAlive() {
    setInterval(() => {
        if (STATE.ws && STATE.ws.readyState === WebSocket.OPEN) {
            STATE.ws.send(JSON.stringify({ type: 'ping' }));
        }
    }, 30000); // Every 30 seconds
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    init();
    startKeepAlive();
});

// Handle page unload
window.addEventListener('beforeunload', () => {
    deactivate();
});
