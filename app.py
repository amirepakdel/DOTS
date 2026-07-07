import os
import json
import time
import requests
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.prompts import PromptTemplate
import psycopg2
import openai
from psycopg2.extras import RealDictCursor
import io
import tempfile

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "chatdb")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASSWORD", "postgres")
CONNECTION_STRING = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
CARTESIA_VERSION = "2026-03-01"

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.3,
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    openai_api_key=os.getenv("OPENAI_API_KEY")
)

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len
)

_vectorstore = None

def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = PGVector(
            connection=CONNECTION_STRING,
            embeddings=embeddings,
            collection_name="knowledge_base",
            distance_strategy="Cosine",
            use_jsonb=True,
        )
    return _vectorstore

def get_db_connection():
    retries = 15
    for i in range(retries):
        try:
            return psycopg2.connect(
                host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                user=DB_USER, password=DB_PASS
            )
        except psycopg2.OperationalError:
            if i < retries - 1:
                time.sleep(2)
            else:
                raise

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # Conversations
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            session_id VARCHAR(100) NOT NULL,
            role VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id, created_at);")

    # Bot Config
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            id SERIAL PRIMARY KEY,
            key VARCHAR(100) UNIQUE NOT NULL,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Decisions
    cur.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id SERIAL PRIMARY KEY,
            question TEXT NOT NULL,
            context TEXT NOT NULL,
            ideal_answer TEXT NOT NULL,
            category VARCHAR(50) NOT NULL,
            authority_level VARCHAR(20) NOT NULL,
            action_type VARCHAR(50) NOT NULL,
            reasoning TEXT NOT NULL,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Behaviors
    cur.execute("""
        CREATE TABLE IF NOT EXISTS behaviors (
            id SERIAL PRIMARY KEY,
            situation TEXT NOT NULL,
            tone VARCHAR(100) NOT NULL,
            example_response TEXT NOT NULL,
            do_rules TEXT NOT NULL,
            dont_rules TEXT NOT NULL,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Authority Rules
    cur.execute("""
        CREATE TABLE IF NOT EXISTS authority_rules (
            id SERIAL PRIMARY KEY,
            action_type VARCHAR(255) NOT NULL,
            allowed VARCHAR(50) NOT NULL,
            condition TEXT NOT NULL,
            fallback_behavior TEXT NOT NULL,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Flagged Questions (Review Panel)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS flagged_questions (
            id SERIAL PRIMARY KEY,
            session_id VARCHAR(100) NOT NULL,
            question TEXT NOT NULL,
            ai_response TEXT,
            context TEXT,
            flag_reason VARCHAR(50) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            admin_answer TEXT,
            converted_to VARCHAR(50),
            converted_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP
        );
    """)

    # Default config
    cur.execute("SELECT COUNT(*) FROM bot_config WHERE key = 'system_prompt'")
    if cur.fetchone()[0] == 0:
        defaults = [
            ('system_prompt', 'You are a real estate investment AI assistant specializing in distressed property acquisition, pricing, negotiation, risk evaluation, and strategy.'),
            ('personality', 'analytical, direct, data-driven, empathetic with emotional sellers'),
            ('allowed_topics', 'real estate investing, distressed properties, pricing, valuation, negotiation, risk assessment, legal issues, strategy, market analysis'),
            ('denied_topics', 'hate speech, violence, illegal activities, personal medical advice, non-real-estate investment schemes'),
            ('response_rules', 'Always show reasoning. Cite specific numbers. If uncertain, say so. Never give legal advice without disclaiming. Respect authority rules. Ask clarifying questions when information is missing.'),
            ('max_history', '10'),
            ('temperature', '0.3'),
            ('company_name', 'DTOS Capital'),
            ('margin_threshold', '25'),
            ('auto_flag_conditional', 'true'),
            ('auto_flag_uncertain', 'true'),
            ('cartesia_voice_id', 'a5136bf9-224c-4d76-b823-52bd5efcffcc'),
            ('cartesia_model', 'sonic-3.5'),
            ('cartesia_speed', '1.0'),
        ]
        for k, v in defaults:
            cur.execute("INSERT INTO bot_config (key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, v))

    conn.commit()
    cur.close()
    conn.close()

# ========== CONFIG ==========
def get_config():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT key, value FROM bot_config")
    rows = {row[0]: row[1] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return rows

def update_config(key, value):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bot_config (key, value, updated_at) 
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
    """, (key, value))
    conn.commit()
    cur.close()
    conn.close()

# ========== DECISIONS ==========
def get_decisions(active_only=True, category=None):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = "SELECT * FROM decisions WHERE 1=1"
    params = []
    if active_only:
        query += " AND active = TRUE"
    if category:
        query += " AND category = %s"
        params.append(category)
    query += " ORDER BY created_at DESC"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def add_decision(question, context, ideal_answer, category, authority_level, action_type, reasoning):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO decisions (question, context, ideal_answer, category, authority_level, action_type, reasoning)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (question, context, ideal_answer, category, authority_level, action_type, reasoning))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    doc_content = f"Question: {question}\nContext: {context}\nAnswer: {ideal_answer}\nReasoning: {reasoning}\nCategory: {category} | Action: {action_type} | Authority: {authority_level}"
    doc = text_splitter.create_documents([doc_content], metadatas=[{"source": f"decision:{category}", "id": str(new_id)}])
    vs = get_vectorstore()
    vs.add_documents(doc)
    return new_id

