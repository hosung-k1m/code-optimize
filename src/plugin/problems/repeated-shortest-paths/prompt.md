# Repeated shortest paths

Build a WASIp1 executable that reads `INPUT_DIR/graph.txt` and writes to `OUTPUT_PATH`, passed as command-line arguments. The input directory is read-only.

The first line is `n m q` (vertices, undirected edges, queries). Vertices are numbered `0` to `n-1`. The next `m` lines are `u v w`; weights are nonnegative signed 64-bit integers, and parallel edges and self-loops are allowed. The next `q` lines are queries `s t`.

For each query, write the shortest-path weight or `-1` if unreachable, as a base-10 integer followed by a newline. A vertex has distance zero to itself. Reachable distances fit in signed 64-bit integers. Write no other output to the file. Queries may repeat sources or pairs; optimize total query time while preserving exact output bytes.

Both bundled cases have 5,000 vertices, 1,444 queries and 13 distinct sources. `mesh` has 25,002 edges; `disconnected` has 25,001.
