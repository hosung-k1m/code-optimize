#include <algorithm>
#include <string>
#include <fstream>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 3) return 1;
    std::ifstream left(std::string(argv[1]) + "/left.txt");
    std::ifstream right(std::string(argv[1]) + "/right.txt");
    std::ofstream output(argv[2], std::ios::binary);
    if (!left || !right || !output) return 1;
    std::vector<long long> left_values, right_values, result;
    long long value;
    while (left >> value) left_values.push_back(value);
    while (right >> value) right_values.push_back(value);
    for (auto item : left_values)
        if (std::find(right_values.begin(), right_values.end(), item) != right_values.end()) result.push_back(item);
    std::sort(result.begin(), result.end());
    result.erase(std::unique(result.begin(), result.end()), result.end());
    output << '[';
    for (size_t i = 0; i < result.size(); ++i) {
        if (i) output << ", ";
        output << result[i];
    }
    output << ']';
    return output ? 0 : 1;
}
