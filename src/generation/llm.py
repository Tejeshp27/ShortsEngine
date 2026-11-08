# =============================================================
# src/generation/llm.py — the ONE place that talks to the AI model
#
# Every AI call in ShortsEngine goes through the LLMClient class
# below. Keeping it in one file means we can:
#   - swap the AI provider later by changing only this file
#   - count every call and stop a run that makes too many
#   - cache answers on disk so we never pay twice for the same thing
#   - estimate the dollar cost of every call and log it
#   - support --dry-run, which makes NO paid calls at all
#
# Prices come from config/pricing.yaml. Model names come from the
# channel config (channels/<name>.yaml), never from this code.
# =============================================================

import csv
import hashlib
import json
import os
from datetime import datetime

import yaml

# Where saved AI answers are kept so the same question is never paid for twice.
LLM_CACHE_FOLDER = os.path.join("cache", "llm")

# One row per run is added to this file so you can see what you're spending.
COST_LOG_PATH = os.path.join("output", "cost_log.csv")

# The price list for each model (dollars per 1 million tokens).
PRICING_PATH = os.path.join("config", "pricing.yaml")

# A rough rule of thumb: one token is about 4 characters of English.
# Only used to ESTIMATE cost in --dry-run, when no real call is made.
CHARS_PER_TOKEN = 4


class CallLimitReached(Exception):
    """Raised when a run tries to make more AI calls than the channel allows."""


class LLMError(Exception):
    """Raised when the AI call fails in a way we can explain to the user."""


def load_pricing():
    """
    Read the price list from config/pricing.yaml.

    Returns:
        dict: Model name -> {"input_per_million": float, "output_per_million": float}.
              Returns an empty dict if the file is missing.
    """
    if not os.path.exists(PRICING_PATH):
        print(f"  WARNING: {PRICING_PATH} not found. Costs will show as $0.")
        return {}
    with open(PRICING_PATH, "r") as f:
        return yaml.safe_load(f) or {}


def calculate_cost(pricing, model, input_tokens, output_tokens,
                   cache_write_tokens=0, cache_read_tokens=0):
    """
    Work out how many dollars an AI call cost.

    Args:
        pricing (dict): The price list from load_pricing().
        model (str): The model name, e.g. "claude-haiku-4-5".
        input_tokens (int): Normal tokens we sent.
        output_tokens (int): Tokens the model wrote back.
        cache_write_tokens (int): Tokens saved into the prompt cache
                                  (charged at 1.25x the input price).
        cache_read_tokens (int): Tokens read from the prompt cache
                                 (charged at only 0.1x the input price).

    Returns:
        float: The cost in US dollars.

    Example:
        prices = {"m": {"input_per_million": 1.0, "output_per_million": 5.0}}
        calculate_cost(prices, "m", 1_000_000, 0)   # -> 1.0
    """
    if model not in pricing:
        print(f"  WARNING: No price for '{model}' in {PRICING_PATH}. Logging $0.")
        return 0.0

    input_price = pricing[model]["input_per_million"] / 1_000_000
    output_price = pricing[model]["output_per_million"] / 1_000_000

    cost = 0.0
    cost += input_tokens * input_price
    cost += output_tokens * output_price
    cost += cache_write_tokens * input_price * 1.25
    cost += cache_read_tokens * input_price * 0.10
    return cost


def make_cache_key(model, system_prompt, user_message, max_tokens, effort):
    """
    Build a short fingerprint that identifies one exact AI request.

    If we ever send the exact same request again, it gets the same
    fingerprint, so we can reuse the saved answer instead of paying.

    Args:
        model, system_prompt, user_message, max_tokens, effort:
            The settings of the request (see LLMClient.ask).

    Returns:
        str: A 32-character fingerprint, e.g. "9b1f0c...".
    """
    # sort_keys=True makes sure the same request always turns into
    # exactly the same text, and so the same fingerprint.
    request_as_text = json.dumps({
        "model": model,
        "system": system_prompt,
        "user": user_message,
        "max_tokens": max_tokens,
        "effort": effort,
    }, sort_keys=True)
    return hashlib.sha256(request_as_text.encode("utf-8")).hexdigest()[:32]


