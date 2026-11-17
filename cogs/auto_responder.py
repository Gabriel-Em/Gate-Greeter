import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
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


# ---------------------------------------------------------
# ABSENT MEMBERS: "where is X / what happened to X / did X leave?" questions
# ---------------------------------------------------------
# Each configured member is one .env entry group sharing a <KEY> suffix:
#   ABSENT_NAMES_<KEY>  comma-separated names / nicknames (matched as whole words, any case)
#   ABSENT_IDS_<KEY>    optional comma-separated Discord user IDs (matches @mentions)
#   ABSENT_REPLY_<KEY>  the reply text
DISCORD_MESSAGE_LIMIT = 2000
_ABSENT_NAMES_PREFIX = "ABSENT_NAMES_"
_ABSENT_IDS_PREFIX = "ABSENT_IDS_"
_ABSENT_REPLY_PREFIX = "ABSENT_REPLY_"
_ABSENT_PREFIXES = (_ABSENT_NAMES_PREFIX, _ABSENT_IDS_PREFIX, _ABSENT_REPLY_PREFIX)
_USER_ID = re.compile(r"[0-9]+")

# Separators inside a list of names: "John and Maria", "John, Maria", "John & Maria", "John/Maria".
_NAME_SEP = r"(?:\s*[,&/]\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)"
# Any single-word name or mention that may share a list with a configured member ("Bob and John").
_OTHER_NAME = r"(?:<@!?[0-9]+>|@?\w[\w.-]*)"
_SWEAR = r"(?:the\W+(?:hell|heck|fuck|f)|tf|da\W+hell|on\W+earth)"
_SEEN_VERB = (
    r"(?:seen\W+or\W+heard\W+from|seen|see|heard\W+(?:from|of|anything\W+(?:from|about))"
    r"|hear\W+(?:from|of)|spoken\W+to|talked\W+to|been\W+in\W+touch\W+with)"
)
_GONE_FROM = r"(?:server|discord|group|community|chat|guild)"
_GONE_STATE = r"(?:missing|quiet|silent|dark|awol|mia|inactive)"
# The name ends the clause ("where is John?", "where's John, ...").
_CLAUSE_END = r"\W*(?:$|[.?!,;:\n])"
# What may follow "where is <name>" without turning it into "where is John from / where did John say that".
_WHERE_TAIL = (
    rf"(?={_CLAUSE_END}|\W+(?:go|gone|went|been|at|now|lately|recently|nowadays|these\W+days|disappear\w*"
    r"|vanish\w*|r[au]n\W+off|hiding|off\W+to|anyway|tho|though|lol|lmao|bro|man|dude|guys)\b)"
)
# What may follow "haven't seen <name>" without turning it into "haven't seen John play guitar".
_SEEN_TAIL = (
    rf"(?={_CLAUSE_END}|\W+(?:in|for|around|lately|recently|since|anywhere|at\W+all|much|online|on\W+here"
    r"|here|today|nowadays|these\W+days|this\W+(?:week|month|year)|anymore|any\W*more|either|tho|though"
    r"|lol|lmao|or\W+heard)\b)"
)
# "did John leave a comment" is not about John leaving; "did John leave the server" is.
_NOT_AN_OBJECT = (
    r"(?!\W+(?:(?:a|an|his|her|their|my|your|our|it|its|that|this|some|any|me|us|you|them|him"
    rf"|comments?|messages?|reviews?|notes?)\b|the\W+(?!{_GONE_FROM}\b)))"
)
_ABSENT_TEMPLATES = (
    # what happened to John / whatever happened with John
    rf"\bwhat(?:ever)?\W+(?:(?:{_SWEAR}|even|really|actually|ever)\W+)*happened\W+(?:to|with)\W+{{N}}",
    # where is / where's / where did John go / where are John and Maria
    rf"\bwhere(?:['’]?s\b)?(?:\W+(?:is|s|are|was|were|has|have|had|did|do|does|{_SWEAR}))*\W+{{N}}{_WHERE_TAIL}",
    # did John leave / has John left / is John gone / did John get banned
    r"\b(?:did|has|have|had|is|are|was|were)\W+{N}\W+"
    r"(?:(?:just|actually|really|finally|ever|already|seriously|completely)\W+)*"
    rf"(?:leave|left|quit|gone|disappear(?:ed)?|vanish(?:ed)?|rage\W*quit|(?:get|got|been)\W+(?:banned|kicked)"
    rf"|banned|kicked|go\W+{_GONE_STATE}|gone\W+{_GONE_STATE})\b{_NOT_AN_OBJECT}",
    # John disappeared / John left the server / John got banned
    r"{N}\W+(?:(?:just|actually|really|finally|kinda|basically|completely|literally)\W+)*"
    rf"(?:disappeared|vanished|rage\W*quit|went\W+{_GONE_STATE}|has\W+gone\W+{_GONE_STATE}"
    rf"|(?:left|quit)\W+(?:the\W+)?{_GONE_FROM}|(?:got|was|has\W+been|been)\W+(?:banned|kicked))\b",
    # John left? / John gone?
    r"{N}\W+(?:(?:just|actually|really)\W+)*(?:left|quit|gone|leaving)\W*\?",
    # John hasn't been on in a while / John has been so quiet / John is gone
    r"{N}\W+(?:hasn['’]?t|hasnt|has\W+not|haven['’]?t|havent|have\W+not|hadn['’]?t)\W+(?:(?:really|even)\W+)?"
    r"(?:been\W+(?:on(?:line)?|around|active|here|posting|chatting|seen)|posted|chatted|shown\W+up"
    r"|logged\W+on|said\W+anything|talked)\b",
    r"{N}\W+(?:has|have|had)\W+been\W+(?:(?:so|really|pretty|very|super)\W+)?"
    r"(?:gone|missing|mia|awol|quiet|inactive|silent|absent|away|offline)\b",
    r"{N}\W+(?:is|are)\W+(?:gone|mia|awol)\b",
    # I haven't seen John in a while / nobody has heard from John
    r"\b(?:haven['’]?t|havent|have\W+not|hasn['’]?t|hasnt|has\W+not|hadn['’]?t|didn['’]?t|didnt|not|never"
    r"|nobody(?:['’]s|\W+has)?|no\W*one(?:['’]s|\W+has)?)\W+(?:(?:really|actually|even|been|ever)\W+)*"
    rf"{_SEEN_VERB}\W+{{N}}{_SEEN_TAIL}",
    # has anyone seen John / have you guys heard from John
    r"\b(?:(?:has|have|did)\W+)?(?:any\W*(?:one|body)|some\W*(?:one|body)|y['’]?all|you(?:\W+(?:guys|all|lot))?"
    rf"|u(?:\W+guys)?|ya)\W+(?:(?:ever|recently|lately|actually)\W+)*{_SEEN_VERB}\W+{{N}}{_SEEN_TAIL}",
    # is John still around / does John still come on here / John still around?
    r"\b(?:is|are|was|does|do)\W+{N}\W+(?:(?:even|ever|really|actually)\W+)?still\W+"
    r"(?:around|active|alive|here|on\W+here|in\W+here|a\W+member|with\W+us|online"
    r"|(?:on|in)\W+(?:the|this)\W+(?:server|discord)|(?:on|in)\W+discord"
    r"|(?:come|comes|coming)\W+(?:on|here|around|by)|(?:post|posts|chat|chats|talk|talks)\W+(?:here|on\W+here|in\W+here))\b",
    r"{N}\W+still\W+(?:around|active|alive|here)\W*\?",
)


