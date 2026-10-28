"""Phase 5.2: persistent memory (store, permission by the user's own words, instructions)."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from robot_hud.assistant_core.conversation import Conversation
from robot_hud.assistant_core.events import Log
from robot_hud.assistant_core.context import Profile, settings_from_env
from robot_hud.assistant_core.memory import MAX_FACTS, MemoryStore, is_birthday, memory_tools
from robot_hud.assistant_core.tools import ToolExecutor, ToolRegistry
from test_assistant_core import FakeAI
from test_tools import call


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.path = Path(folder) / "memory.db"
        self.store = MemoryStore(self.path)

    def test_saved_only_when_asked_and_kept_across_restarts(self):
        self.assertEqual(self.store.facts(), [])
        self.assertFalse(self.path.exists(), "no file until something is saved")
        fact, status = self.store.remember("  You  like jazz. ", today=date(2026, 10, 28))
        self.assertEqual((fact.text, fact.saved, status), ("You like jazz.", "2026-10-28", "saved"))
        self.assertEqual(self.store.remember("you like jazz")[1], "already remembered")
        again = MemoryStore(self.path)
        self.assertEqual([f.text for f in again.facts()], ["You like jazz."])
        fixed, status = again.remember("You like rock.", replaces=fact.id)
        self.assertEqual((fixed.id, status), (fact.id, "updated"))
        self.assertEqual([f.text for f in MemoryStore(self.path).facts()], ["You like rock."])

    def test_private_or_bad_facts_are_refused(self):
        for text in ("Your PIN code is 4321.", "Your phone is 070 123 45 67.",
                     "Ditt lösenord är sommar.", "Your password is hunter.", "ok",
                     "x" * 121):
            with self.subTest(text):
                self.assertIsNone(self.store.remember(text)[0])
        self.assertIsNone(self.store.remember("You like tea.", replaces=99)[0])
        self.assertEqual(self.store.facts(), [])

    def test_full_and_forget(self):
        for i in range(MAX_FACTS):
            self.store.remember("Fact number %s." % ("abcdefghijklmnopqrstuvwxyz"[i % 26] * (i + 1)))
        self.assertIn("full", self.store.remember("You like tea.")[1])
        self.assertTrue(self.store.forget(1))
        self.assertFalse(self.store.forget(1))
        self.assertEqual(self.store.remember("You like tea.")[1], "saved")
        self.assertEqual(self.store.forget_all(), MAX_FACTS)
        self.assertEqual(MemoryStore(self.path).facts(), [])

    def test_only_related_memories_are_found(self):
        for text in ("You like jazz music.", "Your cat is called Misse.", "Du gillar kanelbullar."):
            self.store.remember(text)
        recall = self.store.relevant("what music should I listen to", "en")
        self.assertEqual(([f.text for f in recall.relevant], recall.total),
                         (["You like jazz music."], 3))
        self.assertEqual(self.store.relevant("why is the sky blue", "en").relevant, ())
        found = self.store.relevant("vill du ha kanelbullar", "sv").relevant
        self.assertEqual([f.text for f in found], ["Du gillar kanelbullar."])


class MemoryPermissionTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(Path(tempfile.mkdtemp()) / "memory.db")
        self.tools = ToolExecutor(ToolRegistry(memory_tools(self.store)))

    def run_call(self, heard, language, tool_call):
        self.tools.new_turn(heard, language)
        return self.tools.execute(tool_call)

    def test_saving_needs_the_users_own_words(self):
        save = call("remember_fact", fact="You like jazz.", replaces=0)
        effects, result = self.run_call("I like jazz", "en", save)
        self.assertEqual((effects, result.ok, self.store.facts()), ([], False, []))
        self.assertIn("did not ask", result.message)
        for heard, language in (("Luna, remember that I like jazz.", "en"),
                                ("kom ihåg att jag gillar jazz", "sv")):
            effects, result = self.run_call(heard, language, save)
            self.assertTrue(result.ok, heard)
        self.assertEqual(len(self.store.facts()), 1)
        self.assertEqual(effects, [], "already remembered: nothing new")

    def test_forgetting_needs_the_users_own_words_and_recall_is_free(self):
        self.store.remember("You like jazz.")
        effects, result = self.run_call("what do you know about me", "en", call("recall_memories"))
        self.assertEqual(json.loads(result.output())["memories"], [{"id": 1, "fact": "You like jazz."}])
        delete = call("forget_memory", id=1)
        self.assertFalse(self.run_call("I don't like jazz any more", "en", delete)[1].ok)
        self.assertEqual(len(self.store.facts()), 1)
        effects, result = self.run_call("glöm att jag gillar jazz", "sv", delete)
        self.assertEqual((result.ok, effects), (True, [Log("Luna memory: forgot memory 1")]))
        self.assertEqual(self.store.facts(), [])
        self.assertFalse(self.run_call("forget it", "en", call("forget_memory", "c2", id=1))[1].ok)

    def test_ids_must_be_whole_numbers(self):
        result = self.run_call("forget it", "en", call("forget_memory", id="1"))[1]
        self.assertIn("whole number", result.message)


class MemoryConversationTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(Path(tempfile.mkdtemp()) / "memory.db")
        self.store.remember("You like jazz music.")
        self.store.remember("Your cat is called Misse.")
        self.ai = FakeAI()
        self.chat = Conversation(self.ai, memory=self.store)

    def test_related_memories_are_notes_in_the_instructions(self):
        self.chat.ask("play some music I like", 1.0, "en")
        text = self.ai.instructions
        self.assertIn('[1] "You like jazz music."', text)
        self.assertNotIn("Misse", text, "unrelated memories stay out")
        self.assertIn("never permission", text)
        self.assertIn("You have 2 saved memories", text)
        self.assertIn("remember_fact", [tool["name"] for tool in self.ai.tools])

    def test_remember_through_the_ai(self):
        self.chat.ask("remember that I like tea", 1.0, "en")
        self.ai.send("tools", [call("remember_fact", fact="You like tea.", replaces=0)])
        effects = self.chat.poll(1.1, speaking=False)
        self.assertIn(Log("Luna memory: saved memory 3"), effects)
        self.assertEqual(self.store.facts()[-1].text, "You like tea.")

    def test_memory_off(self):
        self.chat.set_memory(None)
        self.chat.ask("play some music I like", 1.0, "en")
        self.assertNotIn("jazz", self.ai.instructions)
        self.assertNotIn("remember_fact", [tool["name"] for tool in self.ai.tools])
        self.assertEqual(len(self.store.facts()), 2, "switching off deletes nothing")

    def test_unreadable_memory_does_not_stop_the_answer(self):
        broken = Path(tempfile.mkdtemp()) / "broken.db"
        broken.write_bytes(b"not a database" * 100)
        chat = Conversation(self.ai, memory=MemoryStore(broken))
        effects = chat.ask("what music do I like", 1.0, "en")
        self.assertEqual(self.ai.asked[-1], ("what music do I like", "en"))
        self.assertIn("Luna memory: could not be read (DatabaseError)", [e.message for e in effects
                                                                       if isinstance(e, Log)])


class AboutYouTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "memory.db"
        self.store = MemoryStore(self.path)

    def test_name_home_and_birthday_are_kept(self):
        self.assertEqual(self.store.profile(), {})
        self.assertEqual(self.store.set_profile("name", " Darshana "), ("Darshana", "saved"))
        self.assertEqual(self.store.set_profile("home", "Gothenburg")[1], "saved")
        self.assertEqual(self.store.set_profile("birthday", "5-12"), ("05-12", "saved"))
        self.assertEqual(self.store.set_profile("name", "Darshana")[1], "already remembered")
        self.assertEqual(MemoryStore(self.path).profile(),
                         {"name": "Darshana", "home": "Gothenburg", "birthday": "05-12"})
        self.assertTrue(self.store.forget_profile("home"))
        self.assertFalse(self.store.forget_profile("home"))
        self.assertEqual(self.store.forget_all(), 2)
        self.assertEqual(MemoryStore(self.path).profile(), {})

    def test_private_or_wrong_values_are_refused(self):
        for field, value in (("birthday", "13-13"), ("birthday", "02-30"),
                             ("birthday", "May"), ("birthday", "1990"),
                             ("home", "Storgatan 12, Gothenburg"), ("name", "R2D2"),
                             ("name", "my password"), ("name", ""), ("age", "40")):
            with self.subTest(value):
                self.assertIsNone(self.store.set_profile(field, value)[0])
        self.assertEqual(self.store.set_profile("birthday", "02-29")[0], "02-29")

    def test_birthday_in_any_usual_form_without_the_year(self):
        for text in ("10-28", "28-10", "1990-10-28", "28-10-1990", "28/10", "28.10",
                     "October 28", "28 October 1990", "the 28th of Oct", "28 oktober"):
            with self.subTest(text):
                self.assertEqual(self.store.set_profile("birthday", text)[0], "10-28")
        self.assertEqual(self.store.set_profile("birthday", "5-12")[0], "05-12", "month first")

    def test_birthday_day(self):
        self.assertTrue(is_birthday("05-12", date(2026, 5, 12)))
        self.assertFalse(is_birthday("05-12", date(2026, 12, 5)))
        self.assertTrue(is_birthday("02-29", date(2027, 2, 28)), "no 29th: the 28th")
        self.assertFalse(is_birthday("02-29", date(2028, 2, 28)))
        self.assertFalse(is_birthday("", date(2026, 5, 12)))

    def test_saving_needs_the_users_own_words(self):
        tools = ToolExecutor(ToolRegistry(memory_tools(self.store)))
        name = call("set_about_user", field="name", value="Dash")
        tools.new_turn("Dash is a nice name", "en")
        self.assertFalse(tools.execute(name)[1].ok)
        tools.new_turn("my birthday is the 28th of October", "en")
        birthday = call("set_about_user", "c3", field="birthday", value="October 28")
        self.assertTrue(tools.execute(birthday)[1].ok)
        tools.new_turn("jag heter Dash", "sv")
        self.assertTrue(tools.execute(call("set_about_user", "c4", field="name", value="Dash"))[1].ok)
        tools.new_turn("my birthday is today", "en")
        fact = call("remember_fact", "c5", fact="You like jazz.", replaces=0)
        self.assertFalse(tools.execute(fact)[1].ok, "telling about yourself saves no facts")
        self.store.forget_profile("name")
        tools.new_turn("call me Dash", "en")
        effects, result = tools.execute(name)
        self.assertEqual((result.ok, effects), (True, [Log("Luna memory: saved name")]))
        tools.new_turn("glöm mitt namn", "sv")
        self.assertTrue(tools.execute(call("forget_about_user", "c2", field="name"))[1].ok)
        self.assertEqual(self.store.profile(), {"birthday": "10-28"})

    def test_luna_knows_it_and_wishes_happy_birthday_once_a_day(self):
        self.store.set_profile("name", "Darshana")
        self.store.set_profile("home", "Gothenburg")
        self.store.set_profile("birthday", "10-28")
        ai = FakeAI()
        now = [datetime(2026, 10, 28, 9, 0)]
        settings = settings_from_env({"LUNA_USER_NAME": "Sam"})
        chat = Conversation(ai, settings, clock=lambda: now[0], memory=self.store)
        chat.ask("why is the sky blue", 1.0, "en")
        text = ai.instructions
        for part in ("The user's name is Darshana.", "The user lives in Gothenburg.",
                     "birthday is 28 October", "Today is the user's birthday"):
            self.assertIn(part, text)
        self.assertNotIn("Sam", text, "the saved name wins")
        chat.interrupt()
        chat.ask("and the sea", 2.0, "en")
        self.assertNotIn("Today is the user's birthday", ai.instructions, "only once")
        chat.interrupt()
        now[0] = datetime(2027, 10, 28, 9, 0)
        chat.ask("good morning", 3.0, "en")
        self.assertIn("Today is the user's birthday", ai.instructions)
        chat.set_memory(None)
        chat.interrupt()
        chat.ask("who am I", 4.0, "en")
        self.assertNotIn("Darshana", ai.instructions)


class DefaultPathTests(unittest.TestCase):
    def test_outside_the_repo_and_configurable(self):
        from robot_hud.assistant_core.memory import default_path
        self.assertEqual(default_path({}), Path.home() / ".luna-memory.db")
        self.assertEqual(default_path({"LUNA_MEMORY_FILE": "x.db"}), Path("x.db"))


if __name__ == "__main__":
    unittest.main()