def delete_decision(decision_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM decisions WHERE id = %s", (decision_id,))
    conn.commit()
    cur.close()
    conn.close()

def toggle_decision(decision_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE decisions SET active = NOT active WHERE id = %s RETURNING active", (decision_id,))
    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return result[0]

# ========== BEHAVIORS ==========
def get_behaviors(active_only=True):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = "SELECT * FROM behaviors"
    if active_only:
        query += " WHERE active = TRUE"
    query += " ORDER BY created_at DESC"
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def add_behavior(situation, tone, example_response, do_rules, dont_rules):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO behaviors (situation, tone, example_response, do_rules, dont_rules)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
    """, (situation, tone, example_response, do_rules, dont_rules))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    doc_content = f"Situation: {situation}\nTone: {tone}\nResponse: {example_response}\nDo: {do_rules}\nDon't: {dont_rules}"
    doc = text_splitter.create_documents([doc_content], metadatas=[{"source": "behavior", "id": str(new_id)}])
    vs = get_vectorstore()
    vs.add_documents(doc)
    return new_id

def delete_behavior(behavior_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM behaviors WHERE id = %s", (behavior_id,))
    conn.commit()
    cur.close()
    conn.close()

def toggle_behavior(behavior_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE behaviors SET active = NOT active WHERE id = %s RETURNING active", (behavior_id,))
    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return result[0]

# ========== AUTHORITY RULES ==========
def get_authority_rules(active_only=True):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    query = "SELECT * FROM authority_rules"
    if active_only:
        query += " WHERE active = TRUE"
    query += " ORDER BY created_at DESC"
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def add_authority_rule(action_type, allowed, condition, fallback_behavior):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO authority_rules (action_type, allowed, condition, fallback_behavior)
        VALUES (%s, %s, %s, %s) RETURNING id
    """, (action_type, allowed, condition, fallback_behavior))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def delete_authority_rule(rule_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM authority_rules WHERE id = %s", (rule_id,))
    conn.commit()
    cur.close()
    conn.close()

def toggle_authority_rule(rule_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE authority_rules SET active = NOT active WHERE id = %s RETURNING active", (rule_id,))
    result = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    return result[0]

# ========== FLAGGED QUESTIONS ==========
def get_flagged_questions(status=None):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if status:
        cur.execute("SELECT * FROM flagged_questions WHERE status = %s ORDER BY created_at DESC", (status,))
    else:
        cur.execute("SELECT * FROM flagged_questions ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

def get_pending_count():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM flagged_questions WHERE status = 'pending'")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count

def add_flagged_question(session_id, question, ai_response, context, flag_reason):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO flagged_questions (session_id, question, ai_response, context, flag_reason, status)
        VALUES (%s, %s, %s, %s, %s, 'pending') RETURNING id
    """, (session_id, question, ai_response, context, flag_reason))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return new_id

def resolve_flagged_question(flag_id, admin_answer, converted_to=None, converted_id=None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE flagged_questions 
        SET status = 'resolved', admin_answer = %s, converted_to = %s, converted_id = %s, resolved_at = CURRENT_TIMESTAMP
        WHERE id = %s
    """, (admin_answer, converted_to, converted_id, flag_id))
    conn.commit()
    cur.close()
    conn.close()

def dismiss_flagged_question(flag_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE flagged_questions SET status = 'dismissed', resolved_at = CURRENT_TIMESTAMP WHERE id = %s", (flag_id,))
    conn.commit()
    cur.close()
    conn.close()

# ========== CARTESIA TTS ==========
def cartesia_tts(text, voice_id=None, model_id=None, speed=None):
    """Generate speech using Cartesia TTS Bytes API. Returns MP3 bytes."""
    if not CARTESIA_API_KEY:
        raise Exception("CARTESIA_API_KEY not configured")
    
    config = get_config()
    voice_id = voice_id or config.get('cartesia_voice_id', 'a5136bf9-224c-4d76-b823-52bd5efcffcc')
    model_id = model_id or config.get('cartesia_model', 'sonic-3.5')
    speed = float(speed or config.get('cartesia_speed', '1.0'))
    
    payload = {
        "model_id": model_id,
        "transcript": text,
        "voice": {
            "mode": "id",
            "id": voice_id
        },
        "output_format": {
            "container": "mp3",
            "sample_rate": 24000,
            "bit_rate": 128000
        },
        "language": "en",
        "generation_config": {
            "speed": speed,
            "volume": 1.0
        }
    }
    
    headers = {
        "Authorization": f"Bearer {CARTESIA_API_KEY}",
        "Cartesia-Version": CARTESIA_VERSION,
        "Content-Type": "application/json"
    }
    
    resp = requests.post("https://api.cartesia.ai/tts/bytes", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.content

# ========== CARTESIA STT ==========
def cartesia_stt(audio_bytes, filename="audio.wav"):
    """Transcribe audio using Cartesia Batch STT API."""
    if not CARTESIA_API_KEY:
        raise Exception("CARTESIA_API_KEY not configured")
    
    headers = {
        "Authorization": f"Bearer {CARTESIA_API_KEY}",
        "Cartesia-Version": CARTESIA_VERSION
    }
    
    # Cartesia STT accepts various formats: flac, m4a, mp3, mp4, mpeg, mpga, oga, ogg, wav, webm
    files = {
        "file": (filename, io.BytesIO(audio_bytes), "audio/wav")
    }
    data = {
        "model": "ink-whisper",
        "language": "en",
        "timestamp_granularities[]": "word"
    }
    
    resp = requests.post("https://api.cartesia.ai/stt", headers=headers, files=files, data=data, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    
    # Extract text from response
    if isinstance(result, dict) and "text" in result:
        return result["text"]
    elif isinstance(result, list) and len(result) > 0:
        return result[0].get("text", "")
    elif isinstance(result, dict) and "transcript" in result:
        return result["transcript"]
    else:
        return str(result)

# ========== CHAT ENGINE ==========
def save_message(session_id, role, content):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO conversations (session_id, role, content) VALUES (%s, %s, %s)",
        (session_id, role, content)
    )
    conn.commit()
    cur.close()
    conn.close()

def get_history(session_id, limit=20):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT role, content FROM conversations 
           WHERE session_id = %s ORDER BY created_at DESC LIMIT %s""",
        (session_id, limit)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    rows.reverse()
    return rows

def detect_situation(user_message):
    msg_lower = user_message.lower()
    situations = []
    keywords = {
        'emotional_seller': ['emotional', 'crying', 'died', 'memories', 'stress', 'urgent', 'fast', 'widow', 'divorce', 'sad'],
        'bank_deal': ['bank', 'reo', 'asset manager', 'foreclosure', 'auction', 'short sale', 'lender'],
        'wholesaler': ['wholesaler', 'assignment', 'assignment fee', 'virtual', 'double close'],
        'hostile': ['hostile', 'angry', 'refuse', 'won\'t', 'threaten', 'lawsuit', 'sue'],
        'contractor_issue': ['contractor', 'rehab', 'repair', 'damage', 'foundation', 'mold', 'asbestos', 'leak'],
        'legal': ['legal', 'lawsuit', 'court', 'attorney', 'lien', 'bankruptcy', 'probate', 'title'],
        'city_inspector': ['city', 'inspector', 'code', 'permit', 'condemnation', 'zoning', 'violation'],
        'sophisticated_seller': ['investor', 'sophisticated', 'knows', 'margin', 'experienced', 'flipper'],
        'tenant_issue': ['tenant', 'rent', 'lease', 'eviction', 'occupant', 'squatter'],
        'environmental': ['mold', 'asbestos', 'lead', 'radon', 'contamination', 'flood', 'earthquake']
    }
    for situation, words in keywords.items():
        if any(w in msg_lower for w in words):
            situations.append(situation)
    return situations

def check_authority(user_message):
    rules = get_authority_rules(active_only=True)
    msg_lower = user_message.lower()
    violations = []
    for rule in rules:
        action_lower = rule['action_type'].lower()
        keywords = [k for k in action_lower.replace('/', ' ').split() if len(k) > 3]
        if any(kw in msg_lower for kw in keywords):
            violations.append({
                'id': rule['id'],
                'rule': rule['action_type'],
                'allowed': rule['allowed'],
                'condition': rule['condition'],
                'fallback': rule['fallback_behavior']
            })
    return violations

def get_relevant_decisions(user_message, top_k=3):
    vs = get_vectorstore()
    try:
        docs = vs.similarity_search(user_message, k=top_k, filter={"source": {"$regex": "^decision:"}})
        return docs
    except:
        return []

def get_relevant_behaviors(situations):
    if not situations:
        return []
    all_behaviors = get_behaviors(active_only=True)
    matched = []
    for b in all_behaviors:
        b_sit = b['situation'].lower()
        for sit in situations:
            if any(word in b_sit for word in sit.replace('_', ' ').split()):
                matched.append(b)
                break
    return matched[:2]

def build_master_prompt(user_message, config, history, situations, violations, decisions, behaviors):
    system = f"""You are {config.get('company_name', 'DTOS Capital')}'s AI Investment Analyst.
Your job is to evaluate real estate deals, advise on pricing, negotiation, risk, and strategy.
You must follow ALL authority rules. You must apply appropriate behavior styles.
You must show your reasoning with specific numbers.
If you lack critical information, ask clarifying questions instead of guessing.
"""
    personality = config.get('personality', 'analytical, direct')
    system += f"\nPERSONALITY: {personality}"
    allowed = config.get('allowed_topics', 'real estate investing')
    denied = config.get('denied_topics', '')
    system += f"\n\nALLOWED TOPICS: {allowed}"
    if denied:
        system += f"\nDENIED TOPICS: {denied}"
    rules = config.get('response_rules', 'Show reasoning. Cite numbers.')
    system += f"\n\nRESPONSE RULES: {rules}"
    margin = config.get('margin_threshold', '25')
    system += f"\nMINIMUM MARGIN THRESHOLD: {margin}%"

    if violations:
        system += "\n\n=== AUTHORITY CHECK ==="
        for v in violations:
            system += f"\nRULE: {v['rule']}\nALLOWED: {v['allowed']}\nCONDITION: {v['condition']}\nFALLBACK: {v['fallback']}\n"
        if any(v['allowed'] == 'no' for v in violations):
            system += "\nCRITICAL: This triggers a FORBIDDEN authority rule. You MUST refuse and provide the fallback behavior."

    if behaviors:
        system += "\n\n=== BEHAVIOR STYLE ==="
        for b in behaviors:
            system += f"\nSITUATION: {b['situation']}\nTONE: {b['tone']}\nDO: {b['do_rules']}\nDON'T: {b['dont_rules']}\nEXAMPLE: {b['example_response']}\n"

    if decisions:
        system += "\n\n=== RELEVANT DECISION PATTERNS ==="
        for i, d in enumerate(decisions, 1):
            system += f"\n[{i}] {d.page_content[:400]}..."

    history_text = ""
    if history:
        history_text = "\n\nCONVERSATION HISTORY:\n"
        for m in history[:-1]:
            prefix = "User" if m['role'] == 'user' else "Assistant"
            history_text += f"{prefix}: {m['content']}\n"

    full_prompt = f"""{system}

{history_text}

CURRENT USER QUESTION: {user_message}

INSTRUCTIONS:
1. Check if this violates any authority rules. If yes, REFUSE and explain fallback.
2. Detect the situation type and apply matching behavior style (tone, do/don't).
3. Use relevant decision patterns as precedent.
4. Show step-by-step reasoning with numbers.
5. State your final recommendation clearly (BUY / REJECT / NEGOTIATE / DELAY / ESCALATE / NEED MORE INFO).
6. If you need more information to answer accurately, ask specific questions. Do not guess.

Your Response:"""
    return full_prompt

# ========== ROUTES ==========
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "pending_flags": get_pending_count()})

# --- CONFIG ---
@app.route("/api/config", methods=["GET"])
def get_config_api():
    return jsonify(get_config())

@app.route("/api/config", methods=["POST"])
def update_config_api():
    data = request.get_json()
    for key, value in data.items():
        update_config(key, str(value))
    return jsonify({"status": "updated", "config": get_config()})

# --- DECISIONS ---
@app.route("/api/decisions", methods=["GET"])
def get_decisions_api():
    category = request.args.get("category")
    active_only = request.args.get("active_only", "true").lower() == "true"
    return jsonify({"decisions": get_decisions(active_only, category)})

@app.route("/api/decisions", methods=["POST"])
def add_decision_api():
    data = request.get_json()
    required = ['question', 'context', 'ideal_answer', 'category', 'authority_level', 'action_type', 'reasoning']
    for field in required:
        if not data.get(field, '').strip():
            return jsonify({"error": f"{field} is required"}), 400
    new_id = add_decision(data['question'], data['context'], data['ideal_answer'],
                         data['category'], data['authority_level'], data['action_type'], data['reasoning'])
    return jsonify({"status": "added", "id": new_id})

@app.route("/api/decisions/<int:decision_id>", methods=["DELETE"])
def delete_decision_api(decision_id):
    delete_decision(decision_id)
    return jsonify({"status": "deleted"})

@app.route("/api/decisions/<int:decision_id>/toggle", methods=["POST"])
def toggle_decision_api(decision_id):
    return jsonify({"status": "toggled", "active": toggle_decision(decision_id)})

# --- BEHAVIORS ---
@app.route("/api/behaviors", methods=["GET"])
def get_behaviors_api():
    active_only = request.args.get("active_only", "true").lower() == "true"
    return jsonify({"behaviors": get_behaviors(active_only)})

@app.route("/api/behaviors", methods=["POST"])
def add_behavior_api():
    data = request.get_json()
    required = ['situation', 'tone', 'example_response', 'do_rules', 'dont_rules']
    for field in required:
        if not data.get(field, '').strip():
            return jsonify({"error": f"{field} is required"}), 400
    new_id = add_behavior(data['situation'], data['tone'], data['example_response'],
                         data['do_rules'], data['dont_rules'])
    return jsonify({"status": "added", "id": new_id})

@app.route("/api/behaviors/<int:behavior_id>", methods=["DELETE"])
def delete_behavior_api(behavior_id):
    delete_behavior(behavior_id)
    return jsonify({"status": "deleted"})

@app.route("/api/behaviors/<int:behavior_id>/toggle", methods=["POST"])
def toggle_behavior_api(behavior_id):
    return jsonify({"status": "toggled", "active": toggle_behavior(behavior_id)})

# --- AUTHORITY ---
@app.route("/api/authority", methods=["GET"])
def get_authority_api():
    active_only = request.args.get("active_only", "true").lower() == "true"
    return jsonify({"rules": get_authority_rules(active_only)})

@app.route("/api/authority", methods=["POST"])
def add_authority_api():
    data = request.get_json()
    required = ['action_type', 'allowed', 'condition', 'fallback_behavior']
    for field in required:
        if not data.get(field, '').strip():
            return jsonify({"error": f"{field} is required"}), 400
    new_id = add_authority_rule(data['action_type'], data['allowed'], data['condition'], data['fallback_behavior'])
    return jsonify({"status": "added", "id": new_id})

@app.route("/api/authority/<int:rule_id>", methods=["DELETE"])
def delete_authority_api(rule_id):
    delete_authority_rule(rule_id)
    return jsonify({"status": "deleted"})

@app.route("/api/authority/<int:rule_id>/toggle", methods=["POST"])
def toggle_authority_api(rule_id):
    return jsonify({"status": "toggled", "active": toggle_authority_rule(rule_id)})

# --- FLAGGED QUESTIONS ---
@app.route("/api/flags", methods=["GET"])
def get_flags_api():
    status = request.args.get("status")
    return jsonify({"flags": get_flagged_questions(status), "pending_count": get_pending_count()})

@app.route("/api/flags", methods=["POST"])
def add_flag_api():
    data = request.get_json()
    session_id = data.get("session_id", "default")
    question = data.get("question", "").strip()
    ai_response = data.get("ai_response", "")
    context = data.get("context", "")
    flag_reason = data.get("flag_reason", "manual")
    if not question:
        return jsonify({"error": "Question is required"}), 400
    new_id = add_flagged_question(session_id, question, ai_response, context, flag_reason)
    return jsonify({"status": "flagged", "id": new_id, "pending_count": get_pending_count()})

@app.route("/api/flags/<int:flag_id>/resolve", methods=["POST"])
def resolve_flag_api(flag_id):
    data = request.get_json()
    admin_answer = data.get("admin_answer", "").strip()
    converted_to = data.get("converted_to")
    converted_id = None

    if not admin_answer:
        return jsonify({"error": "Admin answer is required"}), 400

    if converted_to == "decision":
        converted_id = add_decision(
            question=data.get("question", "Flagged question"),
            context=data.get("context", "From review panel"),
            ideal_answer=admin_answer,
            category=data.get("category", "general"),
            authority_level=data.get("authority_level", "medium"),
            action_type=data.get("action_type", "escalate"),
            reasoning=data.get("reasoning", "Admin-provided answer from review panel")
        )
    elif converted_to == "behavior":
        converted_id = add_behavior(
            situation=data.get("question", "Flagged situation"),
            tone=data.get("tone", "professional"),
            example_response=admin_answer,
            do_rules=data.get("do_rules", "follow admin guidance"),
            dont_rules=data.get("dont_rules", "ignore admin guidance")
        )
    elif converted_to == "authority":
        converted_id = add_authority_rule(
            action_type=data.get("action_type", "flagged action"),
            allowed=data.get("allowed", "conditional"),
            condition=data.get("condition", "reviewed by admin"),
            fallback_behavior=admin_answer
        )

    resolve_flagged_question(flag_id, admin_answer, converted_to, converted_id)
    return jsonify({"status": "resolved", "converted_to": converted_to, "converted_id": converted_id, "pending_count": get_pending_count()})

@app.route("/api/flags/<int:flag_id>/dismiss", methods=["POST"])
def dismiss_flag_api(flag_id):
    dismiss_flagged_question(flag_id)
    return jsonify({"status": "dismissed", "pending_count": get_pending_count()})

# --- VOICE: TTS ---
@app.route("/api/tts", methods=["POST"])
def tts_api():
    data = request.get_json()
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Text is required"}), 400
    if not CARTESIA_API_KEY:
        return jsonify({"error": "CARTESIA_API_KEY not configured"}), 500
    
    try:
        audio_bytes = cartesia_tts(
            text,
            voice_id=data.get("voice_id"),
            model_id=data.get("model_id"),
            speed=data.get("speed")
        )
        return send_file(
            io.BytesIO(audio_bytes),
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name="speech.mp3"
        )
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"Cartesia TTS error: {e.response.text}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- VOICE: STT ---
@app.route("/api/stt", methods=["POST"])
def stt_api():
    if 'audio' not in request.files:
        return jsonify({"error": "Audio file is required"}), 400
    if not CARTESIA_API_KEY:
        return jsonify({"error": "CARTESIA_API_KEY not configured"}), 500
    
    audio_file = request.files['audio']
    audio_bytes = audio_file.read()
    
    if len(audio_bytes) == 0:
        return jsonify({"error": "Empty audio file"}), 400
    
    try:
        # Cartesia accepts various formats, browser typically records webm or wav
        filename = audio_file.filename or "audio.webm"
        transcript = cartesia_stt(audio_bytes, filename)
        return jsonify({"transcript": transcript})
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"Cartesia STT error: {e.response.text}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- CHAT ---
@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    use_kb = data.get("use_kb", True)

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    config = get_config()
    situations = detect_situation(user_message)
    violations = check_authority(user_message)
    has_forbidden = any(v['allowed'] == 'no' for v in violations)
    has_conditional = any(v['allowed'] == 'conditional' for v in violations)

    save_message(session_id, "user", user_message)

    decisions = []
    behaviors = []
    if use_kb:
        decisions = get_relevant_decisions(user_message, top_k=3)
        behaviors = get_relevant_behaviors(situations)

    history = get_history(session_id, limit=int(config.get('max_history', 10)))
    full_prompt = build_master_prompt(user_message, config, history, situations, violations, decisions, behaviors)

    try:
        response = llm.invoke(full_prompt)
        reply = response.content
    except openai.RateLimitError as e:
        error_code = getattr(e, 'code', None) or (e.body.get('code') if hasattr(e, 'body') else None)
        if error_code == 'insufficient_quota' or 'quota' in str(e).lower():
            reply = "I'm currently out of API credit. Please contact the administrator to check the OpenAI billing plan."
        else:
            reply = "I'm receiving too many requests right now. Please wait a moment and try again."
    except openai.APIError as e:
        reply = "There was a problem connecting to the AI service. Please try again later."
    except Exception as e:
        reply = "Something went wrong while generating a response. Please try again later."

    save_message(session_id, "assistant", reply)

    suggest_flag = False
    flag_reason = None
    if has_conditional and config.get('auto_flag_conditional', 'true').lower() == 'true':
        suggest_flag = True
        flag_reason = "conditional_authority"
    elif "i don't know" in reply.lower() or "uncertain" in reply.lower() or "need more information" in reply.lower():
        if config.get('auto_flag_uncertain', 'true').lower() == 'true':
            suggest_flag = True
            flag_reason = "uncertain_answer"

    return jsonify({
        "reply": reply,
        "session_id": session_id,
        "model": "gpt-4o-mini",
        "situations_detected": situations,
        "authority_violations": len(violations),
        "authority_details": violations,
        "decisions_retrieved": len(decisions),
        "behaviors_applied": len(behaviors),
        "has_forbidden": has_forbidden,
        "has_conditional": has_conditional,
        "suggest_flag": suggest_flag,
        "flag_reason": flag_reason
    })

@app.route("/api/history", methods=["GET"])
def history():
    session_id = request.args.get("session_id", "default")
    return jsonify({"history": get_history(session_id, limit=50)})

@app.route("/api/clear", methods=["POST"])
def clear():
    data = request.get_json()
    session_id = data.get("session_id", "default")
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM conversations WHERE session_id = %s", (session_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "cleared"})

@app.route("/api/stats", methods=["GET"])
def stats():
    conn = get_db_connection()
    cur = conn.cursor()

    stats = {}
    cur.execute("SELECT COUNT(*) FROM decisions WHERE active = TRUE")
    stats['active_decisions'] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM behaviors WHERE active = TRUE")
    stats['active_behaviors'] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM authority_rules WHERE active = TRUE")
    stats['active_authority'] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM flagged_questions WHERE status = 'pending'")
    stats['pending_flags'] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM flagged_questions WHERE status = 'resolved'")
    stats['resolved_flags'] = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM conversations WHERE role = 'user'")
    stats['total_messages'] = cur.fetchone()[0]

    cur.close()
    conn.close()
    return jsonify(stats)

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)