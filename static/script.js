// === STATE ===
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

// === VOICE STATE ===
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recordingStartTime = 0;
let recordingTimer = null;
let currentAudio = null;

// === DOM ===
const navItems = document.querySelectorAll('.nav-item');
const panels = document.querySelectorAll('.panel');
const subTabs = document.querySelectorAll('.sub-tab');
const subPanels = document.querySelectorAll('.sub-panel');
const reviewBadge = document.getElementById('reviewBadge');
const pillPending = document.getElementById('pillPending');

// Chat
const chatBox = document.getElementById('chatBox');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const clearBtn = document.getElementById('clearBtn');
const flagBtn = document.getElementById('flagBtn');
const useKb = document.getElementById('useKb');

// Voice
const voiceBtn = document.getElementById('voiceBtn');
const voiceLabel = document.getElementById('voiceLabel');
const voiceWave = document.getElementById('voiceWave');
const voiceStatus = document.getElementById('voiceStatus');

// Settings
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
const saveConfigBtn = document.getElementById('saveConfigBtn');

// Teaching
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

// Review
const flagsList = document.getElementById('flagsList');
const filterPills = document.querySelectorAll('.pill');

// === INIT ===
navItems.forEach(item => {
    item.addEventListener('click', () => {
        const tab = item.dataset.tab;
        navItems.forEach(i => i.classList.toggle('active', i === item));
        panels.forEach(p => p.classList.toggle('active', p.id === tab + 'Panel'));
        currentTab = tab;
        if (tab === 'teach') {
            loadDecisions(); loadBehaviors(); loadAuthority();
        }
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

// Voice events
voiceBtn.addEventListener('mousedown', startRecording);
voiceBtn.addEventListener('mouseup', stopRecording);
voiceBtn.addEventListener('mouseleave', () => { if (isRecording) stopRecording(); });
voiceBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startRecording(); });
voiceBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopRecording(); });

// === API ===
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
        cartesia_speed: cfgVoiceSpeed.value
    };
    saveConfigBtn.disabled = true;
    saveConfigBtn.innerHTML = '<span class="spinner"></span> Saving...';
    try {
        await fetch('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(config) });
        saveConfigBtn.innerHTML = '✅ Saved!';
        showToast('Configuration saved successfully');
        setTimeout(() => { saveConfigBtn.innerHTML = '💾 Save Configuration'; saveConfigBtn.disabled = false; }, 1500);
    } catch (e) {
        saveConfigBtn.innerHTML = '❌ Error';
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

// === VOICE FUNCTIONS ===

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
        
        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) audioChunks.push(e.data);
        };
        
        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(t => t.stop());
            await processVoiceRecording();
        };
        
        mediaRecorder.start(100);
        isRecording = true;
        recordingStartTime = Date.now();
        
        voiceBtn.classList.add('recording');
        voiceWave.classList.remove('hidden');
        voiceLabel.textContent = 'Recording...';
        
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
    
    if (mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
    }
    
    voiceBtn.classList.remove('recording');
    voiceWave.classList.add('hidden');
    voiceLabel.textContent = 'Hold to speak';
    voiceStatus.textContent = '';
}

async function processVoiceRecording() {
    if (audioChunks.length === 0) return;
    
    const mimeType = mediaRecorder.mimeType || 'audio/webm';
    const ext = mimeType.includes('webm') ? 'webm' : (mimeType.includes('mp4') ? 'm4a' : 'ogg');
    const audioBlob = new Blob(audioChunks, { type: mimeType });
    
    voiceLabel.textContent = 'Transcribing...';
    voiceStatus.textContent = '';
    
    const formData = new FormData();
    formData.append('audio', audioBlob, `recording.${ext}`);
    
    try {
        const res = await fetch('/api/stt', { method: 'POST', body: formData });
        const data = await res.json();
        
        if (data.error) {
            showToast(`STT Error: ${data.error}`, 'error');
            voiceLabel.textContent = 'Hold to speak';
            return;
        }
        
        const transcript = data.transcript?.trim();
        if (!transcript) {
            showToast('No speech detected. Try again.', 'error');
            voiceLabel.textContent = 'Hold to speak';
            return;
        }
        
        userInput.value = transcript;
        userInput.style.height = 'auto';
        userInput.style.height = Math.min(userInput.scrollHeight, 200) + 'px';
        voiceLabel.textContent = 'Hold to speak';
        
        setTimeout(() => sendMessage(), 300);
        
    } catch (e) {
        console.error('STT error:', e);
        showToast('Transcription failed. Try again.', 'error');
        voiceLabel.textContent = 'Hold to speak';
    }
}

