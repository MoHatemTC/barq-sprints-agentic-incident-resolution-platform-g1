import sys
import types

try:
    import langchain_community.llms

    class VertexAI:
        pass

    langchain_community.llms.VertexAI = VertexAI
    import langchain_community.chat_models

    cv = types.ModuleType("langchain_community.chat_models.vertexai")

    class ChatVertexAI:
        pass

    cv.ChatVertexAI = ChatVertexAI
    sys.modules["langchain_community.chat_models.vertexai"] = cv
    langchain_community.chat_models.vertexai = cv
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv(".env", override=True)
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from app.core.config import RetrievalMode, get_retrieval_settings  # noqa: E402

get_retrieval_settings.cache_clear()
print("Active Retrieval Mode:", get_retrieval_settings().retrieval_mode)

from qdrant_client import QdrantClient  # noqa: E402

from app.retrieval.embedding import FastEmbedEngine  # noqa: E402
from scripts.smoke_eval_retrieval import mean_reciprocal_rank, search  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "data" / "corpus"))
import adapters as A  # noqa: E402

turns = A.turns()
client = QdrantClient(url=get_retrieval_settings().qdrant_url)
engine = FastEmbedEngine()

# Test 4 turns
test_turns = ["S02-T3", "S02-T4", "S03-T1", "S06-T3"]

for tid in test_turns:
    turn = next(t for t in turns if t["turn_id"] == tid)
    hits = search(
        client,
        engine,
        RetrievalMode.HYBRID_RERANKED,
        "manual_semantic_sections",
        turn["standalone_input"],
        5,
    )
    retrieved = [h.article_number for h in hits]
    mrr = mean_reciprocal_rank(retrieved, turn)
    s = A.score_retrieval(retrieved, turn)
    print("=" * 30)
    print(f"=== {tid} ===")
    print("Query:", turn["standalone_input"])
    if hits:
        print(f'Top Hit 1: [{hits[0].article_id}] "{hits[0].title}"')
    print(f"Retrieval Score: recall={s['recall']}, precision={s['precision']}, mrr={mrr}")
