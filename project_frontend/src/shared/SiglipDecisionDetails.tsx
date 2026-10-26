import type { SiglipDecisionTrace } from "../admin/adminApi";

const gateLabel = (value: boolean | "not_applied" | "not_evaluated") =>
  value === true ? "passed" : value === false ? "failed" : value;
const numberLabel = (value: number) => Number.isFinite(value) ? value.toFixed(4) : "unavailable";

export default function SiglipDecisionDetails({ decision, verificationPassed }: {
  decision?: SiglipDecisionTrace | null;
  verificationPassed: boolean;
}) {
  if (!decision) return null;
  return (
    <section className="basis-full rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-700">
      <p className="font-bold">{decision.scoring_version}</p>
      <p>Raw Positive Ranking: 1. {decision.raw_top1_category} ({numberLabel(decision.raw_top1_logit)})
        {" · "}2. {decision.raw_top2_category} ({numberLabel(decision.raw_top2_logit)})</p>
      <p>Final Prediction: <strong>{decision.final_predicted_category}</strong></p>
      <p>Decision Reason: {decision.decision_reason}</p>
      <p>Decision Rule: {decision.decision_rule}</p>
      <p>Verification Result: {verificationPassed ? "PASS" : "FAIL"}</p>
      <p>Best Negative: {decision.negative_top1_category} ({numberLabel(decision.negative_top1_logit)})</p>
      <p>Positive–Negative margin (raw logits): {numberLabel(decision.positive_negative_margin)}</p>
      <p>Positive Top-1/Top-2 margin (raw logits): {numberLabel(decision.positive_top1_top2_margin)}</p>
      <p>Positive relative share (not probability): {numberLabel(decision.positive_relative_score)}</p>
      <p>Positive relative margin: {numberLabel(decision.positive_relative_margin)}</p>
      <p>Negative gate: {gateLabel(decision.negative_gate_passed)}
        {" · "}Confidence gate: {gateLabel(decision.confidence_gate_passed)}
        {" · "}Margin gate: {gateLabel(decision.margin_gate_passed)}</p>
      <p>Optional relative-score thresholds: confidence {decision.decision_config.confidence_min_relative_score ?? "disabled"}
        {" · "}margin {decision.decision_config.margin_min_relative_score ?? "disabled"}</p>
    </section>
  );
}