def create_anthropic_client():
    """
    Create the official Anthropic client, with a clear error if the key is missing.

    Returns:
        anthropic.Anthropic: A ready-to-use client.

    Raises:
        LLMError: If ANTHROPIC_API_KEY is not set.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LLMError("Missing ANTHROPIC_API_KEY in .env "
                       "(or run with --dry-run to skip paid calls).")

    # Imported here so --dry-run and the tests work even if the
    # anthropic package has a problem.
    import anthropic
    return anthropic.Anthropic()


class LLMClient:
    """
    Sends questions to the AI model and keeps track of calls and costs for one run.

    Create one LLMClient per run. It remembers how many calls were
    made and what they cost, so the run can be logged at the end.

    Example:
        llm = LLMClient(dry_run=True, max_calls=5)
        answer = llm.ask(model="claude-haiku-4-5",
                         system_prompt="You pick news stories.",
                         user_message="1. Headline A\\n2. Headline B",
                         max_tokens=500, purpose="ranking")
        print(llm.total_cost)
    """

    def __init__(self, dry_run=False, max_calls=5, use_cache=True, api_client=None):
        """
        Set up a new client for one run.

        Args:
            dry_run (bool): If True, never call the real API; return a placeholder.
            max_calls (int): The most paid AI calls this run may make.
            use_cache (bool): If True, reuse saved answers from cache/llm/.
            api_client: Optional ready-made API client (tests pass a fake one).
        """
        self.dry_run = dry_run
        self.max_calls = max_calls
        self.use_cache = use_cache
        self.api_client = api_client
        self.pricing = load_pricing()

        # Running totals for this run, written to the cost log at the end.
        self.calls_made = 0
        self.cache_hits = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0

    def ask(self, model, system_prompt, user_message, max_tokens, purpose, effort=None):
        """
        Send one question to the AI model and return its text answer.

        Args:
            model (str): Which model to use (from the channel config).
            system_prompt (str): Standing instructions (the model's "job description").
            user_message (str): The actual question or material for this call.
            max_tokens (int): The most tokens the model may write (a cost cap).
            purpose (str): A short label for logs, e.g. "ranking" or "writing".
            effort (str): Optional "low", "medium" or "high" — how much the
                          model thinks. Lower is cheaper.

        Returns:
            str: The model's answer as plain text.

        Raises:
            CallLimitReached: If this run already used up its allowed calls.
            LLMError: If the API call fails or the model refuses.
        """
        cache_key = make_cache_key(model, system_prompt, user_message, max_tokens, effort)
        cache_file = os.path.join(LLM_CACHE_FOLDER, f"{cache_key}.json")

        # 1. Free: reuse a saved answer if we've asked this exact question before.
        if self.use_cache and os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.cache_hits += 1
            print(f"  [{purpose}] Using cached answer — $0.00")
            return saved["text"]

        # 2. Spending guard: refuse to go over the per-run limit.
        if self.calls_made >= self.max_calls:
            raise CallLimitReached(
                f"This run already made {self.calls_made} AI calls "
                f"(limit is {self.max_calls}, set in the channel config)."
            )
        self.calls_made += 1

        # 3. Dry run: estimate the cost but don't call anything.
        if self.dry_run:
            return self._fake_answer(model, system_prompt, user_message, max_tokens, purpose)

        # 4. Real call.
        text, usage = self._call_api(model, system_prompt, user_message, max_tokens, effort)
        self._record_usage(model, usage, purpose)

        # Save the answer so a re-run is free.
        os.makedirs(LLM_CACHE_FOLDER, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"model": model, "purpose": purpose, "text": text}, f)

        return text

    def _fake_answer(self, model, system_prompt, user_message, max_tokens, purpose):
        """
        Pretend to answer during --dry-run, and estimate what the call would cost.

        The estimate assumes the worst case: the model writes all
        max_tokens. So real runs should cost less than this.

        Returns:
            str: A placeholder answer that clearly says it is fake.
        """
        estimated_input = (len(system_prompt) + len(user_message)) // CHARS_PER_TOKEN
        estimated_cost = calculate_cost(self.pricing, model, estimated_input, max_tokens)

        self.total_input_tokens += estimated_input
        self.total_output_tokens += max_tokens
        self.total_cost += estimated_cost

        print(f"  [{purpose}] DRY RUN — would call {model} "
              f"(~{estimated_input} tokens in, up to {max_tokens} out, "
              f"at most ${estimated_cost:.4f})")
        return f"[DRY RUN placeholder for {purpose} — no API call was made]"

    def _call_api(self, model, system_prompt, user_message, max_tokens, effort):
        """
        Make the real call to Anthropic's Messages API.

        Returns:
            tuple: (answer text, usage object with token counts)

        Raises:
            LLMError: With a plain-English message if anything goes wrong.
        """
        import anthropic

        if self.api_client is None:
            self.api_client = create_anthropic_client()

        # The system prompt is marked with cache_control so Anthropic
        # can cache it (prompt caching). If the same long prompt is sent
        # again within ~5 minutes, those tokens cost 90% less. Very short
        # prompts are below the model's minimum size and simply aren't
        # cached — that's harmless.
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            "messages": [{"role": "user", "content": user_message}],
        }
        # Not all models support the effort parameter (e.g. Haiku 4.5 does not).
        # We store this in pricing.yaml so we don't hardcode model names here.
        model_config = self.pricing.get(model, {})
        if effort and model_config.get("supports_effort", False):
            request["output_config"] = {"effort": effort}

        try:
            response = self.api_client.messages.create(**request)
        except anthropic.AuthenticationError:
            raise LLMError("Your ANTHROPIC_API_KEY was rejected. Check the key in .env.")
        except anthropic.NotFoundError:
            raise LLMError(f"Model '{model}' was not found. Check the channel config.")
        except anthropic.RateLimitError:
            raise LLMError("Rate limited by Anthropic. Wait a minute and try again.")
        except anthropic.APIStatusError as error:
            raise LLMError(f"Anthropic API error {error.status_code}: {error.message}")
        except anthropic.APIConnectionError:
            raise LLMError("Could not reach the Anthropic API. Check your internet connection.")

        if response.stop_reason == "refusal":
            raise LLMError("The model declined this request. Try a different story.")
        if response.stop_reason == "max_tokens":
            print("  WARNING: The answer hit max_tokens and may be cut off. "
                  "Consider raising the limit in the channel config.")

        # The answer is a list of "blocks" (thinking, text...). We only want the text.
        text_parts = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)

        return "\n".join(text_parts).strip(), response.usage

    def _record_usage(self, model, usage, purpose):
        """
        Add one real call's tokens and cost to this run's running totals.

        Args:
            model (str): The model that was called.
            usage: The 'usage' part of the API response (token counts).
            purpose (str): Label for the printed message.
        """
        # Some fields can be missing (None), so treat missing as 0.
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

        cost = calculate_cost(self.pricing, model, usage.input_tokens, usage.output_tokens,
                              cache_write, cache_read)

        self.total_input_tokens += usage.input_tokens + cache_write + cache_read
        self.total_output_tokens += usage.output_tokens
        self.total_cost += cost

        print(f"  [{purpose}] {model}: {usage.input_tokens} in / "
              f"{usage.output_tokens} out — ${cost:.4f}")

    def write_cost_log(self, channel_name, note=""):
        """
        Add one row describing this run to output/cost_log.csv.

        Args:
            channel_name (str): Which channel this run was for.
            note (str): Anything worth remembering, e.g. the story title.
        """
        os.makedirs(os.path.dirname(COST_LOG_PATH), exist_ok=True)
        file_is_new = not os.path.exists(COST_LOG_PATH)

        with open(COST_LOG_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if file_is_new:
                writer.writerow(["timestamp", "channel", "dry_run", "api_calls",
                                 "cache_hits", "input_tokens", "output_tokens",
                                 "estimated_cost_usd", "note"])
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                channel_name,
                self.dry_run,
                self.calls_made,
                self.cache_hits,
                self.total_input_tokens,
                self.total_output_tokens,
                f"{self.total_cost:.6f}",
                note,
            ])
