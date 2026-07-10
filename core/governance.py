import logging
import time
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from sqlalchemy import create_engine
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .models import BotConfig, Conversation, Behavior, AuthorityRule, Decision, FlaggedQuestion

logger = logging.getLogger(__name__)

# =============================================================================
# LLM INSTANCES (with fast tier)
# =============================================================================

llm_openai = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.3,
    openai_api_key=settings.OPENAI_API_KEY
)

llm_openai_fast = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.3,
    max_tokens=512,
    openai_api_key=settings.OPENAI_API_KEY
)

llm_anthropic = ChatAnthropic(
    model="claude-sonnet-5",
    temperature=None,
    max_tokens=4096,
    anthropic_api_key=settings.ANTHROPIC_API_KEY,
)

llm = llm_anthropic if settings.ANTHROPIC_API_KEY else llm_openai

embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    openai_api_key=settings.OPENAI_API_KEY
)

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len
)

# =============================================================================
# VECTORSTORE WITH CONNECTION POOLING
# =============================================================================

_vectorstore = None
_engine = None

def get_vectorstore():
    global _vectorstore, _engine
    if _vectorstore is None:
        _engine = create_engine(
            settings.CONNECTION_STRING,
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600,
            pool_pre_ping=True
        )
        _vectorstore = PGVector(
            connection=_engine,
            embeddings=embeddings,
            collection_name="knowledge_base",
            distance_strategy="Cosine",
            use_jsonb=True,
        )
    return _vectorstore

# =============================================================================
# THREADPOOL FOR PARALLEL GOVERNANCE FETCHES
# =============================================================================

_executor = ThreadPoolExecutor(max_workers=10)

def fetch_kb_parallel(user_message, situations, session_id, history_limit, use_kb=True, skip_history=False):
    """
    Fetch KB + history in parallel using threads.
    Returns: (decisions, behaviors, authority_docs, history)
    """
    if not use_kb:
        if skip_history:
            return [], [], [], []
        return [], [], [], get_history(session_id, limit=history_limit)

    # Submit all independent I/O jobs in parallel
    f_decisions = _executor.submit(get_relevant_decisions, user_message, 3)
    f_behaviors = _executor.submit(get_relevant_behaviors, situations)
    f_authority = _executor.submit(get_relevant_authority_rules, user_message, 2)
    f_history = _executor.submit(get_history, session_id, history_limit)

    return (
        f_decisions.result(),
        f_behaviors.result(),
        f_authority.result(),
        f_history.result(),
    )

# =============================================================================
# CACHED CONFIG & RULES
# =============================================================================

_CONFIG_CACHE = {"data": None, "ts": 0}
_CONFIG_TTL = 30  # seconds

def get_config():
    global _CONFIG_CACHE
    now = time.time()
    if _CONFIG_CACHE["data"] is None or (now - _CONFIG_CACHE["ts"]) > _CONFIG_TTL:
        _CONFIG_CACHE["data"] = {c['key']: c['value'] for c in BotConfig.objects.values('key', 'value')}
        _CONFIG_CACHE["ts"] = now
    return _CONFIG_CACHE["data"]

def invalidate_config_cache():
    _CONFIG_CACHE["data"] = None

@lru_cache(maxsize=1)
def get_authority_rules_cached(active_only=True):
    return get_authority_rules(active_only=active_only)

@lru_cache(maxsize=1)
def get_behaviors_cached(active_only=True):
    return get_behaviors(active_only=active_only)

def invalidate_authority_cache():
    get_authority_rules_cached.cache_clear()

def invalidate_behavior_cache():
    get_behaviors_cached.cache_clear()

# =============================================================================
# DATA ACCESS
# =============================================================================

def save_message(session_id, role, content):
    if content is None:
        content = ""
    elif not isinstance(content, str):
        try:
            content = str(content)
        except Exception:
            content = "[Unable to serialize response]"
    Conversation.objects.create(session_id=session_id, role=role, content=content)

def get_history(session_id, limit=20):
    qs = Conversation.objects.filter(session_id=session_id).order_by('-created_at')[:limit]
    return list(reversed(list(qs.values('role', 'content'))))

def get_decisions(active_only=True, category=None):
    qs = Decision.objects.all()
    if active_only:
        qs = qs.filter(active=True)
    if category:
        qs = qs.filter(category=category)
    return list(qs.order_by('-created_at').values())

def get_behaviors(active_only=True):
    qs = Behavior.objects.all()
    if active_only:
        qs = qs.filter(active=True)
    return list(qs.order_by('-created_at').values())

def get_authority_rules(active_only=True):
    qs = AuthorityRule.objects.all()
    if active_only:
        qs = qs.filter(active=True)
    return list(qs.order_by('-created_at').values())

