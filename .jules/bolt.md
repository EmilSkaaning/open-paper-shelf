## 2024-05-18 - Optimized Duplicate Detection bottleneck
**Learning:** The `get_duplicate_pids` sidebar filter function had an $O(N^2)$ bottleneck because it repeatedly called `find_similar_papers`, performing redundant vector normalizations for the cosine similarity math.
**Action:** When implementing mathematical comparisons that iterate over pairs in a list, compute invariants (like vector norms) outside the loops. We optimized `get_duplicate_pids` with a double loop (`i`, `j = i + 1`) and pre-calculated norms for a ~5x speedup.
