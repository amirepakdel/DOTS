import os
import logging
import time
import re
import json
import hashlib
import pickle
import threading 
from functools import lru_cache, wraps
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple, Any, Dict

# Hugging Face / HTTP logging suppression
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)


from django.conf import settings
from django.db import connection
from sqlalchemy import create_engine, text
from langchain_anthropic import ChatAnthropic
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# Redis
try:
    import redis
    from redis.connection import ConnectionPool as RedisConnectionPool
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

# Embeddings
try:
    from langchain_huggingface import HuggingFaceEmbeddings
    from sentence_transformers import CrossEncoder
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    HuggingFaceEmbeddings = None
    CrossEncoder = None

from .models import (
    BotConfig, Conversation, Behavior, 
    AuthorityRule, Decision, FlaggedQuestion
)

logger = logging.getLogger(__name__)

# =============================================================================
# REDIS CONFIG
# =============================================================================
REDIS_HOST = getattr(settings, 'REDIS_HOST', 'redis')
REDIS_PORT = getattr(settings, 'REDIS_PORT', 6379)
REDIS_DB = getattr(settings, 'REDIS_DB', 0)
REDIS_PASSWORD = getattr(settings, 'REDIS_PASSWORD', None)

CACHE_TTL = {
    'embedding': 86400 * 30,      # 30 days — embeddings are immutable
    'vector_search': 120,         # 2 min — search results
    'rerank': 60,                 # 1 min — reranked results
    'query_expand': 86400 * 7,    # 7 days — query paraphrases
    'rules': 300,                 # 5 min — governance rules
    'config': 60,                 # 1 min — bot config
    'llm_response': 300,          # 5 min — LLM semantic cache
    'history': 30,                # 30 sec — conversation history
    'full_kb': 60,                # 1 min — entire KB fetch
}

# =============================================================================
# REDIS CLIENT (connection pooled)
# =============================================================================
_redis_client = None

def get_redis_client():
    global _redis_client
    if _redis_client is None and REDIS_AVAILABLE:
        try:
            pool = RedisConnectionPool(
                host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
                password=REDIS_PASSWORD,
                max_connections=50,
                socket_timeout=2,
                socket_connect_timeout=2,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            _redis_client = redis.Redis(connection_pool=pool)
            _redis_client.ping()
            logger.info("[REDIS] Connected")
        except Exception as e:
            logger.warning(f"[REDIS] Failed: {e}")
            _redis_client = False
    return _redis_client if _redis_client is not False else None

def _cache_key(*args, prefix: str = "") -> str:
    content = ":".join(str(a) for a in args)
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"{prefix}:{h}" if prefix else h

def redis_cache(ttl: int, prefix: str = "", use_pickle: bool = False):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            client = get_redis_client()
            if not client:
                return func(*args, **kwargs)
            
            key = _cache_key(func.__name__, *args, *kwargs.items(), prefix=prefix)
            try:
                cached = client.get(key)
                if cached:
                    return pickle.loads(cached) if use_pickle else json.loads(cached)
            except Exception:
                pass
            
            result = func(*args, **kwargs)
            try:
                data = pickle.dumps(result) if use_pickle else json.dumps(result, default=str)
                client.setex(key, ttl, data)
            except Exception:
                pass
            return result
        return wrapper
    return decorator

def invalidate_cache(pattern: str):
    client = get_redis_client()
    if not client:
        return
    try:
        for key in client.scan_iter(match=pattern):
            client.delete(key)
        logger.info(f"[REDIS] Invalidated {pattern}")
    except Exception as e:
        logger.warning(f"[REDIS] Invalidation failed: {e}")

# =============================================================================
# EMBEDDINGS
# =============================================================================
EMBEDDING_MODEL = getattr(settings, 'EMBEDDING_MODEL_NAME', 'sentence-transformers/all-MiniLM-L6-v2')
EMBEDDING_DEVICE = getattr(settings, 'EMBEDDING_DEVICE', 'cpu')
EMBEDDING_BATCH = getattr(settings, 'EMBEDDING_BATCH_SIZE', 32)
RERANKER_MODEL = getattr(settings, 'RERANKER_MODEL', 'cross-encoder/ms-marco-MiniLM-L-6-v2')

if SENTENCE_TRANSFORMERS_AVAILABLE:
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={'device': EMBEDDING_DEVICE, 'trust_remote_code': False},
        encode_kwargs={'normalize_embeddings': True, 'batch_size': EMBEDDING_BATCH}
    )
