import logging
import time
import re
import json
import hashlib
import pickle
import asyncio
from functools import lru_cache, wraps
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple, Any

from django.conf import settings
from sqlalchemy import create_engine, text
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

# =============================================================================
# REDIS CACHE SETUP
# =============================================================================
try:
    import redis
    from redis.connection import ConnectionPool as RedisConnectionPool
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

# =============================================================================
# SENTENCE TRANSFORMERS (unchanged)
# =============================================================================
try:
    from langchain_huggingface import HuggingFaceEmbeddings
    from sentence_transformers import CrossEncoder
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    HuggingFaceEmbeddings = None
    CrossEncoder = None

from .models import BotConfig, Conversation, Behavior, AuthorityRule, Decision, FlaggedQuestion

logger = logging.getLogger(__name__)

# =============================================================================
# REDIS CONFIGURATION
# =============================================================================
REDIS_HOST = getattr(settings, 'REDIS_HOST', 'redis')
REDIS_PORT = getattr(settings, 'REDIS_PORT', 6379)
REDIS_DB = getattr(settings, 'REDIS_DB', 0)
REDIS_PASSWORD = getattr(settings, 'REDIS_PASSWORD', None)
REDIS_SOCKET_TIMEOUT = getattr(settings, 'REDIS_SOCKET_TIMEOUT', 2)
REDIS_SOCKET_CONNECT_TIMEOUT = getattr(settings, 'REDIS_SOCKET_CONNECT_TIMEOUT', 2)

# Cache TTLs (seconds)
CACHE_TTL = {
    'embedding': 86400 * 7,      # 7 days - embeddings don't change
    'vector_search': 300,          # 5 min - search results
    'rerank': 180,                # 3 min - reranked results
    'query_expand': 86400,        # 24 hours - query expansions
    'rules': 300,                 # 5 min - governance rules
    'config': 60,                 # 1 min - config
    'llm_response': 180,          # 3 min - LLM responses (semantic cache)
    'history': 30,                # 30 sec - conversation history
}

# =============================================================================
# REDIS CLIENT WITH CONNECTION POOLING
# =============================================================================
_redis_pool = None
_redis_client = None

