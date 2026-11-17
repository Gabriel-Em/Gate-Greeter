import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from cogs.auto_responder import AutoResponder, absent_member_replies, load_absent_members

ALEX_QUESTIONS = (
    "Is Alex even on the server?",
    "Is this Alex's serveR?",
    "Is O'Connor on the server?",
    "Is Alex O'conner even in the server?",
    "Does Alex have an account on the server?",
    "Does this server belong to Alex?",
    "Is Alex here?",
    "is alex in this discord",
    "wait, is alex actually in here?",
    "Hey guys. Is this Alex O’Connor’s official server?",
    "isn't alex on this server",
    "Is this server run by Alex?",
    "does alex ever come on here?",
    "Has Alex joined the server?",
    "so is this alexs server or what",
    "quick question, is Alex on the server?",
    "I was wondering if Alex is in this server?",
    "Is CosmicSkeptic in this server?",
    "Alex is on this server?",
    "alex here?",
    "Alex isn't on the server, is he?",
    "do you think Alex reads this server",
    "Is Alex even aware of this place?",
    "is he here? alex i mean",
    "Is he on the server?? I mean Alex",
    "Alex, is he in this discord?",
    "does anyone know if Alex is in the server",
    "Do you guys think Alex ever comes on here",
    "does alex know about this community",
)

NOT_ALEX_QUESTIONS = (
    "",
    "Alex is great",
    "This is why Alex said in the podcast that the server of the restaurant was rude.",
    "I think Alex made a good point in his last video",
    "Is Alexander in the server?",
    "Is the server down?",
    "Is Alex right about free will?",
    "Does Alex believe in free will? Anyway this server is cool.",
    "Did Alex release a new video?",
    "Christopher Hitchens debated here before Alex was known",
    "The thesis Alex defends is on the server side of the argument",
    "Did you see what Alex said about the server?",
    "Alex is here",
    "who is alex",
    "Do you know what Alex said about the server?",
    "Do you think Alex is right about determinism?",
    "Is Alex aware of this argument?",
    "is he here? Alex was looking for him",
    "Is he here? I thought he left",
    "Is this place any good? Alex recommended it",
)

JOHN_ID = "111111111111111111"
JOHN_REPLY = "John is taking a break from Discord."
MARIA_REPLY = "Maria left the server on good terms."
ABSENT_ENV = {
    "ABSENT_NAMES_JOHN": "John, Johnny ,J.D.",
    "ABSENT_IDS_JOHN": JOHN_ID,
    "ABSENT_REPLY_JOHN": JOHN_REPLY,
    "ABSENT_NAMES_MARIA": "Maria",
    "ABSENT_REPLY_MARIA": MARIA_REPLY,
}

ABSENT_JOHN_QUESTIONS = (
    "What happened to John?",
    "what happened to johnny",
    "whatever happened to John?",
    "What the hell happened to John",
    "So what happened with John",
    "where is John?",
    "Where's John",
    "wheres john",
    "where did John go?",
    "Where has John been lately?",
    "where tf is johnny",
    "Did John leave?",
    "did john leave the server",
    "Has John left?",
    "has john left the discord?",
    "Did John quit?",
    "did John just leave",
    "is John gone?",
    "did John get banned?",
    "John left?",
    "John left the server?",
    "john disappeared",
    "I haven't seen John in a while",
    "havent seen johnny in ages",
    "Haven't heard from John lately",
    "Has anyone seen John?",
    "anyone seen john recently?",
    "have you guys heard from John?",
    "Is John still around?",
    "is john still active on here?",
    "John hasn't been on in a while",
    "John has been so quiet lately",
    "hey guys, where's John? haven't seen him",
    "WHERE IS JOHN",
    f"yo where is <@{JOHN_ID}>",
    f"what happened to <@!{JOHN_ID}>?",
    "did @John leave",
    "where is J.D.?",
    "where are Bob and John?",
)

