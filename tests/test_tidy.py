"""Run with: python3 -m unittest discover tests"""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
tidy = SourceFileLoader("tidy", str(ROOT / "bin" / "tidy")).load_module()


class Config(unittest.TestCase):
    def test_missing_file_gives_defaults(self):
        cfg = tidy.load_config("/nonexistent/tidy.toml")
        self.assertEqual(cfg["backend"], "claude")
        self.assertEqual(cfg["openai"]["url"], "http://localhost:11434/v1")

    def test_partial_tables_merge_with_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('backend = "openai"\n[openai]\nmodel = "x"\n')
        try:
            cfg = tidy.load_config(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(cfg["backend"], "openai")
        self.assertEqual(cfg["openai"]["model"], "x")
        self.assertEqual(cfg["openai"]["url"], "http://localhost:11434/v1")
        self.assertEqual(cfg["keys"], "CTRL + code:49")

    def test_example_config_parses_and_matches_defaults(self):
        cfg = tidy.load_config(ROOT / "config.example.toml")
        for key in ("backend", "keys", "selection_keys", "notify", "timeout"):
            self.assertEqual(cfg[key], tidy.DEFAULTS[key], key)

    def test_unknown_backend(self):
        with self.assertRaises(RuntimeError):
            tidy.make_backend(dict(tidy.DEFAULTS, backend="nope"))


class Text(unittest.TestCase):
    def test_wrap_unwrap_round_trip(self):
        for s in ["hi", "two\nlines", "- a\n- b"]:
            self.assertEqual(tidy.unwrap(tidy.wrap(s)), s)

    def test_unwrap_plain_answer(self):
        self.assertEqual(tidy.unwrap("  Hello.\n"), "Hello.")

    def test_custom_prompt_still_gets_tag_note(self):
        cfg = dict(tidy.DEFAULTS, prompt="Make it formal.")
        self.assertTrue(tidy.instruction(cfg).startswith("Make it formal."))
        self.assertIn("<text>", tidy.instruction(cfg))


class FakeOpenAI(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenAI.seen.append((self.path, self.headers.get("Authorization"), body))
        user = body["messages"][1]["content"]
        answer = tidy.unwrap(user).replace("tset", "test")
        data = json.dumps({"choices": [{"message": {"content": f"<text>\n{answer}\n</text>"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class OpenAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), FakeOpenAI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/v1"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def cfg(self, **openai):
        return tidy.merge(tidy.DEFAULTS, {"backend": "openai", "openai": dict(url=self.url, model="m", **openai)})

    def test_request_and_answer(self):
        FakeOpenAI.seen.clear()
        out = tidy.make_backend(self.cfg()).clean("a tset")
        self.assertEqual(out, "a test")
        path, auth, body = FakeOpenAI.seen[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertIsNone(auth)
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["messages"][0]["role"], "system")

    def test_key_from_file(self):
        FakeOpenAI.seen.clear()
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("sk-test\n")
        try:
            tidy.make_backend(self.cfg(api_key_file=f.name)).clean("x")
        finally:
            os.unlink(f.name)
        self.assertEqual(FakeOpenAI.seen[0][1], "Bearer sk-test")

    def test_missing_env_key_is_a_clear_error(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TIDY_TEST_KEY", None)
            with self.assertRaisesRegex(RuntimeError, "TIDY_TEST_KEY"):
                tidy.make_backend(self.cfg(api_key_env="TIDY_TEST_KEY")).clean("x")

    def test_missing_model(self):
        cfg = tidy.merge(tidy.DEFAULTS, {"backend": "openai", "openai": {"url": self.url}})
        with self.assertRaisesRegex(RuntimeError, "model"):
            tidy.make_backend(cfg).clean("x")


class Command(unittest.TestCase):
    def test_argv_command_gets_text_and_prompt(self):
        cfg = tidy.merge(tidy.DEFAULTS, {"backend": "command", "prompt": "P!", "command": {
            "cmd": [sys.executable, "-c",
                    "import os,sys; print(os.environ['TIDY_PROMPT'] + '|' + sys.stdin.read().upper())"]}})
        self.assertEqual(tidy.make_backend(cfg).clean("abc"), "P!|ABC")

    def test_shell_string_and_runs_in_empty_dir(self):
        cfg = tidy.merge(tidy.DEFAULTS, {"backend": "command", "command": {"cmd": "ls -A | wc -l"}})
        self.assertEqual(tidy.make_backend(cfg).clean("x").strip(), "0")

    def test_failure_reports_exit_code(self):
        cfg = tidy.merge(tidy.DEFAULTS, {"backend": "command", "command": {"cmd": "echo boom >&2; exit 3"}})
        with self.assertRaisesRegex(RuntimeError, "exited 3: boom"):
            tidy.make_backend(cfg).clean("x")

    def test_empty_command(self):
        cfg = tidy.merge(tidy.DEFAULTS, {"backend": "command"})
        with self.assertRaises(RuntimeError):
            tidy.make_backend(cfg).clean("x")

    def test_api_keys_do_not_leak_into_agents(self):
        cfg = tidy.merge(tidy.DEFAULTS, {"backend": "command", "command": {
            "cmd": "echo ${ANTHROPIC_API_KEY:-none}"}})
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret"}):
            self.assertEqual(tidy.make_backend(cfg).clean("x"), "none")


class Keys(unittest.TestCase):
    def bind(self, cfg, binds):
        calls = []
        with mock.patch.object(tidy, "hypr", calls.append):
            bound, skipped = tidy.bind_keys(dict(tidy.DEFAULTS, **cfg), binds)
        return bound, skipped, " ".join(calls)

    def test_free_keys_are_bound(self):
        bound, skipped, lua = self.bind({"keys": "CTRL + less", "selection_keys": ""}, [])
        self.assertEqual((bound, skipped), (["CTRL + less"], []))
        self.assertIn('hl.bind("CTRL + less", hl.dsp.exec_cmd(', lua)
        self.assertIn(" run", lua)
        self.assertNotIn("--selection", lua)
        self.assertNotIn("unbind", lua)

    def test_someone_elses_key_is_left_alone(self):
        theirs = [{"modmask": 68, "key": "Y", "keycode": 0, "description": "My thing"}]
        bound, skipped, lua = self.bind({"keys": "SUPER + CTRL + Y",
                                         "selection_keys": "SUPER + CTRL + SHIFT + Y"}, theirs)
        self.assertEqual(bound, ["SUPER + CTRL + SHIFT + Y"])
        self.assertEqual(skipped, [("SUPER + CTRL + Y", "My thing")])
        self.assertNotIn('"SUPER + CTRL + Y"', lua)

    def test_own_old_bindings_are_replaced(self):
        old = [{"modmask": 4, "key": "less", "keycode": 0, "description": "Tidy: clean the whole text field"}]
        bound, _, lua = self.bind({}, old)
        self.assertIn('hl.unbind("CTRL + less")', lua)
        self.assertEqual(bound, ["CTRL + code:49", "CTRL + SHIFT + code:49"])

    def test_own_binding_sharing_a_key_with_another_is_not_unbound(self):
        binds = [{"modmask": 4, "key": "less", "keycode": 0, "description": "Tidy: x"},
                 {"modmask": 4, "key": "less", "keycode": 0, "description": "Mine"}]
        _, _, lua = self.bind({"keys": "", "selection_keys": ""}, binds)
        self.assertNotIn("unbind", lua)

    def test_default_key_is_the_key_left_of_1(self):
        binds = [{"modmask": 4, "key": "", "keycode": 49, "description": "VS Code? no, a Hyprland bind"}]
        self.assertEqual(tidy.parse_keys(tidy.DEFAULTS["keys"]), (4, "code:49"))
        self.assertIsNotNone(tidy.key_owner(tidy.DEFAULTS["keys"], binds))

    def test_parse_binds_keeps_key_codes(self):
        text = ("bindd\n\tmodmask: 72\n\tsubmap: \n\tkey: SUPER + ALT + code:35\n\tkeycode: 0\n"
                "\tdescription: Webcam larger\n\tdispatcher: __lua\n\targ: 154\n\n"
                "bindd\n\tmodmask: 4\n\tsubmap: \n\tkey: less\n\tkeycode: 0\n"
                "\tdescription: Tidy: clean the whole text field\n\tdispatcher: __lua\n\targ: 9\n")
        binds = tidy.parse_binds(text)
        self.assertEqual(tidy.bind_id(binds[0]), (72, "code:35"))
        self.assertEqual(tidy.bind_id(binds[1]), (4, "less"))
        self.assertEqual(tidy.key_owner("SUPER + ALT + code:35", binds), "Webcam larger")

    def test_parse_keys_and_back(self):
        self.assertEqual(tidy.parse_keys("SUPER + CTRL + Y"), (68, "y"))
        self.assertEqual(tidy.parse_keys("ctrl+code:94"), (4, "code:94"))
        self.assertEqual(tidy.bind_id({"modmask": 4, "key": "", "keycode": 94}), (4, "code:94"))
        self.assertEqual(tidy.keys_string(69, "g"), "SUPER + CTRL + SHIFT + g")

    def test_lua_string_escapes(self):
        self.assertEqual(tidy.lua_str('a "b" \\ c'), '"a \\"b\\" \\\\ c"')


class AgentCLIs(unittest.TestCase):
    """The safety switches each CLI gets: tools off, no project context."""

    def backend(self, name, **cfg):
        return tidy.make_backend(tidy.merge(tidy.DEFAULTS, dict(backend=name, **cfg)))

    def test_codex_locked_down_and_reads_stdin(self):
        b = self.backend("codex")
        argv = b.argv("m", "/tmp")
        for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "shell_tool", "unified_exec"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")
        self.assertEqual(argv[-1], "-")
        self.assertIn("gpt-6-luna", argv)
        self.assertEqual(b.message("x"), tidy.wrap("x"))

    def test_claude_has_no_tools_or_settings(self):
        argv = self.backend("claude", model="sonnet").argv("m", "/tmp")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertIn("sonnet", argv)

    def test_opencode_agent_denies_everything(self):
        b = self.backend("opencode", model="p/m")
        self.assertEqual(b.argv("m", "/tmp")[-2:], ["-m", "p/m"])
        env = b.env("/run/x")
        agent = json.loads(env["OPENCODE_CONFIG_CONTENT"])["agent"]["tidy"]
        self.assertEqual(agent["permission"], {"*": "deny"})
        self.assertEqual(agent["tools"], {"*": False})
        self.assertEqual(env["OPENCODE_DB"], "/run/x/opencode.db")
        self.assertEqual(env["OPENCODE_DISABLE_CLAUDE_CODE"], "1")

    def test_gemini_policy_file_denies_all_tools(self):
        b = self.backend("gemini")
        with tempfile.TemporaryDirectory() as tmp:
            b.prepare(tmp)
            argv = b.argv("m", tmp)
            policy = Path(argv[argv.index("--policy") + 1]).read_text()
        self.assertIn('toolName = "*"', policy)
        self.assertIn('decision = "deny"', policy)
        self.assertEqual(argv[argv.index("--approval-mode") + 1], "plan")

    def test_text_never_goes_on_the_command_line(self):
        # Process arguments are readable by every local user (ps, /proc/PID/cmdline);
        # the text must only travel on stdin.
        secret = "my private message 4f9c"
        for name, cls in tidy.BACKENDS.items():
            if not issubclass(cls, tidy.CommandBackend):
                continue
            b = self.backend(name, **({"command": {"cmd": ["cat"]}} if name == "command" else {}))
            with tempfile.TemporaryDirectory() as tmp:
                b.prepare(tmp)
                message = b.message(secret)
                argv = b.argv(message, tmp)
                self.assertFalse(any(secret in a for a in argv), name)
                self.assertIn(secret, b.stdin(message), name)

    def test_kimi_backend_was_removed_with_a_hint(self):
        with self.assertRaisesRegex(RuntimeError, "api.moonshot.ai"):
            tidy.make_backend(dict(tidy.DEFAULTS, backend="kimi"))


class Setup(unittest.TestCase):
    def test_render_config_fills_answers_and_keeps_comments(self):
        answers = {"backend": "openai", "keys": "CTRL + less", "selection_keys": "CTRL + SHIFT + greater",
                   "model": "", "openai.url": "http://localhost:11434/v1", "openai.model": "llama3.2:3b"}
        text = tidy.render_config(answers)
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(text)
        try:
            cfg = tidy.load_config(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(cfg["backend"], "openai")
        self.assertEqual(cfg["keys"], "CTRL + less")
        self.assertEqual(cfg["openai"]["model"], "llama3.2:3b")
        self.assertEqual(cfg["model"], "")  # the top-level model, not [openai] model
        self.assertIn("# Which AI cleans the text.", text)

    def test_selection_keys_add_shift(self):
        self.assertEqual(tidy.selection_keys_for("SUPER + ALT + J"), "SUPER + ALT + SHIFT + J")
        self.assertEqual(tidy.selection_keys_for("CTRL + SHIFT + X"), "CTRL + SHIFT + X")

    def test_key_owner(self):
        binds = [{"modmask": 64, "key": "K", "keycode": 0, "description": "Keybindings"},
                 {"modmask": 4, "key": "less", "keycode": 0, "description": "Tidy: x"}]
        self.assertEqual(tidy.key_owner("SUPER + K", binds), "Keybindings")
        self.assertIsNone(tidy.key_owner("CTRL + less", binds))  # Tidy's own
        self.assertIsNone(tidy.key_owner("SUPER + CTRL + Y", binds))

    def test_detect_backends_lists_installed_clis_then_apis(self):
        with mock.patch.object(tidy.shutil, "which", lambda b: "/x" if b in ("codex", "claude") else None), \
             mock.patch.object(tidy, "ollama_models", lambda: None):
            self.assertEqual(tidy.detect_backends(), ["claude", "codex", "openai", "command"])

    def test_preset_keys_have_info_and_backends_have_labels(self):
        for b in ("claude", "codex", "opencode", "gemini", "ollama", "openai", "command"):
            self.assertIn(b, tidy.BACKEND_INFO)


if __name__ == "__main__":
    unittest.main()
