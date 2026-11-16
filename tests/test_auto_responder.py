import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from cogs.auto_responder import AutoResponder

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


if __name__ == "__main__":
    unittest.main()
