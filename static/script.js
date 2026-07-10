// === DTOS Digital Twin Operating System - Frontend Controller ===
const sessionId = 'session_' + Math.random().toString(36).substr(2, 9);
let currentTab = 'chat';
let currentSubTab = 'decisions';
let currentFlagFilter = 'all';
let lastQuestion = '';
let lastAiResponse = '';
let lastContext = '';
let allDecisions = [];
let allBehaviors = [];
let allAuthority = [];

// === VOICE MESSAGE STATE (existing hold-to-record) ===
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recordingStartTime = 0;
let recordingTimer = null;
let currentAudio = null;

// === VOICE CALL STATE (NEW - real-time conversation) ===
let callSession = null;

// === DOM ===
const navItems = document.querySelectorAll('.nav-item');
const panels = document.querySelectorAll('.panel');
const subTabs = document.querySelectorAll('.sub-tab');
const subPanels = document.querySelectorAll('.sub-panel');
const reviewBadge = document.getElementById('reviewBadge');
const pillPending = document.getElementById('pillPending');

const chatBox = document.getElementById('chatBox');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const clearBtn = document.getElementById('clearBtn');
const flagBtn = document.getElementById('flagBtn');
const useKb = document.getElementById('useKb');

// Voice Message elements
const voiceBtn = document.getElementById('voiceBtn');
const voiceLabel = document.getElementById('voiceLabel');
const voiceWave = document.getElementById('voiceWave');
const voiceStatus = document.getElementById('voiceStatus');

// Voice Call elements (NEW)
const callBtn = document.getElementById('callBtn');
const callLabel = document.getElementById('callLabel');
const callWave = document.getElementById('callWave');
const callStatus = document.getElementById('callStatus');
const callTimer = document.getElementById('callTimer');

const cfgCompany = document.getElementById('cfgCompany');
const cfgSystem = document.getElementById('cfgSystem');
const cfgPersonality = document.getElementById('cfgPersonality');
const cfgAllowed = document.getElementById('cfgAllowed');
const cfgDenied = document.getElementById('cfgDenied');
const cfgRules = document.getElementById('cfgRules');
const cfgMargin = document.getElementById('cfgMargin');
const cfgHistory = document.getElementById('cfgHistory');
const cfgTemp = document.getElementById('cfgTemp');
const cfgAutoFlagConditional = document.getElementById('cfgAutoFlagConditional');
const cfgAutoFlagUncertain = document.getElementById('cfgAutoFlagUncertain');
const cfgVoiceId = document.getElementById('cfgVoiceId');
const cfgCartesiaModel = document.getElementById('cfgCartesiaModel');
const cfgVoiceSpeed = document.getElementById('cfgVoiceSpeed');
const cfgVadSensitivity = document.getElementById('cfgVadSensitivity');
const cfgSilenceTimeout = document.getElementById('cfgSilenceTimeout');
const cfgBargeIn = document.getElementById('cfgBargeIn');
const cfgTtsBuffer = document.getElementById('cfgTtsBuffer');
const saveConfigBtn = document.getElementById('saveConfigBtn');

const decQuestion = document.getElementById('decQuestion');
const decContext = document.getElementById('decContext');
const decAnswer = document.getElementById('decAnswer');
const decCategory = document.getElementById('decCategory');
const decAuthority = document.getElementById('decAuthority');
const decAction = document.getElementById('decAction');
const decReasoning = document.getElementById('decReasoning');
const addDecBtn = document.getElementById('addDecBtn');
const decisionsList = document.getElementById('decisionsList');
const decFilter = document.getElementById('decFilter');
const decSearch = document.getElementById('decSearch');

const behSituation = document.getElementById('behSituation');
const behTone = document.getElementById('behTone');
const behExample = document.getElementById('behExample');
const behDo = document.getElementById('behDo');
const behDont = document.getElementById('behDont');
const addBehBtn = document.getElementById('addBehBtn');
const behaviorsList = document.getElementById('behaviorsList');
const behSearch = document.getElementById('behSearch');

const authAction = document.getElementById('authAction');
const authAllowed = document.getElementById('authAllowed');
const authCondition = document.getElementById('authCondition');
const authFallback = document.getElementById('authFallback');
const addAuthBtn = document.getElementById('addAuthBtn');
const authorityList = document.getElementById('authorityList');
const authSearch = document.getElementById('authSearch');

const flagsList = document.getElementById('flagsList');
const filterPills = document.querySelectorAll('.pill');

// === INIT ===
navItems.forEach(item => {
    item.addEventListener('click', () => {
        const tab = item.dataset.tab;
        navItems.forEach(i => i.classList.toggle('active', i === item));
        panels.forEach(p => p.classList.toggle('active', p.id === tab + 'Panel'));
        currentTab = tab;
        if (tab === 'teach') { loadDecisions(); loadBehaviors(); loadAuthority(); }
        if (tab === 'review') loadFlags();
        if (tab === 'settings') { loadConfig(); loadStats(); }
    });
});

subTabs.forEach(btn => {
    btn.addEventListener('click', () => {
        const sub = btn.dataset.sub;
        subTabs.forEach(b => b.classList.toggle('active', b === btn));
        subPanels.forEach(p => p.classList.toggle('active', p.id === 'sub-' + sub));
        currentSubTab = sub;
        if (sub === 'decisions') loadDecisions();
        if (sub === 'behaviors') loadBehaviors();
        if (sub === 'authority') loadAuthority();
    });
});

filterPills.forEach(pill => {
    pill.addEventListener('click', () => {
        filterPills.forEach(p => p.classList.toggle('active', p === pill));
        currentFlagFilter = pill.dataset.filter;
        loadFlags();
    });
});

userInput.addEventListener('input', () => {
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 200) + 'px';
});

userInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'l') {
        e.preventDefault();
        clearChat();
    }
});

sendBtn.addEventListener('click', sendMessage);
clearBtn.addEventListener('click', clearChat);
flagBtn.addEventListener('click', () => manualFlag());
saveConfigBtn.addEventListener('click', saveConfig);
addDecBtn.addEventListener('click', addDecision);
addBehBtn.addEventListener('click', addBehavior);
addAuthBtn.addEventListener('click', addAuthority);
decFilter.addEventListener('change', () => loadDecisions(decFilter.value));
decSearch.addEventListener('input', () => filterDecisions());
behSearch.addEventListener('input', () => filterBehaviors());
authSearch.addEventListener('input', () => filterAuthority());

// === VOICE MESSAGE: Hold-to-Record (existing) ===
voiceBtn.addEventListener('mousedown', startRecording);
voiceBtn.addEventListener('mouseup', stopRecording);
voiceBtn.addEventListener('mouseleave', () => { if (isRecording) stopRecording(); });
voiceBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startRecording(); });
voiceBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopRecording(); });

// === VOICE CALL: Tap-to-Start (NEW) ===
callBtn.addEventListener('click', toggleVoiceCall);

// =============================================================================
// REAL-TIME VOICE CALL SESSION (NEW)
// =============================================================================

class RealtimeCallSession {
    constructor() {
        this.audioContext = null;
        this.mediaStream = null;
        this.processor = null;
        this.source = null;
        this.sttWs = null;
        this.ttsWs = null;
        this.isActive = false;
        this.isUserSpeaking = false;
        this.isAiSpeaking = false;
        this.ttsAudioQueue = [];
        this.ttsCurrentSource = null;
        this.interimTranscript = '';
        this.finalTranscript = '';
        this.silenceStart = null;
        this.callStartTime = null;
        this.callTimerInterval = null;
        this.ttsTextBuffer = '';           // FIX: renamed for clarity
        this.currentContextId = null;
        this.transcriptOverlay = null;
        this.pendingFinalization = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.vadThreshold = parseFloat(cfgVadSensitivity?.value || '0.015');
        this.silenceTimeout = parseInt(cfgSilenceTimeout?.value || '600');
        this.bargeInEnabled = (cfgBargeIn?.value || 'true') === 'true';
        // FIX: Buffer delay 0 because we do sentence-level buffering client-side
        this.ttsBufferDelay = 0;
        this.isFirstChunk = true;            // FIX: track first chunk for spacing
        this.audioQueueOffset = 0;           // FIX: for gapless scheduling
        this.nextPlayTime = 0;               // FIX: schedule audio gaplessly
    }

