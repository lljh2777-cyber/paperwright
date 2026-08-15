from __future__ import annotations

import json
import unittest

from paperwright.llm_cost import (
    COST_CONTRACT_VERSION,
    CostReport,
    canonical_cost_report_json,
    estimate_cost_usd,
    record_call,
    usage_tokens,
)


class LlmCostTests(unittest.TestCase):
    def test_usage_tokens_accepts_mapping_and_openai_object(self):
        self.assertEqual(
            usage_tokens(
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                }
            ),
            (10, 20, 3),
        )

        class Usage:
            prompt_tokens = 5
            completion_tokens = 7

            class completion_tokens_details:
                reasoning_tokens = 2

        self.assertEqual(usage_tokens(Usage()), (5, 7, 2))
        self.assertEqual(usage_tokens(None), (0, 0, 0))

    def test_estimate_cost_uses_configured_pricing(self):
        self.assertEqual(
            estimate_cost_usd("qwen3.7-plus", input_tokens=1_000_000, output_tokens=1_000_000),
            10.0,
        )
        self.assertIsNone(estimate_cost_usd("unknown-model", input_tokens=1, output_tokens=1))

    def test_record_call_and_report_are_canonical(self):
        report = CostReport()
        report.record(
            bridge="fixture-bridge",
            model="qwen3.7-plus",
            step="batch-1",
            usage={
                "prompt_tokens": 1_000,
                "completion_tokens": 500,
                "completion_tokens_details": {"reasoning_tokens": 100},
            },
        )
        report.record(
            bridge="fixture-bridge",
            model="unknown-model",
            step="batch-2",
            usage=None,
        )
        totals = report.totals()
        self.assertEqual(totals["call_count"], 2)
        self.assertEqual(totals["input_tokens"], 1_000)
        self.assertEqual(totals["output_tokens"], 500)
        self.assertEqual(totals["reasoning_tokens"], 100)
        self.assertEqual(totals["unknown_pricing_call_count"], 1)

        canonical = canonical_cost_report_json(report)
        self.assertEqual(
            canonical,
            canonical_cost_report_json(
                CostReport(
                    records=[
                        record_call(
                            bridge="fixture-bridge",
                            model="qwen3.7-plus",
                            step="batch-1",
                            usage={
                                "prompt_tokens": 1_000,
                                "completion_tokens": 500,
                                "completion_tokens_details": {"reasoning_tokens": 100},
                            },
                        ),
                        record_call(
                            bridge="fixture-bridge",
                            model="unknown-model",
                            step="batch-2",
                            usage=None,
                        ),
                    ]
                )
            ),
        )
        self.assertEqual(
            json.loads(canonical)["contract_version"], COST_CONTRACT_VERSION
        )


if __name__ == "__main__":
    unittest.main()