else:
    raise ImportError("pip install sentence-transformers langchain-huggingface")

# =============================================================================
# LLMs
# =============================================================================
llm_anthropic = ChatAnthropic(
    model="claude-sonnet-5", temperature=None, max_tokens=256,
    anthropic_api_key=settings.ANTHROPIC_API_KEY
)
llm = llm_anthropic if getattr(settings, 'ANTHROPIC_API_KEY', None) else None

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000, chunk_overlap=200, length_function=len
)

# =============================================================================
# RERANKER
# =============================================================================
_reranker = None

def get_reranker():
    global _reranker
    if _reranker is None and SENTENCE_TRANSFORMERS_AVAILABLE:
        try:
            _reranker = CrossEncoder(RERANKER_MODEL, device='cpu', max_length=256)
            logger.info("[RERANKER] Loaded")
        except Exception as e:
            logger.warning(f"[RERANKER] Failed: {e}")
            _reranker = False
    return _reranker if _reranker is not False else None

# =============================================================================
# VECTORSTORE + HNSW
# =============================================================================
_vectorstore = None
_engine = None
_vectorstore_lock = threading.Lock() 

def get_vectorstore():
    global _vectorstore, _engine
    if _vectorstore is not None:
        return _vectorstore

    with _vectorstore_lock:              # <-- ONLY ONE THREAD ENTERS HERE
        if _vectorstore is not None:    # double-check after lock
            return _vectorstore

        _engine = create_engine(
            settings.CONNECTION_STRING,
            pool_size=20, max_overflow=40,
            pool_recycle=1800, pool_pre_ping=True,
            pool_use_lifo=True, echo=False
        )
        _vectorstore = PGVector(
            connection=_engine,
            embeddings=embeddings,
            collection_name="knowledge_base",
            distance_strategy="cosine",
            use_jsonb=True,
        )
        _ensure_hnsw()
    return _vectorstore