async function playTTS(text, btnElement) {
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
    
    if (btnElement.classList.contains('playing')) {
        document.querySelectorAll('.tts-btn').forEach(b => b.classList.remove('playing'));
        return;
    }
    
    document.querySelectorAll('.tts-btn').forEach(b => b.classList.remove('playing'));
    btnElement.classList.add('playing');
    
    try {
        const res = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || 'TTS failed');
        }
        
        const audioBlob = await res.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        currentAudio = new Audio(audioUrl);
        
        currentAudio.onended = () => {
            btnElement.classList.remove('playing');
            currentAudio = null;
            URL.revokeObjectURL(audioUrl);
        };
        
        currentAudio.onerror = () => {
            btnElement.classList.remove('playing');
            currentAudio = null;
            URL.revokeObjectURL(audioUrl);
        };
        
        await currentAudio.play();
        
    } catch (e) {
        console.error('TTS error:', e);
        showToast(`TTS Error: ${e.message}`, 'error');
        btnElement.classList.remove('playing');
    }
}

// === DECISIONS ===
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
        decisionsList.innerHTML = '<div class="empty-state"><div class="empty-icon">📊</div><div class="empty-title">No decisions yet</div><div class="empty-desc">Add your first decision pattern to train the AI</div></div>';
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
        showToast('Decision pattern added');
        addDecBtn.innerHTML = '➕ Add Decision'; addDecBtn.disabled = false;
    } catch (e) { addDecBtn.innerHTML = '❌ Error'; addDecBtn.disabled = false; }
}

async function toggleDecision(id) { await fetch(`/api/decisions/${id}/toggle`, { method: 'POST' }); loadDecisions(decFilter.value); }
async function deleteDecision(id) { if (!confirm('Delete this decision?')) return; await fetch(`/api/decisions/${id}`, { method: 'DELETE' }); loadDecisions(decFilter.value); loadStats(); }

// === BEHAVIORS ===
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
        behaviorsList.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🎭</div>
                <div class="empty-title">No behaviors yet</div>
                <div class="empty-desc">Add behavior styles to guide the AI's tone and approach</div>
            </div>
        `;
        return;
    }

    behaviorsList.innerHTML = behaviors.map(b => `
        <div class="material-item ${b.active ? '' : 'inactive'}">
            <div class="material-header">
                <span class="material-title">${escapeHtml(b.situation?.substring(0, 100) || '')}</span>
                <span class="badge-small badge-cat">${escapeHtml(b.tone || '')}</span>
            </div>
            <div class="material-content">
                <span class="label">Example:</span> 
                ${escapeHtml(b.example_response?.substring(0, 150) || '')}
                ${b.example_response?.length > 150 ? '...' : ''}
                
                <span class="label">Do:</span> 
                ${escapeHtml(b.do_rules || '')}
                
                <span class="label">Don't:</span> 
                ${escapeHtml(b.dont_rules || '')}
            </div>
            <div class="material-footer">
                <span class="material-meta">ID: ${escapeHtml(b.id?.toString() || '')}</span>
                <div class="material-actions">
                    <button class="btn-toggle ${b.active ? 'active' : ''}" 
                            onclick="toggleBehavior(${Number(b.id)})">
                        ${b.active ? 'On' : 'Off'}
                    </button>
                    <button class="btn-danger" 
                            onclick="deleteBehavior(${Number(b.id)})">
                        Delete
                    </button>
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
        showToast('Behavior style added');
        addBehBtn.innerHTML = '➕ Add Behavior'; addBehBtn.disabled = false;
    } catch (e) { addBehBtn.innerHTML = '❌ Error'; addBehBtn.disabled = false; }
}

async function toggleBehavior(id) { await fetch(`/api/behaviors/${id}/toggle`, { method: 'POST' }); loadBehaviors(); }
async function deleteBehavior(id) { if (!confirm('Delete this behavior?')) return; await fetch(`/api/behaviors/${id}`, { method: 'DELETE' }); loadBehaviors(); loadStats(); }

// === AUTHORITY ===
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
        authorityList.innerHTML = '<div class="empty-state"><div class="empty-icon">🛡️</div><div class="empty-title">No authority rules yet</div><div class="empty-desc">Add rules to control what the AI can and cannot do</div></div>';
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
        showToast('Authority rule added');
        addAuthBtn.innerHTML = '➕ Add Authority'; addAuthBtn.disabled = false;
    } catch (e) { addAuthBtn.innerHTML = '❌ Error'; addAuthBtn.disabled = false; }
}

async function toggleAuthority(id) { await fetch(`/api/authority/${id}/toggle`, { method: 'POST' }); loadAuthority(); }
async function deleteAuthority(id) { if (!confirm('Delete this rule?')) return; await fetch(`/api/authority/${id}`, { method: 'DELETE' }); loadAuthority(); loadStats(); }

