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
    h('p', {text: 'Ask optimize-code to implement and benchmark a solution, then submit its report for automatic verification.'}),
    markdown(value.prompt),
    settings(config),
    h('p', {text: value.case_summaries.join(' · ')}),
    h('p', {text: value.runtime.tools.join(' · ')}),
    h('details', {}, [h('summary', {text: 'Naive C++17 baseline'}), h('pre', {text: value.baseline_source})]),
  ]);
}

function medianTime(trials, caseId, implementation) {
  const samples = trials.filter(item => item.case_id === caseId && item.implementation === implementation && !item.warmup && item.status === 'ok')
    .map(item => item.elapsed_ns).sort((a, b) => a - b);
  if (!samples.length) return 'No successful measurements';
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
  ]));
  return h('section', {}, [
    h('h2', {text: 'Baseline vs optimized code'}),
    h('p', {class: 'verdict', text: `${summary.eligible ? 'Accepted' : 'Not accepted'} · ${reasons[summary.reason]}`}),
    ...(verdict && !verdict.valid ? [h('p', {text: verdict.message})] : []),
    h('p', {text: `Overall speedup: ${number(summary.suite_speedup)}×`}),
    h('div', {class: 'measurements', role: 'region', 'aria-label': 'Case measurements', tabindex: '0'}, [h('table', {}, [
      h('caption', {text: 'Median elapsed time of successful trials (warmups excluded)'}),
      h('thead', {}, [h('tr', {}, ['Case', 'Output', 'Baseline', 'Optimized', 'Speedup'].map(text => h('th', {text})))]),
      h('tbody', {}, rows),
    ])]),
    h('details', {}, [
      h('summary', {text: 'Technical details'}),
      h('p', {text: `Report: ${value.report_path}`}),
      h('pre', {text: JSON.stringify({candidate: value.candidate, configuration: value.problem_config, runtime: value.runtime, preparation: value.preparation, trials: value.trials, cases: summary.cases}, null, 2)}),
    ]),
  ]);
}

function optimize(value, {verdict}) {
  if (!verdict) throw new Error('Optimization result has no verifier verdict');
  const content = [
    h('h2', {text: 'Baseline vs optimized code'}),
    h('p', {class: 'verdict', text: `${verdict.valid ? 'Accepted' : 'Not accepted'} · ${verdict.message}`}),
  ];
  if (Array.isArray(verdict.details.timings)) {
    const time = ns => ns === null ? 'No successful measurements' : `${number(ns / 1e6)} ms`;
    content.push(
      h('p', {text: `Overall speedup: ${number(verdict.details.speedup)}×`}),
      h('div', {class: 'measurements', role: 'region', 'aria-label': 'Case measurements', tabindex: '0'}, [h('table', {}, [
        h('caption', {text: 'Verified median elapsed times (warmups excluded)'}),
        h('thead', {}, [h('tr', {}, ['Case', 'Output', 'Baseline', 'Optimized', 'Speedup'].map(text => h('th', {text})))]),
        h('tbody', {}, verdict.details.timings.map(item => h('tr', {}, [
          h('td', {text: item.case_id}), h('td', {text: item.outputs_match ? 'Match' : 'Failed'}),
          h('td', {text: time(item.baseline_ns)}), h('td', {text: time(item.candidate_ns)}),
          h('td', {text: `${number(item.speedup)}×`}),
        ]))),
      ])]),
    );
  }
  return h('section', {}, content);
}

export default {ListProblemsOutput: list, DescribeOutput: describe, EvaluateOutput: evaluate, OptimizeOutput: optimize};