@dataclass(frozen=True)
class AbsentMember:
    key: str
    reply: str
    mention: re.Pattern  # finds the member's name or @mention
    question: re.Pattern  # finds a question about the member being gone


def _log(text: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}")


def _split_list(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _member_token(names: list[str], user_ids: list[str]) -> str:
    """Regex for one member's names (whole words, flexible spacing) or @mentions, never possessive."""
    alternatives = []
    if names:
        spelled = (r"\s+".join(map(re.escape, name.split())) for name in sorted(names, key=len, reverse=True))
        alternatives.append(rf"(?<!\w)(?:{'|'.join(spelled)})(?!\w)")
    if user_ids:
        alternatives.append(rf"<@!?(?:{'|'.join(user_ids)})>")
    return rf"(?:{'|'.join(alternatives)})(?!['’]s\b)"


def load_absent_members(environ: Mapping[str, str]) -> tuple[AbsentMember, ...]:
    """Builds the absent-member responders from ABSENT_NAMES_/ABSENT_IDS_/ABSENT_REPLY_<KEY> variables."""
    keys = sorted(
        {name[len(prefix):] for name in environ for prefix in _ABSENT_PREFIXES
         if name.startswith(prefix) and len(name) > len(prefix)}
    )
    entries = []
    for key in keys:
        names = _split_list(environ.get(_ABSENT_NAMES_PREFIX + key))
        user_ids = []
        for raw_id in _split_list(environ.get(_ABSENT_IDS_PREFIX + key)):
            if _USER_ID.fullmatch(raw_id):
                user_ids.append(raw_id)
            else:
                _log(f"⚠️ Ignoring malformed user ID {raw_id!r} in {_ABSENT_IDS_PREFIX}{key}")
        reply = environ.get(_ABSENT_REPLY_PREFIX + key, "").strip()
        if not reply:
            _log(f"⚠️ Skipping absent member {key}: {_ABSENT_REPLY_PREFIX}{key} is missing or empty")
        elif not names and not user_ids:
            _log(f"⚠️ Skipping absent member {key}: no names in {_ABSENT_NAMES_PREFIX}{key} or IDs in {_ABSENT_IDS_PREFIX}{key}")
        elif len(reply) > DISCORD_MESSAGE_LIMIT:
            _log(f"⚠️ Skipping absent member {key}: reply is longer than {DISCORD_MESSAGE_LIMIT} characters")
        else:
            entries.append((key, reply, _member_token(names, user_ids)))

    any_member = "|".join(token for _, _, token in entries)
    members = []
    for key, reply, token in entries:
        listed = rf"(?:(?:{any_member}|{_OTHER_NAME}){_NAME_SEP}){{0,5}}{token}(?:{_NAME_SEP}(?:{any_member}|{_OTHER_NAME})){{0,5}}"
        question = "|".join(template.replace("{N}", listed) for template in _ABSENT_TEMPLATES)
        members.append(AbsentMember(key, reply, re.compile(token, _FLAGS), re.compile(question, _FLAGS)))
    return tuple(members)


def absent_members_asked_about(content: str, members: tuple[AbsentMember, ...]) -> list[AbsentMember]:
    """Members the message asks about, in the order they are first named."""
    asked = [member for member in members if member.question.search(content)]
    return sorted(asked, key=lambda member: member.mention.search(content).start())


def absent_member_replies(content: str, members: tuple[AbsentMember, ...]) -> list[str]:
    return [member.reply for member in absent_members_asked_about(content, members)]


def _combine_replies(replies: list[str]) -> list[str]:
    """Joins replies into as few messages as Discord's length limit allows, never splitting a reply."""
    messages = []
    for reply in replies:
        if messages and len(messages[-1]) + 2 + len(reply) <= DISCORD_MESSAGE_LIMIT:
            messages[-1] = f"{messages[-1]}\n\n{reply}"
        else:
            messages.append(reply)
    return messages


def asks_what_vo2_max_is(content: str) -> bool:
    return any(pattern.search(content) for pattern in VO2_PATTERNS)


def asks_whether_alex_is_here(content: str) -> bool:
    return any(pattern.search(content) for pattern in ALEX_PATTERNS)


class AutoResponder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.absent_members = load_absent_members(os.environ)

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

        # 3. Absent Members Check
        if is_env_enabled("ENABLE_RESP_ABSENT") and self.absent_members:
            asked = absent_members_asked_about(message.content, self.absent_members)
            for text in _combine_replies([member.reply for member in asked]):
                await message.reply(text)
            if asked:
                _log(f"Replied about absent member(s) {', '.join(m.key for m in asked)} to {message.author} in #{message.channel}")


async def setup(bot):
    await bot.add_cog(AutoResponder(bot))