def get_redis_client():
    global _redis_pool, _redis_client
    if _redis_client is None and REDIS_AVAILABLE:
        try:
            _redis_pool = RedisConnectionPool(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
                max_connections=50,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            _redis_client = redis.Redis(connection_pool=_redis_pool)
            # Test connection
            _redis_client.ping()
            logger.info("[REDIS] Connected successfully")
        except Exception as e:
            logger.warning(f"[REDIS] Connection failed: {e}. Running without cache.")
            _redis_client = False  # Sentinel
    return _redis_client if _redis_client is not False else None

def _generate_cache_key(*args, prefix: str = "") -> str:
    """Generate deterministic cache key from arguments."""
    content = ":".join(str(a) for a in args)
    hash_val = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"{prefix}:{hash_val}" if prefix else hash_val

# =============================================================================
# CACHE DECORATORS
# =============================================================================
def redis_cache(ttl: int, prefix: str = "", serializer: str = "json"):
    """Decorator to cache function results in Redis with fallback."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            client = get_redis_client()
            if client is None:
                return func(*args, **kwargs)
            
            # Build cache key from function args
            cache_key = _generate_cache_key(func.__name__, *args, **kwargs, prefix=prefix)
            
            try:
                cached = client.get(cache_key)
                if cached:
                    if serializer == "pickle":
                        return pickle.loads(cached)
                    return json.loads(cached)
            except Exception as e:
                logger.debug(f"[REDIS] Cache read error: {e}")
            
            # Execute function
            result = func(*args, **kwargs)
            
            # Cache result
            try:
                if serializer == "pickle":
                    value = pickle.dumps(result)
                else:
                    value = json.dumps(result, default=str)
                client.setex(cache_key, ttl, value)
            except Exception as e:
                logger.debug(f"[REDIS] Cache write error: {e}")
            
            return result
        return wrapper
    return decorator

def redis_cache_invalidate(pattern: str):
    """Invalidate Redis keys matching pattern."""
    client = get_redis_client()
    if client is None:
        return
    try:
        keys = client.scan_iter(match=pattern)
        for key in keys:
            client.delete(key)
        logger.info(f"[REDIS] Invalidated pattern: {pattern}")
    except Exception as e:
        logger.warning(f"[REDIS] Invalidation failed: {e}")

# =============================================================================
# EMBEDDING CONFIGURATION
# =============================================================================
EMBEDDING_MODEL_NAME = getattr(settings, 'EMBEDDING_MODEL_NAME', 'sentence-transformers/all-MiniLM-L6-v2')
EMBEDDING_DEVICE = getattr(settings, 'EMBEDDING_DEVICE', 'cpu')
EMBEDDING_BATCH_SIZE = getattr(settings, 'EMBEDDING_BATCH_SIZE', 32)
RERANKER_MODEL = getattr(settings, 'RERANKER_MODEL', 'cross-encoder/ms-marco-MiniLM-L-6-v2')

if SENTENCE_TRANSFORMERS_AVAILABLE:
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME,
        model_kwargs={'device': EMBEDDING_DEVICE, 'trust_remote_code': False},
        encode_kwargs={
            'normalize_embeddings': True,
            'batch_size': EMBEDDING_BATCH_SIZE,
        }
    )
else:
    raise ImportError(
        "sentence-transformers and langchain-huggingface are required. "
        "Run: pip install sentence-transformers langchain-huggingface"
    )

# =============================================================================
# LLM INSTANCES
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
    model="claude-haiku-4-5",
    temperature=None,
    max_tokens=2048,
    anthropic_api_key=settings.ANTHROPIC_API_KEY
)

llm = llm_anthropic if settings.ANTHROPIC_API_KEY else llm_openai

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len
)

# =============================================================================
# CROSS-ENCODER RE-RANKER (lazy-loaded)
# =============================================================================
_reranker = None

def get_reranker():
    global _reranker
    if _reranker is None and SENTENCE_TRANSFORMERS_AVAILABLE:
        try:
            _reranker = CrossEncoder(
                RERANKER_MODEL,
                device='cpu',
                max_length=256
            )
            logger.info("[KB] Cross-encoder re-ranker loaded.")
        except Exception as e:
            logger.warning(f"[KB] Re-ranker load failed: {e}")
            _reranker = False
    return _reranker if _reranker is not False else None

# =============================================================================
# VECTORSTORE WITH ADVANCED CONNECTION POOLING
# =============================================================================
_vectorstore = None
_engine = None

def get_vectorstore():
    global _vectorstore, _engine
    if _vectorstore is None:
        _engine = create_engine(
            settings.CONNECTION_STRING,
            pool_size=20,              # Increased from 10
            max_overflow=40,           # Increased from 20
            pool_recycle=1800,         # 30 min
            pool_pre_ping=True,
            pool_use_lifo=True,        # LIFO for better locality
            echo=False,
        )
        _vectorstore = PGVector(
            connection=_engine,
            embeddings=embeddings,
            collection_name="knowledge_base",
            distance_strategy="cosine",
            use_jsonb=True,
        )
        # Ensure HNSW index exists
        _ensure_hnsw_index()
    return _vectorstore

def _ensure_hnsw_index():
    """Create HNSW index if not exists for fast approximate search."""
    try:
        with _engine.connect() as conn:
            # Check if HNSW index exists
            result = conn.execute(text("""
                SELECT indexname FROM pg_indexes 
                WHERE tablename = 'knowledge_base' AND indexname LIKE '%hnsw%'
            """))
            if not result.fetchone():
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_kb_hnsw 
                    ON knowledge_base 
                    USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                """))
                conn.commit()
                logger.info("[PGVECTOR] HNSW index created")
    except Exception as e:
        logger.warning(f"[PGVECTOR] HNSW index check/creation failed: {e}")

