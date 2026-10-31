"""Execute one untrusted WASI module with bounded memory and instruction fuel."""

import wasmtime


def run_module(command, project_dir, input_dir, output_dir, memory_bytes, fuel):
    config = wasmtime.Config()
    config.consume_fuel = True
    config.wasm_threads = False
    config.shared_memory = False
    config.wasm_memory64 = False
    config.wasm_multi_memory = False
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, str(project_dir / command[0]))
    store = wasmtime.Store(engine)
    store.set_limits(memory_size=memory_bytes, memories=1, instances=1)
    store.set_fuel(fuel)
    wasi = wasmtime.WasiConfig()
    wasi.argv = [command[0], *('/inputs' if arg == '{input_dir}' else '/output/result.bin'
                               if arg == '{output_path}' else arg for arg in command[1:])]
    wasi.inherit_stdout()
    wasi.inherit_stderr()
    wasi.preopen_dir(str(input_dir.resolve(strict=True)), '/inputs', fs_mutable=False)
    wasi.preopen_dir(str(output_dir.resolve(strict=True)), '/output', fs_mutable=True)
    store.set_wasi(wasi)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    instance = linker.instantiate(store, module)
    instance.exports(store)['_start'](store)
