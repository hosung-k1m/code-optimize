# Sorted intersection

Build an executable in any language. It receives `input_dir` and `output_path` as command-line arguments. Read two whitespace-separated integer sequences from `left.txt` and `right.txt` in `input_dir`. Write the intersection as UTF-8 text using Python `repr` of a list of integers in ascending order. Treat the sequences as sets: duplicate values appear once in the result. All input integers fit in signed 64-bit integers. The input directory is read-only; write only the requested output file. Optimize for large inputs while preserving exact output bytes.