def _ensure_hnsw():
    try:
        with _engine.connect() as conn:
            res = conn.execute(text("""
                SELECT 1 FROM pg_indexes 
                WHERE tablename = 'langchain_pg_embedding' 
                AND indexdef LIKE '%hnsw%'
            """))
            if not res.fetchone():
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_embedding_hnsw 
                    ON langchain_pg_embedding 
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """))
                conn.commit()
                logger.info("[PGVECTOR] HNSW index created")
    except Exception as e:
        logger.warning(f"[PGVECTOR] HNSW check failed: {e}")

# =============================================================================
# EMBEDDING CACHE (Redis-backed)
# =============================================================================
def _get_cached_embedding(text: str) -> Optional[List[float]]:
    client = get_redis_client()
    if not client:
        return None
    try:
        key = _cache_key(text, prefix="emb")
        data = client.get(key)
        return json.loads(data) if data else None
    except Exception:
        return None

def _set_cached_embedding(text: str, embedding: List[float]):
    client = get_redis_client()
    if not client:
        return
    try:
        key = _cache_key(text, prefix="emb")
        client.setex(key, CACHE_TTL['embedding'], json.dumps(embedding))
    except Exception:
        pass

def embed_query_cached(text: str) -> List[float]:
    cached = _get_cached_embedding(text)
    if cached is not None:
        return cached
    emb = embeddings.embed_query(text)
    _set_cached_embedding(text, emb)
    return emb

def embed_batch(texts: List[str]) -> List[List[float]]:
    """Batch embed with dedup + Redis cache."""
    if not texts:
        return []
    
    unique = list(dict.fromkeys(texts))
    results = {}
    uncached = []
    
    for t in unique:
        emb = _get_cached_embedding(t)
        if emb:
            results[t] = emb
        else:
            uncached.append(t)
    
    if uncached:
        computed = embeddings.embed_documents(uncached)
        for t, emb in zip(uncached, computed):
            results[t] = emb
            _set_cached_embedding(t, emb)
    
    return [results[t] for t in texts]

# =============================================================================
# VECTOR SEARCH WITH REDIS CACHE
# =============================================================================
def _cached_vector_search(query: str, top_k: int, doc_prefix: str) -> List[Document]:
    client = get_redis_client()
    cache_key = _cache_key(query, top_k, doc_prefix, prefix="vs")
    
    if client:
        try:
            cached = client.get(cache_key)
            if cached:
                data = json.loads(cached)
                return [Document(page_content=d['c'], metadata=d['m']) for d in data]
        except Exception:
            pass
    
    vs = get_vectorstore()
    docs = vs.similarity_search(query, k=top_k)
    filtered = [d for d in docs if d.metadata.get("source", "").startswith(doc_prefix)]
    
    if client and filtered:
        try:
            payload = [{'c': d.page_content, 'm': d.metadata} for d in filtered]
            client.setex(cache_key, CACHE_TTL['vector_search'], json.dumps(payload))
        except Exception:
            pass
    
    return filtered

# =============================================================================
# PARALLEL FETCH
# =============================================================================
_executor = ThreadPoolExecutor(max_workers=10)

def fetch_kb_parallel(user_message, situations, session_id, history_limit, use_kb=True, skip_history=False):
    if not use_kb:
        if skip_history:
            return [], [], [], []
        return [], [], [], get_history(session_id, limit=history_limit)
    
    # Check full cache
    client = get_redis_client()
    if client:
        try:
            key = _cache_key(user_message, situations, session_id, prefix="full_kb")
            cached = client.get(key)
            if cached:
                data = json.loads(cached)
                return (
                    [Document(page_content=d['c'], metadata=d['m']) for d in data['decisions']],
                    data['behaviors'],
                    [Document(page_content=d['c'], metadata=d['m']) for d in data['authority']],
                    data['history']
                )
        except Exception:
            pass
    
    expanded = list(_expand_query(user_message))
    
    f_decisions = _executor.submit(_get_relevant_decisions_multi, expanded, 3)
    f_behaviors = _executor.submit(get_relevant_behaviors, situations)
    f_authority = _executor.submit(_get_relevant_authority_multi, expanded, 2)
    f_history = _executor.submit(get_history, session_id, history_limit)
    
    decisions = f_decisions.result()
    behaviors = f_behaviors.result()
    authority = f_authority.result()
    history = f_history.result()
    
    if client:
        try:
            payload = {
                'decisions': [{'c': d.page_content, 'm': d.metadata} for d in decisions],
                'behaviors': behaviors,
                'authority': [{'c': d.page_content, 'm': d.metadata} for d in authority],
                'history': history
            }
            client.setex(_cache_key(user_message, situations, session_id, prefix="full_kb"), 
                        CACHE_TTL['full_kb'], json.dumps(payload, default=str))
        except Exception:
            pass
    
    return decisions, behaviors, authority, history

# =============================================================================
# CACHED DATA ACCESS (matches your models exactly)
# =============================================================================
@redis_cache(ttl=CACHE_TTL['config'], prefix="cfg")
def get_config():
    return {c['key']: c['value'] for c in BotConfig.objects.values('key', 'value')}

@redis_cache(ttl=CACHE_TTL['rules'], prefix="auth", use_pickle=True)
def get_authority_rules_cached(active_only=True):
    return get_authority_rules(active_only=active_only)

@redis_cache(ttl=CACHE_TTL['rules'], prefix="beh", use_pickle=True)
def get_behaviors_cached(active_only=True):
    return get_behaviors(active_only=active_only)

def invalidate_config_cache():
    invalidate_cache("cfg:*")

def invalidate_authority_cache():
    invalidate_cache("auth:*")
    get_authority_rules_cached.cache_clear() if hasattr(get_authority_rules_cached, 'cache_clear') else None

def invalidate_behavior_cache():
    invalidate_cache("beh:*")
    get_behaviors_cached.cache_clear() if hasattr(get_behaviors_cached, 'cache_clear') else None

# =============================================================================
# DATA ACCESS (your exact models)
# =============================================================================
def save_message(session_id, role, content):
    if content is None:
        content = ""
    elif not isinstance(content, str):
        try:
            content = str(content)
        except Exception:
            content = "[Unable to serialize]"
    
    Conversation.objects.create(session_id=session_id, role=role, content=content)
    invalidate_cache(f"hist:{session_id}*")
    invalidate_cache(f"full_kb:*{session_id}*")

@redis_cache(ttl=CACHE_TTL['history'], prefix="hist")
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
# SITUATION DETECTION
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

# =============================================================================
# QUERY EXPANSION (Redis cached)
# =============================================================================
@redis_cache(ttl=CACHE_TTL['query_expand'], prefix="expand")
def _expand_query(user_message: str):
    if len(user_message) < 15:
        return (user_message,)
    
    cfg = get_config()
    if cfg.get('disable_query_expansion', 'false').lower() == 'true':
        return (user_message,)
    
    prompt = (
        f"Generate 2 alternative phrasings of this search query for better semantic retrieval. "
        f"Return ONLY a JSON array of strings, no markdown.\n\nQuery: {user_message}"
    )
    try:
        resp = llm.invoke(prompt)
        content = getattr(resp, 'content', '[]')
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            variants = json.loads(match.group())
            if isinstance(variants, list):
                result = [user_message] + [v.strip() for v in variants 
                         if isinstance(v, str) and len(v.strip()) > 5][:2]
                return tuple(result)
    except Exception as e:
        logger.debug(f"[EXPAND] Failed: {e}")
    
    return (user_message,)

# =============================================================================
# MULTI-VECTOR SEARCH
# =============================================================================
def _multi_vector_search(queries, top_k_per_query, doc_prefix):
    if not queries:
        return []
    
    # Pre-warm embedding cache
    embed_batch(list(queries))
    
    futures = []
    for q in queries:
        futures.append(_executor.submit(_cached_vector_search, q, top_k_per_query, doc_prefix))
    
    seen = set()
    docs = []
    for f in futures:
        try:
            for d in f.result():
                doc_id = d.metadata.get("id")
                if doc_id and doc_id not in seen:
                    seen.add(doc_id)
                    docs.append(d)
                elif not doc_id:
                    docs.append(d)
        except Exception as e:
            logger.error(f"[SEARCH] Failed: {e}")
    
    return docs

def _reciprocal_rank_fuse(lists, k=60):
    scores, items = {}, {}
    for doc_list in lists:
        for rank, item in enumerate(doc_list):
            item_id = item.metadata.get("id") if hasattr(item, 'metadata') else None
            if item_id is None:
                item_id = hash(item.page_content if hasattr(item, 'page_content') else str(item))
            scores[item_id] = scores.get(item_id, 0) + 1.0 / (k + rank + 1)
            items[item_id] = item
    return [items[i] for i, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)]

def _rerank_candidates(query: str, documents, top_n=15):
    if not documents:
        return documents
    
    # Check cache
    client = get_redis_client()
    cache_key = _cache_key(query, *[d.metadata.get('id', d.page_content[:40]) for d in documents[:top_n]], prefix="rerank")
    
    if client:
        try:
            cached = client.get(cache_key)
            if cached:
                order = {d['id']: i for i, d in enumerate(json.loads(cached))}
                return sorted(documents, key=lambda d: order.get(d.metadata.get('id'), 999))[:top_n]
        except Exception:
            pass
    
    reranker = get_reranker()
    if not reranker:
        return documents[:top_n]
    
    candidates = documents[:top_n]
    texts = [d.page_content[:512] for d in candidates]
    pairs = [[query, t] for t in texts]
    
    try:
        scores = reranker.predict(pairs, batch_size=8, show_progress_bar=False)
        scored = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        result = [doc for doc, _ in scored]
        
        if client:
            try:
                payload = [{'id': d.metadata.get('id'), 's': float(s)} for d, s in scored]
                client.setex(cache_key, CACHE_TTL['rerank'], json.dumps(payload))
            except Exception:
                pass
        
        return result
    except Exception as e:
        logger.warning(f"[RERANK] Failed: {e}")
        return candidates

# =============================================================================
# SEMANTIC SEARCH (optimized for your Decision/AuthorityRule models)
# =============================================================================
def _get_relevant_decisions_multi(queries, top_k=3):
    # ------------------------------------------------------------------
    # LAYER 0: Exact question match (NEW)
    # ------------------------------------------------------------------
    exact_docs = _get_exact_decision_matches(queries[0] if queries else "", top_k)
    
    # ------------------------------------------------------------------
    # LAYER 1: Vector search (FIXED candidate pool)
    # ------------------------------------------------------------------
    # Was: max(top_k * 5, 15)  — too small when mixed with authority/behavior docs
    candidates_per_query = max(top_k * 20, 100)
    docs = _multi_vector_search(queries, candidates_per_query, "decision:")
    
    # ------------------------------------------------------------------
    # Merge: exact matches always win, then vector results, deduped by ID
    # ------------------------------------------------------------------
    seen = set()
    merged = []
    
    for d in exact_docs:
        did = d.metadata.get("id")
        if did and did not in seen:
            seen.add(did)
            merged.append(d)
    
    # RRF fusion if we have multiple query variants
    if len(queries) > 1:
        lists = []
        for q in queries:
            try:
                lists.append(_cached_vector_search(q, candidates_per_query, "decision:"))
            except Exception:
                lists.append([])
        if len(lists) > 1 and any(lists):
            fused = _reciprocal_rank_fuse([l for l in lists if l])
            for d in fused:
                did = d.metadata.get("id")
                if did and did not in seen:
                    seen.add(did)
                    merged.append(d)
                elif not did:
                    merged.append(d)
    else:
        for d in docs:
            did = d.metadata.get("id")
            if did and did not in seen:
                seen.add(did)
                merged.append(d)
            elif not did:
                merged.append(d)
    
    # ------------------------------------------------------------------
    # Keyword boost (existing logic, kept for hybrid scoring)
    # ------------------------------------------------------------------
    doc_ids = [d.metadata.get("id") for d in merged if d.metadata.get("id")]
    decision_map = {}
    if doc_ids:
        for d in Decision.objects.filter(id__in=doc_ids).values(
            'id', 'question', 'context', 'ideal_answer'
        ):
            decision_map[str(d['id'])] = f"{d['question']} {d['context']} {d['ideal_answer']}"
    
    boosted = []
    primary_query = queries[0].lower() if queries else ""
    q_keywords = set(re.findall(r'\b[a-z]{4,}\b', primary_query))
    
    for d in merged:
        score = 0.0
        doc_id = d.metadata.get("id")
        # Exact matches get massive boost
        if d.metadata.get("match_type") == "exact_question":
            score = 10.0
        elif doc_id and str(doc_id) in decision_map:
            d_text = decision_map[str(doc_id)].lower()
            score = sum(1 for w in q_keywords if w in d_text) / max(len(q_keywords), 1)
        boosted.append((d, score))
    
    boosted.sort(key=lambda x: x[1], reverse=True)
    docs = [d for d, _ in boosted]
    
    # ------------------------------------------------------------------
    # Rerank & truncate
    # ------------------------------------------------------------------
    docs = _rerank_candidates(queries[0] if queries else "", docs, top_n=20)
    return docs[:top_k]

def _get_exact_decision_matches(query: str, top_k: int = 3) -> List[Document]:
    """
    Direct ORM fallback for exact or near-exact question matches.
    This runs BEFORE vector search so saved questions are always found.
    """
    query_clean = query.strip()
    if not query_clean:
        return []

    matches = []
    # 1) Exact case-insensitive match on the question field
    qs = Decision.objects.filter(active=True, question__iexact=query_clean)
    if qs.exists():
        matches = list(qs)
    else:
        # 2) The decision question contains the user's query
        qs = Decision.objects.filter(active=True, question__icontains=query_clean)
        if qs.exists():
            matches = list(qs[:top_k])
        else:
            # 3) The user's query contains the decision question
            all_active = Decision.objects.filter(active=True).only('id', 'question')
            matches = [d for d in all_active if d.question.lower() in query_clean.lower()][:top_k]

    docs = []
    for d in matches:
        doc_content = (
            f"Question: {d.question}\nContext: {d.context}\n"
            f"Answer: {d.ideal_answer}\nReasoning: {d.reasoning}\n"
            f"Category: {d.category} | Action: {d.action_type} | Authority: {d.authority_level}"
        )
        docs.append(Document(
            page_content=doc_content,
            metadata={
                "source": f"decision:{d.category}",
                "id": str(d.id),
                "match_type": "exact_question"   # <-- key flag for reasoning trace
            }
        ))
    return docs

def _get_relevant_authority_multi(queries, top_k=3):
    candidates_per_query = max(top_k * 5, 15)
    docs = _multi_vector_search(queries, candidates_per_query, "authority:")
    
    if not docs:
        return []
    
    if len(queries) > 1:
        lists = []
        for q in queries:
            try:
                lists.append(_cached_vector_search(q, candidates_per_query, "authority:"))
            except Exception:
                lists.append([])
        if len(lists) > 1 and any(lists):
            docs = _reciprocal_rank_fuse([l for l in lists if l])
    
    doc_ids = [d.metadata.get("id") for d in docs if d.metadata.get("id")]
    rule_map = {}
    if doc_ids:
        for r in AuthorityRule.objects.filter(id__in=doc_ids).values(
            'id', 'action_type', 'condition', 'fallback_behavior'
        ):
            rule_map[str(r['id'])] = f"{r['action_type']} {r['condition']} {r['fallback_behavior']}"
    
    boosted = []
    for d in docs:
        score = 0.0
        doc_id = d.metadata.get("id")
        if doc_id and str(doc_id) in rule_map:
            q_keywords = set(re.findall(r'\b[a-z]{4,}\b', queries[0].lower()))
            score = sum(1 for w in q_keywords if w in rule_map[str(doc_id)].lower()) / max(len(q_keywords), 1)
        boosted.append((d, score))
    
    boosted.sort(key=lambda x: x[1], reverse=True)
    docs = [d for d, _ in boosted]
    
    docs = _rerank_candidates(queries[0], docs, top_n=12)
    return docs[:top_k]

# Backward compat
def get_relevant_decisions(user_message, top_k=3):
    return _get_relevant_decisions_multi(_expand_query(user_message), top_k)

def get_relevant_authority_rules(user_message, top_k=3):
    return _get_relevant_authority_multi(_expand_query(user_message), top_k)

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
# LLM SEMANTIC CACHE
# =============================================================================
def get_cached_llm_response(prompt_hash: str) -> Optional[str]:
    client = get_redis_client()
    if not client:
        return None
    try:
        data = client.get(f"llm:{prompt_hash}")
        return json.loads(data) if data else None
    except Exception:
        return None

def cache_llm_response(prompt_hash: str, response: str):
    client = get_redis_client()
    if not client:
        return
    try:
        client.setex(f"llm:{prompt_hash}", CACHE_TTL['llm_response'], json.dumps(response))
    except Exception:
        pass

# =============================================================================
# PROMPT BUILDER (your exact format)
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

    if violations:
        system += "\n\n=== AUTHORITY CHECK (KEYWORD MATCH) ==="
        for v in violations:
            system += f"\nRULE: {v['rule']}\nALLOWED: {v['allowed']}\nCONDITION: {v['condition']}\nFALLBACK: {v['fallback']}\n"
        if any(v['allowed'] == 'no' for v in violations):
            system += "\nCRITICAL: This triggers a FORBIDDEN authority rule. You MUST refuse and provide the fallback behavior."

    if authority_docs:
        system += "\n\n=== RELEVANT AUTHORITY RULES (SEMANTIC MATCH) ==="
        for i, doc in enumerate(authority_docs, 1):
            system += f"\n[{i}] {doc.page_content[:300]}..."

    if behaviors:
        system += "\n\n=== BEHAVIOR STYLE ==="
        for b in behaviors:
            system += f"\nSITUATION: {b['situation']}\nTONE: {b['tone']}\nDO: {b['do_rules']}\nDON'T: {b['dont_rules']}\nEXAMPLE: {b['example_response']}\n"

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

    return f"""{system}

