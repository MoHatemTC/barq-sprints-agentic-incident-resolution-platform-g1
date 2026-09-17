# Sprint 2 Retrieval Audit Report

## 1. Margin of Improvement
Based on the `eval/ablation_results.json`, we compared three retrieval modes on the 25-incident evaluation set. 
- **Dense-Only** achieved a context recall of **0.76** and an overall accuracy of **0.76** (successfully identifying relevant chunks for all 19 answerable incidents, but failing to refuse the 6 unanswerable incidents due to high cosine similarity scores > 0.30).
- **Hybrid** search (Dense + Sparse with RRF) performed similarly on recall and accuracy.
- **Hybrid Reranked** (`fastembed`'s `TextCrossEncoder`) achieved a context recall of **0.96** and a perfect accuracy of **1.0**. The cross-encoder correctly refused all 6 unanswerable incidents (scores < 0.0) while retaining the answerable ones.

## 2. Latency Profile
- **Dense-Only**: p50 = ~57ms, mean = ~65ms
- **Hybrid**: p50 = ~61ms, mean = ~63ms
- **Hybrid Reranked**: p50 = ~745ms, mean = ~750ms

The cross-encoder introduces a significant latency penalty (~680ms increase). While accuracy is flawless, this must be evaluated against the "latency headroom" NFR. If latency becomes critical, falling back to Hybrid mode provides a much faster response with a tradeoff in confidence calibration for unanswerable queries.

## 3. Sparse Rescue Cases
The ablation harness searches for "sparse rescues"—incidents where the dense-only model failed to retrieve a relevant article, but the hybrid search (via BM25 sparse vectors) succeeded. 

**Analysis:** The `sparse_rescue_cases` list is empty (`[]`) in the final output. The `BAAI/bge-small-en-v1.5` dense model achieved 100% recall on the answerable subset (19/19) without deduplication, meaning there were no dense-only failures for the sparse model to rescue. If the requirement anticipated two specific failures, this would have required the dense model to miss exact-match keywords (like error codes), but the current embedding model successfully bridged that gap on its own.

## 4. Duplicate Article IDs Analysis
The evaluation metrics (`context_precision`) treat multiple chunks from the same highly-relevant article as multiple separate correct hits. We verified whether we should deduplicate chunks by `article_id` before returning them. 
**Conclusion:** Duplicates are **expected chunk-level results**. The LLM generator requires top-n *passages* for its context window. Providing 5 highly-relevant chunks from the *same* correct article yields a better generation context than forcing 1 correct chunk and 4 irrelevant padding articles. Thus, we preserved the duplicate `article_id` behavior in `hybrid_search.py` and the chunk-level precision metric.
