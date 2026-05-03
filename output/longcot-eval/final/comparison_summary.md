# LongCoT Pilot Benchmark Comparison Report

**Generated:** 2026-05-01T02:36:06.947837+00:00

## Model Information

- **Model:** DeepSeek V4 Flash
- **Provider:** OpenRouter
- **Benchmark:** LongCoT (longcot-mini)
- **Tasks:** 100

## Overall Results

| Metric | Direct Mode | RLM Mode | Delta (RLM − Direct) |
|--------|-------------|----------|----------------------|
| Accuracy | 18.84% | 33.00% | **+14.16%** |
| Overall Accuracy | 13.00% | 33.00% | **+20.00%** |
| Correct | 13 | 33 | +20 |
| Incorrect | 56 | 67 | +11 |
| Failed | 31 | 0 | -31 |

## Per-Domain Breakdown

| Domain | Direct Correct | Direct Incorrect | Direct Failed | RLM Correct | RLM Incorrect | RLM Failed |
|--------|---------------:|-----------------:|--------------:|------------:|--------------:|-----------:|
| chemistry | 6 | 13 | 1 | 0 | 20 | 0 |
| chess | 2 | 15 | 3 | 8 | 12 | 0 |
| cs | 1 | 10 | 9 | 7 | 13 | 0 |
| logic | 2 | 13 | 5 | 16 | 4 | 0 |
| math | 2 | 5 | 13 | 2 | 18 | 0 |

## RLM Transport Success

- **Transport success rate:** 100.00%
- **Successful responses:** 55 / 55

## Execution Timestamps

- **Direct eval file:** `longcot-eval-longcot_or_deepseek_v4_flash_all_longcot-mini_MERGED_100.json`
- **RLM eval file:** `longcot-eval-longcot_rlm_all_longcot-mini_MERGED_100.json`
- **RLM transport summary:** `longcot-rlm-transport-summary.json`

## Source Files

- Direct eval: `/output/longcot-eval/longcot-eval-longcot_or_deepseek_v4_flash_all_longcot-mini_MERGED_100.json`
- RLM eval: `/output/longcot-eval/longcot-eval-longcot_rlm_all_longcot-mini_MERGED_100.json`
