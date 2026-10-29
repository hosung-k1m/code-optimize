"""Exact deterministic scoring arithmetic shared with the evaluator."""

from fractions import Fraction
from math import exp, log, prod
from statistics import median

from contract import CaseSummary, Summary


def _fraction(value):
    return Fraction(str(value))


def _relative_mad(values):
    center = median(values)
    if center <= 0:
        return Fraction(0)
    return median([abs(value - center) for value in values]) / center


def summarize(config, trials):
    """Derive case timings and eligibility from the raw ordered trial evidence."""
    settings = config.measurement
    required = settings.warmup_pairs + settings.measured_pairs
    cases = []
    ratios = []
    output_ok = True
    stable = True
    complete = True
    for case in config.cases:
        base = {}
        candidate = {}
        outputs_match = True
        for trial in trials:
            if trial.case_id != case.id:
                continue
            target = base if trial.implementation == "baseline" else candidate
            target[trial.pair] = trial
        pairs = range(required)
        valid = len(base) == required and len(candidate) == required
        valid = valid and all(pair in base and pair in candidate for pair in pairs)
        if not valid:
            complete = False
            speedup = Fraction(0)
            bm = cm = Fraction(0)
        else:
            outputs_match = all(
                base[p].result is not None
                and candidate[p].result is not None
                and base[p].result.size == candidate[p].result.size
                and base[p].result.sha256 == candidate[p].result.sha256
                for p in range(required)
            )
            measured = range(settings.warmup_pairs, required)
            btimes = [base[p].elapsed_ns for p in measured]
            ctimes = [candidate[p].elapsed_ns for p in measured]
            if any(t <= 0 for t in btimes + ctimes):
                complete = False
                speedup = Fraction(0)
                bm = cm = Fraction(0)
            else:
                speedup = median([Fraction(base[p].elapsed_ns, candidate[p].elapsed_ns) for p in measured])
                bm = _relative_mad([Fraction(t) for t in btimes])
                cm = _relative_mad([Fraction(t) for t in ctimes])
                ratios.append(speedup)
                limit = _fraction(config.policy.maximum_relative_mad)
                stable = stable and bm <= limit and cm <= limit
        output_ok = output_ok and outputs_match
        cases.append(
            CaseSummary(
                case_id=case.id,
                speedup=float(speedup),
                baseline_relative_mad=float(bm),
                candidate_relative_mad=float(cm),
                outputs_match=outputs_match,
            )
        )
    if ratios and len(ratios) == len(config.cases):
        # Compare the geometric mean threshold without floating point rounding.
        suite_product = prod(ratios)
        suite_float = exp(sum(log(float(ratio)) for ratio in ratios) / len(ratios))
    else:
        suite_product = Fraction(0)
        suite_float = 0.0
    statuses_ok = all(
        t.status == "ok" and t.exit_code == 0 and t.inputs_unchanged and t.cleanup_confirmed for t in trials
    )
    if not complete:
        reason = "incomplete_trials"
    elif not statuses_ok:
        reason = "execution_failure"
    elif not output_ok:
        reason = "wrong_output"
    elif not stable:
        reason = "unstable_timing"
    elif suite_product < _fraction(config.policy.minimum_speedup) ** len(config.cases):
        reason = "insufficient_speedup"
    elif any(ratio < 1 / _fraction(config.policy.maximum_case_slowdown) for ratio in ratios):
        reason = "case_regression"
    else:
        reason = "pass"
    eligible = reason == "pass"
    return Summary(reason=reason, eligible=eligible, suite_speedup=suite_float, cases=cases)
