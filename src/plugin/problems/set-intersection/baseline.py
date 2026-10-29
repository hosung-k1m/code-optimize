from pathlib import Path


def solve(input_dir, output_path):
    directory = Path(input_dir)
    left = [int(value) for value in (directory / "left.txt").read_text().split()]
    right = [int(value) for value in (directory / "right.txt").read_text().split()]
    result = sorted({value for value in left if value in right})
    Path(output_path).write_text(repr(result), encoding="utf-8")