# =============================================================================
# OPTIMIZED EMBEDDING WITH REDIS CACHE
# =============================================================================
def _get_cached_embedding(text: str) -> Optional[List[float]]:
    """Get embedding from Redis cache."""
    client = get_redis_client()
    if client is None:
        return None
    try:
        key = _generate_cache_key(text, prefix="emb")
        cached = client.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    return None

def _set_cached_embedding(text: str, embedding: List[float]):
    """Store embedding in Redis cache."""
    client = get_redis_client()
    if client is None:
        return
    try:
        key = _generate_cache_key(text, prefix="emb")
        client.setex(key, CACHE_TTL['embedding'], json.dumps(embedding))
    except Exception:
        pass

def embed_query_cached(text: str) -> List[float]:
    """Get embedding with Redis fallback + LRU cache."""
    # Check Redis first
    cached = _get_cached_embedding(text)
    if cached is not None:
        return cached
    
    # Compute embedding
    embedding = embeddings.embed_query(text)
    
    # Cache in Redis
    _set_cached_embedding(text, embedding)
    return embedding

# =============================================================================
# BATCH EMBEDDING FOR MULTI-QUERY
# =============================================================================
def embed_queries_batch(texts: List[str]) -> List[List[float]]:
    """Batch embed multiple queries with caching."""
    if not texts:
        return []
    
    # Deduplicate while preserving order
    unique_texts = list(dict.fromkeys(texts))
    
    # Check Redis cache for all
    results = {}
    uncached = []
    
    for t in unique_texts:
        emb = _get_cached_embedding(t)
        if emb is not None:
            results[t] = emb
        else:
            uncached.append(t)
    
    # Batch compute uncached
    if uncached:
        computed = embeddings.embed_documents(uncached)
        for t, emb in zip(uncached, computed):
            results[t] = emb
            _set_cached_embedding(t, emb)
    
    return [results[t] for t in texts]

# =============================================================================
# THREADPOOL FOR PARALLEL OPERATIONS
# =============================================================================
_executor = ThreadPoolExecutor(max_workers=10)

# =============================================================================
# SMART VECTOR SEARCH WITH REDIS CACHING
# =============================================================================
def _cached_vector_search(query: str, top_k: int, doc_type_prefix: str) -> List[Document]:
    """
    Cached vector search. Stores doc IDs + scores in Redis, reconstructs Documents.
    """
    client = get_redis_client()
    cache_key = _generate_cache_key(query, top_k, doc_type_prefix, prefix="vs")
    
    # Try cache
    if client:
        try:
            cached = client.get(cache_key)
            if cached:
                data = json.loads(cached)
                return [Document(
                    page_content=d['content'],
                    metadata=d['metadata']
                ) for d in data]
        except Exception:
            pass
    
    # Search
    vs = get_vectorstore()
    docs = vs.similarity_search(query, k=top_k)
    filtered = [d for d in docs if d.metadata.get("source", "").startswith(doc_type_prefix)]
    
    # Cache results
    if client and filtered:
        try:
            cache_data = [{
                'content': d.page_content,
                'metadata': d.metadata
            } for d in filtered]
            client.setex(cache_key, CACHE_TTL['vector_search'], json.dumps(cache_data))
        except Exception:
            pass
    
    return filtered