def get_flagged_questions(status=None):
    qs = FlaggedQuestion.objects.all()
    if status:
        qs = qs.filter(status=status)
    return list(qs.order_by('-created_at').values())

def get_pending_count():
    return FlaggedQuestion.objects.filter(status='pending').count()

# =============================================================================
# SITUATION & AUTHORITY DETECTION
# =============================================================================

def detect_situation(user_message):
    msg_lower = user_message.lower()
    situations = []
    keywords = {
        'high_stakes_meeting': ['board', 'investor', 'partner', 'stakeholder', 'critical', 'urgent', 'deadline', 'ceo', 'founder'],
        'governance_risk': ['contract', 'sign', 'commit', 'obligation', 'financial', 'liability', 'guarantee', 'binding'],
        'authority_boundary': ['authority', 'permission', 'approve', 'authorize', 'override', 'escalate', 'forbidden', 'never list'],
        'emotional_seller': ['emotional', 'crying', 'died', 'memories', 'stress', 'urgent', 'fast', 'widow', 'divorce', 'sad'],
        'hostile': ['hostile', 'angry', 'refuse', "won't", 'threaten', 'lawsuit', 'sue'],
        'legal': ['legal', 'lawsuit', 'court', 'attorney', 'lien', 'bankruptcy', 'probate', 'title'],
        'technical_issue': ['bug', 'error', 'crash', 'latency', 'sync', 'avatar', 'voice', 'disconnect', 'failure'],
        'compliance_audit': ['audit', 'compliance', 'regulation', 'policy', 'trace', 'log', 'evidence', 'grounded'],
        'persona_switch': ['persona', 'mode', 'style', 'tone', 'formal', 'casual', 'friendly', 'strict'],
        'human_override': ['override', 'takeover', 'big red button', 'stop', 'silence', 'manual', 'human-in-the-loop']
    }
    for situation, words in keywords.items():
        if any(w in msg_lower for w in words):
            situations.append(situation)
    return situations

def check_authority(user_message):
    rules = get_authority_rules_cached(active_only=True)
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

def add_authority_to_vectorstore(rule):
    doc_content = (
        f"Action: {rule.action_type}\nAllowed: {rule.allowed}\n"
        f"Condition: {rule.condition}\nFallback: {rule.fallback_behavior}"
    )
    doc = text_splitter.create_documents(
        [doc_content],
        metadatas=[{"source": f"authority:{rule.action_type}", "id": str(rule.id)}]
    )
    vs = get_vectorstore()
    vs.add_documents(doc)

# =============================================================================
# SEMANTIC SEARCH
# =============================================================================

def get_relevant_decisions(user_message, top_k=3):
    vs = get_vectorstore()
    try:
        docs = vs.similarity_search(user_message, k=top_k * 5)
        filtered = [d for d in docs if d.metadata.get("source", "").startswith("decision:")]
        return filtered[:top_k]
    except Exception as e:
        logger.error(f"Decision similarity search failed: {e}")
        return []

def get_relevant_authority_rules(user_message, top_k=3):
    vs = get_vectorstore()
    try:
        docs = vs.similarity_search(user_message, k=top_k * 5)
        filtered = [d for d in docs if d.metadata.get("source", "").startswith("authority:")]
        return filtered[:top_k]
    except Exception as e:
        logger.error(f"Authority similarity search failed: {e}")
        return []

def get_relevant_behaviors(situations):
    if not situations:
        return []
    all_behaviors = get_behaviors_cached(active_only=True)
    matched = []
    for b in all_behaviors:
        b_sit = b['situation'].lower()
        for sit in situations:
            if any(word in b_sit for word in sit.replace('_', ' ').split()):
                matched.append(b)
                break
    return matched[:2]

# =============================================================================
# MODEL TIER ROUTING
# =============================================================================

def estimate_complexity(user_message, situations, violations):
    """Route simple queries to fast model to save latency & cost."""
    if not situations and not violations and len(user_message) < 200:
        return "fast"
    if any(v['allowed'] == 'no' for v in violations):
        return "high"
    if any(s in ('legal', 'governance_risk', 'high_stakes_meeting') for s in situations):
        return "high"
    if len(situations) > 2 or len(user_message) > 500:
        return "medium"
    return "fast"

# =============================================================================
# PROMPT BUILDER (trimmed & optimized)
# =============================================================================

