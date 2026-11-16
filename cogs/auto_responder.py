import re
from datetime import datetime

from discord.ext import commands

from utils import is_env_enabled

_FLAGS = re.IGNORECASE

# ---------------------------------------------------------
# VO2 MAX: "what is that?" questions (not "what is YOUR vo2 max")
# ---------------------------------------------------------
_VO2 = r"vo\s?[2₂](?:[\s-]?max)?\b"
# Words that may sit between the question word and the term without changing the question
# ("what the hell is a ...", "what do you mean by ..."). Possessives and adjectives such as
# "your" or "a good" are deliberately absent: those ask for a value, not for a definition.
_VO2_FILLER = (
    r"(?:\W+(?:is|s|even|exactly|actually|the|hell|heck|fuck|tf|a|an|this|that|does|do|you|mean|by|on|earth))"
)
VO2_PATTERNS = tuple(
    re.compile(pattern, _FLAGS)
    for pattern in (
        # what is / what's / wtf is / what does ... vo2 max
        rf"\b(?:whats|(?:what|wtf|wth){_VO2_FILLER}){_VO2_FILLER}{{0,5}}\W+{_VO2}",
        # (no idea) what vo2 max is / means / stands for
        rf"\bwhat\W+(?:a\W+|the\W+)?{_VO2}\W+(?:even\W+|actually\W+)?(?:is|means?|stands?\W+for)\b"
        r"(?!\W+(?:good|normal|average|considered|best|ideal|needed|required|healthy)\b)",
        # explain / define vo2 max
        rf"\b(?:explain|define)\W+(?:to\W+me\W+)?(?:what\W+)?(?:a\W+|the\W+)?{_VO2}",
        rf"\b(?:meaning|definition)\W+of\W+{_VO2}",
        rf"\bnever\W+heard\W+of\W+{_VO2}",
        # a message that is nothing but "vo2 max?"
        rf"^\W*{_VO2}\W*\?+\W*$",
    )
)

VO2_RESPONSE = (
    "VO2 max (maximal oxygen consumption) is the maximum amount of oxygen "
    "your body can absorb and use during intense exercise. It's one of the best "
    "indicators of your cardiovascular fitness and endurance!"
)

# ---------------------------------------------------------
# ALEX: "is Alex on this server / is this Alex's server?" questions
# ---------------------------------------------------------
_ALEX = r"\b(?:alex(?:\s+o['’]?conn[oe]r)?|o['’]?conn[oe]r|cosmic\s?skeptic)(?:['’]?s)?\b"
_ASK = r"\b(?:is|isn['’]?t|was|does|doesn['’]?t|did|has|hasn['’]?t)\b"
_SAME_SENTENCE = r"[^.?!\n]"
# Only these words may separate the question verb from the name ("is this the official Alex ..."),
# which keeps "did you see what Alex ..." from reading as a question about Alex.
_TO_NAME = r"(?:\W+(?:this|that|the|even|actually|really|it|true|an?|official|real|our))*\W+"
# Words that name this server.
_PLACE = r"(?:server|discord|account|this\s+(?:place|community))"
# "do you think / does anyone know if ..." openers that turn a statement about Alex into a question.
_OPINION = (
    r"\b(?:do|does|did)\W+(?:you|u|we|ya|y['’]?all|anyone|anybody|someone|somebody)"
    r"(?:\W+(?:guys|all|even|actually|really))*\W+(?:think|know|reckon|suppose|believe)"
    r"(?:\W+(?:if|whether|that))?"
)
_ALEX_CORE = (
    r"(?:"
    # is Alex (even) on the server / does Alex have an account / is this Alex's server / is Alex aware of this place
    rf"(?:{_ASK}|{_OPINION}){_TO_NAME}{_ALEX}{_SAME_SENTENCE}{{0,60}}?\b{_PLACE}\b"
    # is Alex (actually in) here
    rf"|(?:{_ASK}|{_OPINION}){_TO_NAME}{_ALEX}{_SAME_SENTENCE}{{0,25}}?\bhere\b"
    # does this server belong to Alex / is this server run by Alex
    rf"|{_ASK}{_SAME_SENTENCE}{{0,15}}?\b(?:this|the)\s+(?:server|discord)\b{_SAME_SENTENCE}{{0,40}}?{_ALEX}"
    r")"
)
# "is he here?" style question whose "he" is named right next to it.
_HE_QUESTION = rf"{_ASK}\W+he\b{_SAME_SENTENCE}{{0,40}}?\b(?:{_PLACE}|here)\b{_SAME_SENTENCE}*\?+"
_I_MEAN = r"(?:i\W+meant?|talking\W+about|as\W+in|meaning)"
# Greetings and hedges that may open a sentence before the actual question.
_LEAD_IN = r"(?:(?:so|wait|hey|hi|hello|um+|uh+|ok|okay|but|and|lol|btw|guys|question|quick|sorry|also)\W+){0,4}"
ALEX_PATTERNS = (
    # The question opens a sentence or clause ...
    re.compile(rf"(?:^|[.?!,;:\n])\s*{_LEAD_IN}{_ALEX_CORE}", _FLAGS),
    # ... or sits anywhere in a sentence that ends with a question mark.
    re.compile(rf"{_ALEX_CORE}{_SAME_SENTENCE}*\?", _FLAGS),
    # Statement turned into a question by its question mark: "Alex is on this server?", "Alex here?"
    re.compile(
        rf"{_ALEX}\s+(?:(?:is|isn['’]?t|was)\b{_SAME_SENTENCE}{{0,25}}?\b(?:server|discord|here)\b|here\b)"
        rf"{_SAME_SENTENCE}*\?",
        _FLAGS,
    ),
    # Pronoun question clarified afterwards: "is he here? alex i mean", "is he on the server? I mean Alex"
    re.compile(
        rf"{_HE_QUESTION}\W*(?:{_I_MEAN}\W+)?{_ALEX}(?:\W+(?:{_I_MEAN}|obviously|ofc|btw))*\W*$",
        _FLAGS,
    ),
    # Name first, then the pronoun question: "Alex, is he in this discord?"
    re.compile(rf"{_ALEX}\W+{_HE_QUESTION}", _FLAGS),
)

ALEX_RESPONSE = (
    "Alex does have an account here and gave the server his blessing a long time ago, "
    "but he doesn't actively use Discord. This is entirely a fan-made server "
    "(he doesn't have an official one!).\n\n"
    "**Please note:** The views and opinions expressed by members on this server "
    "do not reflect Alex's personal views."
)


def asks_what_vo2_max_is(content: str) -> bool:
    return any(pattern.search(content) for pattern in VO2_PATTERNS)


def asks_whether_alex_is_here(content: str) -> bool:
    return any(pattern.search(content) for pattern in ALEX_PATTERNS)


class AutoResponder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        # 1. VO2 Max Check
        if is_env_enabled("ENABLE_RESP_VO2MAX") and asks_what_vo2_max_is(message.content):
            await message.reply(VO2_RESPONSE)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Replied about VO2 max to {message.author} in #{message.channel}")
            return

        # 2. Alex O'Connor Check
        if is_env_enabled("ENABLE_RESP_ALEX") and asks_whether_alex_is_here(message.content):
            await message.reply(ALEX_RESPONSE)
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Replied about Alex O'Connor to {message.author} in #{message.channel}")
            return


async def setup(bot):
    await bot.add_cog(AutoResponder(bot))