# =============================================================================
# OPTIMIZED FETCH WITH REDIS
# =============================================================================
def fetch_kb_parallel(user_message, situations, session_id, history_limit, use_kb=True, skip_history=False):
    """
    Fetch KB + history in parallel using threads + Redis caching.
    """
    if not use_kb:
        if skip_history:
            return [], [], [], []
        return [], [], [], get_history(session_id, limit=history_limit)

    # Check if we have a fully cached response for this exact query
    client = get_redis_client()
    if client:
        try:
            full_cache_key = _generate_cache_key(user_message, situations, session_id, prefix="full_kb")
            cached = client.get(full_cache_key)
            if cached:
                data = json.loads(cached)
                return (
                    [Document(**d) for d in data['decisions']],
                    data['behaviors'],
                    [Document(**d) for d in data['authority']],
                    data['history']
                )
        except Exception:
            pass

    # Pre-expand query
    expanded_queries = list(_expand_query(user_message))

    # Parallel fetch with Redis caching
    f_decisions = _executor.submit(_get_relevant_decisions_multi, expanded_queries, 3)
    f_behaviors = _executor.submit(get_relevant_behaviors, situations)
    f_authority = _executor.submit(_get_relevant_authority_multi, expanded_queries, 2)
    f_history = _executor.submit(get_history, session_id, history_limit)

    decisions = f_decisions.result()
    behaviors = f_behaviors.result()
    authority = f_authority.result()
    history = f_history.result()

    # Cache full result
    if client:
        try:
            cache_data = {
                'decisions': [{'page_content': d.page_content, 'metadata': d.metadata} for d in decisions],
                'behaviors': behaviors,
                'authority': [{'page_content': d.page_content, 'metadata': d.metadata} for d in authority],
                'history': history
            }
            client.setex(full_cache_key, 60, json.dumps(cache_data, default=str))
        except Exception:
            pass

    return decisions, behaviors, authority, history

# =============================================================================
# REDIS-CACHED CONFIG & RULES
# =============================================================================
@redis_cache(ttl=CACHE_TTL['config'], prefix="cfg")
def get_config():
    return {c['key']: c['value'] for c in BotConfig.objects.values('key', 'value')}

def invalidate_config_cache():
    redis_cache_invalidate("cfg:*")

@redis_cache(ttl=CACHE_TTL['rules'], prefix="auth")
def get_authority_rules_cached(active_only=True):
    return get_authority_rules(active_only=active_only)

@redis_cache(ttl=CACHE_TTL['rules'], prefix="beh")
def get_behaviors_cached(active_only=True):
    return get_behaviors(active_only=active_only)

def invalidate_authority_cache():
    redis_cache_invalidate("auth:*")
    get_authority_rules_cached.cache_clear() if hasattr(get_authority_rules_cached, 'cache_clear') else None

def invalidate_behavior_cache():
    redis_cache_invalidate("beh:*")
    get_behaviors_cached.cache_clear() if hasattr(get_behaviors_cached, 'cache_clear') else None

# =============================================================================
# DATA ACCESS WITH REDIS
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
    # Invalidate history cache
    redis_cache_invalidate(f"hist:{session_id}*")

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

# =============================================================================
# SMART RETRIEVAL UTILITIES
# =============================================================================
def _extract_keywords(text: str):
    stopwords = {'what', 'when', 'where', 'which', 'who', 'whom', 'whose', 'why', 'how',
                 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had',
                 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might',
                 'must', 'shall', 'can', 'need', 'dare', 'ought', 'used', 'to', 'of', 'and',
                 'the', 'a', 'an', 'in', 'on', 'at', 'by', 'for', 'with', 'about', 'against',
                 'between', 'into', 'through', 'during', 'before', 'after', 'above', 'below',
                 'from', 'up', 'down', 'out', 'off', 'over', 'under', 'again', 'further',
                 'then', 'once', 'here', 'there', 'all', 'any', 'both', 'each', 'few',
                 'more', 'most', 'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own',
                 'same', 'so', 'than', 'too', 'very', 'just', 'also', 'this', 'that', 'these',
                 'those', 'am', 'it', 'its', 'i', 'me', 'my', 'we', 'our', 'you', 'your',
                 'he', 'him', 'his', 'she', 'her', 'they', 'them', 'their'}
    words = re.findall(r'\b[a-z]{4,}\b', text.lower())
    return set(w for w in words if w not in stopwords)

def _keyword_boost_score(query: str, doc_text: str) -> float:
    q_keywords = _extract_keywords(query)
    if not q_keywords:
        return 0.0
    d_keywords = _extract_keywords(doc_text)
    matches = len(q_keywords & d_keywords)
    return matches / len(q_keywords)

