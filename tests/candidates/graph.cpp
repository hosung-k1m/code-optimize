#include <fstream>
#include <functional>
#include <limits>
#include <queue>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 3) return 1;
    std::ifstream input(std::string(argv[1]) + "/graph.txt");
    std::ofstream output(argv[2], std::ios::binary);
    int n, m, q;
    if (!input || !output || !(input >> n >> m >> q)) return 1;
    using Distance = long long;
    using State = std::pair<Distance, int>;
    std::vector<std::vector<std::pair<int, Distance>>> graph(n);
    for (int i = 0; i < m; ++i) {
        int u, v;
        Distance weight;
        if (!(input >> u >> v >> weight)) return 1;
        graph[u].emplace_back(v, weight);
        graph[v].emplace_back(u, weight);
    }
    std::unordered_map<int, std::vector<Distance>> distances;
    std::unordered_map<int, std::vector<bool>> reached;
    const auto maximum = std::numeric_limits<Distance>::max();
    for (int i = 0; i < q; ++i) {
        int source, target;
        if (!(input >> source >> target)) return 1;
        if (!distances.count(source)) {
            auto& distance = distances[source];
            auto& found = reached[source];
            distance.assign(n, 0);
            found.assign(n, false);
            found[source] = true;
            std::priority_queue<State, std::vector<State>, std::greater<State>> pending;
            pending.emplace(0, source);
            while (!pending.empty()) {
                auto [cost, vertex] = pending.top();
                pending.pop();
                if (cost != distance[vertex]) continue;
                for (auto [neighbor, weight] : graph[vertex]) {
                    if (weight > maximum - cost) continue;
                    Distance next = cost + weight;
                    if (!found[neighbor] || next < distance[neighbor]) {
                        found[neighbor] = true;
                        distance[neighbor] = next;
                        pending.emplace(next, neighbor);
                    }
                }
            }
        }
        output << (reached[source][target] ? distances[source][target] : -1) << '\n';
    }
    return output ? 0 : 1;
}
