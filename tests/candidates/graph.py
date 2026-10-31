import heapq
import sys
from pathlib import Path


def main():
    tokens = iter(map(int, (Path(sys.argv[1]) / "graph.txt").read_bytes().split()))
    n, m, q = next(tokens), next(tokens), next(tokens)
    graph = [[] for _ in range(n)]
    for _ in range(m):
        u, v, weight = next(tokens), next(tokens), next(tokens)
        graph[u].append((v, weight))
        graph[v].append((u, weight))
    cache = {}
    answers = []
    for _ in range(q):
        source, target = next(tokens), next(tokens)
        if source not in cache:
            distances = [None] * n
            distances[source] = 0
            queue = [(0, source)]
            while queue:
                distance, vertex = heapq.heappop(queue)
                if distance != distances[vertex]:
                    continue
                for neighbor, weight in graph[vertex]:
                    candidate = distance + weight
                    if distances[neighbor] is None or candidate < distances[neighbor]:
                        distances[neighbor] = candidate
                        heapq.heappush(queue, (candidate, neighbor))
            cache[source] = distances
        result = cache[source][target]
        answers.append(str(result if result is not None else -1))
    Path(sys.argv[2]).write_text("".join(answer + "\n" for answer in answers))


if __name__ == "__main__":
    main()
