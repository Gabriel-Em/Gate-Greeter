import os
import json
import asyncio
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from google import genai
from google.genai import types, errors
import httpx
import discord
from discord.ext import commands

from utils import (
    ensure_json_file,
    load_json_data,
    save_json_data,
    get_env_int,
    is_env_enabled,
)

# ---------------------------------------------------------
# MODULE-LEVEL CONSTANTS
# ---------------------------------------------------------
OPTOUT_FILE = "llm_optout.json"
COOLDOWN_FILE = "llm_cooldowns.json"
DISCORD_MAX_MESSAGE_LENGTH = 2000
SAFETY_REJECTION_MESSAGE = "Your request was blocked by safety filters."

SAFETY_FINISH_REASONS = {
    types.FinishReason.SAFETY,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.SPII,
    types.FinishReason.IMAGE_SAFETY,
    types.FinishReason.IMAGE_PROHIBITED_CONTENT,
}


class SafetyBlockedError(Exception):
    """Raised when Google's safety filters or the Pass 1 Router blocks a request."""
    pass


class RouterOutput(BaseModel):
    is_safe: bool = Field(
        description=(
            "True if the query is safe to process; False if the query contains TOS violations, "
            "hate speech, harassment, sexually explicit content, or dangerous/illegal requests."
        )
    )
    needs_context: bool = Field(
        description="True if the query refers to or requires recent Discord chat history/conversations to answer; False if it is a standalone general knowledge or conceptual question."
    )
    intent: str = Field(
        description="A brief summary of the user's intent and what they are asking for."
    )
    optimized_question: str = Field(
        description="A clear, well-formulated version of the user's question optimized for the synthesizer LLM."
    )
    target_usernames: list[str] = Field(
        default_factory=list,
        description="List of specific usernames, display names, or user IDs mentioned or targeted in the query (empty list if none or if referring to the general chat).",
    )


