"""Report-only settings shared by serving and frozen evaluation; no quota grant."""
from copy import deepcopy

def quality_report_profile(base):
    result=deepcopy(base)
    result.update(report_generation_profile='quality-v3-direct',report_input_presentation='plain-facts-v2-program-scope',
                  max_prompt_utf8_bytes=20000,max_output_tokens=4096,cloud_total_token_budget=16384,report_thinking=False)
    result.pop('reasoning_effort',None)
    return result
