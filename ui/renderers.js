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

function describe(value) {
  const config = value.problem_config;
  return h('section', {}, [
    h('h2', {text: config.id}),
    markdown(value.prompt),
    h('p', {text: `Required suite speedup ${config.policy.minimum_speedup}×; maximum case slowdown ${config.policy.maximum_case_slowdown}×; relative MAD ≤ ${config.policy.maximum_relative_mad}.`}),
    h('p', {text: value.case_summaries.join(' · ')}),
    h('details', {}, [h('summary', {text: 'Baseline source'}), h('pre', {text: value.baseline_source})]),
  ]);
}

function evaluate(value, {verdict}) {
  const summary = value.summary;
  if (!Object.hasOwn(reasons, summary.reason)) throw new Error('Unknown assessment reason');
  const rows = summary.cases.map(item => h('tr', {}, [
    h('td', {text: item.case_id}),
    h('td', {text: item.outputs_match ? 'Match' : 'Failed'}),
    h('td', {text: `${number(item.speedup)}×`}),
    h('td', {text: number(item.baseline_relative_mad)}),
    h('td', {text: number(item.candidate_relative_mad)}),
  ]));
  return h('section', {}, [
    h('h2', {text: reasons[summary.reason]}),
    h('p', {text: `Suite speedup ${number(summary.suite_speedup)}× · ${value.trials.length} guarded executions`}),
    h('div', {class: 'measurements', role: 'region', 'aria-label': 'Case measurements', tabindex: '0'}, [h('table', {}, [
      h('thead', {}, [h('tr', {}, ['Case', 'Output', 'Speedup', 'Baseline MAD', 'Candidate MAD'].map(text => h('th', {text})))]),
      h('tbody', {}, rows),
    ])]),
    h('p', {text: `${value.runtime.python_implementation} ${value.runtime.python_version} · ${value.runtime.platform} ${value.runtime.architecture}`}),
    h('p', {text: `Report: ${value.report_path}`}),
    artifact(value.candidate),
    h('details', {}, [h('summary', {text: 'Trial evidence'}), h('pre', {text: JSON.stringify(value.trials, null, 2)})]),
    ...(verdict ? [h('p', {text: verdict.message})] : []),
  ]);
}

export default {DescribeOutput: describe, EvaluateOutput: evaluate};
