from pathlib import Path


def solve(input_dir, output_path):
    values = [int(value) for value in (Path(input_dir) / "data.txt").read_text().split()]
    counts = [(value, values.count(value)) for value in sorted(set(values))]
    Path(output_path).write_text(repr(counts), encoding="utf-8")