    async start() {
        if (this.isActive) return;
        try {
            this.mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    sampleRate: 16000,
                    channelCount: 1
                }
            });
            this.audioContext = new AudioContext({ sampleRate: 24000 }); // FIX: TTS outputs 24kHz
            await this.connectSTT();
            await this.connectTTS();
            this.startVAD();
            this.isActive = true;
            this.callStartTime = Date.now();
            this.startCallTimer();
            this.showTranscriptOverlay();
            callBtn.classList.add('active');
            callLabel.textContent = 'End Call';
            callWave.classList.remove('hidden');
            callStatus.textContent = 'Listening... Speak naturally';
            showToast('Voice call started — speak naturally');
        } catch (err) {
            console.error('Call start error:', err);
            showToast('Could not start voice call: ' + err.message, 'error');
            this.cleanup();
        }
    }

    stop() {
        if (!this.isActive) return;
        this.isActive = false;
        this.cleanup();
        callBtn.classList.remove('active');
        callLabel.textContent = 'Voice Call';
        callWave.classList.add('hidden');
        callStatus.textContent = 'Tap to start a real-time voice conversation';
        callTimer.classList.add('hidden');
        this.hideTranscriptOverlay();
        showToast('Voice call ended');
    }

    cleanup() {
        if (this.callTimerInterval) { clearInterval(this.callTimerInterval); this.callTimerInterval = null; }
        if (this.processor) { try { this.processor.disconnect(); } catch(e) {} this.processor = null; }
        if (this.source) { try { this.source.disconnect(); } catch(e) {} this.source = null; }
        if (this.mediaStream) { this.mediaStream.getTracks().forEach(t => t.stop()); this.mediaStream = null; }
        if (this.audioContext) { try { this.audioContext.close(); } catch(e) {} this.audioContext = null; }
        if (this.sttWs) { try { this.sttWs.close(); } catch(e) {} this.sttWs = null; }
        if (this.ttsWs) { try { this.ttsWs.close(); } catch(e) {} this.ttsWs = null; }
        this.ttsAudioQueue = [];
        if (this.ttsCurrentSource) { try { this.ttsCurrentSource.stop(); } catch(e) {} this.ttsCurrentSource = null; }
        this.isAiSpeaking = false;
        this.isUserSpeaking = false;
        this.pendingFinalization = false;
        this.currentContextId = null;
        this.ttsTextBuffer = '';
        this.finalTranscript = '';
        this.interimTranscript = '';
        this.isFirstChunk = true;
        this.nextPlayTime = 0;
    }

    startCallTimer() {
        callTimer.classList.remove('hidden');
        this.callTimerInterval = setInterval(() => {
            const elapsed = Math.floor((Date.now() - this.callStartTime) / 1000);
            const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
            const secs = String(elapsed % 60).padStart(2, '0');
            callTimer.textContent = `${mins}:${secs}`;
        }, 1000);
    }

    startVAD() {
        this.source = this.audioContext.createMediaStreamSource(this.mediaStream);
        this.processor = this.audioContext.createScriptProcessor(1024, 1, 1);
        this.processor.onaudioprocess = (e) => {
            if (!this.isActive) return;
            const input = e.inputBuffer.getChannelData(0);
            const volume = this.calculateVolume(input);
            if (volume > this.vadThreshold && this.isAiSpeaking && this.bargeInEnabled) {
                this.interruptAI();
            }
            if (volume > this.vadThreshold) {
                if (!this.isUserSpeaking) {
                    this.isUserSpeaking = true;
                    this.updateCallStatus('You are speaking...');
                }
                this.silenceStart = null;
                const pcm = this.floatToInt16(input);
                if (this.sttWs?.readyState === WebSocket.OPEN) {
                    this.sttWs.send(pcm.buffer);
                }
            } else {
                if (this.isUserSpeaking) {
                    if (!this.silenceStart) {
                        this.silenceStart = Date.now();
                    } else if (Date.now() - this.silenceStart > this.silenceTimeout) {
                        this.isUserSpeaking = false;
                        this.silenceStart = null;
                        this.updateCallStatus('Processing...');
                        if (this.sttWs?.readyState === WebSocket.OPEN && !this.pendingFinalization) {
                            this.pendingFinalization = true;
                            this.sttWs.send('finalize');
                        }
                    }
                }
            }
        };
        this.source.connect(this.processor);
        this.processor.connect(this.audioContext.destination);
    }

    calculateVolume(buffer) {
        let sum = 0;
        for (let i = 0; i < buffer.length; i++) { sum += buffer[i] * buffer[i]; }
        return Math.sqrt(sum / buffer.length);
    }

    floatToInt16(floatArray) {
        const int16 = new Int16Array(floatArray.length);
        for (let i = 0; i < floatArray.length; i++) {
            const s = Math.max(-1, Math.min(1, floatArray[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        return int16;
    }

    interruptAI() {
        console.log('[Call] Barge-in detected');
        this.isAiSpeaking = false;
        this.ttsAudioQueue = [];
        if (this.ttsCurrentSource) {
            try { this.ttsCurrentSource.stop(); } catch(e) {}
            this.ttsCurrentSource = null;
        }
        if (this.ttsWs?.readyState === WebSocket.OPEN && this.currentContextId) {
            this.ttsWs.send(JSON.stringify({
                type: 'cancel',
                context_id: this.currentContextId
            }));
        }
        this.ttsTextBuffer = '';
        this.currentContextId = null;
        this.isFirstChunk = true;
        this.nextPlayTime = 0;
        this.updateCallStatus('You interrupted — listening...');
    }

    async connectSTT() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/api/stt/`;
        return new Promise((resolve, reject) => {
            this.sttWs = new WebSocket(wsUrl);
            let resolved = false;
            this.sttWs.onopen = () => {
                this.sttWs.send(JSON.stringify({
                    type: 'config',
                    model: 'ink-whisper',
                    language: 'en',
                    encoding: 'pcm_s16le',
                    sample_rate: 16000
                }));
            };
            this.sttWs.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'ready') {
                    if (!resolved) { resolved = true; resolve(); }
                }
                else if (data.type === 'transcript') {
                    if (data.is_final) {
                        this.finalTranscript = (this.finalTranscript + ' ' + data.text).trim();
                        this.updateTranscriptOverlay(data.text, false);
                    } else {
                        this.interimTranscript = data.text;
                        this.updateTranscriptOverlay(data.text, true);
                    }
                }
                else if (data.type === 'flush_done') {
                    this.pendingFinalization = false;
                    if (this.finalTranscript && !this.isUserSpeaking) {
                        const text = this.finalTranscript;
                        this.finalTranscript = '';
                        this.interimTranscript = '';
                        this.processUserTurn(text);
                    }
                }
                else if (data.type === 'done') {
                    this.pendingFinalization = false;
                }
                else if (data.type === 'error') {
                    console.error('[STT] Error:', data.error);
                    this.pendingFinalization = false;
                    this.updateCallStatus('STT Error — retrying...');
                }
            };
            this.sttWs.onerror = (err) => {
                console.error('[STT] WS error:', err);
                if (!resolved) { resolved = true; reject(err); }
            };
            this.sttWs.onclose = () => {
                if (!resolved) { resolved = true; reject(new Error('STT closed')); }
                if (this.isActive) {
                    this.reconnectAttempts++;
                    if (this.reconnectAttempts <= this.maxReconnectAttempts) {
                        setTimeout(() => this.connectSTT().catch(console.error), 1000 * this.reconnectAttempts);
                    }
                }
            };
            setTimeout(() => { if (!resolved) { resolved = true; reject(new Error('STT timeout')); } }, 10000);
        });
    }

    async connectTTS() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws/api/tts/`;
        return new Promise((resolve, reject) => {
            this.ttsWs = new WebSocket(wsUrl);
            let resolved = false;
            this.ttsWs.onopen = () => {
                this.ttsWs.send(JSON.stringify({
                    type: 'config',
                    model_id: cfgCartesiaModel.value || 'sonic-3.5',
                    voice_id: cfgVoiceId.value || 'a5136bf9-224c-4d76-b823-52bd5efcffcc',
                    language: 'en'
                }));
            };
            this.ttsWs.onmessage = async (event) => {
                if (event.data instanceof Blob) {
                    const arrayBuffer = await event.data.arrayBuffer();
                    // FIX: Schedule for gapless playback instead of queue+shift
                    this.scheduleAudioPlayback(arrayBuffer);
                    if (!this.isAiSpeaking) {
                        this.isAiSpeaking = true;
                        this.updateCallStatus('DTOS is speaking...');
                    }
                    return;
                }
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'ready') {
                        if (!resolved) { resolved = true; resolve(); }
                    }
                    else if (data.type === 'flush_done') {
                        // Context fully flushed
                    }
                    else if (data.type === 'done') {
                        // All audio for context delivered, check if done playing
                        setTimeout(() => {
                            if (!this.isAiSpeaking && this.ttsAudioQueue.length === 0) {
                                this.currentContextId = null;
                                this.updateCallStatus('Listening...');
                            }
                        }, 500);
                    }
                    else if (data.type === 'error') {
                        console.error('[TTS] Error:', data.error);
                    }
                } catch(e) {}
            };
            this.ttsWs.onerror = (err) => {
                console.error('[TTS] WS error:', err);
                if (!resolved) { resolved = true; reject(err); }
            };
            this.ttsWs.onclose = () => {
                if (!resolved) { resolved = true; reject(new Error('TTS closed')); }
                if (this.isActive) {
                    setTimeout(() => this.connectTTS().catch(console.error), 1000);
                }
            };
            setTimeout(() => { if (!resolved) { resolved = true; reject(new Error('TTS timeout')); } }, 10000);
        });
    }

    // FIX: Gapless audio scheduling using AudioContext clock
    scheduleAudioPlayback(arrayBuffer) {
        if (!this.audioContext || !this.isActive) return;
        if (this.audioContext.state === 'suspended') {
            this.audioContext.resume().catch(() => {});
        }

        const int16 = new Int16Array(arrayBuffer);
        if (int16.length === 0) return;

        const float32 = new Float32Array(int16.length);
        for (let i = 0; i < int16.length; i++) {
            float32[i] = int16[i] / 32768.0;
        }

        const audioBuffer = this.audioContext.createBuffer(1, float32.length, 24000);
        audioBuffer.copyToChannel(float32, 0);

        const source = this.audioContext.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(this.audioContext.destination);

        const now = this.audioContext.currentTime;
        // Start immediately if first chunk, otherwise schedule after previous chunk ends
        if (this.nextPlayTime <= now) {
            this.nextPlayTime = now;
        }
        source.start(this.nextPlayTime);
        this.nextPlayTime += audioBuffer.duration;

        // Mark AI as done speaking after last chunk finishes
        source.onended = () => {
            const timeUntilEnd = this.nextPlayTime - this.audioContext.currentTime;
            if (timeUntilEnd <= 0.05) {  // All queued audio has played
                this.isAiSpeaking = false;
                this.currentContextId = null;
                this.isFirstChunk = true;
                this.updateCallStatus('Listening...');
            }
        };
    }

    async processUserTurn(text) {
        if (!text || !this.isActive) return;
        console.log('[Call] User turn:', text);
        this.updateCallStatus('DTOS is thinking...');
        appendMessage('user', text);
        lastQuestion = text;
        try {
            const response = await fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    session_id: sessionId,
                    use_kb: true
                })
            });
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let fullResponse = '';
            this.currentContextId = crypto.randomUUID();
            this.isFirstChunk = true;
            this.ttsTextBuffer = '';
            while (this.isActive) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();
                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const chunk = JSON.parse(line.slice(6));
                            if (chunk.text) {
                                fullResponse += chunk.text;
                                this.streamTextToTTS(chunk.text);
                            }
                            if (chunk.done) {
                                lastAiResponse = fullResponse;
                            }
                        } catch(e) {}
                    }
                }
            }
            this.flushTTSBuffer();
        } catch (err) {
            console.error('[Call] LLM stream error:', err);
            this.updateCallStatus('Error — try again');
        }
    }

    // FIX: Complete rewrite of text streaming to TTS
    // Key insight from Cartesia docs: "Streamed inputs should form a valid transcript when joined"
    // We accumulate text and only send complete sentences, preserving exact spacing
    streamTextToTTS(textChunk) {
        if (!this.isActive || !this.ttsWs || this.ttsWs.readyState !== WebSocket.OPEN) return;
        if (!this.currentContextId) return;

        this.ttsTextBuffer += textChunk;

        // Look for complete sentences (ending with . ! ? followed by space or end)
        // We need to be careful not to split mid-word
        let buffer = this.ttsTextBuffer;
        let sentUpTo = 0;

        // Find all sentence boundaries
        const sentenceEndRegex = /[.!?]+(?:\s+|$)/g;
        let match;
        let lastSentenceEnd = -1;

        while ((match = sentenceEndRegex.exec(buffer)) !== null) {
            lastSentenceEnd = match.index + match[0].length;
        }

        if (lastSentenceEnd > 0) {
            // We have at least one complete sentence
            const textToSend = buffer.substring(0, lastSentenceEnd);
            const remaining = buffer.substring(lastSentenceEnd);

            // Send the complete text
            const payload = {
                type: 'chunk',
                context_id: this.currentContextId,
                text: textToSend,
                continue: true,
                voice_id: cfgVoiceId.value || 'a5136bf9-224c-4d76-b823-52bd5efcffcc',
                model_id: cfgCartesiaModel.value || 'sonic-3.5',
                language: 'en',
                max_buffer_delay_ms: 0  // FIX: 0 because we buffer client-side
            };

            const speed = parseFloat(cfgVoiceSpeed.value || '1.0');
            if (speed !== 1.0) payload.speed = speed;

            console.log('[TTS] Sending chunk:', JSON.stringify({
                text: textToSend.substring(0, 80) + (textToSend.length > 80 ? '...' : ''),
                len: textToSend.length,
                continue: true
            }));

            this.ttsWs.send(JSON.stringify(payload));
            this.ttsTextBuffer = remaining;
        }
    }

    flushTTSBuffer() {
        if (this.ttsTextBuffer.length > 0 && this.ttsWs?.readyState === WebSocket.OPEN && this.currentContextId) {
            const payload = {
                type: 'chunk',
                context_id: this.currentContextId,
                text: this.ttsTextBuffer,
                continue: false,
                voice_id: cfgVoiceId.value || 'a5136bf9-224c-4d76-b823-52bd5efcffcc',
                model_id: cfgCartesiaModel.value || 'sonic-3.5',
                language: 'en',
                max_buffer_delay_ms: 0
            };
            const speed = parseFloat(cfgVoiceSpeed.value || '1.0');
            if (speed !== 1.0) payload.speed = speed;

            console.log('[TTS] Flushing final chunk:', JSON.stringify({
                text: this.ttsTextBuffer.substring(0, 80) + (this.ttsTextBuffer.length > 80 ? '...' : ''),
                len: this.ttsTextBuffer.length,
                continue: false
            }));

            this.ttsWs.send(JSON.stringify(payload));
            this.ttsTextBuffer = '';
        }
    }

    updateCallStatus(status) {
        callStatus.textContent = status;
    }

    showTranscriptOverlay() {
        this.transcriptOverlay = document.createElement('div');
        this.transcriptOverlay.className = 'call-transcript-overlay';
        this.transcriptOverlay.innerHTML = `
            <div class="transcript-label">Live Transcript</div>
            <div class="transcript-text" id="liveTranscript">Say something...</div>
        `;
        document.body.appendChild(this.transcriptOverlay);
    }

    hideTranscriptOverlay() {
        if (this.transcriptOverlay) {
            this.transcriptOverlay.remove();
            this.transcriptOverlay = null;
        }
    }

    updateTranscriptOverlay(text, isInterim) {
        const el = document.getElementById('liveTranscript');
        if (el) {
            el.textContent = text || 'Say something...';
            el.classList.toggle('interim', isInterim);
        }
    }
}

