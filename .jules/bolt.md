## 2024-05-18 - Optimized Duplicate Detection bottleneck
**Learning:** The `get_duplicate_pids` sidebar filter function had an $O(N^2)$ bottleneck because it repeatedly called `find_similar_papers`, performing redundant vector normalizations for the cosine similarity math.
**Action:** When implementing mathematical comparisons that iterate over pairs in a list, compute invariants (like vector norms) outside the loops. We optimized `get_duplicate_pids` with a double loop (`i`, `j = i + 1`) and pre-calculated norms for a ~5x speedup.
## 2024-05-19 - Duplicate API call overhead
**Learning:** `find_similar_papers` had an O(N) function call overhead bottleneck due to calling `cosine_similarity` for every document comparison in the loop. Furthermore, the query norm was being redundantly calculated inside `cosine_similarity` for each individual comparison.
**Action:** Inline math calculations when iterating over collections and pre-compute constants. We moved the `norm_query` logic to the start of `find_similar_papers` and inlined the vector dot product loop, yielding an approximately ~30% improvement in matching speed.
## 2024-05-20 - Cache key signature bottleneck with large lists
**Learning:** Computing cache signatures inside Streamlit's tight rerun loops by converting large arrays inside frozen Pydantic models (like 384-dimension embedding lists) into tuples `tuple(list)` and sorting them causes severe `O(N log N) + O(N * D)` main thread blocking.
**Action:** When a Pydantic model is `frozen=True` and replacing nested arrays generates a new object identity, use `frozenset((id_field, id(array_field)))` instead of sorting deep tuples. This brings the signature calculation down to `O(N)` and prevents UI freezing.
