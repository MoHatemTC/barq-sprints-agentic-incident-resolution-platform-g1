import sys
import types

try:
    import langchain_community.llms
    class VertexAI: pass
    langchain_community.llms.VertexAI = VertexAI
    import langchain_community.chat_models
    cv = types.ModuleType('langchain_community.chat_models.vertexai')
    class ChatVertexAI: pass
    cv.ChatVertexAI = ChatVertexAI
    sys.modules['langchain_community.chat_models.vertexai'] = cv
    langchain_community.chat_models.vertexai = cv
except Exception:
    pass

import os, json
from dotenv import load_dotenv
load_dotenv('.env', override=True)
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'src'))

from app.core.config import get_retrieval_settings
get_retrieval_settings.cache_clear()
print('Active Retrieval Mode:', get_retrieval_settings().retrieval_mode)

from qdrant_client import QdrantClient
from agent.retrieval import QdrantRetriever
from app.retrieval.embedding import FastEmbedEngine
from app.models.knowledge import Classification
from eval.run_manual_stage_a import RagasJudge, _load_adapters

adapters = _load_adapters()
policy = adapters.load_metric_policy()
judge = RagasJudge(policy)
rows = adapters.retrieval_only_rows()

client = QdrantClient(url='http://127.0.0.1:16333')
retriever = QdrantRetriever(
    lambda: client,
    FastEmbedEngine,
    collection_name='stage_a_scratch',
)

# Test 4 turns
test_turns = ['S02-T3', 'S02-T4', 'S03-T1', 'S06-T3']

for tid in test_turns:
    row = next(r for r in rows if r['turn_id'] == tid)
    res = retriever.search(row['user_input'], classification=Classification.OTHER, top_k=8, threshold=0.55)
    contexts = [h.text for h in res.hits]
    score = judge.score(row, contexts)
    print('='*30)
    print(f'=== {tid} ===')
    print('Query:', row['user_input'])
    print(f'Top Hit 1: [{res.hits[0].article_id}] "{res.hits[0].title}"')
    print(f'RAGAS Result: {score}')