// Toggle voice call on/off
async function toggleVoiceCall() {
    if (callSession && callSession.isActive) {
        callSession.stop();
        callSession = null;
    } else {
        callSession = new RealtimeCallSession();
        await callSession.start();
    }
}

// =============================================================================
// API & CONFIG
// =============================================================================

async function loadConfig() {
    try {
        const res = await fetch('/api/config');
        const config = await res.json();
        cfgCompany.value = config.company_name || '';
        cfgSystem.value = config.system_prompt || '';
        cfgPersonality.value = config.personality || '';
        cfgAllowed.value = config.allowed_topics || '';
        cfgDenied.value = config.denied_topics || '';
        cfgRules.value = config.response_rules || '';
        cfgMargin.value = config.margin_threshold || '25';
        cfgHistory.value = config.max_history || '10';
        cfgTemp.value = config.temperature || '0.3';
        cfgAutoFlagConditional.value = config.auto_flag_conditional || 'true';
        cfgAutoFlagUncertain.value = config.auto_flag_uncertain || 'true';
        cfgVoiceId.value = config.cartesia_voice_id || 'e07c00bc-4134-4eae-9ea4-1a55fb45746b';
        cfgCartesiaModel.value = config.cartesia_model || 'sonic-3.5';
        cfgVoiceSpeed.value = config.cartesia_speed || '1.0';

        if (config.vad_sensitivity) cfgVadSensitivity.value = config.vad_sensitivity;
        if (config.silence_timeout) cfgSilenceTimeout.value = config.silence_timeout;
        if (config.barge_in) cfgBargeIn.value = config.barge_in;
        if (config.tts_buffer_delay) cfgTtsBuffer.value = config.tts_buffer_delay;
    } catch (e) { console.error(e); }
}