@redis_cache(ttl=CACHE_TTL['query_expand'], prefix="expand")
def _expand_query(user_message: str):
    """
    Generate paraphrased query variants using fast LLM.
    Cached in Redis to avoid recomputation across workers.
    """
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
        resp = llm_openai_fast.invoke(prompt)
        content = getattr(resp, 'content', '[]')
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            variants = json.loads(match.group())
            if isinstance(variants, list):
                result = [user_message] + [v.strip() for v in variants if isinstance(v, str) and len(v.strip()) > 5][:2]
                return tuple(result)
    except Exception as e:
        logger.debug(f"[KB] Query expansion failed: {e}")

    return (user_message,)

# =============================================================================
# OPTIMIZED MULTI-VECTOR SEARCH WITH REDIS
# =============================================================================
def _multi_vector_search(queries, top_k_per_query, doc_type_prefix):
    """
    Search with multiple queries, deduplicate, and cache.
    Uses batch embedding for speed.
    """
    if not queries:
        return []
    
    # Batch embed all queries for cache warming
    embed_queries_batch(list(queries))
    
    # Parallel search with caching
    futures = []
    for q in queries:
        futures.append(_executor.submit(_cached_vector_search, q, top_k_per_query, doc_type_prefix))
    
    seen_ids = set()
    all_docs = []
    
    for f in futures:
        try:
            docs = f.result()
            for d in docs:
                doc_id = d.metadata.get("id")
                if doc_id and doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    all_docs.append(d)
                elif not doc_id:
                    all_docs.append(d)
        except Exception as e:
            logger.error(f"[KB] Vector search failed: {e}")

    return all_docs

def _reciprocal_rank_fuse(lists, k=60):
    scores = {}
    items = {}
    for doc_list in lists:
        for rank, item in enumerate(doc_list):
            item_id = item.metadata.get("id") if hasattr(item, 'metadata') else None
            if item_id is None:
                item_id = hash(item.page_content if hasattr(item, 'page_content') else str(item))
            if item_id not in scores:
                scores[item_id] = 0
                items[item_id] = item
            scores[item_id] += 1.0 / (k + rank + 1)
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [items[item_id] for item_id, _ in fused]

def _rerank_candidates(query: str, documents, top_n=15):
    """
    Re-rank with cross-encoder + Redis cache.
    """
    if not documents:
        return documents
    
    # Check Redis cache for rerank
    client = get_redis_client()
    cache_key = _generate_cache_key(query, *[d.metadata.get('id', d.page_content[:50]) for d in documents[:top_n]], prefix="rerank")
    
    if client:
        try:
            cached = client.get(cache_key)
            if cached:
                data = json.loads(cached)
                id_order = {d['id']: i for i, d in enumerate(data)}
                return sorted(documents, key=lambda d: id_order.get(d.metadata.get('id'), 999))
        except Exception:
            pass
    
    reranker = get_reranker()
    if reranker is None:
        return documents[:top_n]
    
    candidates = documents[:top_n]
    texts = [d.page_content[:512] for d in candidates]
    pairs = [[query, text] for text in texts]
    
    try:
        scores = reranker.predict(pairs, batch_size=8, show_progress_bar=False)
        scored = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        result = [doc for doc, _ in scored]
        
        # Cache result
        if client:
            try:
                cache_data = [{'id': d.metadata.get('id'), 'score': float(s)} for d, s in scored]
                client.setex(cache_key, CACHE_TTL['rerank'], json.dumps(cache_data))
            except Exception:
                pass
        
        return result
    except Exception as e:
        logger.warning(f"[KB] Re-ranking failed: {e}")
        return candidates