NOT_ABSENT_QUESTIONS = (
    "",
    "John is great",
    "John is here",
    "I'm John and I just joined",
    "Where is the link John posted?",
    "What happened to John's car?",
    "where is john's video",
    "John left a comment on my post",
    "Did John leave a review?",
    "Where did John say that?",
    "where is John from?",
    "I haven't seen John's latest video",
    "Has anyone seen John play guitar?",
    "Did John go to the store?",
    "did john like my post",
    "is John right about that?",
    "Johnathan left the server",
    "where is Johnson?",
    "where is JxD?",
    "what happened to Bob?",
    "where is <@222222222222222222>",
    f"where is <@&{JOHN_ID}>",
    "John and Maria are cool",
)

VO2_QUESTIONS = (
    "what is vo2 max",
    "What's VO2 max?",
    "whats vo2max",
    "What even is VO2 max?",
    "what is a vo2 max",
    "wtf is vo2 max",
    "what the hell is a VO2 max",
    "What does VO2 max mean?",
    "what does vo2 stand for",
    "what is VO2?",
    "idk what vo2 max is",
    "I have no idea what VO2 max even means",
    "can someone explain vo2 max to me",
    "explain what vo2 max is please",
    "vo2 max?",
    "VO2 max??",
    "what's that vo2 max thing everyone talks about",
    "never heard of vo2 max",
    "what is VO₂ max",
    "what do you mean by vo2 max",
    "what is vo2-max",
)

NOT_VO2_QUESTIONS = (
    "",
    "what a great day. my vo2 max improved",
    "my vo2 max is 52",
    "what is your vo2 max?",
    "what's a good vo2 max for a 30 year old",
    "What should I eat? Also my vo2 max went up.",
    "vo2 max training is brutal",
    "I improved my VO2 max by running intervals",
    "what is the average vo2 max for cyclists",
    "what vo2 max is considered elite?",
    "whatever, vo2 max is overrated",
)


