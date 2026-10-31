#include <string>
#include <fstream>
#include <functional>
#include <limits>
#include <queue>
#include <utility>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 3) return 1;
    std::ifstream input(std::string(argv[1]) + "/graph.txt");
    std::ofstream output(argv[2], std::ios::binary);
    int n, m, q;
    if (!input || !output || !(input >> n >> m >> q)) return 1;
    using Edge = std::pair<int, long long>;
    std::vector<std::vector<Edge>> graph(n);
    for (int i = 0; i < m; ++i) {
        int u, v;
        long long weight;
        if (!(input >> u >> v >> weight)) return 1;
        graph[u].emplace_back(v, weight);
        graph[v].emplace_back(u, weight);
    }
    using Distance = long long;
    using State = std::pair<Distance, int>;
    const Distance infinity = std::numeric_limits<Distance>::max();
    for (int i = 0; i < q; ++i) {
        int source, target;
        if (!(input >> source >> target)) return 1;
        std::vector<Distance> distance(n);
        std::vector<bool> reached(n, false);
        std::priority_queue<State, std::vector<State>, std::greater<State>> queue;
        distance[source] = 0;
        reached[source] = true;
        queue.emplace(0, source);
        while (!queue.empty()) {
            auto [cost, vertex] = queue.top();
            queue.pop();
            if (cost != distance[vertex]) continue;
            if (vertex == target) break;
            for (auto [neighbor, weight] : graph[vertex]) {
                if (weight > infinity - cost) continue;
                Distance candidate = cost + weight;
                if (!reached[neighbor] || candidate < distance[neighbor]) {
                    reached[neighbor] = true;
                    distance[neighbor] = candidate;
                    queue.emplace(candidate, neighbor);
                }
            }
        }
        output << (reached[target] ? distance[target] : -1) << '\n';
    }
    return output ? 0 : 1;
}