async function saveConfig() {
    const config = {
        company_name: cfgCompany.value,
        system_prompt: cfgSystem.value,
        personality: cfgPersonality.value,
        allowed_topics: cfgAllowed.value,
        denied_topics: cfgDenied.value,
        response_rules: cfgRules.value,
        margin_threshold: cfgMargin.value,
        max_history: cfgHistory.value,
        temperature: cfgTemp.value,
        auto_flag_conditional: cfgAutoFlagConditional.value,
        auto_flag_uncertain: cfgAutoFlagUncertain.value,
        cartesia_voice_id: cfgVoiceId.value,
        cartesia_model: cfgCartesiaModel.value,
        cartesia_speed: cfgVoiceSpeed.value,
        vad_sensitivity: cfgVadSensitivity.value,
        silence_timeout: cfgSilenceTimeout.value,
        barge_in: cfgBargeIn.value,
        tts_buffer_delay: cfgTtsBuffer.value
    };
    saveConfigBtn.disabled = true;
    saveConfigBtn.innerHTML = '<span class="spinner"></span> Saving...';
    try {
        await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
        saveConfigBtn.innerHTML = 'Saved!';
        showToast('Governance configuration saved successfully');
        setTimeout(() => { saveConfigBtn.innerHTML = 'Save Configuration'; saveConfigBtn.disabled = false; }, 1500);
    } catch (e) {
        saveConfigBtn.innerHTML = 'Error';
        saveConfigBtn.disabled = false;
    }
}

async function loadStats() {
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        document.getElementById('statDecisions').textContent = data.active_decisions || 0;
        document.getElementById('statBehaviors').textContent = data.active_behaviors || 0;
        document.getElementById('statAuthority').textContent = data.active_authority || 0;
        document.getElementById('statPending').textContent = data.pending_flags || 0;
        document.getElementById('statResolved').textContent = data.resolved_flags || 0;
        document.getElementById('statMessages').textContent = data.total_messages || 0;
        updateBadge(data.pending_flags || 0);
    } catch (e) { console.error(e); }
}

function updateBadge(count) {
    reviewBadge.textContent = count;
    pillPending.textContent = count;
    reviewBadge.classList.toggle('hidden', count === 0);
}

// =============================================================================
// VOICE MESSAGE: Hold-to-Record (existing — unchanged functionality)
// =============================================================================

async function audioBufferToRawPCM(audioBuffer, targetSampleRate = 16000) {
    const numChannels = audioBuffer.numberOfChannels;
    const originalRate = audioBuffer.sampleRate;
    const duration = audioBuffer.duration;
    const targetLength = Math.floor(duration * targetSampleRate);
    const monoData = new Float32Array(targetLength);

    for (let ch = 0; ch < numChannels; ch++) {
        const channelData = audioBuffer.getChannelData(ch);
        for (let i = 0; i < targetLength; i++) {
            const srcIdx = (i / targetSampleRate) * originalRate;
            const idx0 = Math.floor(srcIdx);
            const idx1 = Math.min(idx0 + 1, channelData.length - 1);
            const frac = srcIdx - idx0;
            const sample = channelData[idx0] * (1 - frac) + channelData[idx1] * frac;
            monoData[i] += sample / numChannels;
        }
    }

    const int16Data = new Int16Array(targetLength);
    for (let i = 0; i < targetLength; i++) {
        let sample = monoData[i];
        sample = Math.max(-1.0, Math.min(1.0, sample));
        int16Data[i] = Math.round(sample * 32767);
    }
    return int16Data.buffer;
}

async function startRecording() {
    if (isRecording) return;
    if (!navigator.mediaDevices || !window.MediaRecorder) {
        showToast('Voice recording not supported in this browser', 'error');
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const mimeType = MediaRecorder.isTypeSupported('audio/webm')
            ? 'audio/webm'
            : (MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' : 'audio/ogg');

        mediaRecorder = new MediaRecorder(stream, { mimeType });
        audioChunks = [];
        mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop());
            await processVoiceRecording(mimeType);
        };
        mediaRecorder.start(100);
        isRecording = true;
        recordingStartTime = Date.now();
        voiceBtn.classList.add('recording');
        voiceWave.classList.remove('hidden');
        voiceLabel.textContent = 'Recording voice input...';
        recordingTimer = setInterval(() => {
            const secs = Math.floor((Date.now() - recordingStartTime) / 1000);
            voiceStatus.textContent = `${secs}s`;
            if (secs > 60) stopRecording();
        }, 500);
    } catch (err) {
        console.error('Mic error:', err);
        showToast('Could not access microphone. Check permissions.', 'error');
    }
}

