# Repeated shortest paths

Build an executable in any language. It receives two command-line arguments: `INPUT_DIR` and `OUTPUT_PATH`. Read `INPUT_DIR/graph.txt` and write only the requested output file. The input directory is read-only.

The first line contains `n m q`: the number of vertices, undirected edges, and queries. Vertices are numbered from 0 to `n-1`. The next `m` lines contain `u v w`, an edge of nonnegative integer weight `w`; parallel edges and self-loops are allowed. The next `q` lines contain `s t`. For each query, output the minimum total weight of any path from `s` to `t`, or `-1` if no path exists. A vertex has distance zero to itself. Write each answer as a base-10 integer followed by a newline, with no other output in the file. Edge weights fit in a signed 64-bit integer, and every reachable shortest distance fits in a signed 64-bit integer.

Queries may repeat sources or entire pairs. Optimize the total time across all queries while preserving exact output bytes.

The bundled `mesh` case has 5,000 vertices, 25,002 edges, and 1,444 queries. The `disconnected` case has 5,000 vertices, 25,001 edges, and 1,444 queries. Each case has 13 distinct query sources.
