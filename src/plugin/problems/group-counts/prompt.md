# Group counts

Build an executable in any language. It receives `input_dir` and `output_path` as command-line arguments. Read whitespace-separated integers from `data.txt` inside `input_dir`. Write the result as UTF-8 text in `output_path` using Python `repr` of a list of `(integer, count)` tuples. Include each distinct integer once and order tuples by ascending integer. All input integers fit in signed 64-bit integers. The input directory is read-only; write only the requested output file. Optimize for large input while preserving the exact output bytes.