function stopRecording() {
    if (!isRecording || !mediaRecorder) return;
    clearInterval(recordingTimer);
    isRecording = false;
    if (mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    voiceBtn.classList.remove('recording');
    voiceWave.classList.add('hidden');
    voiceLabel.textContent = 'Processing voice input...';
    voiceStatus.textContent = '';
}

async function processVoiceRecording(mimeType) {
    if (audioChunks.length === 0) {
        showToast('No audio captured. Hold the button longer.', 'error');
        voiceLabel.textContent = 'Hold to speak to DTOS';
        return;
    }
    const audioBlob = new Blob(audioChunks, { type: mimeType });
    if (audioBlob.size === 0) {
        showToast('No audio captured. Hold the button longer.', 'error');
        voiceLabel.textContent = 'Hold to speak to DTOS';
        audioChunks = [];
        return;
    }
    try {
        await processVoiceViaWebSocket(audioBlob);
    } catch (e) {
        console.warn('STT WebSocket failed, falling back to HTTP:', e.message);
        await processVoiceViaHTTP(audioBlob, mimeType);
    }
}

async function processVoiceViaWebSocket(audioBlob) {
    voiceLabel.textContent = 'Converting audio...';
    let audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const arrayBuffer = await audioBlob.arrayBuffer();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
    const rawPCM = await audioBufferToRawPCM(audioBuffer, 16000);

    voiceLabel.textContent = 'Transcribing via WebSocket...';
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/api/stt/`;

    return new Promise((resolve, reject) => {
        const ws = new WebSocket(wsUrl);
        const transcriptParts = [];
        let wsReady = false;
        let resolved = false;

        ws.onopen = () => {
            ws.send(JSON.stringify({
                type: 'config', model: 'ink-whisper', language: 'en',
                encoding: 'pcm_s16le', sample_rate: 16000
            }));
        };

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (data.type === 'ready') {
                wsReady = true;
                const chunkSize = 3200;
                const uint8View = new Uint8Array(rawPCM);
                (async () => {
                    for (let i = 0; i < uint8View.length; i += chunkSize) {
                        if (ws.readyState !== WebSocket.OPEN) break;
                        ws.send(uint8View.slice(i, i + chunkSize));
                        await new Promise(r => setTimeout(r, 10));
                    }
                    if (ws.readyState === WebSocket.OPEN) ws.send('finalize');
                })();
            } else if (data.type === 'transcript' && data.is_final && data.text) {
                transcriptParts.push(data.text);
            } else if (data.type === 'flush_done') {
                if (!resolved) {
                    resolved = true;
                    ws.close();
                    const transcript = transcriptParts.join(' ').trim();
                    if (!transcript) {
                        reject(new Error('No speech detected'));
                    } else {
                        userInput.value = transcript;
                        userInput.style.height = 'auto';
                        userInput.style.height = Math.min(userInput.scrollHeight, 200) + 'px';
                        voiceLabel.textContent = 'Hold to speak to DTOS';
                        setTimeout(() => sendMessage(), 300);
                        resolve(transcript);
                    }
                }
            } else if (data.type === 'done') {
                // Session close ack, ignore if already resolved
            } else if (data.type === 'error') {
                if (!resolved) { resolved = true; ws.close(); reject(new Error(data.error)); }
            }
        };

        ws.onerror = () => {
            if (!resolved) { resolved = true; reject(new Error('WebSocket connection failed')); }
        };

        ws.onclose = () => {
            if (!resolved) { resolved = true; reject(new Error('WebSocket closed unexpectedly')); }
        };

        setTimeout(() => {
            if (!resolved) { resolved = true; ws.close(); reject(new Error('STT timeout')); }
        }, 20000);
    });
}

async function processVoiceViaHTTP(audioBlob, mimeType) {
    voiceLabel.textContent = 'Transcribing voice input...';
    voiceStatus.textContent = '';
    const ext = mimeType.includes('webm') ? 'webm' : (mimeType.includes('mp4') ? 'm4a' : 'ogg');
    const formData = new FormData();
    formData.append('audio', audioBlob, `recording.${ext}`);
    try {
        const res = await fetch('/api/stt', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.error) { showToast(`STT Error: ${data.error}`, 'error'); voiceLabel.textContent = 'Hold to speak to DTOS'; return; }
        const transcript = data.transcript?.trim();
        if (!transcript) { showToast('No speech detected. Try again.', 'error'); voiceLabel.textContent = 'Hold to speak to DTOS'; return; }
        userInput.value = transcript;
        userInput.style.height = 'auto';
        userInput.style.height = Math.min(userInput.scrollHeight, 200) + 'px';
        voiceLabel.textContent = 'Hold to speak to DTOS';
        setTimeout(() => sendMessage(), 300);
    } catch (e) {
        console.error('STT fetch error:', e);
        showToast('Transcription failed. Try again.', 'error');
        voiceLabel.textContent = 'Hold to speak to DTOS';
    } finally {
        audioChunks = [];
    }
}

// =============================================================================
// TTS PLAYBACK for chat messages (existing — simplified, no WebSocket TTS)
// =============================================================================

async function playTTS(text, btnElement) {
    if (btnElement.classList.contains('playing')) {
        if (window._ttsAudio) {
            window._ttsAudio.pause();
            window._ttsAudio = null;
        }
        btnElement.classList.remove('playing');
        return;
    }

    document.querySelectorAll('.tts-btn').forEach(b => b.classList.remove('playing'));
    btnElement.classList.add('playing');

    try {
        const res = await fetch('/api/tts', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        if (!res.ok) { const err = await res.json(); throw new Error(err.error || 'TTS failed'); }
        const audioBlob = await res.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        window._ttsAudio = new Audio(audioUrl);
        window._ttsAudio.onended = () => { btnElement.classList.remove('playing'); window._ttsAudio = null; URL.revokeObjectURL(audioUrl); };
        window._ttsAudio.onerror = () => { btnElement.classList.remove('playing'); window._ttsAudio = null; URL.revokeObjectURL(audioUrl); };
        await window._ttsAudio.play();
    } catch (e) {
        console.error('TTS error:', e);
        showToast(`TTS Error: ${e.message}`, 'error');
        btnElement.classList.remove('playing');
    }
}

// =============================================================================
// CHAT
// =============================================================================

function appendMessage(role, text, analysis = null) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    if (analysis && role === 'assistant') {
        const bar = document.createElement('div');
        bar.className = 'analysis-bar';
        if (analysis.situations_detected?.length > 0) {
            const s = document.createElement('span'); s.className = 'analysis-badge ab-info';
            s.textContent = `📍 Situations: ${analysis.situations_detected.join(', ')}`; bar.appendChild(s);
        }
        if (analysis.has_forbidden) {
            const a = document.createElement('span'); a.className = 'analysis-badge ab-danger';
            a.textContent = '🛡️ FORBIDDEN'; bar.appendChild(a);
        } else if (analysis.has_conditional) {
            const a = document.createElement('span'); a.className = 'analysis-badge ab-warn';
            a.textContent = '🛡️ Conditional'; bar.appendChild(a);
        } else {
            const a = document.createElement('span'); a.className = 'analysis-badge ab-ok';
            a.textContent = '🛡️ Cleared'; bar.appendChild(a);
        }
        if (analysis.decisions_retrieved > 0) {
            const d = document.createElement('span'); d.className = 'analysis-badge ab-ok';
            d.textContent = `📚 ${analysis.decisions_retrieved} decision patterns`; bar.appendChild(d);
        }
        if (analysis.behaviors_applied > 0) {
            const b = document.createElement('span'); b.className = 'analysis-badge ab-ok';
            b.textContent = `🎭 ${analysis.behaviors_applied} persona styles`; bar.appendChild(b);
        }
        div.appendChild(bar);
        if (analysis.suggest_flag) {
            const banner = document.createElement('div');
            banner.className = 'flag-banner';
            banner.innerHTML = `<span>⚠️ This response may need audit review. Confidence is low or authority is conditional.</span><button onclick="manualFlag()">Flag for Audit</button>`;
            div.appendChild(banner);
        }
    }
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    if (role === 'assistant') {
        bubble.innerHTML = formatText(escapeHtml(text));
        const ttsBtn = document.createElement('button');
        ttsBtn.className = 'tts-btn';
        ttsBtn.title = 'Read aloud via voice engine';
        ttsBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>`;
        ttsBtn.onclick = () => playTTS(text, ttsBtn);
        bubble.appendChild(ttsBtn);
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
        copyBtn.title = 'Copy response to clipboard';
        copyBtn.onclick = () => { navigator.clipboard.writeText(text); showToast('Copied to clipboard'); };
        bubble.appendChild(copyBtn);
    } else {
        bubble.textContent = text;
    }
    div.appendChild(bubble);
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function showTyping() {
    const div = document.createElement('div');
    div.className = 'message assistant typing';
    div.id = 'typingIndicator';
    div.innerHTML = '<div class="bubble"><span></span><span></span><span></span></div>';
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
}

function hideTyping() {
    const el = document.getElementById('typingIndicator');
    if (el) el.remove();
}

async function sendMessage() {
    const text = userInput.value.trim();
    if (!text) return;
    lastQuestion = text;
    appendMessage('user', text);
    userInput.value = ''; userInput.style.height = 'auto';
    sendBtn.disabled = true; showTyping();
    
    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, session_id: sessionId, use_kb: useKb.checked })
        });
        const data = await res.json();
        hideTyping();
        
        if (data.error) {
            appendMessage('assistant', 'Error: ' + data.error);
        } else {
            lastAiResponse = data.reply;
            appendMessage('assistant', data.reply, data);
            // NEW: Render thoughts panel
            if (data.thoughts) {
                renderThoughts(data.thoughts);
            }
        }
    } catch (e) {
        hideTyping();
        appendMessage('assistant', 'Network error...');
    } finally {
        sendBtn.disabled = false; userInput.focus();
    }
}