{history_text}

CURRENT USER QUESTION: {user_message}

INSTRUCTIONS:
1. Check if this violates any authority rules (keyword or semantic). If yes, REFUSE and explain fallback.
2. Detect the situation type and apply matching persona behavior style (tone, do/don't).
3. Use relevant decision patterns as precedent.
4. State your final recommendation clearly (PROCEED / REJECT / ESCALATE / DELAY / NEGOTIATE / NEED MORE INFO).
5. If you need more information to answer accurately, ask specific questions. Do not guess.{reasoning_instruction}{output_format}

Your Response:"""

# =============================================================================
# VECTORSTORE SYNC WITH INVALIDATION
# =============================================================================
def add_decision_to_vectorstore(decision):
    vs = get_vectorstore()
    
    # Chunk A: Question-only (highly retrievable when user asks the exact question)
    question_doc = Document(
        page_content=f"Question: {decision.question}\nCategory: {decision.category}",
        metadata={
            "source": f"decision:{decision.category}",
            "id": str(decision.id),
            "chunk_type": "question"
        }
    )
    
    # Chunk B: Full context (for rich answers)
    full_content = (
        f"Question: {decision.question}\nContext: {decision.context}\n"
        f"Answer: {decision.ideal_answer}\nReasoning: {decision.reasoning}\n"
        f"Category: {decision.category} | Action: {decision.action_type} | Authority: {decision.authority_level}"
    )
    full_doc = Document(
        page_content=full_content,
        metadata={
            "source": f"decision:{decision.category}",
            "id": str(decision.id),
            "chunk_type": "full"
        }
    )
    
    vs.add_documents([question_doc, full_doc])
    invalidate_cache("vs:*decision*")
    invalidate_cache("full_kb:*")
    logger.info(f"[VECTOR] Indexed decision {decision.id} (2 chunks)")

def sync_all_decisions_to_vectorstore():
    """Backfill all active decisions into PGVector. Safe to re-run."""
    vs = get_vectorstore()          # initializes _engine under the lock
    engine = _engine

    # 1) Clear old decision vectors to avoid duplicates
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                DELETE FROM langchain_pg_embedding 
                WHERE cmetadata->>'source' LIKE 'decision:%'
            """))
            conn.commit()
        logger.info("[SYNC] Cleared old decision vectors")
    except Exception as e:
        logger.warning(f"[SYNC] Could not clear old vectors: {e}")

    # 2) Re-add all active decisions
    docs = []
    for d in Decision.objects.filter(active=True):
        docs.append(Document(
            page_content=f"Question: {d.question}\nCategory: {d.category}",
            metadata={
                "source": f"decision:{d.category}",
                "id": str(d.id),
                "chunk_type": "question"
            }
        ))
        full = (
            f"Question: {d.question}\nContext: {d.context}\n"
            f"Answer: {d.ideal_answer}\nReasoning: {d.reasoning}\n"
            f"Category: {d.category} | Action: {d.action_type} | Authority: {d.authority_level}"
        )
        docs.append(Document(
            page_content=full,
            metadata={
                "source": f"decision:{d.category}",
                "id": str(d.id),
                "chunk_type": "full"
            }
        ))

    if docs:
        vs.add_documents(docs)
        invalidate_cache("vs:*decision*")
        invalidate_cache("full_kb:*")
        logger.info(f"[SYNC] Re-indexed {len(docs)} decision chunks")
    else:
        logger.info("[SYNC] No active decisions to index")
        
def add_behavior_to_vectorstore(behavior):
    doc_content = (
        f"Situation: {behavior.situation}\nTone: {behavior.tone}\n"
        f"Response: {behavior.example_response}\nDo: {behavior.do_rules}\nDon't: {behavior.dont_rules}"
    )
    doc = text_splitter.create_documents(
        [doc_content],
        metadatas=[{"source": "behavior", "id": str(behavior.id)}]
    )
    get_vectorstore().add_documents(doc)
    invalidate_cache("beh:*")

def add_authority_to_vectorstore(rule):
    doc_content = (
        f"Action: {rule.action_type}\nAllowed: {rule.allowed}\n"
        f"Condition: {rule.condition}\nFallback: {rule.fallback_behavior}"
    )
    doc = text_splitter.create_documents(
        [doc_content],
        metadatas=[{"source": f"authority:{rule.action_type}", "id": str(rule.id)}]
    )
    get_vectorstore().add_documents(doc)
    invalidate_cache("vs:*authority*")
    invalidate_cache("auth:*")
    invalidate_cache("full_kb:*")