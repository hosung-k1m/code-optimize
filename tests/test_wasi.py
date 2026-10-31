import pytest
import wasmtime

from plugin.wasi import run_module


def run(tmp_path, text, *, fuel=10000, memory=65536):
    (tmp_path / 'main.wat').write_text(text)
    (tmp_path / 'inputs').mkdir(exist_ok=True)
    (tmp_path / 'output').mkdir(exist_ok=True)
    return run_module(['./main.wat', '{input_dir}', '{output_path}'], tmp_path,
                      tmp_path / 'inputs', tmp_path / 'output', memory, fuel)


def test_fuel_stops_infinite_loop(tmp_path):
    with pytest.raises(wasmtime.Trap, match='fuel'):
        run(tmp_path, '(module (func (export "_start") (loop br 0)))')


def test_memory_limit_rejects_initial_memory(tmp_path):
    with pytest.raises(wasmtime.WasmtimeError):
        run(tmp_path, '(module (memory 2) (func (export "_start")))')


def test_shared_memory_is_rejected(tmp_path):
    with pytest.raises(wasmtime.WasmtimeError):
        run(tmp_path, '(module (memory 1 2 shared) (func (export "_start")))')


def test_memory_growth_is_limited(tmp_path):
    run(tmp_path, '''(module (memory 1 2) (func (export "_start")
        i32.const 1 memory.grow i32.const -1 i32.ne if unreachable end))''')

@pytest.mark.parametrize('name,rights', [('data.txt', 64), ('../outside.txt', 2)])
def test_input_write_and_directory_escape_are_denied(tmp_path, name, rights):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'data.txt').write_text('original')
    (tmp_path / 'outside.txt').write_text('outside capability')
    run(tmp_path, f'''(module
      (import "wasi_snapshot_preview1" "path_open"
        (func $open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
      (memory (export "memory") 1)
      (data (i32.const 0) "{name}")
      (func (export "_start")
        i32.const 3 i32.const 0 i32.const 0 i32.const {len(name)} i32.const 0
        i64.const {rights} i64.const 0 i32.const 0 i32.const 64
        call $open i32.eqz if unreachable end))''', fuel=10000000)
    assert (inputs / 'data.txt').read_text() == 'original'