function renderThoughts(thoughts) {
    const container = document.getElementById('thoughtsBody');
    const time = new Date().toLocaleTimeString('en-US', { hour12: false });
    
    // Determine confidence styling
    const score = thoughts.confidence_score || 75;
    const confClass = score >= 80 ? 'high' : score >= 50 ? 'med' : 'low';
    const confFill = score >= 80 ? 'fill-high' : score >= 50 ? 'fill-med' : 'fill-low';
    const confColor = score >= 80 ? 'conf-high' : score >= 50 ? 'conf-med' : 'conf-low';
    
    // Authority status
    let authHtml = '';
    if (thoughts.has_forbidden) {
        authHtml = `<div class="auth-status auth-forbidden">🛡️ FORBIDDEN — Autonomous action blocked</div>`;
    } else if (thoughts.has_conditional) {
        authHtml = `<div class="auth-status auth-conditional">🛡️ Conditional Authority — ${thoughts.authority_violations.length} rule(s) matched</div>`;
    } else {
        authHtml = `<div class="auth-status auth-cleared">🛡️ Authority Cleared — No violations</div>`;
    }
    
    // Situation tags
    const tagsHtml = (thoughts.situations_detected || []).map(s => {
        const cls = s.includes('hostile') || s.includes('forbidden') ? 'tag-danger' : 
                    s.includes('risk') || s.includes('boundary') ? 'tag-warn' : 'tag-info';
        return `<span class="sit-tag ${cls}">${s}</span>`;
    }).join('');
    
    // References
    const refsHtml = (thoughts.references || []).map(ref => {
        const icons = { decision: '📊', authority: '🛡️', behavior: '🎭' };
        return `
        <div class="ref-item" onclick="highlightRef('${ref.id}')">
            <span class="ref-icon">${icons[ref.type] || '📄'}</span>
            <span class="ref-id">${ref.id}</span>
            <span class="ref-title">${escapeHtml(ref.title)}</span>
            <span class="ref-score">${ref.score.toFixed(2)}</span>
        </div>`;
    }).join('');
    
    const card = document.createElement('div');
    card.className = 'thought-card active';
    card.innerHTML = `
        <div class="thought-header">
            <span class="thought-title">Governance Analysis</span>
            <span class="thought-time">${time}</span>
        </div>
        ${tagsHtml ? `<div class="situation-tags">${tagsHtml}</div>` : ''}
        ${authHtml}
        <div class="confidence-row">
            <span class="confidence-label">Confidence</span>
            <div class="confidence-bar-bg">
                <div class="confidence-bar-fill ${confFill}" style="width: ${score}%"></div>
            </div>
            <span class="confidence-value ${confColor}">${score}%</span>
        </div>
        <div class="thought-content">${escapeHtml(thoughts.reasoning_trace)}</div>
        <div class="refs-section">
            <div class="refs-label">📚 Knowledge Base References</div>
            <div class="refs-list">${refsHtml || '<span class="ref-item">No references retrieved</span>'}</div>
        </div>
    `;
    
    // Remove empty state if present
    const empty = container.querySelector('.empty-thoughts');
    if (empty) empty.remove();
    
    // Remove 'active' from previous cards
    container.querySelectorAll('.thought-card').forEach(c => c.classList.remove('active'));
    
    container.prepend(card); // Newest first
}

function quickAsk(text) {
    userInput.value = text;
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 200) + 'px';
    userInput.focus();
}