// === REVIEW / FLAGS ===
async function loadFlags() {
    try {
        flagsList.innerHTML = renderSkeleton(2);
        const url = currentFlagFilter === 'all' ? '/api/flags' : `/api/flags?status=${currentFlagFilter}`;
        const res = await fetch(url);
        const data = await res.json();
        updateBadge(data.pending_count || 0);

        if (!data.flags || data.flags.length === 0) {
            flagsList.innerHTML = '<div class="empty-state"><div class="empty-icon">🚩</div><div class="empty-title">No flagged questions</div><div class="empty-desc">Questions flagged for review will appear here</div></div>';
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
                    <button class="btn-success" onclick="resolveFlag(${f.id})">💾 Save Answer</button>
                    <button class="btn-muted" onclick="dismissFlag(${f.id})">Dismiss</button>
                </div>
            </div>
        `;
    } else {
        actionsHtml = `
            <div class="admin-answer-display">
                <span class="ans-label">Admin Answer</span>
                <div class="ans-text">${formatText(escapeHtml(f.admin_answer || ''))}</div>
                ${f.converted_to ? `<span class="converted-badge ${f.converted_to}">Converted to ${f.converted_to}</span>` : ''}
            </div>
        `;
    }

    return `
        <div class="flag-card ${statusClass}">
            <div class="flag-header">
                <div class="flag-question">${escapeHtml(f.question)}</div>
                <span class="flag-status status-${statusClass}">${statusLabel}</span>
            </div>
            <div class="flag-reason">
                <span>🚩</span> ${f.flag_reason.replace(/_/g, ' ')}
            </div>
            ${f.context ? `<div class="flag-context"><span class="ctx-label">Context</span>${escapeHtml(f.context)}</div>` : ''}
            ${f.ai_response ? `<div class="flag-ai-response"><span class="ctx-label">AI Response</span>${formatText(escapeHtml(f.ai_response))}</div>` : ''}
            <div class="flag-actions">
                ${actionsHtml}
            </div>
        </div>
    `;
}

function toggleConvert(flagId, type) {
    const container = document.getElementById(`convFields-${flagId}`);
    const hasDec = document.getElementById(`convDec-${flagId}`).checked;
    const hasBeh = document.getElementById(`convBeh-${flagId}`).checked;
    const hasAuth = document.getElementById(`convAuth-${flagId}`).checked;

    if (!hasDec && !hasBeh && !hasAuth) {
        container.classList.remove('active');
        container.innerHTML = '';
        return;
    }

    container.classList.add('active');
    let html = '';

    if (hasDec) {
        html += `
            <div class="convert-section-title">📊 Decision Fields</div>
            <div class="form-row">
                <div class="form-group third">
                    <select id="cfDecCat-${flagId}">
                        <option value="pricing">Pricing</option><option value="acquisition">Acquisition</option>
                        <option value="negotiation">Negotiation</option><option value="risk">Risk</option>
                        <option value="strategy">Strategy</option><option value="legal">Legal</option>
                    </select>
                </div>
                <div class="form-group third">
                    <select id="cfDecAuth-${flagId}">
                        <option value="low">Low</option><option value="medium">Medium</option>
                        <option value="high">High</option><option value="forbidden">Forbidden</option>
                    </select>
                </div>
                <div class="form-group third">
                    <select id="cfDecAct-${flagId}">
                        <option value="buy">Buy</option><option value="reject">Reject</option>
                        <option value="negotiate">Negotiate</option><option value="escalate">Escalate</option>
                        <option value="delay">Delay</option><option value="conditional">Conditional</option>
                    </select>
                </div>
            </div>
            <input type="text" id="cfDecCtx-${flagId}" placeholder="Context summary" value="From review panel">
            <input type="text" id="cfDecReason-${flagId}" placeholder="Reasoning summary" value="Admin-provided answer">
        `;
    }
    if (hasBeh) {
        html += `
            <div class="convert-section-title" style="color:var(--info)">🎭 Behavior Fields</div>
            <input type="text" id="cfBehTone-${flagId}" placeholder="Tone (e.g., calm but firm)" value="professional">
        `;
    }
    if (hasAuth) {
        html += `
            <div class="convert-section-title" style="color:var(--warning)">🛡️ Authority Fields</div>
            <input type="text" id="cfAuthAct-${flagId}" placeholder="Action type name" value="flagged action">
            <select id="cfAuthAllow-${flagId}">
                <option value="conditional">Conditional</option><option value="yes">Yes</option><option value="no">No</option>
            </select>
            <input type="text" id="cfAuthCond-${flagId}" placeholder="Condition" value="reviewed by admin">
        `;
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
        admin_answer: answer,
        converted_to: convertedTo,
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
        await fetch(`/api/flags/${flagId}/resolve`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
        });
        loadFlags(); loadStats(); showToast('Flag resolved and answer saved');
    } catch (e) { showToast('Failed to resolve', 'error'); }
}

async function dismissFlag(flagId) {
    if (!confirm('Dismiss this flag?')) return;
    try {
        await fetch(`/api/flags/${flagId}/dismiss`, { method: 'POST' });
        loadFlags(); loadStats();
    } catch (e) { showToast('Failed to dismiss', 'error'); }
}

async function manualFlag() {
    if (!lastQuestion) { showToast('No question to flag', 'error'); return; }
    try {
        await fetch('/api/flags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: sessionId, question: lastQuestion,
                ai_response: lastAiResponse, context: lastContext, flag_reason: 'manual'
            })
        });
        showToast('🚩 Flagged for review');
        loadStats();
    } catch (e) { showToast('Failed to flag', 'error'); }
}

// === CHAT ===
function appendMessage(role, text, analysis = null) {
    const div = document.createElement('div');
    div.className = `message ${role}`;

    if (analysis && role === 'assistant') {
        const bar = document.createElement('div');
        bar.className = 'analysis-bar';

        if (analysis.situations_detected?.length > 0) {
            const s = document.createElement('span'); s.className = 'analysis-badge ab-info';
            s.textContent = `📍 ${analysis.situations_detected.join(', ')}`; bar.appendChild(s);
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
            d.textContent = `📚 ${analysis.decisions_retrieved} patterns`; bar.appendChild(d);
        }
        if (analysis.behaviors_applied > 0) {
            const b = document.createElement('span'); b.className = 'analysis-badge ab-ok';
            b.textContent = `🎭 ${analysis.behaviors_applied} styles`; bar.appendChild(b);
        }
        div.appendChild(bar);

        if (analysis.suggest_flag) {
            const banner = document.createElement('div');
            banner.className = 'flag-banner';
            banner.innerHTML = `<span>⚠️ This answer may need review. AI confidence is low or authority is conditional.</span><button onclick="manualFlag()">Flag for Review</button>`;
            div.appendChild(banner);
        }
    }

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    if (role === 'assistant') {
        bubble.innerHTML = formatText(escapeHtml(text));
        
        // TTS button
        const ttsBtn = document.createElement('button');
        ttsBtn.className = 'tts-btn';
        ttsBtn.title = 'Read aloud';
        ttsBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>`;
        ttsBtn.onclick = () => playTTS(text, ttsBtn);
        bubble.appendChild(ttsBtn);
        
        const copyBtn = document.createElement('button');
        copyBtn.className = 'copy-btn';
        copyBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
        copyBtn.title = 'Copy response';
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
    lastContext = '';
    lastAiResponse = '';

    appendMessage('user', text);
    userInput.value = '';
    userInput.style.height = 'auto';
    sendBtn.disabled = true;
    showTyping();

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
            if (data.suggest_flag) {
                lastContext = `Situations: ${data.situations_detected?.join(', ') || 'none'}. Authority: ${data.authority_violations} checks.`;
            }
        }
    } catch (e) {
        hideTyping();
        appendMessage('assistant', 'Network error. Please try again.');
    } finally {
        sendBtn.disabled = false;
        userInput.focus();
    }
}

