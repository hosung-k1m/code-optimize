#include <algorithm>
#include <string>
#include <fstream>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 3) return 1;
    std::ifstream input(std::string(argv[1]) + "/data.txt");
    std::ofstream output(argv[2], std::ios::binary);
    if (!input || !output) return 1;
    std::vector<long long> values;
    long long value;
    while (input >> value) values.push_back(value);
    auto unique = values;
    std::sort(unique.begin(), unique.end());
    unique.erase(std::unique(unique.begin(), unique.end()), unique.end());
    output << '[';
    for (size_t i = 0; i < unique.size(); ++i) {
        if (i) output << ", ";
        output << '(' << unique[i] << ", " << std::count(values.begin(), values.end(), unique[i]) << ')';
    }
    output << ']';
    return output ? 0 : 1;
}
