from dataclasses import dataclass


@dataclass
class TokenStatus:
    input_tokens: int = 0
    output_tokens: int = 0
    saved_tokens_est: int = 0
    saved_chars: int = 0
    saving_ops: int = 0
    provider_name: str = ""
    model: str = ""
    reasoning: str = "high"