function quickAsk(text) {
    userInput.value = text;
    userInput.style.height = 'auto';
    userInput.style.height = Math.min(userInput.scrollHeight, 200) + 'px';
    userInput.focus();
}

async function clearChat() {
    try {
        await fetch('/api/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId })
        });
    } catch (error) {
        console.warn('Failed to clear session on server:', error);
    }

    chatBox.innerHTML = `
        <div class="message assistant welcome">
            <div class="bubble">
                <div class="bubble-title">Welcome to DTOS AI</div>
                <p>I'm your investment analyst. Describe any deal, negotiation, or situation and I'll analyze it using your configured policies and knowledge base.</p>
                <div class="quick-pills">
                    <button class="quick-pill" onclick="quickAsk('I have a distressed property with $200k debt. Should I buy?')">Distressed + debt</button>
                    <button class="quick-pill" onclick="quickAsk('The seller is emotional and wants to close fast. How do I negotiate?')">Emotional seller</button>
                    <button class="quick-pill" onclick="quickAsk('A bank REO is listed at 80% of ARV. What is my max offer?')">Bank REO pricing</button>
                </div>
            </div>
        </div>
    `;

    lastQuestion = '';
    lastAiResponse = '';
    lastContext = '';
}

// === UTILS ===
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
loadConfig();
loadStats();
loadDecisions();
loadBehaviors();
loadAuthority();
loadFlags();