class LLM(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Async Locks for File I/O Safety
        self.optout_lock = asyncio.Lock()
        self.cooldown_lock = asyncio.Lock()

        # Load Configurable Constants from Environment
        self.rate_limit_count = get_env_int("LLM_RATE_LIMIT_COUNT", 2)
        self.rate_limit_hours = get_env_int("LLM_RATE_LIMIT_HOURS", 24)
        self.history_fetch_limit = get_env_int("LLM_HISTORY_FETCH_LIMIT", 300)
        self.request_timeout_seconds = get_env_int("LLM_REQUEST_TIMEOUT_SECONDS", 120)

        self.router_model = os.getenv("LLM_ROUTER_MODEL", "gemini-3.5-flash-lite").strip()
        self.synthesizer_model = os.getenv("LLM_SYNTHESIZER_MODEL", "gemini-3.8-flash").strip()

        # Initialize JSON Ledgers
        ensure_json_file(OPTOUT_FILE)
        ensure_json_file(COOLDOWN_FILE)

    # ---------------------------------------------------------
    # INTERNAL HELPERS
    # ---------------------------------------------------------
    def _get_genai_client(self) -> genai.Client:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured in `.env`.")
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=self.request_timeout_seconds * 1000,
                retry_options=types.HttpRetryOptions(attempts=2),
            ),
        )

    @staticmethod
    def _validate_response_safety(response: types.GenerateContentResponse) -> None:
        """Checks a Gemini response for prompt feedback blocks or safety finish reasons."""
        if getattr(response, "prompt_feedback", None) and getattr(
            response.prompt_feedback, "block_reason", None
        ):
            raise SafetyBlockedError(
                f"Prompt blocked: {response.prompt_feedback.block_reason}"
            )

        candidates = getattr(response, "candidates", None)
        if not candidates:
            raise SafetyBlockedError("No candidates returned (blocked by safety filter).")

        for candidate in candidates:
            finish_reason = getattr(candidate, "finish_reason", None)
            if finish_reason in SAFETY_FINISH_REASONS or (
                finish_reason and "SAFETY" in str(finish_reason).upper()
            ):
                raise SafetyBlockedError(f"Candidate blocked: {finish_reason}")

    @staticmethod
    def _format_remaining_time(seconds: float) -> str:
        """Formats remaining cooldown seconds into a human-readable exact string."""
        total_seconds = max(1, int(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)

        parts = []
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        if minutes > 0:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if secs > 0 or not parts:
            parts.append(f"{secs} second{'s' if secs != 1 else ''}")

        return ", ".join(parts)

    async def _check_cooldown(self, user_id: int) -> tuple[bool, float]:
        """
        Checks if a user is within the rate limit window without recording a new usage.
        Returns (is_allowed, seconds_until_next_available).
        """
        now_ts = datetime.now(timezone.utc).timestamp()
        window_seconds = self.rate_limit_hours * 3600
        uid = str(user_id)

        async with self.cooldown_lock:
            data = load_json_data(COOLDOWN_FILE)
            timestamps = data.get(uid, [])
            valid_timestamps = [
                ts for ts in timestamps if (now_ts - ts) < window_seconds
            ]
            valid_timestamps.sort()

            if len(valid_timestamps) != len(timestamps):
                data[uid] = valid_timestamps
                save_json_data(COOLDOWN_FILE, data)

            if len(valid_timestamps) >= self.rate_limit_count:
                oldest_ts = valid_timestamps[0]
                remaining = window_seconds - (now_ts - oldest_ts)
                return False, max(1.0, remaining)

            return True, 0.0

    async def _record_usage(self, user_id: int) -> None:
        """Records a completed request timestamp for the user."""
        now_ts = datetime.now(timezone.utc).timestamp()
        window_seconds = self.rate_limit_hours * 3600
        uid = str(user_id)

        async with self.cooldown_lock:
            data = load_json_data(COOLDOWN_FILE)
            timestamps = data.get(uid, [])
            valid_timestamps = [
                ts for ts in timestamps if (now_ts - ts) < window_seconds
            ]
            valid_timestamps.append(now_ts)
            valid_timestamps.sort()
            data[uid] = valid_timestamps
            save_json_data(COOLDOWN_FILE, data)

    @staticmethod
    def _matches_target(member_or_user: discord.abc.User, targets: list[str]) -> bool:
        """Checks if a Discord user/member matches any of the router's extracted target strings."""
        if not targets:
            return True

        uid_str = str(member_or_user.id)
        uname = member_or_user.name.lower()
        dname = member_or_user.display_name.lower()
        gname = (getattr(member_or_user, "global_name", None) or "").lower()

        for raw_target in targets:
            clean = raw_target.strip().lstrip("@").lower()
            if clean.startswith("<@") and clean.endswith(">"):
                clean = clean.strip("<@!>")
            if not clean:
                continue

            if (
                clean == uid_str
                or clean in uname
                or clean in dname
                or (gname and clean in gname)
            ):
                return True

        return False

    async def _route_query(
        self, client: genai.Client, raw_query: str
    ) -> RouterOutput:
        """Pass 1 (The Router): Evaluates safety, classifies intent, and extracts context requirements via structured JSON."""
        system_instruction = (
            "You are Pass 1 (The Router & Safety Filter) of a two-pass Discord bot LLM pipeline.\n"
            "Analyze the user's raw query and output structured JSON matching the provided schema:\n"
            "1. SAFETY FILTER (`is_safe`):\n"
            "   - Evaluate the user's raw query for Terms of Service (TOS) violations, hate speech, harassment, "
            "sexually explicit content, self-harm, or dangerous/illegal requests.\n"
            "   - If the query contains ANY of these violations, you MUST set `is_safe` to false (and do NOT repeat "
            "or quote the unsafe content in `intent` or `optimized_question`).\n"
            "   - Otherwise, set `is_safe` to true.\n"
            "2. CONTEXT ROUTING (`needs_context`, `target_usernames`, `intent`, `optimized_question`):\n"
            "   - Set `needs_context` to true ONLY if answering the question requires reading recent messages "
            "from the Discord channel (e.g., 'what were X and Y arguing about?', 'summarize the chat', "
            "'what did Bob say earlier?').\n"
            "   - Set `needs_context` to false if it is a standalone question (e.g., 'explain nonreductive functionalism', "
            "'how does a compiler work?').\n"
            "   - Extract `target_usernames` ONLY if specific participants or users are referenced in a contextual query. "
            "If the query is about the general channel conversation as a whole, leave `target_usernames` empty."
        )

        try:
            response = await client.aio.models.generate_content(
                model=self.router_model,
                contents=raw_query,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=RouterOutput,
                    temperature=0.1,
                ),
            )
            self._validate_response_safety(response)

            if response.parsed and isinstance(response.parsed, RouterOutput):
                return response.parsed

            if not response.text:
                raise SafetyBlockedError("Empty router response due to safety filters.")

            parsed_dict = json.loads(response.text)
            return RouterOutput(**parsed_dict)

        except SafetyBlockedError:
            raise
        except errors.APIError as e:
            if "SAFETY" in str(e).upper() or "BLOCKED" in str(e).upper() or "PROHIBITED" in str(e).upper():
                raise SafetyBlockedError(str(e)) from e
            raise
        except (ValueError, json.JSONDecodeError) as e:
            raise SafetyBlockedError("Failed to parse router output due to safety block.") from e

    async def _fetch_channel_context(
        self,
        ctx: commands.Context,
        target_usernames: list[str],
    ) -> tuple[list[str], list[str]]:
        """
        Fetches up to `self.history_fetch_limit` most recent messages from the channel.
        Filters out opted-out users and isolates relevant context in chronological order.
        Returns (formatted_messages, opted_out_names_encountered).
        """
        async with self.optout_lock:
            optout_data = load_json_data(OPTOUT_FILE)

        opted_out_ids = set(optout_data.keys())
        opted_out_encountered = set()

        # Check if any explicitly targeted users in the guild have opted out
        if ctx.guild and target_usernames:
            for uid_str, info in optout_data.items():
                member = ctx.guild.get_member(int(uid_str)) if uid_str.isdigit() else None
                if member and self._matches_target(member, target_usernames):
                    opted_out_encountered.add(member.display_name)
                elif isinstance(info, dict):
                    saved_name = info.get("name", "")
                    for raw_target in target_usernames:
                        clean = raw_target.strip().lstrip("@").lower().strip("<@!>")
                        if clean and (clean == uid_str or clean in saved_name.lower()):
                            opted_out_encountered.add(saved_name or f"User {uid_str}")

        collected_messages = []

        async for msg in ctx.channel.history(
            limit=self.history_fetch_limit, before=ctx.message, oldest_first=False
        ):
            if msg.author.bot:
                continue
            if msg.content.startswith("!"):
                continue

            uid_str = str(msg.author.id)

            # Ignore messages from users who have opted out
            if uid_str in opted_out_ids:
                if not target_usernames or self._matches_target(msg.author, target_usernames):
                    opted_out_encountered.add(msg.author.display_name)
                continue

            # Filter by target usernames if specific users were requested
            if target_usernames and not self._matches_target(msg.author, target_usernames):
                continue

            content_clean = msg.clean_content.strip()
            if not content_clean and msg.attachments:
                content_clean = "[Attachment]"
            if not content_clean:
                continue

            timestamp_str = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            collected_messages.append(
                f"[{timestamp_str}] {msg.author.display_name} (@{msg.author.name}): {content_clean}"
            )

        # Reverse so the transcript is in chronological order (oldest -> newest)
        collected_messages.reverse()

        return collected_messages, sorted(opted_out_encountered)

    async def _synthesize_response(
        self,
        client: genai.Client,
        raw_query: str,
        router_output: RouterOutput,
        chat_lines: list[str],
        opted_out_names: list[str],
    ) -> str:
        """Pass 2 (The Synthesizer): Combines intent, optimized question, opt-out notices, and chat context."""
        system_instruction = (
            "You are Pass 2 (The Synthesizer) of a stateless Discord bot assistant.\n"
            "Answer the user's question accurately, concisely, and helpfully using clear Markdown formatting "
            "appropriate for Discord.\n"
            "If chat history is provided, ground your answer in those messages. "
            "If the prompt notes that certain users opted out of message collection, keep that in mind "
            "and mention it if relevant to why parts of a conversation are missing."
        )

        prompt_sections = [
            f"### User Query\n{raw_query}",
            f"### Extracted Intent\n{router_output.intent}",
            f"### Optimized Question\n{router_output.optimized_question}",
        ]

        if router_output.needs_context:
            if opted_out_names:
                names_str = ", ".join(opted_out_names)
                prompt_sections.append(
                    f"### Privacy Opt-Out Notice\n"
                    f"Note: You do not have messages from the following user(s) because they opted out of LLM chat history collection: {names_str}. "
                    f"Their messages were completely omitted from the transcript below."
                )

            if chat_lines:
                transcript = "\n".join(chat_lines)
                prompt_sections.append(
                    f"### Channel Chat History (Last {self.history_fetch_limit} Messages)\n{transcript}"
                )
            else:
                prompt_sections.append(
                    f"### Channel Chat History (Last {self.history_fetch_limit} Messages)\n"
                    f"[No matching messages found in the last {self.history_fetch_limit} messages of this channel.]"
                )

        final_prompt = "\n\n".join(prompt_sections)

        try:
            response = await client.aio.models.generate_content(
                model=self.synthesizer_model,
                contents=final_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                ),
            )
            self._validate_response_safety(response)

            if not response.text:
                raise SafetyBlockedError("Empty synthesizer response due to safety filters.")

            return response.text.strip()

        except SafetyBlockedError:
            raise
        except errors.APIError as e:
            if "SAFETY" in str(e).upper() or "BLOCKED" in str(e).upper() or "PROHIBITED" in str(e).upper():
                raise SafetyBlockedError(str(e)) from e
            raise
        except ValueError as e:
            raise SafetyBlockedError("Synthesizer response text blocked by safety filter.") from e

    @staticmethod
    async def _send_chunked_response(ctx: commands.Context, text: str) -> None:
        """Sends a response to Discord, splitting into chunks if it exceeds the 2000-character limit."""
        if len(text) <= DISCORD_MAX_MESSAGE_LENGTH:
            await ctx.reply(text)
            return

        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= DISCORD_MAX_MESSAGE_LENGTH:
                chunks.append(remaining)
                break

            split_idx = remaining.rfind("\n", 0, DISCORD_MAX_MESSAGE_LENGTH)
            if split_idx == -1 or split_idx < DISCORD_MAX_MESSAGE_LENGTH // 2:
                split_idx = remaining.rfind(" ", 0, DISCORD_MAX_MESSAGE_LENGTH)
            if split_idx == -1 or split_idx < DISCORD_MAX_MESSAGE_LENGTH // 2:
                split_idx = DISCORD_MAX_MESSAGE_LENGTH

            chunks.append(remaining[:split_idx].rstrip())
            remaining = remaining[split_idx:].lstrip("\n")

        for i, chunk in enumerate(chunks):
            if i == 0:
                await ctx.reply(chunk)
            else:
                await ctx.send(chunk)

    # ---------------------------------------------------------
    # LLM COMMANDS
    # ---------------------------------------------------------
    @commands.command(name="ask")
    async def ask(self, ctx: commands.Context, *, query: str):
        if not is_env_enabled("ENABLE_CMD_ASK"):
            return

        # Resolve user mentions in the raw query to readable names so the Router can identify them
        clean_query = ctx.message.clean_content
        if clean_query.lower().startswith("!ask"):
            clean_query = clean_query[4:].strip()
        if not clean_query:
            clean_query = query.strip()

        # 1. Rate Limit Check
        is_allowed, remaining_seconds = await self._check_cooldown(ctx.author.id)
        if not is_allowed:
            time_str = self._format_remaining_time(remaining_seconds)
            await ctx.reply(
                f"⏳ **Rate Limit Reached:** You can only make **{self.rate_limit_count}** requests "
                f"per **{self.rate_limit_hours}-hour** period. Please wait **{time_str}** before your next available request."
            )
            return

        status_msg = await ctx.reply("🤔 Analyzing your question...")

        try:
            client = self._get_genai_client()

            # 2. Pass 1: The Router (Intent & Safety Check)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔍 [Pass 1] Routing !ask query from {ctx.author} using {self.router_model}..."
            )
            router_output = await asyncio.wait_for(
                self._route_query(client, clean_query),
                timeout=self.request_timeout_seconds,
            )

            # Halt Pipeline Immediately if Router Flags Request as Unsafe
            if not router_output.is_safe:
                await status_msg.delete()
                await ctx.reply(SAFETY_REJECTION_MESSAGE, ephemeral=True)
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🛡️ Blocked unsafe !ask query from {ctx.author} (Pass 1 Router)."
                )
                return

            # 3. Conditional Context Fetching
            chat_lines = []
            opted_out_names = []
            if router_output.needs_context:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📜 Fetching up to {self.history_fetch_limit} messages for {ctx.author} (targets={router_output.target_usernames})..."
                )
                await status_msg.edit(
                    content=f"📜 Fetching up to {self.history_fetch_limit} recent messages from channel history..."
                )
                chat_lines, opted_out_names = await self._fetch_channel_context(
                    ctx, router_output.target_usernames
                )

            # 4. Pass 2: The Synthesizer
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🧠 [Pass 2] Synthesizing response for {ctx.author} using {self.synthesizer_model} ({len(chat_lines)} context msgs)..."
            )
            await status_msg.edit(content="🧠 Synthesizing response...")
            answer = await asyncio.wait_for(
                self._synthesize_response(
                    client,
                    clean_query,
                    router_output,
                    chat_lines,
                    opted_out_names,
                ),
                timeout=self.request_timeout_seconds,
            )

            # Record usage only after a successful LLM pipeline execution
            await self._record_usage(ctx.author.id)

            await status_msg.delete()
            await self._send_chunked_response(ctx, answer)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🤖 Answered !ask query from {ctx.author} "
                f"(needs_context={router_output.needs_context}, targets={router_output.target_usernames})"
            )

        except SafetyBlockedError:
            await status_msg.delete()
            await ctx.reply(SAFETY_REJECTION_MESSAGE, ephemeral=True)
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🛡️ Blocked !ask query from {ctx.author} (SDK Safety Exception)."
            )
            return

        except (asyncio.TimeoutError, httpx.TimeoutException):
            await status_msg.delete()
            await ctx.reply(
                f"⏱️ **Request Timed Out:** The AI model took longer than **{self.request_timeout_seconds} seconds** to respond. "
                f"Your request did **not** count toward your rate limit—please try again later!"
            )
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⏱️ !ask query from {ctx.author} timed out after {self.request_timeout_seconds}s."
            )
            return

        except errors.ServerError as e:
            await status_msg.delete()
            await ctx.reply(
                "⚠️ **AI Service Temporarily Busy:** The model is currently experiencing high demand. "
                "Your request did **not** count toward your rate limit—please try again in a moment!"
            )
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ Gemini ServerError ({e.code}) for {ctx.author}: {e.message}"
            )
            return

        except errors.APIError as e:
            await status_msg.delete()
            if e.code == 429:
                await ctx.reply(
                    "⏳ **AI API Quota Reached:** The bot's Gemini API rate limit is temporarily saturated. "
                    "Your request did **not** count toward your personal limit—please try again shortly!"
                )
            else:
                await ctx.reply(
                    "⚠️ **AI Service Error:** An unexpected error occurred while communicating with the AI provider. "
                    "Your request did **not** count toward your rate limit."
                )
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ Gemini APIError ({e.code}) for {ctx.author}: {e.message}"
            )
            return

        except Exception as e:
            await status_msg.delete()
            raise e

    @commands.command(name="optout")
    async def optout(self, ctx: commands.Context):
        if not is_env_enabled("ENABLE_CMD_OPTOUT"):
            return

        uid = str(ctx.author.id)

        async with self.optout_lock:
            data = load_json_data(OPTOUT_FILE)
            if uid in data:
                del data[uid]
                save_json_data(OPTOUT_FILE, data)
                opted_out = False
            else:
                data[uid] = {
                    "name": str(ctx.author),
                    "display_name": ctx.author.display_name,
                    "opted_out_at": datetime.now(timezone.utc).isoformat(),
                }
                save_json_data(OPTOUT_FILE, data)
                opted_out = True

        if opted_out:
            await ctx.reply(
                "🔒 **Opt-Out Confirmed:** Your messages will now be ignored whenever `!ask` fetches channel history. "
                "Run `!optout` again at any time to opt back in."
            )
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔒 {ctx.author} opted out of LLM context collection.")
        else:
            await ctx.reply(
                "🔓 **Opt-In Confirmed:** Your messages can now be included again when `!ask` fetches channel history. "
                "Run `!optout` at any time to opt out."
            )
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔓 {ctx.author} opted back in to LLM context collection.")


async def setup(bot):
    await bot.add_cog(LLM(bot))
