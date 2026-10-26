import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from clawd import llm
from clawd.claude import Haiku, HaikuError
from clawd.settings import Settings


class Server:
    """A fake OpenAI-compatible endpoint that records what it was sent."""

    def __init__(self, status=200, body=None):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                outer.requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(length))))
                payload = json.dumps(
                    body if body is not None else {"choices": [{"message": {"content": "hello there"}}]}
                ).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    made = []

    def make(**kw):
        made.append(Server(**kw))
        return made[-1]

    yield make
    for s in made:
        s.close()


def test_sends_an_openai_style_request(server):
    s = server()
    assert llm.complete(s.base, "sk-test", "my-model", "be Clawd", "hi") == "hello there"
    path, headers, body = s.requests[0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body == {"model": "my-model", "messages": [
        {"role": "system", "content": "be Clawd"}, {"role": "user", "content": "hi"}]}


def test_no_key_means_no_authorization_header_and_openrouter_gets_its_headers(server):
    s = server()
    llm.complete(s.base, "", "m", "s", "p")
    assert "Authorization" not in s.requests[0][1]
    llm.complete(s.base, "k", "m", "s", "p", provider="openrouter")
    headers = {k.lower(): v for k, v in s.requests[1][1].items()}
    assert headers["x-title"] == "Clawd" and "http-referer" in headers


def test_accepts_a_full_endpoint_address_and_list_content(server):
    s = server(body={"choices": [{"message": {"content": [{"type": "text", "text": "a"}, {"text": "b"}]}}]})
    assert llm.complete(s.base + "/chat/completions", "", "m", "s", "p") == "ab"
    assert s.requests[0][0] == "/v1/chat/completions"


@pytest.mark.parametrize("status,fatal", [(401, True), (404, True), (429, False), (500, False)])
def test_http_errors_say_whether_retrying_helps(server, status, fatal):
    s = server(status=status, body={"error": {"message": "nope"}})
    with pytest.raises(llm.ApiError) as err:
        llm.complete(s.base, "k", "m", "s", "p")
    assert err.value.fatal is fatal and "nope" in str(err.value) and str(status) in str(err.value)


def test_bad_settings_fail_before_any_request():
    with pytest.raises(llm.ApiError) as err:
        llm.complete("localhost:1234", "", "m", "s", "p")
    assert err.value.fatal
    with pytest.raises(llm.ApiError):
        llm.complete("http://x", "", "  ", "s", "p")


def test_unreachable_server_is_not_fatal():
    with pytest.raises(llm.ApiError) as err:
        llm.complete("http://127.0.0.1:1/v1", "", "m", "s", "p")
    assert not err.value.fatal


def test_empty_or_odd_answers_are_errors(server):
    for body in ({"choices": []}, {"choices": [{"message": {"content": "  "}}]}, {"nope": 1}):
        s = server(body=body)
        with pytest.raises(llm.ApiError):
            llm.complete(s.base, "", "m", "s", "p")


def test_haiku_uses_the_api_when_configured(server, tmp_path):
    s = server()
    haiku = Haiku(tmp_path, "ru", True)
    haiku.configure("openrouter", s.base, "k", "some/model")
    assert haiku.run(lambda lang: f"say hi in {lang}") == "hello there"
    _, _, body = s.requests[0]
    assert "Russian" in body["messages"][0]["content"] and body["messages"][1]["content"] == "say hi in ru"
    assert haiku.run_web(lambda lang: "x") == "hello there"  # no web tools over the API, but it still answers
    haiku.enabled = False
    with pytest.raises(HaikuError) as err:
        haiku.run(lambda lang: "x")
    assert err.value.fatal


def test_haiku_api_errors_carry_the_fatal_flag(server, tmp_path):
    s = server(status=401, body={"error": {"message": "bad key"}})
    haiku = Haiku(tmp_path, "en", True)
    haiku.configure("openai", s.base, "k", "m")
    with pytest.raises(HaikuError) as err:
        haiku.run(lambda lang: "x")
    assert err.value.fatal and "bad key" in str(err.value)


def test_settings_keep_api_choices_and_fill_presets(tmp_path):
    path = tmp_path / "s.json"
    settings = Settings(language="en", provider="openrouter", api={"openrouter": {"key": "sk-1", "model": "x/y"}})
    settings.save(path)
    loaded = Settings.load(path)
    assert loaded == settings
    conf = loaded.api_config()
    assert conf == {"base": "https://openrouter.ai/api/v1", "key": "sk-1", "model": "x/y"}
    assert loaded.api_config("openai")["model"] == "gpt-4o-mini" and loaded.api_config("openai")["key"] == ""
    path.write_text('{"provider": "weird", "api": {"openai": {"key": 5, "model": "m"}, "bad": 1}}', encoding="utf-8")
    broken = Settings.load(path)
    assert broken.provider == "claude" and broken.api == {"openai": {"model": "m"}}


def test_haiku_tries_other_names_of_the_same_model(tmp_path, monkeypatch):
    from clawd import claude

    calls = []

    def fake_ask(cli, workdir, lang, prompt, web=False, model="haiku"):
        calls.append(model)
        if model != "claude-3-5-haiku-latest":
            raise HaikuError(f"claude failed: There's an issue with the selected model ({model}). It may not exist")
        return "fine"

    monkeypatch.setattr(claude, "ask", fake_ask)
    haiku = Haiku(tmp_path, "en", True)
    haiku.cli = tmp_path / "claude"
    assert haiku.api_model == "claude-haiku-5-5"
    assert haiku.run(lambda lang: "x") == "fine"
    assert calls == [
        "claude-haiku-5-5", "haiku", "claude-haiku-4-5", "claude-haiku-4-5-20251001", "claude-3-5-haiku-latest"
    ]
    assert haiku.api_model == "claude-3-5-haiku-latest"
    assert haiku.run(lambda lang: "x") == "fine" and calls[-1] == "claude-3-5-haiku-latest" and len(calls) == 6


def test_haiku_never_falls_back_to_another_family_and_explains(tmp_path, monkeypatch):
    from clawd import claude

    calls = []

    def fake_ask(cli, workdir, lang, prompt, web=False, model="haiku"):
        calls.append(model)
        raise HaikuError("claude failed: There's an issue with the selected model. It may not exist")

    monkeypatch.setattr(claude, "ask", fake_ask)
    haiku = Haiku(tmp_path, "en", True)
    haiku.cli = tmp_path / "claude"
    with pytest.raises(HaikuError) as err:
        haiku.run(lambda lang: "x")
    assert "" not in calls and "exact Haiku name" in str(err.value) and err.value.fatal
    # A model the user chose explicitly is not swapped for another family.
    calls.clear()
    haiku.api_model = "sonnet"
    with pytest.raises(HaikuError):
        haiku.run(lambda lang: "x")
    assert calls == ["sonnet"]


def test_claude_model_is_part_of_the_cli_arguments(tmp_path, monkeypatch):
    from clawd import claude

    seen = []

    def fake_run(args, **kw):
        seen.append(args)
        return type("R", (), {"returncode": 0, "stdout": b"ok", "stderr": b""})()

    monkeypatch.setattr(claude.subprocess, "run", fake_run)
    claude.ask(tmp_path / "claude", tmp_path, "en", "hi", False, "sonnet")
    claude.ask(tmp_path / "claude", tmp_path, "en", "hi", False, "")
    assert seen[0][seen[0].index("--model") + 1] == "sonnet" and "--model" not in seen[1]