async function clearChat() {
    try {
        await fetch('/api/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sessionId }) });
    } catch (error) { console.warn('Failed to clear session on server:', error); }
    chatBox.innerHTML = `
        <div class="message assistant welcome">
            <div class="bubble">
                <div class="bubble-title">Welcome to DTOS</div>
                <p>I am your Digital Twin Operating System. Describe any meeting, governance scenario, or advisory situation and I will respond using evidence-grounded reasoning, governance controls, and your configured authority boundaries.</p>
                <div class="quick-pills">
                    <button class="quick-pill" onclick="quickAsk('I have a high-stakes board meeting tomorrow. What governance checks should I run?')">Board meeting prep</button>
                    <button class="quick-pill" onclick="quickAsk('A stakeholder is asking me to sign a financial commitment. What is my authority?')">Authority boundary</button>
                    <button class="quick-pill" onclick="quickAsk('I need to switch to a more formal persona for an investor call. How should I adapt?')">Persona switch</button>
                </div>
            </div>
        </div>`;
    lastQuestion = ''; lastAiResponse = ''; lastContext = '';
}

// =============================================================================
// DECISIONS
// =============================================================================

async function loadDecisions(category = '') {
    try {
        decisionsList.innerHTML = renderSkeleton(3);
        const url = category ? `/api/decisions?category=${category}` : '/api/decisions';
        const res = await fetch(url);
        const data = await res.json();
        allDecisions = data.decisions || [];
        filterDecisions();
    } catch (e) { console.error(e); }
}

function filterDecisions() {
    const search = decSearch.value.toLowerCase();
    const filtered = allDecisions.filter(d => {
        const text = (d.question + ' ' + d.context + ' ' + d.ideal_answer + ' ' + d.reasoning).toLowerCase();
        return text.includes(search);
    });
    renderDecisions(filtered);
}

function renderDecisions(decisions) {
    if (!decisions || decisions.length === 0) {
        decisionsList.innerHTML = '<div class="empty-state"><div class="empty-icon">📊</div><div class="empty-title">No decision patterns yet</div><div class="empty-desc">Add your first governance decision pattern to train the Digital Twin</div></div>';
        return;
    }
    decisionsList.innerHTML = decisions.map(d => {
        const authClass = {
            'high': 'badge-auth-high', 'medium': 'badge-auth-medium',
            'low': 'badge-auth-low', 'forbidden': 'badge-auth-forbidden'
        }[d.authority_level] || 'badge-auth-low';
        return `
        <div class="material-item ${d.active ? '' : 'inactive'}">
            <div class="material-header">
                <span class="material-title">${escapeHtml(d.question.substring(0, 100))}${d.question.length > 100 ? '...' : ''}</span>
                <div class="material-badges">
                    <span class="badge-small badge-cat">${d.category}</span>
                    <span class="badge-small ${authClass}">${d.authority_level}</span>
                    <span class="badge-small badge-action">${d.action_type}</span>
                </div>
            </div>
            <div class="material-content">
                <span class="label">Context:</span> ${escapeHtml(d.context.substring(0, 150))}${d.context.length > 150 ? '...' : ''}
                <span class="label">Answer:</span> ${escapeHtml(d.ideal_answer.substring(0, 150))}${d.ideal_answer.length > 150 ? '...' : ''}
                <span class="label">Reasoning:</span> ${escapeHtml(d.reasoning.substring(0, 200))}${d.reasoning.length > 200 ? '...' : ''}
            </div>
            <div class="material-footer">
                <span class="material-meta">ID: ${d.id} · ${d.created_at ? d.created_at.slice(0, 10) : ''}</span>
                <div class="material-actions">
                    <button class="btn-toggle ${d.active ? 'active' : ''}" onclick="toggleDecision(${d.id})">${d.active ? 'On' : 'Off'}</button>
                    <button class="btn-danger" onclick="deleteDecision(${d.id})">Delete</button>
                </div>
            </div>
        </div>`;
    }).join('');
}

async function addDecision() {
    clearErrors();
    const data = {
        question: decQuestion.value.trim(), context: decContext.value.trim(),
        ideal_answer: decAnswer.value.trim(), category: decCategory.value,
        authority_level: decAuthority.value, action_type: decAction.value,
        reasoning: decReasoning.value.trim()
    };
    let hasError = false;
    if (!data.question) { showError('errDecQuestion', 'Question is required'); hasError = true; }
    if (!data.context) { showError('errDecContext', 'Context is required'); hasError = true; }
    if (!data.ideal_answer) { showError('errDecAnswer', 'Ideal answer is required'); hasError = true; }
    if (!data.reasoning) { showError('errDecReasoning', 'Reasoning is required'); hasError = true; }
    if (hasError) return;
    addDecBtn.disabled = true; addDecBtn.innerHTML = '<span class="spinner"></span> Adding...';
    try {
        await fetch('/api/decisions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        decQuestion.value = ''; decContext.value = ''; decAnswer.value = ''; decReasoning.value = '';
        loadDecisions(decFilter.value); loadStats();
        showToast('Governance decision pattern added');
        addDecBtn.innerHTML = 'Add Decision Pattern'; addDecBtn.disabled = false;
    } catch (e) { addDecBtn.innerHTML = 'Error'; addDecBtn.disabled = false; }
}

async function toggleDecision(id) { await fetch(`/api/decisions/${id}/toggle`, { method: 'POST' }); loadDecisions(decFilter.value); }
async function deleteDecision(id) { if (!confirm('Delete this governance decision pattern?')) return; await fetch(`/api/decisions/${id}`, { method: 'DELETE' }); loadDecisions(decFilter.value); loadStats(); }

// =============================================================================
// BEHAVIORS
// =============================================================================

async function loadBehaviors() {
    try {
        behaviorsList.innerHTML = renderSkeleton(3);
        const res = await fetch('/api/behaviors');
        const data = await res.json();
        allBehaviors = data.behaviors || [];
        filterBehaviors();
    } catch (e) { console.error(e); }
}

function filterBehaviors() {
    const search = behSearch.value.toLowerCase();
    const filtered = allBehaviors.filter(b => {
        const text = (b.situation + ' ' + b.tone + ' ' + b.example_response + ' ' + b.do_rules + ' ' + b.dont_rules).toLowerCase();
        return text.includes(search);
    });
    renderBehaviors(filtered);
}

function renderBehaviors(behaviors) {
    if (!behaviors || behaviors.length === 0) {
        behaviorsList.innerHTML = "<div class='empty-state'><div class='empty-icon'>🎭</div><div class='empty-title'>No persona behaviors yet</div><div class='empty-desc'>Add persona behavior styles to guide the Digital Twin's tone and approach</div></div>";
        return;
    }
    behaviorsList.innerHTML = behaviors.map(b => `
        <div class="material-item ${b.active ? '' : 'inactive'}">
            <div class="material-header">
                <span class="material-title">${escapeHtml(b.situation?.substring(0, 100) || '')}</span>
                <span class="badge-small badge-cat">${escapeHtml(b.tone || '')}</span>
            </div>
            <div class="material-content">
                <span class="label">Example:</span> ${escapeHtml(b.example_response?.substring(0, 150) || '')}${b.example_response?.length > 150 ? '...' : ''}
                <span class="label">Do:</span> ${escapeHtml(b.do_rules || '')}
                <span class="label">Don't:</span> ${escapeHtml(b.dont_rules || '')}
            </div>
            <div class="material-footer">
                <span class="material-meta">ID: ${escapeHtml(b.id?.toString() || '')}</span>
                <div class="material-actions">
                    <button class="btn-toggle ${b.active ? 'active' : ''}" onclick="toggleBehavior(${Number(b.id)})">${b.active ? 'On' : 'Off'}</button>
                    <button class="btn-danger" onclick="deleteBehavior(${Number(b.id)})">Delete</button>
                </div>
            </div>
        </div>
    `).join('');
}

async function addBehavior() {
    clearErrors();
    const data = {
        situation: behSituation.value.trim(), tone: behTone.value.trim(),
        example_response: behExample.value.trim(), do_rules: behDo.value.trim(), dont_rules: behDont.value.trim()
    };
    let hasError = false;
    if (!data.situation) { showError('errBehSituation', 'Situation is required'); hasError = true; }
    if (!data.tone) { showError('errBehTone', 'Tone is required'); hasError = true; }
    if (!data.example_response) { showError('errBehExample', 'Example response is required'); hasError = true; }
    if (!data.do_rules) { showError('errBehDo', 'Do rules are required'); hasError = true; }
    if (!data.dont_rules) { showError('errBehDont', "Don't rules are required"); hasError = true; }
    if (hasError) return;
    addBehBtn.disabled = true; addBehBtn.innerHTML = '<span class="spinner"></span> Adding...';
    try {
        await fetch('/api/behaviors', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        behSituation.value = ''; behTone.value = ''; behExample.value = ''; behDo.value = ''; behDont.value = '';
        loadBehaviors(); loadStats();
        showToast('Persona behavior style added');
        addBehBtn.innerHTML = 'Add Persona Behavior'; addBehBtn.disabled = false;
    } catch (e) { addBehBtn.innerHTML = 'Error'; addBehBtn.disabled = false; }
}

async function toggleBehavior(id) { await fetch(`/api/behaviors/${id}/toggle`, { method: 'POST' }); loadBehaviors(); }
async function deleteBehavior(id) { if (!confirm('Delete this persona behavior?')) return; await fetch(`/api/behaviors/${id}`, { method: 'DELETE' }); loadBehaviors(); loadStats(); }

// =============================================================================
// AUTHORITY
// =============================================================================

async function loadAuthority() {
    try {
        authorityList.innerHTML = renderSkeleton(3);
        const res = await fetch('/api/authority');
        const data = await res.json();
        allAuthority = data.rules || [];
        filterAuthority();
    } catch (e) { console.error(e); }
}

function filterAuthority() {
    const search = authSearch.value.toLowerCase();
    const filtered = allAuthority.filter(r => {
        const text = (r.action_type + ' ' + r.condition + ' ' + r.fallback_behavior).toLowerCase();
        return text.includes(search);
    });
    renderAuthority(filtered);
}

function renderAuthority(rules) {
    if (!rules || rules.length === 0) {
        authorityList.innerHTML = '<div class="empty-state"><div class="empty-icon">🛡️</div><div class="empty-title">No authority rules yet</div><div class="empty-desc">Add authority and safety rules to control what the Digital Twin can and cannot do</div></div>';
        return;
    }
    authorityList.innerHTML = rules.map(r => {
        const cls = r.allowed === 'no' ? 'badge-auth-forbidden' : r.allowed === 'conditional' ? 'badge-auth-medium' : 'badge-auth-low';
        return `
        <div class="material-item ${r.active ? '' : 'inactive'}">
            <div class="material-header">
                <span class="material-title">${escapeHtml(r.action_type)}</span>
                <span class="badge-small ${cls}">${r.allowed.toUpperCase()}</span>
            </div>
            <div class="material-content">
                <span class="label">Condition:</span> ${escapeHtml(r.condition)}
                <span class="label">Fallback:</span> ${escapeHtml(r.fallback_behavior)}
            </div>
            <div class="material-footer">
                <span class="material-meta">ID: ${r.id}</span>
                <div class="material-actions">
                    <button class="btn-toggle ${r.active ? 'active' : ''}" onclick="toggleAuthority(${r.id})">${r.active ? 'On' : 'Off'}</button>
                    <button class="btn-danger" onclick="deleteAuthority(${r.id})">Delete</button>
                </div>
            </div>
        </div>`;
    }).join('');
}

async function addAuthority() {
    clearErrors();
    const data = {
        action_type: authAction.value.trim(), allowed: authAllowed.value,
        condition: authCondition.value.trim(), fallback_behavior: authFallback.value.trim()
    };
    let hasError = false;
    if (!data.action_type) { showError('errAuthAction', 'Action type is required'); hasError = true; }
    if (!data.condition) { showError('errAuthCondition', 'Condition is required'); hasError = true; }
    if (!data.fallback_behavior) { showError('errAuthFallback', 'Fallback behavior is required'); hasError = true; }
    if (hasError) return;
    addAuthBtn.disabled = true; addAuthBtn.innerHTML = '<span class="spinner"></span> Adding...';
    try {
        await fetch('/api/authority', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
        authAction.value = ''; authCondition.value = ''; authFallback.value = '';
        loadAuthority(); loadStats();
        showToast('Authority and safety rule added');
        addAuthBtn.innerHTML = 'Add Authority & Safety Rule'; addAuthBtn.disabled = false;
    } catch (e) { addAuthBtn.innerHTML = 'Error'; addAuthBtn.disabled = false; }
}

async function toggleAuthority(id) { await fetch(`/api/authority/${id}/toggle`, { method: 'POST' }); loadAuthority(); }
async function deleteAuthority(id) { if (!confirm('Delete this authority and safety rule?')) return; await fetch(`/api/authority/${id}`, { method: 'DELETE' }); loadAuthority(); loadStats(); }

// =============================================================================
// REVIEW / FLAGS
// =============================================================================

async function loadFlags() {
    try {
        flagsList.innerHTML = renderSkeleton(2);
        const url = currentFlagFilter === 'all' ? '/api/flags' : `/api/flags?status=${currentFlagFilter}`;
        const res = await fetch(url);
        const data = await res.json();
        updateBadge(data.pending_count || 0);
        if (!data.flags || data.flags.length === 0) {
            flagsList.innerHTML = '<div class="empty-state"><div class="empty-icon">🚩</div><div class="empty-title">No flagged interactions</div><div class="empty-desc">Interactions flagged for audit review will appear here</div></div>';
            return;
        }
        flagsList.innerHTML = data.flags.map(f => renderFlagCard(f)).join('');
    } catch (e) { console.error(e); }
}

function renderFlagCard(f) {
    const statusClass = f.status;
    const statusLabel = f.status.toUpperCase();
    let actionsHtml = '';
    if (f.status === 'pending') {
        actionsHtml = `
            <div class="flag-answer-form">
                <textarea id="flagAnswer-${f.id}" placeholder="Enter the ideal answer or guidance..."></textarea>
                <div class="convert-options">
                    <label><input type="checkbox" id="convDec-${f.id}" onchange="toggleConvert(${f.id}, 'dec')"> 📊 Convert to Decision</label>
                    <label><input type="checkbox" id="convBeh-${f.id}" onchange="toggleConvert(${f.id}, 'beh')"> 🎭 Convert to Behavior</label>
                    <label><input type="checkbox" id="convAuth-${f.id}" onchange="toggleConvert(${f.id}, 'auth')"> 🛡️ Convert to Authority</label>
                </div>
                <div id="convFields-${f.id}" class="convert-fields"></div>
                <div class="flag-btn-row">
                    <button class="btn-success" onclick="resolveFlag(${f.id})">Save Answer</button>
                    <button class="btn-muted" onclick="dismissFlag(${f.id})">Dismiss</button>
                </div>
            </div>`;
    } else {
        actionsHtml = `
            <div class="admin-answer-display">
                <span class="ans-label">Admin Answer</span>
                <div class="ans-text">${formatText(escapeHtml(f.admin_answer || ''))}</div>
                ${f.converted_to ? `<span class="converted-badge ${f.converted_to}">Converted to ${f.converted_to}</span>` : ''}
            </div>`;
    }
    return `
        <div class="flag-card ${statusClass}">
            <div class="flag-header">
                <div class="flag-question">${escapeHtml(f.question)}</div>
                <span class="flag-status status-${statusClass}">${statusLabel}</span>
            </div>
            <div class="flag-reason"><span>🚩</span> ${f.flag_reason.replace(/_/g, ' ')}</div>
            ${f.context ? `<div class="flag-context"><span class="ctx-label">Context</span>${escapeHtml(f.context)}</div>` : ''}
            ${f.ai_response ? `<div class="flag-ai-response"><span class="ctx-label">AI Response</span>${formatText(escapeHtml(f.ai_response))}</div>` : ''}
            <div class="flag-actions">${actionsHtml}</div>
        </div>`;
}

function toggleConvert(flagId, type) {
    const container = document.getElementById(`convFields-${flagId}`);
    const hasDec = document.getElementById(`convDec-${flagId}`).checked;
    const hasBeh = document.getElementById(`convBeh-${flagId}`).checked;
    const hasAuth = document.getElementById(`convAuth-${flagId}`).checked;
    if (!hasDec && !hasBeh && !hasAuth) { container.classList.remove('active'); container.innerHTML = ''; return; }
    container.classList.add('active');
    let html = '';
    if (hasDec) {
        html += `<div class="convert-section-title">📊 Decision Fields</div>
            <div class="form-row">
                <div class="form-group third"><select id="cfDecCat-${flagId}"><option value="pricing">Pricing</option><option value="acquisition">Acquisition</option><option value="negotiation">Negotiation</option><option value="risk">Risk</option><option value="strategy">Strategy</option><option value="legal">Legal</option></select></div>
                <div class="form-group third"><select id="cfDecAuth-${flagId}"><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="forbidden">Forbidden</option></select></div>
                <div class="form-group third"><select id="cfDecAct-${flagId}"><option value="buy">Buy</option><option value="reject">Reject</option><option value="negotiate">Negotiate</option><option value="escalate">Escalate</option><option value="delay">Delay</option><option value="conditional">Conditional</option></select></div>
            </div>
            <input type="text" id="cfDecCtx-${flagId}" placeholder="Context summary" value="From review panel">
            <input type="text" id="cfDecReason-${flagId}" placeholder="Reasoning summary" value="Admin-provided answer">`;
    }
    if (hasBeh) {
        html += `<div class="convert-section-title" style="color:var(--info)">🎭 Behavior Fields</div>
            <input type="text" id="cfBehTone-${flagId}" placeholder="Tone (e.g., calm but firm)" value="professional">`;
    }
    if (hasAuth) {
        html += `<div class="convert-section-title" style="color:var(--warning)">🛡️ Authority Fields</div>
            <input type="text" id="cfAuthAct-${flagId}" placeholder="Action type name" value="flagged action">
            <select id="cfAuthAllow-${flagId}"><option value="conditional">Conditional</option><option value="yes">Yes</option><option value="no">No</option></select>
            <input type="text" id="cfAuthCond-${flagId}" placeholder="Condition" value="reviewed by admin">`;
    }
    container.innerHTML = html;
}

async function resolveFlag(flagId) {
    const answer = document.getElementById(`flagAnswer-${flagId}`).value.trim();
    if (!answer) { showToast('Please provide an answer', 'error'); return; }
    const hasDec = document.getElementById(`convDec-${flagId}`)?.checked;
    const hasBeh = document.getElementById(`convBeh-${flagId}`)?.checked;
    const hasAuth = document.getElementById(`convAuth-${flagId}`)?.checked;
    let convertedTo = null;
    if (hasDec) convertedTo = 'decision';
    else if (hasBeh) convertedTo = 'behavior';
    else if (hasAuth) convertedTo = 'authority';
    const payload = {
        admin_answer: answer, converted_to: convertedTo,
        question: document.querySelector(`#flagAnswer-${flagId}`).closest('.flag-card').querySelector('.flag-question').textContent
    };
    if (hasDec) {
        payload.category = document.getElementById(`cfDecCat-${flagId}`).value;
        payload.authority_level = document.getElementById(`cfDecAuth-${flagId}`).value;
        payload.action_type = document.getElementById(`cfDecAct-${flagId}`).value;
        payload.context = document.getElementById(`cfDecCtx-${flagId}`).value;
        payload.reasoning = document.getElementById(`cfDecReason-${flagId}`).value;
    }
    if (hasBeh) {
        payload.tone = document.getElementById(`cfBehTone-${flagId}`).value;
        payload.do_rules = 'follow admin guidance';
        payload.dont_rules = 'ignore admin guidance';
    }
    if (hasAuth) {
        payload.action_type = document.getElementById(`cfAuthAct-${flagId}`).value;
        payload.allowed = document.getElementById(`cfAuthAllow-${flagId}`).value;
        payload.condition = document.getElementById(`cfAuthCond-${flagId}`).value;
    }
    try {
        await fetch(`/api/flags/${flagId}/resolve`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        loadFlags(); loadStats(); showToast('Audit flag resolved and governance answer saved');
    } catch (e) { showToast('Failed to resolve', 'error'); }
}

async function dismissFlag(flagId) {
    if (!confirm('Dismiss this audit flag?')) return;
    try { await fetch(`/api/flags/${flagId}/dismiss`, { method: 'POST' }); loadFlags(); loadStats(); }
    catch (e) { showToast('Failed to dismiss audit flag', 'error'); }
}

async function manualFlag() {
    if (!lastQuestion) { showToast('No interaction to flag for audit', 'error'); return; }
    try {
        await fetch('/api/flags', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, question: lastQuestion, ai_response: lastAiResponse, context: lastContext, flag_reason: 'manual' })
        });
        showToast('🚩 Flagged for audit review'); loadStats();
    } catch (e) { showToast('Failed to flag for audit', 'error'); }
}

// =============================================================================
// UTILS
// =============================================================================

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatText(text) {
    return text
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/__(.+?)__/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/_(.+?)_/g, '<em>$1</em>')
        .replace(/^#{1,6}\s+(.+)$/gm, '<strong style="color:var(--primary);display:block;margin:12px 0 4px;">$1</strong>')
        .replace(/^[-•]\s+(.+)$/gm, '<li style="margin-left:16px;margin-bottom:4px;">$1</li>')
        .replace(/\n/g, '<br>');
}

function showError(id, msg) {
    const el = document.getElementById(id);
    if (el) { el.textContent = msg; el.style.display = 'block'; }
}

function clearErrors() {
    document.querySelectorAll('.field-error').forEach(el => { el.textContent = ''; el.style.display = 'none'; });
}

function showToast(msg, type = 'success') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => { toast.classList.add('fade-out'); setTimeout(() => toast.remove(), 300); }, 3000);
}

function renderSkeleton(count) {
    return Array(count).fill(0).map(() => `
        <div class="skeleton-item">
            <div class="skeleton-line short"></div>
            <div class="skeleton-line"></div>
            <div class="skeleton-line medium"></div>
        </div>
    `).join('');
}

// Initial load
loadConfig(); loadStats(); loadDecisions(); loadBehaviors(); loadAuthority(); loadFlags();