def build_master_prompt(
    user_message,
    config,
    history,
    situations,
    violations,
    decisions,
    behaviors,
    authority_docs=None,
    include_reasoning_in_output=True
):
    authority_docs = authority_docs or []

    # Skip semantic authority docs if we already have keyword violations (redundant)
    if violations:
        authority_docs = []

    system = f"""You are {config.get('company_name', 'DTOS')}'s Digital Twin Operating System.
Your job is to participate in meetings as a regulated autonomous operator, provide evidence-grounded advisory responses, and enforce governance boundaries.
You must follow ALL authority rules. You must apply appropriate behavior styles.
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
    system += f"\nMINIMUM CONFIDENCE THRESHOLD: {margin}%"

    # 1. Hard keyword violations
    if violations:
        system += "\n\n=== AUTHORITY CHECK (KEYWORD MATCH) ==="
        for v in violations:
            system += f"\nRULE: {v['rule']}\nALLOWED: {v['allowed']}\nCONDITION: {v['condition']}\nFALLBACK: {v['fallback']}\n"
        if any(v['allowed'] == 'no' for v in violations):
            system += "\nCRITICAL: This triggers a FORBIDDEN authority rule. You MUST refuse and provide the fallback behavior."

    # 2. Semantically similar authority rules (only if no keyword violations)
    if authority_docs:
        system += "\n\n=== RELEVANT AUTHORITY RULES (SEMANTIC MATCH) ==="
        for i, doc in enumerate(authority_docs, 1):
            system += f"\n[{i}] {doc.page_content[:300]}..."

    # 3. Behaviors (only if situations detected)
    if behaviors:
        system += "\n\n=== BEHAVIOR STYLE ==="
        for b in behaviors:
            system += f"\nSITUATION: {b['situation']}\nTONE: {b['tone']}\nDO: {b['do_rules']}\nDON'T: {b['dont_rules']}\nEXAMPLE: {b['example_response']}\n"

    # 4. Decision patterns (trimmed to 200 chars)
    if decisions:
        system += "\n\n=== RELEVANT DECISION PATTERNS ==="
        for i, d in enumerate(decisions, 1):
            system += f"\n[{i}] {d.page_content[:200]}..."

    history_text = ""
    if history:
        history_text = "\n\nCONVERSATION HISTORY:\n"
        for m in history[:-1]:
            prefix = "User" if m['role'] == 'user' else "Assistant"
            history_text += f"{prefix}: {m['content']}\n"

    # Build reasoning instruction based on flag
    if include_reasoning_in_output:
        reasoning_instruction = "\n4. Show step-by-step reasoning with evidence sources and confidence levels."
        output_format = ""
    else:
        reasoning_instruction = ""
        output_format = """

CRITICAL OUTPUT FORMAT — YOU MUST FOLLOW THIS EXACTLY:
- Respond in plain, natural conversational prose only.
- NEVER use structured headers like "Recommendation:", "Reasoning:", "Analysis:", "Confidence:", "Step-by-step:", "Final Answer:", or similar.
- NEVER output labels like "PROCEED", "REJECT", "ESCALATE", "DELAY", "NEGOTIATE", or "NEED MORE INFO" as standalone lines or headers.
- If you need more information, simply ask follow-up questions naturally — do NOT label it as "NEED MORE INFO".
- If a request is forbidden, simply refuse politely and explain why — do NOT use "REJECT" or "FORBIDDEN" labels.
- Your response should read like a human assistant speaking directly to the user, with no meta-commentary about your internal process.
- The user will see your governance analysis separately; do NOT mention it in your response."""

    full_prompt = f"""{system}

{history_text}

CURRENT USER QUESTION: {user_message}

INSTRUCTIONS:
1. Check if this violates any authority rules (keyword or semantic). If yes, REFUSE and explain fallback.
2. Detect the situation type and apply matching persona behavior style (tone, do/don't).
3. Use relevant decision patterns as precedent.
4. State your final recommendation clearly (PROCEED / REJECT / ESCALATE / DELAY / NEGOTIATE / NEED MORE INFO).
5. If you need more information to answer accurately, ask specific questions. Do not guess.{reasoning_instruction}{output_format}

Your Response:"""
    return full_prompt

# =============================================================================
# VECTORSTORE SYNC
# =============================================================================

def add_decision_to_vectorstore(decision):
    doc_content = (
        f"Question: {decision.question}\nContext: {decision.context}\n"
        f"Answer: {decision.ideal_answer}\nReasoning: {decision.reasoning}\n"
        f"Category: {decision.category} | Action: {decision.action_type} | Authority: {decision.authority_level}"
    )
    doc = text_splitter.create_documents(
        [doc_content],
        metadatas=[{"source": f"decision:{decision.category}", "id": str(decision.id)}]
    )
    vs = get_vectorstore()
    vs.add_documents(doc)

def add_behavior_to_vectorstore(behavior):
    doc_content = (
        f"Situation: {behavior.situation}\nTone: {behavior.tone}\n"
        f"Response: {behavior.example_response}\nDo: {behavior.do_rules}\nDon't: {behavior.dont_rules}"
    )
    doc = text_splitter.create_documents(
        [doc_content],
        metadatas=[{"source": "behavior", "id": str(behavior.id)}]
    )
    vs = get_vectorstore()
    vs.add_documents(doc)