class TestAutoResponder(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = AutoResponder(MagicMock())

    async def _reply_to(self, content, bot=False, guild="default", env=None):
        message = MagicMock(spec=discord.Message)
        message.content = content
        message.author.bot = bot
        message.guild = MagicMock() if guild == "default" else guild
        message.reply = AsyncMock()
        toggles = {"ENABLE_RESP_VO2MAX": "True", "ENABLE_RESP_ALEX": "True", **(env or {})}
        with patch.dict(os.environ, toggles), patch("builtins.print"):
            await self.cog.on_message(message)
        if message.reply.await_count == 0:
            return None
        message.reply.assert_awaited_once()
        return message.reply.call_args.args[0]

    async def test_questions_about_alex_get_the_alex_answer(self):
        for text in ALEX_QUESTIONS:
            with self.subTest(text=text):
                reply = await self._reply_to(text)
                self.assertIsNotNone(reply)
                self.assertIn("fan-made server", reply)

    async def test_other_mentions_of_alex_or_the_server_are_ignored(self):
        for text in NOT_ALEX_QUESTIONS:
            with self.subTest(text=text):
                self.assertIsNone(await self._reply_to(text))

    async def test_questions_about_what_vo2_max_is_get_the_explanation(self):
        for text in VO2_QUESTIONS:
            with self.subTest(text=text):
                reply = await self._reply_to(text)
                self.assertIsNotNone(reply)
                self.assertIn("maximal oxygen consumption", reply)

    async def test_other_mentions_of_vo2_max_are_ignored(self):
        for text in NOT_VO2_QUESTIONS:
            with self.subTest(text=text):
                self.assertIsNone(await self._reply_to(text))

    async def test_bots_and_direct_messages_are_ignored(self):
        self.assertIsNone(await self._reply_to("what is vo2 max", bot=True))
        self.assertIsNone(await self._reply_to("Is Alex here?", guild=None))

    async def test_each_responder_can_be_disabled(self):
        self.assertIsNone(await self._reply_to("what is vo2 max", env={"ENABLE_RESP_VO2MAX": "False"}))
        self.assertIsNone(await self._reply_to("Is Alex here?", env={"ENABLE_RESP_ALEX": "False"}))
        self.assertIsNotNone(await self._reply_to("Is Alex here?", env={"ENABLE_RESP_VO2MAX": "False"}))

    async def test_message_matching_both_gets_a_single_reply(self):
        reply = await self._reply_to("what is vo2 max and is Alex here?")
        self.assertIn("maximal oxygen consumption", reply)


class TestAbsentMemberConfig(unittest.TestCase):
    def setUp(self):
        patcher = patch("builtins.print")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_complete_entry_is_loaded(self):
        members = load_absent_members(ABSENT_ENV)
        self.assertEqual(sorted(m.key for m in members), ["JOHN", "MARIA"])
        self.assertEqual({m.key: m.reply for m in members}, {"JOHN": JOHN_REPLY, "MARIA": MARIA_REPLY})

    def test_empty_environment_loads_nothing(self):
        self.assertEqual(load_absent_members({}), ())
        self.assertEqual(absent_member_replies("where is John?", ()), [])

    def test_unrelated_variables_are_ignored(self):
        self.assertEqual(load_absent_members({"BOT_TOKEN": "x", "ENABLE_RESP_ABSENT": "True"}), ())

    def test_entries_missing_a_reply_or_any_name_are_skipped(self):
        env = {
            "ABSENT_NAMES_NOREPLY": "Ghost",
            "ABSENT_NAMES_BLANKREPLY": "Casper",
            "ABSENT_REPLY_BLANKREPLY": "   ",
            "ABSENT_REPLY_NONAME": "nobody to match",
            "ABSENT_NAMES_BLANKNAMES": " , ,",
            "ABSENT_REPLY_BLANKNAMES": "blank names",
        }
        self.assertEqual(load_absent_members(env), ())

    def test_entry_with_only_user_ids_answers_mentions(self):
        members = load_absent_members({"ABSENT_IDS_X": f" {JOHN_ID} ", "ABSENT_REPLY_X": "X is away."})
        self.assertEqual(absent_member_replies(f"where is <@{JOHN_ID}>?", members), ["X is away."])
        self.assertEqual(absent_member_replies("where is X?", members), [])

    def test_malformed_user_ids_are_dropped(self):
        members = load_absent_members({"ABSENT_IDS_X": "abc,-5,12.3,", "ABSENT_REPLY_X": "X is away."})
        self.assertEqual(members, ())
        members = load_absent_members({"ABSENT_IDS_X": "abc,42", "ABSENT_REPLY_X": "X is away."})
        self.assertEqual(absent_member_replies("where is <@42>", members), ["X is away."])
        self.assertEqual(absent_member_replies("where is <@abc>", members), [])

    def test_reply_too_long_for_discord_is_skipped(self):
        env = {"ABSENT_NAMES_X": "Xavier", "ABSENT_REPLY_X": "a" * 2001}
        self.assertEqual(load_absent_members(env), ())
        env["ABSENT_REPLY_X"] = "a" * 2000
        self.assertEqual(len(load_absent_members(env)), 1)

    def test_multiline_reply_is_kept_verbatim(self):
        members = load_absent_members({"ABSENT_NAMES_X": "Xavier", "ABSENT_REPLY_X": "line one\n**line two**"})
        self.assertEqual(absent_member_replies("where is Xavier?", members), ["line one\n**line two**"])

    def test_short_name_does_not_match_inside_longer_words(self):
        members = load_absent_members({"ABSENT_NAMES_AL": "Al", "ABSENT_REPLY_AL": "Al is away."})
        self.assertEqual(absent_member_replies("where is Al?", members), ["Al is away."])
        self.assertEqual(absent_member_replies("where is Alex?", members), [])
        self.assertEqual(absent_member_replies("where is Sal?", members), [])
        self.assertEqual(absent_member_replies("Sal disappeared", members), [])
        self.assertEqual(absent_member_replies("Hal left the server", members), [])

    def test_multi_word_name_matches_with_any_spacing(self):
        members = load_absent_members({"ABSENT_NAMES_X": "Mr Smith", "ABSENT_REPLY_X": "Away."})
        self.assertEqual(absent_member_replies("what happened to mr   smith?", members), ["Away."])


class TestAbsentMemberMatching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("builtins.print"):
            cls.members = load_absent_members(ABSENT_ENV)

    def test_questions_about_an_absent_member_get_their_reply(self):
        for text in ABSENT_JOHN_QUESTIONS:
            with self.subTest(text=text):
                self.assertEqual(absent_member_replies(text, self.members), [JOHN_REPLY])

    def test_other_mentions_are_ignored(self):
        for text in NOT_ABSENT_QUESTIONS:
            with self.subTest(text=text):
                self.assertEqual(absent_member_replies(text, self.members), [])

    def test_several_members_in_one_message_get_every_reply_in_mention_order(self):
        cases = {
            "where are John and Maria?": [JOHN_REPLY, MARIA_REPLY],
            "what happened to Maria and John": [MARIA_REPLY, JOHN_REPLY],
            "has anyone seen Maria or Johnny lately": [MARIA_REPLY, JOHN_REPLY],
            "haven't seen John, Maria in ages": [JOHN_REPLY, MARIA_REPLY],
            "Where's Maria? and what happened to John?": [MARIA_REPLY, JOHN_REPLY],
            "did John and Maria leave?": [JOHN_REPLY, MARIA_REPLY],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(absent_member_replies(text, self.members), expected)

    def test_member_asked_about_twice_gets_one_reply(self):
        text = f"where is John? seriously where is <@{JOHN_ID}>"
        self.assertEqual(absent_member_replies(text, self.members), [JOHN_REPLY])

    def test_matching_is_idempotent_across_calls(self):
        text = "where are John and Maria?"
        self.assertEqual(absent_member_replies(text, self.members), absent_member_replies(text, self.members))


class TestAbsentMemberResponder(unittest.IsolatedAsyncioTestCase):
    async def _replies(self, content, env=None, bot=False, guild="default"):
        full_env = {
            "ENABLE_RESP_VO2MAX": "True",
            "ENABLE_RESP_ALEX": "True",
            "ENABLE_RESP_ABSENT": "True",
            **ABSENT_ENV,
            **(env or {}),
        }
        message = MagicMock(spec=discord.Message)
        message.content = content
        message.author.bot = bot
        message.guild = MagicMock() if guild == "default" else guild
        message.reply = AsyncMock()
        with patch.dict(os.environ, full_env), patch("builtins.print"):
            cog = AutoResponder(MagicMock())
            await cog.on_message(message)
        return [call.args[0] for call in message.reply.await_args_list]

    async def test_single_member_gets_one_reply(self):
        self.assertEqual(await self._replies("where is John?"), [JOHN_REPLY])

    async def test_several_members_are_combined_into_one_reply(self):
        self.assertEqual(await self._replies("where are John and Maria?"), [f"{JOHN_REPLY}\n\n{MARIA_REPLY}"])

    async def test_combined_reply_over_discord_limit_is_split_between_members(self):
        long_john, long_maria = "j" * 1500, "m" * 1500
        env = {"ABSENT_REPLY_JOHN": long_john, "ABSENT_REPLY_MARIA": long_maria}
        self.assertEqual(await self._replies("where are John and Maria?", env=env), [long_john, long_maria])

    async def test_responder_can_be_disabled(self):
        self.assertEqual(await self._replies("where is John?", env={"ENABLE_RESP_ABSENT": "False"}), [])

    async def test_bots_and_direct_messages_are_ignored(self):
        self.assertEqual(await self._replies("where is John?", bot=True), [])
        self.assertEqual(await self._replies("where is John?", guild=None), [])

    async def test_alex_answer_takes_priority(self):
        replies = await self._replies("Is Alex here? where is John?")
        self.assertEqual(len(replies), 1)
        self.assertIn("fan-made server", replies[0])


if __name__ == "__main__":
    unittest.main()