# =============================================================================
# SEMANTIC SEARCH (OPTIMIZED)
# =============================================================================
def _get_relevant_decisions_multi(queries, top_k=3):
    """
    Smart multi-stage retrieval with Redis caching.
    """
    candidates_per_query = max(top_k * 5, 15)
    docs = _multi_vector_search(queries, candidates_per_query, "decision:")

    if not docs:
        return []

    # RRF fusion when multiple queries
    if len(queries) > 1:
        lists = []
        for q in queries:
            try:
                docs_list = _cached_vector_search(q, candidates_per_query, "decision:")
                lists.append(docs_list)
            except Exception:
                lists.append([])
        if len(lists) > 1 and any(lists):
            docs = _reciprocal_rank_fuse([l for l in lists if l])

    # Keyword boost
    doc_ids = [d.metadata.get("id") for d in docs if d.metadata.get("id")]
    decision_map = {}
    if doc_ids:
        for d in Decision.objects.filter(id__in=doc_ids).values('id', 'question', 'context', 'ideal_answer'):
            decision_map[str(d['id'])] = f"{d['question']} {d['context']} {d['ideal_answer']}"

    boosted = []
    for d in docs:
        score = 0.0
        doc_id = d.metadata.get("id")
        if doc_id and str(doc_id) in decision_map:
            score = _keyword_boost_score(queries[0], decision_map[str(doc_id)])
        boosted.append((d, score))

    boosted.sort(key=lambda x: x[1], reverse=True)
    docs = [d for d, _ in boosted]

    docs = _rerank_candidates(queries[0], docs, top_n=15)
    return docs[:top_k]

def _get_relevant_authority_multi(queries, top_k=3):
    candidates_per_query = max(top_k * 5, 15)
    docs = _multi_vector_search(queries, candidates_per_query, "authority:")

    if not docs:
        return []

    if len(queries) > 1:
        lists = []
        for q in queries:
            try:
                docs_list = _cached_vector_search(q, candidates_per_query, "authority:")
                lists.append(docs_list)
            except Exception:
                lists.append([])
        if len(lists) > 1 and any(lists):
            docs = _reciprocal_rank_fuse([l for l in lists if l])

    doc_ids = [d.metadata.get("id") for d in docs if d.metadata.get("id")]
    rule_map = {}
    if doc_ids:
        for r in AuthorityRule.objects.filter(id__in=doc_ids).values('id', 'action_type', 'condition', 'fallback_behavior'):
            rule_map[str(r['id'])] = f"{r['action_type']} {r['condition']} {r['fallback_behavior']}"

    boosted = []
    for d in docs:
        score = 0.0
        doc_id = d.metadata.get("id")
        if doc_id and str(doc_id) in rule_map:
            score = _keyword_boost_score(queries[0], rule_map[str(doc_id)])
        boosted.append((d, score))

    boosted.sort(key=lambda x: x[1], reverse=True)
    docs = [d for d, _ in boosted]

    docs = _rerank_candidates(queries[0], docs, top_n=12)
    return docs[:top_k]

# Backward compatibility
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
# LLM RESPONSE CACHE (SEMANTIC CACHE)
# =============================================================================
def get_cached_llm_response(prompt_hash: str) -> Optional[str]:
    """Get cached LLM response from Redis."""
    client = get_redis_client()
    if client is None:
        return None
    try:
        key = f"llm:{prompt_hash}"
        cached = client.get(key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    return None

def cache_llm_response(prompt_hash: str, response: str, ttl: int = None):
    """Cache LLM response in Redis."""
    client = get_redis_client()
    if client is None:
        return
    try:
        key = f"llm:{prompt_hash}"
        client.setex(key, ttl or CACHE_TTL['llm_response'], json.dumps(response))
    except Exception:
        pass

# =============================================================================
# PROMPT BUILDER
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
# VECTORSTORE SYNC WITH INVALIDATION
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
    # Invalidate caches
    redis_cache_invalidate("vs:*decision*")
    redis_cache_invalidate("full_kb:*")

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
    redis_cache_invalidate("beh:*")

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
    redis_cache_invalidate("vs:*authority*")
    redis_cache_invalidate("auth:*")
    redis_cache_invalidate("full_kb:*")