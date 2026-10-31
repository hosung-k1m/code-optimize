import { h, markdown } from './kit/components.js';

const number = value => value.toFixed(3);
const reasons = {
  pass: 'Assessment passed',
  wrong_output: 'Output differs from the baseline',
  execution_failure: 'Candidate execution failed',
  unstable_timing: 'Timing samples were unstable',
  insufficient_speedup: 'Speedup below target',
  case_regression: 'A case exceeded the slowdown limit',
  incomplete_trials: 'Assessment incomplete',
};

const artifact = value => h('p', {text: `${value.path} · ${value.size} bytes · SHA-256 ${value.sha256}`});

function list(value) {
  return h('section', {}, [
    h('h2', {text: 'Available optimization problems'}),
    ...value.problems.map(item => h('article', {}, [
      h('h3', {text: item.reference.directory}),
      h('p', {text: item.description}),
      h('pre', {text: JSON.stringify(item.reference, null, 2)}),
    ])),
  ]);
}

function settings(config) {
  return h('details', {}, [
    h('summary', {text: 'Current benchmark configuration'}),
    h('p', {text: `${config.measurement.warmup_pairs} warmup + ${config.measurement.measured_pairs} measured pairs per case; alternating order. ${config.execution.per_trial_timeout_seconds}s per trial; ${config.execution.total_timeout_seconds}s total including preparation.`}),
    h('p', {text: `Exact output bytes; suite speedup ≥ ${config.policy.minimum_speedup}×; case slowdown ≤ ${config.policy.maximum_case_slowdown}×; relative MAD ≤ ${config.policy.maximum_relative_mad}.`}),
    h('pre', {text: JSON.stringify(config, null, 2)}),
  ]);
}

function describe(value) {
  const config = value.problem_config;
  return h('section', {}, [
    h('h2', {text: config.id}),
    markdown(value.prompt),
    settings(config),
    h('p', {text: value.case_summaries.join(' · ')}),
    h('p', {text: value.runtime.tools.join(' · ')}),
    h('details', {}, [h('summary', {text: 'Naive C++17 baseline'}), h('pre', {text: value.baseline_source})]),
  ]);
}

function medianTime(trials, caseId, implementation) {
  const samples = trials.filter(item => item.case_id === caseId && item.implementation === implementation && !item.warmup)
    .map(item => item.elapsed_ns).sort((a, b) => a - b);
  const middle = Math.floor(samples.length / 2);
  const nanoseconds = samples.length % 2 ? samples[middle] : (samples[middle - 1] + samples[middle]) / 2;
  return `${number(nanoseconds / 1e6)} ms`;
}

function evaluate(value, {verdict}) {
  const summary = value.summary;
  if (!Object.hasOwn(reasons, summary.reason)) throw new Error('Unknown assessment reason');
  const rows = summary.cases.map(item => h('tr', {}, [
    h('td', {text: item.case_id}),
    h('td', {text: item.outputs_match ? 'Match' : 'Failed'}),
    h('td', {text: medianTime(value.trials, item.case_id, 'baseline')}),
    h('td', {text: medianTime(value.trials, item.case_id, 'candidate')}),
    h('td', {text: `${number(item.speedup)}×`}),
    h('td', {text: number(item.baseline_relative_mad)}),
    h('td', {text: number(item.candidate_relative_mad)}),
  ]));
  return h('section', {}, [
    h('h2', {text: reasons[summary.reason]}),
    h('p', {text: `Suite speedup ${number(summary.suite_speedup)}× · ${value.trials.length} guarded executions`}),
    h('div', {class: 'measurements', role: 'region', 'aria-label': 'Case measurements', tabindex: '0'}, [h('table', {}, [
      h('thead', {}, [h('tr', {}, ['Case', 'Output', 'Baseline median', 'Candidate median', 'Speedup', 'Baseline MAD', 'Candidate MAD'].map(text => h('th', {text})))]),
      h('tbody', {}, rows),
    ])]),
    h('p', {text: `Wasmtime ${value.runtime.wasmtime_version} · Zig ${value.runtime.zig_version} · one WASI execution thread · ${value.runtime.wasi_memory_bytes} bytes linear memory · ${value.runtime.wasi_fuel} fuel units per trial`}),
    h('p', {text: `Timing host: ${value.runtime.platform} ${value.runtime.architecture}. Runtime overhead is outside the guest memory cap.`}),
    h('p', {text: `Report: ${value.report_path}`}),
    artifact(value.candidate),
    h('details', {}, [h('summary', {text: 'Build and dependency evidence'}), h('pre', {text: JSON.stringify(value.preparation, null, 2)})]),
    settings(value.problem_config),
    h('details', {}, [h('summary', {text: 'Runtime and available tools'}), h('pre', {text: JSON.stringify(value.runtime, null, 2)})]),
    h('details', {}, [h('summary', {text: 'Trial evidence'}), h('pre', {text: JSON.stringify(value.trials, null, 2)})]),
    ...(verdict ? [h('p', {text: verdict.message})] : []),
  ]);
}

export default {ListProblemsOutput: list, DescribeOutput: describe, EvaluateOutput: evaluate};
